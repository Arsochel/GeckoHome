"""Зональная детекция: полигоны, эвристика поилки, направления от черепа."""

import pytest

from geckohome.services import zones


@pytest.mark.parametrize(
    ("cx", "cy", "expected"),
    [
        (160, 300, "skull"),  # внутри полигона черепа
        (100, 520, "sauna"),  # внутри полигона сауны
        (zones.WATER_CX, zones.WATER_CY, "water"),  # центр поилки
        (zones.SKULL_CX, 700, "water"),  # низ террариума — ближе к поилке
        (zones.SKULL_CX, 100, "above skull"),
        (330, zones.SKULL_CY, "right of skull"),
        (10, zones.SKULL_CY, "left of skull"),
    ],
)
def test_detect_zone(cx, cy, expected):
    assert zones.detect_zone(cx, cy) == expected


def test_zone_centers_are_inside_calibrated_resolution():
    for cx, cy in [
        (zones.SKULL_CX, zones.SKULL_CY),
        (zones.WATER_CX, zones.WATER_CY),
    ]:
        assert 0 <= cx < zones.ZONE_W
        assert 0 <= cy < zones.ZONE_H
