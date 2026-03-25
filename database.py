from contextlib import asynccontextmanager
from datetime import datetime

import aiosqlite

DB_PATH = "gecko.db"


@asynccontextmanager
async def _db(write=False):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db
        if write:
            await db.commit()


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS schedules (
                id         TEXT PRIMARY KEY,
                lamp_type  TEXT NOT NULL,
                hour       INTEGER NOT NULL,
                minute     INTEGER NOT NULL,
                end_hour   INTEGER NOT NULL DEFAULT 0,
                end_minute INTEGER NOT NULL DEFAULT 0,
                duration_h REAL NOT NULL,
                paused     INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                temperature REAL,
                humidity    REAL
            );
            CREATE TABLE IF NOT EXISTS lamp_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                lamp_type   TEXT NOT NULL,
                action      TEXT NOT NULL,
                source      TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS allowed_users (
                user_id  INTEGER PRIMARY KEY,
                username TEXT,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS access_requests (
                user_id      INTEGER PRIMARY KEY,
                username     TEXT,
                first_name   TEXT,
                requested_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS photos (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                taken_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                source   TEXT DEFAULT 'web',
                data     BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS motion_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                photo_file_id TEXT,
                status        TEXT DEFAULT 'pending',
                caption       TEXT
            );
            CREATE TABLE IF NOT EXISTS feedings (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                fed_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        for col in ("end_hour INTEGER NOT NULL DEFAULT 0",
                    "end_minute INTEGER NOT NULL DEFAULT 0",
                    "duration_h REAL NOT NULL DEFAULT 0"):
            try:
                await db.execute(f"ALTER TABLE schedules ADD COLUMN {col}")
            except Exception:
                pass
        try:
            await db.execute("ALTER TABLE photos ADD COLUMN caption TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE allowed_users ADD COLUMN first_name TEXT")
        except Exception:
            pass
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS gecko_state (
                    id         INTEGER PRIMARY KEY CHECK (id = 1),
                    state      TEXT NOT NULL,
                    updated_at DATETIME NOT NULL
                )
            """)
        except Exception:
            pass
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_actions (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    user_id    INTEGER NOT NULL,
                    username   TEXT,
                    action     TEXT NOT NULL
                )
            """)
        except Exception:
            pass
        await db.commit()


async def log_user_action(user_id: int, username: str | None, action: str):
    async with _db(write=True) as db:
        await db.execute(
            "INSERT INTO user_actions (user_id, username, action) VALUES (?, ?, ?)",
            (user_id, username, action),
        )


async def get_user_stats() -> list[dict]:
    async with _db() as db:
        async with db.execute("""
            SELECT username, user_id,
                SUM(action = 'snapshot')  AS snapshots,
                SUM(action = 'clip_30')   AS clips_30,
                SUM(action = 'clip_180')  AS clips_3min,
                SUM(action = 'stream')    AS streams,
                MAX(occurred_at)          AS last_seen
            FROM user_actions
            GROUP BY user_id
            ORDER BY snapshots + clips_30 + clips_3min + streams DESC
        """) as cur:
            return [dict(r) for r in await cur.fetchall()]


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


# ── Lamp events ──

async def log_lamp_event(lamp_type: str, action: str, source: str):
    async with _db(write=True) as db:
        await db.execute(
            "INSERT INTO lamp_events (lamp_type, action, source) VALUES (?,?,?)",
            (lamp_type, action, source),
        )


# ── Sensor readings ──

async def log_sensor_reading(temperature: float | None, humidity: float | None):
    async with _db(write=True) as db:
        await db.execute(
            "INSERT INTO sensor_readings (temperature, humidity) VALUES (?,?)",
            (temperature, humidity),
        )


# ── Users ──

async def get_allowed_users() -> list[dict]:
    async with _db() as db:
        async with db.execute("SELECT * FROM allowed_users") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def is_user_allowed(user_id: int) -> bool:
    async with _db() as db:
        async with db.execute("SELECT 1 FROM allowed_users WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone() is not None


async def add_allowed_user(user_id: int, username: str = None, first_name: str = None):
    async with _db(write=True) as db:
        await db.execute(
            "INSERT OR IGNORE INTO allowed_users (user_id, username, first_name) VALUES (?,?,?)",
            (user_id, username, first_name),
        )


async def remove_allowed_user(user_id: int):
    async with _db(write=True) as db:
        await db.execute("DELETE FROM allowed_users WHERE user_id = ?", (user_id,))


# ── Access requests ──

async def add_access_request(user_id: int, username: str = None, first_name: str = None):
    async with _db(write=True) as db:
        await db.execute(
            "INSERT OR IGNORE INTO access_requests (user_id, username, first_name) VALUES (?,?,?)",
            (user_id, username, first_name),
        )


async def get_access_requests() -> list[dict]:
    async with _db() as db:
        async with db.execute("SELECT * FROM access_requests") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def remove_access_request(user_id: int):
    async with _db(write=True) as db:
        await db.execute("DELETE FROM access_requests WHERE user_id = ?", (user_id,))


async def has_pending_request(user_id: int) -> bool:
    async with _db() as db:
        async with db.execute("SELECT 1 FROM access_requests WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone() is not None


# ── Photos ──

async def save_photo(data: bytes, source: str = "web", caption: str = None) -> int:
    async with _db(write=True) as db:
        cur = await db.execute(
            "INSERT INTO photos (data, source, caption) VALUES (?,?,?)", (data, source, caption)
        )
        return cur.lastrowid


async def get_photos(limit: int = 20, offset: int = 0) -> list[dict]:
    async with _db() as db:
        async with db.execute(
            "SELECT id, taken_at, source, caption FROM photos ORDER BY taken_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_photo_data(photo_id: int) -> bytes | None:
    async with _db() as db:
        async with db.execute("SELECT data FROM photos WHERE id = ?", (photo_id,)) as cur:
            row = await cur.fetchone()
            return bytes(row["data"]) if row else None


async def delete_photo(photo_id: int):
    async with _db(write=True) as db:
        await db.execute("DELETE FROM photos WHERE id = ?", (photo_id,))


# ── Motion events ──

async def add_motion_event(photo_file_id: str, caption: str) -> int:
    async with _db(write=True) as db:
        cur = await db.execute(
            "INSERT INTO motion_events (photo_file_id, caption) VALUES (?,?)",
            (photo_file_id, caption),
        )
        return cur.lastrowid


async def get_motion_event(event_id: int) -> dict | None:
    async with _db() as db:
        async with db.execute("SELECT * FROM motion_events WHERE id = ?", (event_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_motion_status(event_id: int, status: str):
    async with _db(write=True) as db:
        await db.execute("UPDATE motion_events SET status = ? WHERE id = ?", (status, event_id))


# ── Feedings ──

_last_feeding_time: datetime | None = None


async def load_last_feeding():
    global _last_feeding_time
    async with _db() as db:
        async with db.execute("SELECT fed_at FROM feedings ORDER BY fed_at DESC LIMIT 1") as cur:
            row = await cur.fetchone()
            if row:
                _last_feeding_time = datetime.fromisoformat(row["fed_at"])


async def log_feeding():
    global _last_feeding_time
    _last_feeding_time = datetime.now()
    async with _db(write=True) as db:
        await db.execute("INSERT INTO feedings (fed_at) VALUES (?)", (_last_feeding_time,))


def get_last_feeding_cached() -> datetime | None:
    return _last_feeding_time


# ── Gecko state ──

async def set_gecko_state(state: str):
    now = datetime.now()
    async with _db(write=True) as db:
        await db.execute(
            "INSERT INTO gecko_state (id, state, updated_at) VALUES (1, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
            (state, now),
        )


async def get_gecko_state() -> tuple[str | None, datetime | None]:
    async with _db() as db:
        async with db.execute("SELECT state, updated_at FROM gecko_state WHERE id = 1") as cur:
            row = await cur.fetchone()
            if not row:
                return None, None
            return row["state"], datetime.fromisoformat(row["updated_at"])


async def get_feeding_history(limit: int = 10) -> list[datetime]:
    async with _db() as db:
        async with db.execute(
            "SELECT fed_at FROM feedings ORDER BY fed_at DESC LIMIT ?", (limit,)
        ) as cur:
            return [datetime.fromisoformat(r["fed_at"]) for r in await cur.fetchall()]
