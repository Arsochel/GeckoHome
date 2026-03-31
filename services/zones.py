"""Зональная детекция геккона — общая логика для motion monitor и gecko_detect."""
import cv2
import numpy as np

# Разрешение в котором откалиброваны зоны (DISP_W x DISP_H из gecko_detect.py)
ZONE_W, ZONE_H = 450, 800

PRESET_ZONES = [
    {"name": "skull",   "pts": [(67, 460), (77, 515), (102, 539), (109, 580), (130, 587), (141, 547), (164, 547), (178, 609), (192, 622), (266, 575), (310, 451), (317, 427), (328, 392), (309, 347), (222, 320), (138, 334), (123, 383), (80, 412), (71, 436)]},
    {"name": "water",   "pts": [(374, 451), (329, 464), (310, 494), (331, 564), (398, 613), (447, 589), (446, 491), (390, 449)]},
    {"name": "hammock", "pts": [(8, 212), (14, 315), (64, 382), (134, 399), (202, 399), (299, 423), (407, 420), (447, 408), (447, 307), (355, 303), (282, 293), (216, 256), (154, 195), (118, 135), (105, 127), (83, 172), (64, 191), (18, 195), (6, 197)]},
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
    # skull и hammock — по полигону
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
