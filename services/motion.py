import asyncio
import json
import os
import subprocess
import tempfile
import threading
import time
from datetime import datetime

os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
import cv2
import httpx

from config import CAMERA_RTSP_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_SUPER_ADMINS
import shutil

from database import save_photo, add_motion_event, set_gecko_state

_last_motion_time: datetime | None = None


def _find_dataset_dir() -> str | None:
    """Find gecko-dataset folder by searching drives with known label."""
    import string
    for letter in string.ascii_uppercase:
        path = f"{letter}:\\gecko-dataset\\images"
        if os.path.isdir(path):
            return path
    return None


_DATASET_IMAGES_DIR = _find_dataset_dir()


def get_last_motion_time() -> datetime | None:
    return _last_motion_time

# ── Настройки ──────────────────────────────────────────────────────────────
MOTION_THRESHOLD = 8       # порог разницы пикселей (0–255)
MOTION_MIN_AREA  = 100     # мин. площадь контура чтобы считать движением
MOTION_TIMEOUT   = 45      # секунд без движения → конец сессии
SNAPSHOT_INTERVAL = 10     # секунд между снапшотами во время движения
MIN_FRAMES       = 1       # минимум кадров чтобы отправить видео
# ───────────────────────────────────────────────────────────────────────────


_TG = lambda method: f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"


async def _send_photo_with_approval(photo_bytes: bytes, caption: str) -> str | None:
    """Send photo to super admin with Опубликовать/Пропустить buttons. Returns photo_file_id."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_SUPER_ADMINS:
        return None
    event_id = await add_motion_event("", caption)
    keyboard = json.dumps({"inline_keyboard": [[
        {"text": "✅ Опубликовать", "callback_data": f"motion_pub_{event_id}"},
        {"text": "❌ Пропустить",   "callback_data": f"motion_skip_{event_id}"},
    ]]})
    file_id = None
    for admin_id in TELEGRAM_SUPER_ADMINS:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    _TG("sendPhoto"),
                    data={"chat_id": admin_id, "caption": caption,
                          "reply_markup": keyboard},
                    files={"photo": ("motion.jpg", photo_bytes, "image/jpeg")},
                )
            data = resp.json()
            if data.get("ok") and file_id is None:
                file_id = data["result"]["photo"][-1]["file_id"]
                async with __import__("aiosqlite").connect("gecko.db") as db:
                    await db.execute(
                        "UPDATE motion_events SET photo_file_id = ? WHERE id = ?",
                        (file_id, event_id),
                    )
                    await db.commit()
                print(f"[Motion] photo sent, event_id={event_id}")
        except Exception as e:
            print(f"[Motion] photo send error: {e}")
    return file_id


async def _send_telegram_video(video_path: str, caption: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_SUPER_ADMINS:
        return
    for admin_id in TELEGRAM_SUPER_ADMINS:
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                with open(video_path, "rb") as f:
                    await client.post(
                        _TG("sendVideo"),
                        data={"chat_id": admin_id, "caption": caption},
                        files={"video": ("motion.mp4", f, "video/mp4")},
                    )
            print("[Motion] video sent to Telegram")
        except Exception as e:
            print(f"[Motion] Telegram send error: {e}")


def _compile_video_sync(snapshot_paths: list[str]) -> str | None:
    fd, list_path = tempfile.mkstemp(suffix=".txt")
    fd2, out_path = tempfile.mkstemp(suffix=".mp4", prefix="gecko_motion_")
    os.close(fd)
    os.close(fd2)

    try:
        with open(list_path, "w") as f:
            for p in snapshot_paths:
                # escape backslashes for ffmpeg on Windows
                escaped = p.replace("\\", "/")
                f.write(f"file '{escaped}'\n")
                f.write("duration 1\n")

        result = subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-vf", "transpose=1,scale=1280:-2",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-an",
            "-movflags", "+faststart",
            out_path,
        ], capture_output=True, timeout=120)
    finally:
        try:
            os.unlink(list_path)
        except Exception:
            pass

    if result.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return out_path
    print(f"[Motion] ffmpeg error: {result.stderr.decode()[-300:]}")
    try:
        os.unlink(out_path)
    except Exception:
        pass
    return None


class MotionMonitor:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event = threading.Event()

    async def start(self):
        if not CAMERA_RTSP_URL:
            print("[Motion] no CAMERA_RTSP_URL, skipping")
            return
        self._stop_event.clear()
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._monitor())
        print("[Motion] monitor started")

    async def stop(self):
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._task = None
        print("[Motion] monitor stopped")

    async def _monitor(self):
        while True:
            try:
                await asyncio.to_thread(self._run_sync)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[Motion] loop error: {e}, retry in 15s")
                await asyncio.sleep(15)

    def _run_sync(self):
        cap = cv2.VideoCapture(CAMERA_RTSP_URL, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000)
        if not cap.isOpened():
            raise RuntimeError("Cannot open RTSP stream for motion detection")

        prev_gray      = None
        motion_active  = False
        last_motion_t  = 0.0
        last_snap_t    = 0.0
        snapshots: list[str] = []

        try:
            while not self._stop_event.is_set():
                ret, frame = cap.read()
                if not ret:
                    if self._stop_event.is_set():
                        break
                    time.sleep(1)
                    continue

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray = cv2.GaussianBlur(gray, (21, 21), 0)

                if prev_gray is None:
                    prev_gray = gray
                    continue

                diff = cv2.absdiff(prev_gray, gray)
                _, thresh = cv2.threshold(diff, MOTION_THRESHOLD, 255, cv2.THRESH_BINARY)
                contours, _ = cv2.findContours(
                    thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )
                motion = any(cv2.contourArea(c) > MOTION_MIN_AREA for c in contours)

                now = time.monotonic()

                if motion:
                    last_motion_t = now
                    if not motion_active:
                        motion_active = True
                        global _last_motion_time
                        _last_motion_time = datetime.now()
                        print("[Motion] started")
                        asyncio.run_coroutine_threadsafe(
                            set_gecko_state("roaming"), self._loop
                        )

                    if now - last_snap_t >= SNAPSHOT_INTERVAL:
                        fd, path = tempfile.mkstemp(suffix=".jpg", prefix="gecko_motion_")
                        os.close(fd)
                        cv2.imwrite(path, frame)
                        snapshots.append(path)
                        last_snap_t = now
                        print(f"[Motion] snap #{len(snapshots)}")

                elif motion_active and (now - last_motion_t) >= MOTION_TIMEOUT:
                    motion_active = False
                    captured = snapshots[:]
                    snapshots = []
                    print(f"[Motion] ended — {len(captured)} frames")

                    if len(captured) >= MIN_FRAMES:
                        asyncio.run_coroutine_threadsafe(
                            self._process(captured), self._loop
                        )
                    else:
                        for p in captured:
                            try:
                                os.unlink(p)
                            except Exception:
                                pass

                prev_gray = gray
                if self._stop_event.wait(0.3):
                    break

        finally:
            cap.release()

    async def _process(self, snapshot_paths: list[str]):
        try:
            await set_gecko_state("resting")
            with open(snapshot_paths[0], "rb") as f:
                photo_bytes = f.read()

            caption = f"🦎 Движение! {len(snapshot_paths)} кадров"

            # Первый кадр → в галерею + в Telegram с кнопками одобрения
            await save_photo(photo_bytes, source="motion")
            await _send_photo_with_approval(photo_bytes, caption)

            # Все кадры → копируем в датасет для разметки
            if _DATASET_IMAGES_DIR:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                for i, p in enumerate(snapshot_paths):
                    dst = os.path.join(_DATASET_IMAGES_DIR, f"{ts}_{i:02d}.jpg")
                    shutil.copy2(p, dst)
                print(f"[Dataset] {len(snapshot_paths)} frames → {_DATASET_IMAGES_DIR}")

            # Средний кадр → сохраняем как хайлайт
            mid_path = snapshot_paths[len(snapshot_paths) // 2]
            with open(mid_path, "rb") as f:
                mid_bytes = f.read()
            await save_photo(mid_bytes, source="highlight", caption=f"{len(snapshot_paths)} frames")

            # Видео → сразу суперадмину (без одобрения)
            video_path = await asyncio.to_thread(_compile_video_sync, snapshot_paths)
            if video_path:
                await _send_telegram_video(video_path, f"🎬 {caption}")
                try:
                    os.unlink(video_path)
                except Exception:
                    pass
        except Exception as e:
            print(f"[Motion] process error: {e}")
        finally:
            for p in snapshot_paths:
                try:
                    os.unlink(p)
                except Exception:
                    pass


monitor = MotionMonitor()
