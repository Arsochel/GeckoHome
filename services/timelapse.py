import os
import pathlib
import subprocess
import tempfile
from datetime import datetime

import cv2

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
