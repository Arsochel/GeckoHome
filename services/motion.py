import asyncio
import json
import os
import subprocess
import tempfile
import threading
import time
from datetime import datetime

os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
os.environ["OPENCV_LOG_LEVEL"] = "OFF"
import cv2
import httpx

from config import CAMERA_RTSP_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_SUPER_ADMINS
import shutil

from database import save_photo, add_motion_event, set_gecko_state

_last_motion_time: datetime | None = None


def get_last_motion_time() -> datetime | None:
    return _last_motion_time


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
MOTION_THRESHOLD = 25      # порог разницы пикселей (0–255)
MOTION_MIN_AREA  = 1342    # мин. площадь контура чтобы считать движением
MOTION_DEBUG     = True    # сохранять дебаг-кадр (доступен на /api/motion/debug)
_DEBUG_FRAME_PATH = os.path.join(tempfile.gettempdir(), "gecko_motion_debug.jpg")
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
        self._latest_frame = None

    def get_latest_frame(self):
        return self._latest_frame

    async def start(self):
        if not CAMERA_RTSP_URL:
            print("[Motion] no CAMERA_RTSP_URL, skipping")
            return
        self._stop_event.clear()
        self._loop = asyncio.get_running_loop()
        t = threading.Thread(target=self._thread_loop, daemon=True)
        t.start()
        print("[Motion] monitor started")

    async def stop(self):
        self._stop_event.set()
        print("[Motion] monitor stopped")

    def _thread_loop(self):
        while not self._stop_event.is_set():
            try:
                self._run_sync()
            except Exception as e:
                if not self._stop_event.is_set():
                    print(f"[Motion] loop error: {e}, retry in 15s")
                    self._stop_event.wait(15)

    def _run_sync(self):
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        cap = cv2.VideoCapture(CAMERA_RTSP_URL, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
        if not cap.isOpened():
            raise RuntimeError("Cannot open RTSP stream for motion detection")

        # отдельный поток захвата — всегда держит свежий кадр
        latest_frame: list = [None]
        def _capture_loop():
            while not self._stop_event.is_set():
                ret, f = cap.read()
                if ret:
                    latest_frame[0] = f
                    self._latest_frame = f
        cap_thread = threading.Thread(target=_capture_loop, daemon=True)
        cap_thread.start()

        prev_gray      = None
        motion_active  = False
        last_motion_t  = 0.0
        last_snap_t    = 0.0
        snapshots: list[str] = []

        try:
            while not self._stop_event.is_set():
                frame = latest_frame[0]
                if frame is None:
                    time.sleep(0.1)
                    continue
                latest_frame[0] = None  # сбрасываем чтобы не обрабатывать один кадр дважды

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray = cv2.GaussianBlur(gray, (21, 21), 0)

                if prev_gray is None:
                    prev_gray = gray
                    continue

                diff = cv2.absdiff(prev_gray, gray)
                # маскируем timestamp камеры (нижний левый угол)
                h, w = diff.shape
                diff[int(h * 0.88):, :int(w * 0.35)] = 0
                _, thresh = cv2.threshold(diff, MOTION_THRESHOLD, 255, cv2.THRESH_BINARY)
                contours, _ = cv2.findContours(
                    thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )
                big_contours = [c for c in contours if cv2.contourArea(c) > MOTION_MIN_AREA]
                motion = len(big_contours) > 0

                if MOTION_DEBUG:
                    debug = frame.copy()
                    cv2.drawContours(debug, big_contours, -1, (0, 255, 0), 2)
                    max_area = int(max((cv2.contourArea(c) for c in contours), default=0))
                    label = f"MOTION area={int(max((cv2.contourArea(c) for c in big_contours), default=0))}" if motion else f"quiet max={max_area}"
                    cv2.putText(debug, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0) if motion else (100, 100, 100), 2)
                    cv2.imwrite(_DEBUG_FRAME_PATH, debug)

                now = time.monotonic()

                if motion:
                    last_motion_t = now
                    if not motion_active:
                        motion_active = True
                        global _last_motion_time
                        _last_motion_time = datetime.now()
                        print("\033[92m[Motion] started\033[0m")
                        asyncio.run_coroutine_threadsafe(
                            set_gecko_state("roaming"), self._loop
                        )
                        asyncio.run_coroutine_threadsafe(
                            self._record_and_send(), self._loop
                        )
                    else:
                        _last_motion_time = datetime.now()

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
                    print(f"\033[93m[Motion] ended — {len(captured)} frames\033[0m")

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

    async def _record_and_send(self):
        """Записывает 30-секундный клип при срабатывании и отправляет суперадмину."""
        try:
            from services.camera import clip as camera_clip
            print("[Motion] recording 30s clip...")
            video_path = await camera_clip(30)
            if video_path:
                await _send_telegram_video(video_path, "🦎 Движение!")
                try:
                    os.unlink(video_path)
                except Exception:
                    pass
            else:
                print("[Motion] clip failed")
        except Exception as e:
            print(f"[Motion] record error: {e}")

    async def _process(self, snapshot_paths: list[str]):
        """Сохраняет кадры в датасет и галерею."""
        try:
            await set_gecko_state("resting")

            # Средний кадр → в галерею
            mid_path = snapshot_paths[len(snapshot_paths) // 2]
            with open(mid_path, "rb") as f:
                mid_bytes = f.read()
            await save_photo(mid_bytes, source="motion", caption=f"{len(snapshot_paths)} frames")

            # Все кадры → в датасет
            if _DATASET_IMAGES_DIR:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                for i, p in enumerate(snapshot_paths):
                    dst = os.path.join(_DATASET_IMAGES_DIR, f"{ts}_{i:02d}.jpg")
                    shutil.copy2(p, dst)
                print(f"[Dataset] {len(snapshot_paths)} frames → {_DATASET_IMAGES_DIR}")
        except Exception as e:
            print(f"[Motion] process error: {e}")
        finally:
            for p in snapshot_paths:
                try:
                    os.unlink(p)
                except Exception:
                    pass


monitor = MotionMonitor()
