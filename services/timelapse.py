import asyncio
import os
import pathlib
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta

import cv2
import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_SUPER_ADMINS

_BASE_DIR = os.path.dirname(os.path.dirname(__file__))
TIMELAPSE_FRAMES_DIR = os.path.join(_BASE_DIR, "timelapse", "frames")


def capture_timelapse_frame():
    from services.motion import monitor as motion_monitor
    frame = motion_monitor.get_latest_frame()
    if frame is None:
        return
    frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%H%M%S")
    folder = os.path.join(TIMELAPSE_FRAMES_DIR, today)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{stamp}.jpg")
    if not cv2.imwrite(path, frame):
        print(f"[Timelapse] failed to write {path}")


def _compile_timelapse(frames_dir: str, fps: int, output_path: str) -> bool:
    """Собирает видео из папки с кадрами. Возвращает True при успехе."""
    frames = sorted(f for f in os.listdir(frames_dir) if f.endswith(".jpg"))
    if len(frames) < 10:
        return False

    # Пишем filelist.txt для concat demuxer
    fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="timelapse_")
    try:
        with os.fdopen(fd, "w") as f:
            for name in frames:
                f.write(f"file '{pathlib.Path(frames_dir, name).as_posix()}'\n")
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-r", str(fps),
                "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,"
                       "pad=720:1280:(ow-iw)/2:(oh-ih)/2",
                "-pix_fmt", "yuv420p",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                output_path,
            ],
            capture_output=True, timeout=300,
        )
        if result.returncode != 0:
            print(f"[Timelapse] ffmpeg error: {result.stderr.decode(errors='replace')[-500:]}")
            return False
        return True
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass


async def _send_video(path: str, caption: str, recipients: set[int]):
    """Отправляет видео в Telegram."""
    if not TELEGRAM_BOT_TOKEN or not recipients:
        return
    async with httpx.AsyncClient(timeout=120) as client:
        for uid in recipients:
            try:
                with open(path, "rb") as f:
                    resp = await client.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo",
                        data={"chat_id": str(uid), "caption": caption},
                        files={"video": ("timelapse.mp4", f, "video/mp4")},
                    )
                data = resp.json()
                if not data.get("ok"):
                    print(f"[Timelapse] sendVideo failed for {uid}: {data.get('description')}")
            except Exception as e:
                print(f"[Timelapse] send error to {uid}: {e}")


async def generate_and_send_timelapse():
    """Генерирует таймлапс из вчерашних кадров и отправляет в Telegram."""
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    frames_dir = os.path.join(TIMELAPSE_FRAMES_DIR, yesterday)

    if not os.path.isdir(frames_dir):
        print(f"[Timelapse] no frames for {yesterday}, skipping")
        return

    frame_count = sum(1 for f in os.listdir(frames_dir) if f.endswith(".jpg"))
    if frame_count < 10:
        print(f"[Timelapse] only {frame_count} frames for {yesterday}, skipping")
        shutil.rmtree(frames_dir, ignore_errors=True)
        return

    print(f"[Timelapse] generating from {frame_count} frames for {yesterday}")

    # Фаза тестирования: 3 варианта скорости → только супер-админам
    fps_variants = [15, 24, 30]
    recipients = TELEGRAM_SUPER_ADMINS

    tmp_files = []
    try:
        for fps in fps_variants:
            fd, out_path = tempfile.mkstemp(suffix=".mp4", prefix=f"timelapse_{fps}fps_")
            os.close(fd)
            tmp_files.append(out_path)
            ok = await asyncio.to_thread(_compile_timelapse, frames_dir, fps, out_path)
            if ok:
                caption = f"🎬 Таймлапс {yesterday} • {fps}fps ({frame_count} кадров)"
                await _send_video(out_path, caption, recipients)
                print(f"[Timelapse] sent {fps}fps to {len(recipients)} recipients")
            else:
                print(f"[Timelapse] ffmpeg failed for {fps}fps")
    finally:
        for p in tmp_files:
            try:
                os.unlink(p)
            except OSError:
                pass
        shutil.rmtree(frames_dir, ignore_errors=True)
