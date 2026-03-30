"""
Живой мониторинг геккона через YOLO.
Запуск: python gecko_detect.py
Левый клик — рисовать зону (прямоугольник: 2 клика, полигон: много кликов + Enter)
Правый клик — отменить последнюю точку / последнюю зону
"""
import os
import time
import cv2
import numpy as np
from ultralytics import YOLO
from dotenv import load_dotenv

load_dotenv()

MODEL_PATH = os.getenv("YOLO_MODEL_PATH", r"C:\Users\artem\runs\detect\train5\weights\best.pt")
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
print("  Левый клик — добавить точку")
print("  Enter — завершить зону (полигон)")
print("  Правый клик — отменить последнюю точку / последнюю зону")

zone_names  = ["skull", "water", "hammock"]

# Зоны, размеченные вручную (координаты в повёрнутом кадре)
PRESET_ZONES = [
    {"name": "skull",   "pts": [(67, 460), (77, 515), (102, 539), (109, 580), (130, 587), (141, 547), (164, 547), (178, 609), (192, 622), (266, 575), (310, 451), (317, 427), (328, 392), (309, 347), (222, 320), (138, 334), (123, 383), (80, 412), (71, 436)]},
    {"name": "water",   "pts": [(374, 451), (329, 464), (310, 494), (331, 564), (398, 613), (447, 589), (446, 491), (390, 449)]},
    {"name": "hammock", "pts": [(8, 212), (14, 315), (64, 382), (134, 399), (202, 399), (299, 423), (407, 420), (447, 408), (447, 307), (355, 303), (282, 293), (216, 256), (154, 195), (118, 135), (105, 127), (83, 172), (64, 191), (18, 195), (6, 197)]},
]


def _poly_center(pts):
    arr = np.array(pts, dtype=np.float32)
    return int(arr[:, 0].mean()), int(arr[:, 1].mean())


def _relative_to_skull(cx, cy):
    skull = next((z for z in PRESET_ZONES if z["name"] == "skull"), None)
    if skull is None:
        return "other"
    sx, sy = _poly_center(skull["pts"])
    dx, dy = cx - sx, cy - sy
    if abs(dx) > abs(dy):
        return "right of skull" if dx > 0 else "left of skull"
    else:
        return "below skull" if dy > 0 else "above skull"


def _detect_zone(cx, cy):
    for zone in PRESET_ZONES:
        pts = np.array(zone["pts"], dtype=np.int32)
        if cv2.pointPolygonTest(pts, (cx, cy), False) >= 0:
            return zone["name"]
    return _relative_to_skull(cx, cy)

mouse_pos   = [0, 0]
current_pts = []          # точки текущей рисуемой зоны
zones       = []          # list of {"name": str, "pts": [(x,y),...], "poly": bool}
colors      = [(0, 165, 255), (255, 0, 255), (0, 255, 255), (255, 165, 0)]


def _current_zone_name():
    idx = len(zones)
    return zone_names[idx] if idx < len(zone_names) else f"zone{idx}"


def _finish_zone():
    if len(current_pts) < 3:
        print("[Zone] нужно минимум 3 точки")
        return
    name = _current_zone_name()
    zones.append({"name": name, "pts": list(current_pts)})
    print(f"[Zone] '{name}': {current_pts}")
    current_pts.clear()


def _letterbox_params(win_w, win_h):
    """Возвращает (scale, pad_x, pad_y) для letterbox DISP_W×DISP_H в окно win_w×win_h."""
    scale = min(win_w / DISP_W, win_h / DISP_H)
    pad_x = (win_w - int(DISP_W * scale)) // 2
    pad_y = (win_h - int(DISP_H * scale)) // 2
    return scale, pad_x, pad_y


def _on_mouse(event, x, y, flags, param):
    rect = cv2.getWindowImageRect("Gecko Detect")
    win_w, win_h = rect[2], rect[3]
    if win_w and win_h:
        scale, pad_x, pad_y = _letterbox_params(win_w, win_h)
        fx = int((x - pad_x) / scale)
        fy = int((y - pad_y) / scale)
        fx = max(0, min(DISP_W - 1, fx))
        fy = max(0, min(DISP_H - 1, fy))
    else:
        fx, fy = x, y
    mouse_pos[0] = fx
    mouse_pos[1] = fy
    if event == cv2.EVENT_LBUTTONDOWN:
        current_pts.append((x, y))
        print(f"[Zone] точка ({x}, {y})")
    elif event == cv2.EVENT_RBUTTONDOWN:
        if current_pts:
            removed = current_pts.pop()
            print(f"[Zone] убрана точка {removed}")
        elif zones:
            removed = zones.pop()
            print(f"[Zone] удалена зона '{removed['name']}'")


import threading
latest = [None]

def _capture():
    while True:
        ret, f = cap.read()
        if ret:
            latest[0] = f

threading.Thread(target=_capture, daemon=True).start()

DISP_W, DISP_H = 450, 800

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
        gecko_zone = _detect_zone(cx, cy)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.circle(frame, (cx, cy), 4, (0, 255, 0), -1)
        cv2.putText(frame, f"gecko {conf:.2f}", (x1, y1 - 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(frame, gecko_zone, (x1, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

    found = len(results.boxes)
    zone_str = gecko_zone if gecko_zone else "-"
    print(f"\r[YOLO] gecko: {'YES' if found else 'NO '} | зона: {zone_str}   ", end="", flush=True)

    # показать все зоны (preset + нарисованные в сессии)
    all_zones = PRESET_ZONES + zones
    overlay = frame.copy()
    for i, zone in enumerate(all_zones):
        c = colors[i % len(colors)]
        pts = np.array(zone["pts"], dtype=np.int32)
        cv2.fillPoly(overlay, [pts], c)
    cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)
    for i, zone in enumerate(all_zones):
        c = colors[i % len(colors)]
        pts = np.array(zone["pts"], dtype=np.int32)
        cv2.polylines(frame, [pts], isClosed=True, color=c, thickness=1)
        cv2.putText(frame, zone["name"], (zone["pts"][0][0] + 4, zone["pts"][0][1] + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1)

    # текущие точки в процессе рисования
    if current_pts:
        for pt in current_pts:
            cv2.circle(frame, pt, 4, (255, 255, 255), -1)
        if len(current_pts) > 1:
            cv2.polylines(frame, [np.array(current_pts)], isClosed=False,
                          color=(255, 255, 255), thickness=1)
        cv2.line(frame, current_pts[-1], tuple(mouse_pos), (255, 255, 255), 1)

    next_zone = _current_zone_name()
    cv2.putText(frame, f"({mouse_pos[0]}, {mouse_pos[1]}) | next: {next_zone} [Enter]",
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
