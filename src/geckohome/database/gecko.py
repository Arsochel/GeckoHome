"""Gecko state and zone (single-row latest)."""

from datetime import datetime

from geckohome.database._core import _db

# ── Gecko state ──


async def set_gecko_state(state: str):
    now = datetime.now()
    async with _db(write=True) as db:
        await db.execute(
            "INSERT INTO gecko_state (id, state, updated_at) VALUES (1, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
            (state, now),
        )


async def get_gecko_birthday() -> str | None:
    async with _db() as db:
        async with db.execute("SELECT value FROM gecko_profile WHERE key='birthday'") as cur:
            row = await cur.fetchone()
            return row["value"] if row else None


async def get_gecko_state() -> tuple[str | None, datetime | None]:
    async with _db() as db:
        async with db.execute("SELECT state, updated_at FROM gecko_state WHERE id = 1") as cur:
            row = await cur.fetchone()
            if not row:
                return None, None
            return row["state"], datetime.fromisoformat(row["updated_at"])


# ── Gecko zone (single-row, latest only) ──


async def log_gecko_zone(zone: str, confidence: float | None = None, seconds: int = 5):
    """Обновляет текущую зону и копит дневной агрегат времени по зонам.

    ``seconds`` — сколько времени представляет одна детекция (интервал YOLO).
    """
    async with _db(write=True) as db:
        await db.execute(
            "INSERT INTO gecko_zone (id, zone, confidence, updated_at) VALUES (1, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET zone=excluded.zone, confidence=excluded.confidence, updated_at=excluded.updated_at",
            (zone, round(confidence, 3) if confidence is not None else None, datetime.now()),
        )
        await db.execute(
            "INSERT INTO gecko_zone_daily (day, zone, seconds)"
            " VALUES (date('now', 'localtime'), ?, ?)"
            " ON CONFLICT(day, zone) DO UPDATE SET seconds = seconds + excluded.seconds",
            (zone, seconds),
        )


async def get_gecko_zone() -> tuple[str | None, datetime | None]:
    async with _db() as db:
        async with db.execute("SELECT zone, updated_at FROM gecko_zone WHERE id = 1") as cur:
            row = await cur.fetchone()
            if not row:
                return None, None
            return row["zone"], datetime.fromisoformat(row["updated_at"])


async def get_zone_daily(days: int = 30) -> list[dict]:
    """Дневные агрегаты времени по зонам за последние ``days`` дней."""
    async with (
        _db() as db,
        db.execute(
            "SELECT day, zone, seconds FROM gecko_zone_daily"
            " WHERE day >= date('now', 'localtime', ?) ORDER BY day, zone",
            (f"-{days} days",),
        ) as cur,
    ):
        return [dict(r) for r in await cur.fetchall()]
