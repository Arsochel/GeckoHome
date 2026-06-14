import asyncio
import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from datetime import datetime

log = logging.getLogger(__name__)

os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
os.environ["OPENCV_LOG_LEVEL"] = "OFF"
import cv2
import httpx

from geckohome.config import (
    CAMERA_RTSP_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_SUPER_ADMINS, TELEGRAM_ADMINS, YOLO_MODEL_PATH,
    MOTION_THRESHOLD, MOTION_MIN_AREA, MOTION_TIMEOUT, MOTION_DEBUG,
)
from geckohome.database import save_photo, add_motion_event, set_gecko_state, log_gecko_zone, update_motion_photo, DB_PATH, get_blocked_user_ids, set_user_blocked
from geckohome.services.yolo import get_model as _get_yolo
from geckohome.services.zones import detect_zone, ZONE_W, ZONE_H

_last_motion_time: datetime | None = None
_motion_lock = threading.Lock()

yolo_debug_conf: float = 0.8  # пороговый conf для дебаг-стрима, меняется через POST /debug/yolo-conf


def get_last_motion_time() -> datetime | None:
    return _last_motion_time


# ── Настройки (MOTION_THRESHOLD/MIN_AREA/TIMEOUT/DEBUG — из config.py/.env) ──
_DEBUG_FRAME_PATH = os.path.join(tempfile.gettempdir(), "gecko_motion_debug.jpg")
SNAPSHOT_INTERVAL = 10     # секунд между снапшотами во время движения
MIN_FRAMES        = 1      # минимум кадров чтобы отправить видео
YOLO_INTERVAL     = 5      # секунд между запусками YOLO детекции зоны
# ───────────────────────────────────────────────────────────────────────────


_TG_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else ""


def _tg_url(method: str) -> str:
    return f"{_TG_BASE}/{method}"


async def _send_telegram_video(video_path: str, caption: str):
    blocked = await get_blocked_user_ids()
    _motion_recipients = (TELEGRAM_SUPER_ADMINS | TELEGRAM_ADMINS) - blocked
    if not TELEGRAM_BOT_TOKEN or not _motion_recipients:
        return
    for admin_id in _motion_recipients:
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=90) as client:
                    with open(video_path, "rb") as f:
                        r = await client.post(
                            _tg_url("sendVideo"),
                            data={"chat_id": admin_id, "caption": caption},
                            files={"video": ("motion.mp4", f, "video/mp4")},
                        )
                data = r.json()
                if data.get("ok"):
                    log.info("video sent to Telegram (attempt %d)", attempt + 1)
                    await set_user_blocked(admin_id, False)
                elif "blocked" in data.get("description", "").lower():
                    await set_user_blocked(admin_id, True)
                    log.warning("user %s blocked the bot, skipping", admin_id)
                else:
                    log.error("Telegram send failed (attempt %d/3): %s", attempt + 1, data.get("description"))
                    if attempt < 2:
                        await asyncio.sleep(15 * (attempt + 1))
                    continue
                break
            except Exception as e:
                log.error("Telegram send error (attempt %d/3): %s: %s", attempt + 1, type(e).__name__, e)
                if attempt < 2:
                    await asyncio.sleep(15 * (attempt + 1))


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
        except Exception as e:
            log.debug("concat list unlink: %s", e)

    if result.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return out_path
    log.error("ffmpeg error:\n%s", result.stderr.decode()[-300:])
    try:
        os.unlink(out_path)
    except Exception as e:
        log.debug("ffmpeg out unlink: %s", e)
    return None


class MotionMonitor:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event = threading.Event()
        self._latest_frame = None
        self._debug_frame = None
        self._warmup_done = False

    def get_latest_frame(self):
        return self._latest_frame

    def get_debug_frame(self):
        """Returns annotated frame with MOG2 contours; falls back to latest frame."""
        return self._debug_frame if self._debug_frame is not None else self._latest_frame

    def get_warmup_done(self) -> bool:
        return self._warmup_done

    def is_running(self) -> bool:
        return self._loop is not None and not self._stop_event.is_set()

    async def start(self):
        if not CAMERA_RTSP_URL:
            log.warning("no CAMERA_RTSP_URL, skipping motion monitor")
            return
        self._stop_event.clear()
        self._loop = asyncio.get_running_loop()
        # прогреваем YOLO заранее, чтобы не тормозило при первом движении
        if YOLO_MODEL_PATH:
            await asyncio.to_thread(_get_yolo)
        t = threading.Thread(target=self._thread_loop, daemon=True)
        t.start()
        log.info("monitor started")

    async def stop(self):
        self._stop_event.set()
        log.info("monitor stopped")

    def _thread_loop(self):
        while not self._stop_event.is_set():
            try:
                self._run_sync()
            except Exception as e:
                if not self._stop_event.is_set():
                    log.error("loop error: %s, retry in 15s", e)
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

        bg_sub = cv2.createBackgroundSubtractorMOG2(
            history=200, varThreshold=MOTION_THRESHOLD, detectShadows=False
        )
        motion_active    = False
        last_motion_t    = 0.0
        last_snap_t      = 0.0
        last_yolo_t      = 0.0
        snapshots: list[str] = []
        warmup_frames    = 0
        last_frame_sum   = None
        frozen_count     = 0
        FROZEN_LIMIT     = 150   # ~45 сек при 0.3s интервале
        last_frame_t     = time.monotonic()
        NO_FRAME_TIMEOUT = 30.0  # сек без кадров → реконнект

        try:
            while not self._stop_event.is_set():
                frame = latest_frame[0]
                if frame is None:
                    if time.monotonic() - last_frame_t > NO_FRAME_TIMEOUT:
                        raise RuntimeError("no new frame for 30s, reconnecting")
                    time.sleep(0.1)
                    continue
                latest_frame[0] = None
                last_frame_t = time.monotonic()

                # Детектор заморозки: если кадр идентичен предыдущему N раз подряд — реконнект
                frame_sum = int(frame.sum())
                if frame_sum == last_frame_sum:
                    frozen_count += 1
                    if frozen_count >= FROZEN_LIMIT:
                        raise RuntimeError("RTSP stream frozen, reconnecting")
                else:
                    last_frame_sum = frame_sum
                    frozen_count = 0

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray = cv2.GaussianBlur(gray, (11, 11), 0)

                fg_mask = bg_sub.apply(gray)

                # маскируем timestamp камеры (нижний левый угол)
                h, w = fg_mask.shape
                fg_mask[int(h * 0.88):, :int(w * 0.35)] = 0

                # морфология: убираем шум
                kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
                fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)

                contours, _ = cv2.findContours(
                    fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )
                big_contours = [c for c in contours if cv2.contourArea(c) > MOTION_MIN_AREA]

                # MOG2 нужно ~30 кадров чтобы выучить фон — в этот период не детектим
                warmup_frames += 1
                if warmup_frames > 30:
                    self._warmup_done = True
                motion = len(big_contours) > 0 and warmup_frames > 30

                debug = frame.copy()
                cv2.drawContours(debug, big_contours, -1, (0, 255, 0), 2)
                max_area = int(max((cv2.contourArea(c) for c in contours), default=0))
                label = f"MOTION area={int(max((cv2.contourArea(c) for c in big_contours), default=0))}" if motion else f"quiet max={max_area} {'(warmup)' if warmup_frames <= 30 else ''}"
                cv2.putText(debug, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0) if motion else (100, 100, 100), 2)
                self._debug_frame = debug
                if MOTION_DEBUG:
                    cv2.imwrite(_DEBUG_FRAME_PATH, debug)

                now = time.monotonic()

                if motion:
                    last_motion_t = now
                    if not motion_active:
                        motion_active = True
                        global _last_motion_time
                        with _motion_lock:
                            _last_motion_time = datetime.now()
                        log.info("motion started")
                        asyncio.run_coroutine_threadsafe(
                            set_gecko_state("roaming"), self._loop
                        )
                        asyncio.run_coroutine_threadsafe(
                            self._record_and_send(), self._loop
                        )
                    else:
                        with _motion_lock:
                            _last_motion_time = datetime.now()

                    if now - last_snap_t >= SNAPSHOT_INTERVAL:
                        fd, path = tempfile.mkstemp(suffix=".jpg", prefix="gecko_motion_")
                        os.close(fd)
                        cv2.imwrite(path, frame)
                        snapshots.append(path)
                        last_snap_t = now
                        log.debug("snap #%d", len(snapshots))

                elif motion_active and (now - last_motion_t) >= MOTION_TIMEOUT:
                    motion_active = False
                    captured = snapshots[:]
                    snapshots = []
                    log.info("motion ended — %d frames", len(captured))

                    if len(captured) >= MIN_FRAMES:
                        asyncio.run_coroutine_threadsafe(
                            self._process(captured), self._loop
                        )
                    else:
                        for p in captured:
                            try:
                                os.unlink(p)
                            except Exception as e:
                                log.debug("snapshot unlink: %s", e)

                # YOLO зональная детекция каждые YOLO_INTERVAL секунд
                if now - last_yolo_t >= YOLO_INTERVAL:
                    last_yolo_t = now
                    model = _get_yolo()
                    if model is not None:
                        try:
                            zoomed = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
                            zoomed = cv2.resize(zoomed, (ZONE_W, ZONE_H))
                            results = model(zoomed, verbose=False, conf=0.8)[0]
                            if results.boxes:
                                box = max(results.boxes, key=lambda b: float(b.conf[0]))
                                x1, y1, x2, y2 = map(int, box.xyxy[0])
                                conf = float(box.conf[0])
                                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                                zone = detect_zone(cx, cy)
                                asyncio.run_coroutine_threadsafe(
                                    log_gecko_zone(zone, conf), self._loop
                                )
                        except Exception as e:
                            log.error("YOLO error: %s", e)

                if self._stop_event.wait(0.3):
                    break

        finally:
            cap.release()

    async def _record_and_send(self):
        """Записывает 30-секундный клип при срабатывании и отправляет суперадмину."""
        try:
            from geckohome.services.camera import clip as camera_clip
            log.info("recording 30s clip...")
            video_path = await camera_clip(30)
            if video_path:
                await _send_telegram_video(video_path, "🦎 Движение!")
                try:
                    os.unlink(video_path)
                except Exception as e:
                    log.debug("clip unlink: %s", e)
            else:
                log.warning("clip failed")
        except Exception as e:
            log.error("record error: %s", e)

    async def _process(self, snapshot_paths: list[str]):
        """Сохраняет кадры в датасет и галерею."""
        try:
            await set_gecko_state("resting")

            # Средний кадр → в галерею
            mid_path = snapshot_paths[len(snapshot_paths) // 2]
            with open(mid_path, "rb") as f:
                mid_bytes = f.read()
            await save_photo(mid_bytes, source="motion", caption=f"{len(snapshot_paths)} frames")


        except Exception as e:
            log.error("process error: %s", e)
        finally:
            for p in snapshot_paths:
                try:
                    os.unlink(p)
                except Exception as e:
                    log.debug("process snap unlink: %s", e)


monitor = MotionMonitor()
