import os
import cv2
from datetime import datetime

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
