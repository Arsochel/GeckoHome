"""
Живой мониторинг геккона через YOLO.
Запуск: python gecko_detect.py

Клавиши:
  Левый клик — добавить точку
  Правый клик — отменить последнюю точку
  Enter — завершить зону
  1/2/3 — начать перерисовку зоны skull/water/hammock
  Esc — отменить текущее рисование
  Q — выход
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

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|video_codec;h264|thread_type;slice"

model = YOLO(MODEL_PATH)
print(f"[YOLO] model loaded: {MODEL_PATH}")

cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
if not cap.isOpened():
    print("[ERROR] Cannot open RTSP stream")
    exit(1)

print("[YOLO] stream opened, press Q to quit")
print("  1/2/3 — redraw skull/water/hammock zone")
print("  Left click — add point, Enter — finish, Right click — undo, Esc — cancel")

zone_names  = [z["name"] for z in PRESET_ZONES]
colors      = [(0, 165, 255), (255, 0, 255), (0, 255, 255), (255, 165, 0)]

mouse_pos    = [0, 0]
current_pts  = []
editing_zone = [None]   # имя зоны которую сейчас перерисовываем (None = новая)

# рабочая копия PRESET_ZONES — можно изменять
working_zones = [dict(z) for z in PRESET_ZONES]

DB_PATH = os.path.join(os.path.dirname(__file__), "gecko.db")
_last_zone: str | None = None
_last_zone_time: float = 0
_ZONE_DEBOUNCE = 10


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


def _save_zones_to_file():
    """Перезаписывает PRESET_ZONES в services/zones.py."""
    zones_py = os.path.join(os.path.dirname(__file__), "services", "zones.py")
    with open(zones_py) as f:
        src = f.read()

    import re
    new_list = "PRESET_ZONES = [\n"
    for z in working_zones:
        pts_str = ", ".join(f"({x}, {y})" for x, y in z["pts"])
        new_list += f'    {{"name": "{z["name"]}",   "pts": [{pts_str}]}},\n'
    new_list += "]"

    src = re.sub(r"PRESET_ZONES = \[[\s\S]*?\n\]", new_list, src)
    with open(zones_py, "w") as f:
        f.write(src)
    print(f"\n[Zone] saved to services/zones.py")


def _finish_zone():
    global editing_zone
    if len(current_pts) < 3:
        print("[Zone] need at least 3 points")
        return
    name = editing_zone[0]
    if name:
        # заменяем существующую зону
        for z in working_zones:
            if z["name"] == name:
                z["pts"] = list(current_pts)
                print(f"[Zone] updated '{name}': {current_pts}")
                break
    else:
        # добавляем новую (не должно быть нужно обычно)
        idx = len([z for z in working_zones if z["name"] not in zone_names])
        name = f"zone{idx}"
        working_zones.append({"name": name, "pts": list(current_pts)})
        print(f"[Zone] added '{name}': {current_pts}")
    current_pts.clear()
    editing_zone[0] = None
    _save_zones_to_file()


def _start_edit(zone_name: str):
    current_pts.clear()
    editing_zone[0] = zone_name
    print(f"\n[Zone] redrawing '{zone_name}' — click points, Enter to finish, Esc to cancel")


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
    if results.boxes:
        box = max(results.boxes, key=lambda b: float(b.conf[0]))
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

    # полупрозрачная заливка зон
    working_zones_np = [np.array(z["pts"], dtype=np.int32) for z in working_zones]
    overlay = frame.copy()
    for i, pts_np in enumerate(working_zones_np):
        cv2.fillPoly(overlay, [pts_np], colors[i % len(colors)])
    cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)
    for i, (zone, pts_np) in enumerate(zip(working_zones, working_zones_np)):
        c = colors[i % len(colors)]
        # подсвечиваем редактируемую зону ярче
        thickness = 2 if zone["name"] == editing_zone[0] else 1
        cv2.polylines(frame, [pts_np], isClosed=True, color=c, thickness=thickness)
        cv2.putText(frame, zone["name"], (zone["pts"][0][0] + 4, zone["pts"][0][1] + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1)

    if current_pts:
        for pt in current_pts:
            cv2.circle(frame, pt, 4, (255, 255, 255), -1)
        if len(current_pts) > 1:
            cv2.polylines(frame, [np.array(current_pts)], isClosed=False,
                          color=(255, 255, 255), thickness=1)
        cv2.line(frame, current_pts[-1], tuple(mouse_pos), (255, 255, 255), 1)

    # статусная строка
    if editing_zone[0]:
        hint = f"editing: {editing_zone[0]} ({len(current_pts)} pts) | Enter=finish  Esc=cancel"
    else:
        hint = "1=skull  2=water  3=hammock  Q=quit"
    cv2.putText(frame, hint, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)

    cv2.imshow("Gecko Detect", frame)
    key = cv2.waitKey(1) & 0xFF
    if key == ord("q"):
        break
    elif key == ord("1"):
        _start_edit("skull")
    elif key == ord("2"):
        _start_edit("water")
    elif key == ord("3"):
        _start_edit("hammock")
    elif key == 13:  # Enter
        if current_pts:
            _finish_zone()
    elif key == 27:  # Esc
        current_pts.clear()
        editing_zone[0] = None
        print("\n[Zone] cancelled")

cap.release()
cv2.destroyAllWindows()
