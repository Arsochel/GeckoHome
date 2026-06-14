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

async def log_gecko_zone(zone: str, confidence: float | None = None):
    async with _db(write=True) as db:
        await db.execute(
            "INSERT INTO gecko_zone (id, zone, confidence, updated_at) VALUES (1, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET zone=excluded.zone, confidence=excluded.confidence, updated_at=excluded.updated_at",
            (zone, round(confidence, 3) if confidence is not None else None, datetime.now()),
        )


async def get_gecko_zone() -> tuple[str | None, datetime | None]:
    async with _db() as db:
        async with db.execute("SELECT zone, updated_at FROM gecko_zone WHERE id = 1") as cur:
            row = await cur.fetchone()
            if not row:
                return None, None
            return row["zone"], datetime.fromisoformat(row["updated_at"])


