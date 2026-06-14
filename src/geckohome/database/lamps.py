"""Lamp on/off event log."""

import logging
from datetime import datetime, timedelta

from geckohome.database._core import _db

log = logging.getLogger(__name__)


# ── Lamp events ──

async def log_lamp_event(lamp_type: str, action: str, source: str):
    async with _db(write=True) as db:
        await db.execute(
            "INSERT INTO lamp_events (occurred_at, lamp_type, action, source) VALUES (?,?,?,?)",
            (datetime.now(), lamp_type, action, source),
        )


async def get_last_lamp_states() -> dict[str, bool | None]:
    """Возвращает последнее известное состояние каждой лампы из lamp_events."""
    result = {}
    async with _db() as db:
        for lamp in ("uv", "heat"):
            cur = await db.execute(
                "SELECT action FROM lamp_events WHERE lamp_type=? ORDER BY occurred_at DESC LIMIT 1",
                (lamp,),
            )
            row = await cur.fetchone()
            if row:
                result[lamp] = row[0] == "on"
    return result


async def purge_lamp_events():
    """Удаляет lamp_events старше 2 дней."""
    cutoff = datetime.now() - timedelta(days=2)
    async with _db(write=True) as db:
        cur = await db.execute("DELETE FROM lamp_events WHERE occurred_at < ?", (cutoff,))
        if cur.rowcount:
            log.info("purged %d lamp_events older than 2 days", cur.rowcount)


