"""Sensor readings and history."""

from geckohome.database._core import _db

# ── Sensor readings ──


async def log_sensor_reading(temperature: float | None, humidity: float | None):
    async with _db(write=True) as db:
        await db.execute(
            "INSERT INTO sensor_readings (temperature, humidity) VALUES (?,?)",
            (temperature, humidity),
        )


async def get_last_sensor_reading() -> tuple[float | None, float | None]:
    async with (
        _db() as db,
        db.execute(
            "SELECT temperature, humidity FROM sensor_readings ORDER BY id DESC LIMIT 1"
        ) as cur,
    ):
        row = await cur.fetchone()
    if not row:
        return None, None
    return row["temperature"], row["humidity"]


async def get_sensor_history(hours: int = 24) -> list[dict]:
    async with (
        _db() as db,
        db.execute(
            "SELECT recorded_at, temperature, humidity FROM sensor_readings "
            "WHERE recorded_at >= datetime('now', ? || ' hours') ORDER BY recorded_at",
            (f"-{hours}",),
        ) as cur,
    ):
        return [dict(r) for r in await cur.fetchall()]
