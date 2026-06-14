"""Lamp schedules CRUD."""

from geckohome.database._core import _db


# ── Schedules ──

async def get_schedules() -> list[dict]:
    async with _db() as db:
        async with db.execute("SELECT * FROM schedules") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def save_schedule(id: str, lamp_type: str, hour: int, minute: int, duration_h: float,
                        end_hour: int = 0, end_minute: int = 0):
    async with _db(write=True) as db:
        await db.execute(
            "INSERT INTO schedules (id, lamp_type, hour, minute, end_hour, end_minute, duration_h) VALUES (?,?,?,?,?,?,?)",
            (id, lamp_type, hour, minute, end_hour, end_minute, duration_h),
        )


async def delete_schedule(id: str):
    async with _db(write=True) as db:
        await db.execute("DELETE FROM schedules WHERE id = ?", (id,))


async def set_schedule_paused(id: str, paused: bool):
    async with _db(write=True) as db:
        await db.execute("UPDATE schedules SET paused = ? WHERE id = ?", (int(paused), id))


