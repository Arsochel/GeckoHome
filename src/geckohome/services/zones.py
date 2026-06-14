"""Зональная детекция геккона — общая логика для motion monitor и gecko_detect."""
import cv2
import numpy as np

# Разрешение в котором откалиброваны зоны (DISP_W x DISP_H из gecko_detect.py)
ZONE_W, ZONE_H = 450, 800

PRESET_ZONES = [
    {"name": "skull",   "pts": [(86, 219), (72, 238), (79, 266), (80, 311), (90, 326), (208, 400), (263, 375), (271, 353), (264, 308), (241, 281), (225, 243), (184, 208), (127, 210)]},
    {"name": "water",   "pts": [(387, 484), (349, 519), (347, 557), (353, 588), (437, 637), (449, 618), (449, 491), (435, 481), (405, 480)]},
    {"name": "sauna",   "pts": [(12, 441), (47, 400), (118, 408), (212, 474), (250, 588), (203, 653), (57, 660), (8, 608), (3, 485)]},
]

PRESET_ZONES_NP = [np.array(z["pts"], dtype=np.int32) for z in PRESET_ZONES]

_skull_pts = next(z["pts"] for z in PRESET_ZONES if z["name"] == "skull")
SKULL_CX   = int(np.mean([p[0] for p in _skull_pts]))
SKULL_CY   = int(np.mean([p[1] for p in _skull_pts]))

_water_pts = next(z["pts"] for z in PRESET_ZONES if z["name"] == "water")
WATER_CX   = int(np.mean([p[0] for p in _water_pts]))
WATER_CY   = int(np.mean([p[1] for p in _water_pts]))


def _dist2(ax, ay, bx, by) -> float:
    return (ax - bx) ** 2 + (ay - by) ** 2


def detect_zone(cx: int, cy: int) -> str:
    """Определяет зону по центру bbox в координатах ZONE_W x ZONE_H."""
    # skull и sauna — по полигону
    for i, pts_np in enumerate(PRESET_ZONES_NP):
        if PRESET_ZONES[i]["name"] == "water":
            continue
        if cv2.pointPolygonTest(pts_np, (cx, cy), False) >= 0:
            return PRESET_ZONES[i]["name"]
    # water — по расстоянию: если геккон ближе к поилке чем к черепу → у поилки
    if _dist2(cx, cy, WATER_CX, WATER_CY) < _dist2(cx, cy, SKULL_CX, SKULL_CY):
        return "water"
    dx, dy = cx - SKULL_CX, cy - SKULL_CY
    if abs(dx) > abs(dy):
        return "right of skull" if dx > 0 else "left of skull"
    return "below skull" if dy > 0 else "above skull"
