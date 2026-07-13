"""Дневные агрегаты времени геккона по зонам."""

from geckohome.database import get_gecko_zone, get_zone_daily, log_gecko_zone
from geckohome.database._core import _db


async def test_zone_time_accumulates():
    await log_gecko_zone("skull", 0.9, seconds=5)
    await log_gecko_zone("skull", 0.8, seconds=5)
    await log_gecko_zone("water", 0.95, seconds=5)

    rows = await get_zone_daily(days=1)
    by_zone = {r["zone"]: r["seconds"] for r in rows}
    assert by_zone == {"skull": 10, "water": 5}


async def test_latest_zone_still_updated():
    await log_gecko_zone("sauna", 0.9)
    zone, updated = await get_gecko_zone()
    assert zone == "sauna"
    assert updated is not None


async def test_window_excludes_old_days():
    await log_gecko_zone("skull", 0.9, seconds=60)
    async with _db(write=True) as db:
        await db.execute(
            "INSERT INTO gecko_zone_daily (day, zone, seconds)"
            " VALUES (date('now', 'localtime', '-40 days'), 'water', 999)"
        )
    rows = await get_zone_daily(days=30)
    zones = {r["zone"] for r in rows}
    assert "water" not in zones
    assert "skull" in zones

    rows_all = await get_zone_daily(days=365)
    assert {r["zone"] for r in rows_all} == {"skull", "water"}


async def test_empty_returns_empty_list():
    assert await get_zone_daily() == []
