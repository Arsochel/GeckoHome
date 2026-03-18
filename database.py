import aiosqlite

DB_PATH = "gecko.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id          TEXT PRIMARY KEY,
                lamp_type   TEXT NOT NULL,
                hour        INTEGER NOT NULL,
                minute      INTEGER NOT NULL,
                end_hour    INTEGER NOT NULL DEFAULT 0,
                end_minute  INTEGER NOT NULL DEFAULT 0,
                duration_h  REAL NOT NULL,
                paused      INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                temperature REAL,
                humidity    REAL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS access_requests (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                requested_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS allowed_users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                added_at    DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS lamp_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                lamp_type   TEXT NOT NULL,
                action      TEXT NOT NULL,
                source      TEXT NOT NULL
            )
        """)
        for col in ("end_hour INTEGER NOT NULL DEFAULT 0", "end_minute INTEGER NOT NULL DEFAULT 0", "duration_h REAL NOT NULL DEFAULT 0"):
            try:
                await db.execute(f"ALTER TABLE schedules ADD COLUMN {col}")
            except Exception:
                pass
        await db.commit()

async def get_schedules() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM schedules") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def save_schedule(id: str, lamp_type: str, hour: int, minute: int, duration_h: float,
                        end_hour: int = 0, end_minute: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO schedules (id, lamp_type, hour, minute, end_hour, end_minute, duration_h) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (id, lamp_type, hour, minute, end_hour, end_minute, duration_h)
        )
        await db.commit()

async def delete_schedule(id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM schedules WHERE id = ?", (id,))
        await db.commit()

async def set_schedule_paused(id: str, paused: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE schedules SET paused = ? WHERE id = ?", (int(paused), id))
        await db.commit()

async def log_lamp_event(lamp_type: str, action: str, source: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO lamp_events (lamp_type, action, source) VALUES (?, ?, ?)",
            (lamp_type, action, source)
        )
        await db.commit()

async def get_allowed_users() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM allowed_users") as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def is_user_allowed(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM allowed_users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone() is not None


async def add_allowed_user(user_id: int, username: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO allowed_users (user_id, username) VALUES (?, ?)",
            (user_id, username)
        )
        await db.commit()


async def remove_allowed_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM allowed_users WHERE user_id = ?", (user_id,))
        await db.commit()


async def add_access_request(user_id: int, username: str = None, first_name: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO access_requests (user_id, username, first_name) VALUES (?, ?, ?)",
            (user_id, username, first_name)
        )
        await db.commit()


async def get_access_requests() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM access_requests") as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def remove_access_request(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM access_requests WHERE user_id = ?", (user_id,))
        await db.commit()


async def has_pending_request(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM access_requests WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone() is not None


async def log_sensor_reading(temperature: float | None, humidity: float | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO sensor_readings (temperature, humidity) VALUES (?, ?)",
            (temperature, humidity)
        )
        await db.commit()
