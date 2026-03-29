"""
Живой мониторинг геккона через YOLO.
Запуск: python gecko_detect.py
"""
import os
import time
import cv2
from ultralytics import YOLO
from dotenv import load_dotenv

load_dotenv()

MODEL_PATH = r"C:\Users\artem\runs\detect\train3\weights\best.pt"
RTSP_URL   = os.getenv("CAMERA_RTSP_URL", "")

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

model = YOLO(MODEL_PATH)
print(f"[YOLO] model loaded: {MODEL_PATH}")

cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
if not cap.isOpened():
    print("[ERROR] Cannot open RTSP stream")
    exit(1)

print("[YOLO] stream opened, press Q to quit")

latest = [None]

import threading
def _capture():
    while True:
        ret, f = cap.read()
        if ret:
            latest[0] = f
threading.Thread(target=_capture, daemon=True).start()

while True:
    frame = latest[0]
    if frame is None:
        time.sleep(0.05)
        continue
    latest[0] = None

    frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    results = model(frame, verbose=False, conf=0.7)[0]

    for box in results.boxes:
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        conf = float(box.conf[0])
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(frame, f"gecko {conf:.2f}", (x1, y1 - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

    found = len(results.boxes)
    print(f"\r[YOLO] gecko: {'YES' if found else 'NO '} | detections: {found}", end="", flush=True)

    cv2.namedWindow("Gecko Detect", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Gecko Detect", 450, 800)
    cv2.imshow("Gecko Detect", frame)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()
