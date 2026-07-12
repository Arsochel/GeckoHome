"""Healthcheck веб-процесса из бота: staleness по sensor_readings + переходы алерта."""

from geckohome.bot.health import OK_MSG, STALE_MSG, HealthMonitor, is_web_stale
from geckohome.database import log_sensor_reading
from geckohome.database._core import _db


async def test_empty_db_is_stale():
    assert await is_web_stale() is True


async def test_fresh_reading_is_not_stale():
    await log_sensor_reading(253, 45)
    assert await is_web_stale() is False


async def test_old_reading_is_stale():
    await log_sensor_reading(253, 45)
    async with _db(write=True) as db:
        await db.execute("UPDATE sensor_readings SET recorded_at = datetime('now', '-2 hours')")
    assert await is_web_stale() is True


def test_monitor_alerts_once_and_recovers():
    m = HealthMonitor()
    assert m.transition(False) is None  # всё ок — тишина
    assert m.transition(True) == STALE_MSG  # упало — алерт
    assert m.transition(True) is None  # всё ещё лежит — не спамим
    assert m.transition(False) == OK_MSG  # поднялось — отбой
    assert m.transition(False) is None  # и снова тишина
