"""
Живой мониторинг геккона через YOLO.
Запуск: python gecko_detect.py
Левый клик — добавить точку зоны, Enter — завершить, правый клик — отменить
"""
import os
import time
import sqlite3
import threading
import cv2
import numpy as np
from ultralytics import YOLO
from dotenv import load_dotenv

load_dotenv()

MODEL_PATH = os.getenv("YOLO_MODEL_PATH", r"C:\Users\artem\runs\detect\train5\weights\best.pt")
RTSP_URL   = os.getenv("CAMERA_RTSP_URL", "")

from services.zones import PRESET_ZONES, PRESET_ZONES_NP, SKULL_CX, SKULL_CY, ZONE_W, ZONE_H, detect_zone
DISP_W, DISP_H = ZONE_W, ZONE_H

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

model = YOLO(MODEL_PATH)
print(f"[YOLO] model loaded: {MODEL_PATH}")

cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
if not cap.isOpened():
    print("[ERROR] Cannot open RTSP stream")
    exit(1)

print("[YOLO] stream opened, press Q to quit")
print("  Left click — add point, Enter — finish zone, Right click — undo")

zone_names  = [z["name"] for z in PRESET_ZONES]
colors      = [(0, 165, 255), (255, 0, 255), (0, 255, 255), (255, 165, 0)]

mouse_pos   = [0, 0]
current_pts = []  # точки текущей рисуемой зоны
zones       = []  # list of {"name": str, "pts": [(x,y),...]}

DB_PATH = os.path.join(os.path.dirname(__file__), "gecko.db")
_last_zone: str | None = None
_last_zone_time: float = 0
_ZONE_DEBOUNCE = 10  # секунд — не писать одну и ту же зону повторно


def _log_zone(zone: str, conf: float):
    global _last_zone, _last_zone_time
    now = time.time()
    if zone == _last_zone and now - _last_zone_time < _ZONE_DEBOUNCE:
        return
    _last_zone = zone
    _last_zone_time = now
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "INSERT INTO gecko_zone_events (zone, confidence) VALUES (?, ?)",
            (zone, round(conf, 3)),
        )
        con.commit()
        con.close()
    except Exception as e:
        print(f"\n[Zone DB] error: {e}")



def _current_zone_name():
    idx = len(zones)
    return zone_names[idx] if idx < len(zone_names) else f"zone{idx}"


def _finish_zone():
    if len(current_pts) < 3:
        print("[Zone] need at least 3 points")
        return
    name = _current_zone_name()
    zones.append({"name": name, "pts": list(current_pts)})
    print(f"[Zone] '{name}': {current_pts}")
    current_pts.clear()


def _on_mouse(event, x, y, flags, param):
    rect = cv2.getWindowImageRect("Gecko Detect")
    win_w, win_h = rect[2], rect[3]
    if win_w and win_h:
        fx = max(0, min(DISP_W - 1, int(x * DISP_W / win_w)))
        fy = max(0, min(DISP_H - 1, int(y * DISP_H / win_h)))
    else:
        fx, fy = x, y
    mouse_pos[0] = fx
    mouse_pos[1] = fy
    if event == cv2.EVENT_LBUTTONDOWN:
        current_pts.append((fx, fy))
        print(f"[Zone] point ({fx}, {fy})")
    elif event == cv2.EVENT_RBUTTONDOWN:
        if current_pts:
            print(f"[Zone] removed point {current_pts.pop()}")
        elif zones:
            print(f"[Zone] removed zone '{zones.pop()['name']}'")


latest = [None]

def _capture():
    while True:
        ret, f = cap.read()
        if ret:
            latest[0] = f

threading.Thread(target=_capture, daemon=True).start()

cv2.namedWindow("Gecko Detect", cv2.WINDOW_NORMAL)
cv2.resizeWindow("Gecko Detect", DISP_W, DISP_H)
cv2.setMouseCallback("Gecko Detect", _on_mouse)

while True:
    frame = latest[0]
    if frame is None:
        time.sleep(0.05)
        continue
    latest[0] = None

    frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    frame = cv2.resize(frame, (DISP_W, DISP_H))
    results = model(frame, verbose=False, conf=0.7)[0]

    gecko_zone = None
    for box in results.boxes:
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        conf = float(box.conf[0])
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        gecko_zone = detect_zone(cx, cy)
        _log_zone(gecko_zone, conf)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.circle(frame, (cx, cy), 4, (0, 255, 0), -1)
        cv2.putText(frame, f"gecko {conf:.2f}", (x1, y1 - 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(frame, gecko_zone, (x1, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

    zone_str = gecko_zone if gecko_zone else "-"
    print(f"\r[YOLO] gecko: {'YES' if gecko_zone else 'NO '} | zone: {zone_str}   ", end="", flush=True)

    # полупрозрачная заливка зон за один проход
    all_zones    = PRESET_ZONES + zones
    all_zones_np = PRESET_ZONES_NP + [np.array(z["pts"], dtype=np.int32) for z in zones]
    overlay = frame.copy()
    for i, pts_np in enumerate(all_zones_np):
        cv2.fillPoly(overlay, [pts_np], colors[i % len(colors)])
    cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)
    for i, (zone, pts_np) in enumerate(zip(all_zones, all_zones_np)):
        c = colors[i % len(colors)]
        cv2.polylines(frame, [pts_np], isClosed=True, color=c, thickness=1)
        cv2.putText(frame, zone["name"], (zone["pts"][0][0] + 4, zone["pts"][0][1] + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1)

    if current_pts:
        for pt in current_pts:
            cv2.circle(frame, pt, 4, (255, 255, 255), -1)
        if len(current_pts) > 1:
            cv2.polylines(frame, [np.array(current_pts)], isClosed=False,
                          color=(255, 255, 255), thickness=1)
        cv2.line(frame, current_pts[-1], tuple(mouse_pos), (255, 255, 255), 1)

    cv2.putText(frame, f"({mouse_pos[0]}, {mouse_pos[1]}) | next: {_current_zone_name()} [Enter]",
                (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)

    cv2.imshow("Gecko Detect", frame)
    key = cv2.waitKey(1) & 0xFF
    if key == ord("q"):
        break
    elif key == 13:  # Enter
        if current_pts:
            _finish_zone()

cap.release()
cv2.destroyAllWindows()
