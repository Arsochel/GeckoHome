import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import aiosqlite

log = logging.getLogger(__name__)

DB_PATH       = "gecko.db"
MEDIA_DB_PATH = "gecko_media.db"


@asynccontextmanager
async def _db(write=False):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db
        if write:
            await db.commit()


@asynccontextmanager
async def _media_db(write=False):
    async with aiosqlite.connect(MEDIA_DB_PATH) as db:
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
                occurred_at DATETIME NOT NULL,
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
            CREATE TABLE IF NOT EXISTS motion_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                photo_file_id TEXT,
                status        TEXT DEFAULT 'pending',
                caption       TEXT
            );
            CREATE TABLE IF NOT EXISTS feedings (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                fed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                notes  TEXT
            );
            CREATE TABLE IF NOT EXISTS gecko_state (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                state      TEXT NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cricket_batches (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                bought_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                count     INTEGER DEFAULT 60
            );
            CREATE TABLE IF NOT EXISTS alert_messages (
                user_id    INTEGER NOT NULL,
                alert_type TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, alert_type)
            );
        """)

        # schedules migrations
        for col in ("end_hour INTEGER NOT NULL DEFAULT 0",
                    "end_minute INTEGER NOT NULL DEFAULT 0",
                    "duration_h REAL NOT NULL DEFAULT 0"):
            try:
                await db.execute(f"ALTER TABLE schedules ADD COLUMN {col}")
            except Exception:
                pass

        # allowed_users migrations
        for col in ("first_name TEXT", "lang TEXT", "blocked_bot INTEGER DEFAULT 0", "blocked_at DATETIME", "revoked INTEGER DEFAULT 0"):
            try:
                await db.execute(f"ALTER TABLE allowed_users ADD COLUMN {col}")
            except Exception:
                pass
        try:
            await db.execute("UPDATE allowed_users SET lang = 'ru' WHERE lang IS NULL")
            await db.execute("UPDATE allowed_users SET lang = 'en' WHERE user_id = 5157476563")
        except Exception:
            pass

        # migrate user_lang table if exists
        try:
            await db.execute("""
                UPDATE allowed_users SET lang = (
                    SELECT lang FROM user_lang WHERE user_lang.user_id = allowed_users.user_id
                ) WHERE EXISTS (
                    SELECT 1 FROM user_lang WHERE user_lang.user_id = allowed_users.user_id
                )
            """)
        except Exception:
            pass

        # migrate user_actions → one row per user (user_id, snapshots, clips_30, clips_3min, streams)
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_actions_new (
                    user_id   INTEGER PRIMARY KEY,
                    snapshots INTEGER NOT NULL DEFAULT 0,
                    clips_30  INTEGER NOT NULL DEFAULT 0,
                    clips_3min INTEGER NOT NULL DEFAULT 0,
                    streams   INTEGER NOT NULL DEFAULT 0
                )
            """)
            async with db.execute("PRAGMA table_info(user_actions)") as cur:
                cols = [r[1] for r in await cur.fetchall()]
            if "action" in cols:
                # migrate from (user_id, action, count) schema
                await db.execute("""
                    INSERT INTO user_actions_new (user_id, snapshots, clips_30, clips_3min, streams)
                    SELECT user_id,
                        COALESCE(SUM(CASE WHEN action='snapshot' THEN count ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN action='clip_30'  THEN count ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN action='clip_180' THEN count ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN action='stream'   THEN count ELSE 0 END), 0)
                    FROM user_actions GROUP BY user_id
                    ON CONFLICT(user_id) DO UPDATE SET
                        snapshots  = snapshots  + excluded.snapshots,
                        clips_30   = clips_30   + excluded.clips_30,
                        clips_3min = clips_3min + excluded.clips_3min,
                        streams    = streams    + excluded.streams
                """)
                await db.execute("DROP TABLE user_actions")
                await db.execute("ALTER TABLE user_actions_new RENAME TO user_actions")
            elif "occurred_at" in cols:
                # migrate from old raw-events schema
                await db.execute("""
                    INSERT INTO user_actions_new (user_id, snapshots, clips_30, clips_3min, streams)
                    SELECT user_id,
                        SUM(action='snapshot'), SUM(action='clip_30'),
                        SUM(action='clip_180'), SUM(action='stream')
                    FROM user_actions GROUP BY user_id
                    ON CONFLICT(user_id) DO UPDATE SET
                        snapshots  = snapshots  + excluded.snapshots,
                        clips_30   = clips_30   + excluded.clips_30,
                        clips_3min = clips_3min + excluded.clips_3min,
                        streams    = streams    + excluded.streams
                """)
                await db.execute("DROP TABLE user_actions")
                await db.execute("ALTER TABLE user_actions_new RENAME TO user_actions")
            else:
                await db.execute("DROP TABLE user_actions_new")
        except Exception:
            pass

        # replace gecko_zone_events with single-row gecko_zone table
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS gecko_zone (
                    id         INTEGER PRIMARY KEY CHECK (id = 1),
                    zone       TEXT NOT NULL,
                    confidence REAL,
                    updated_at DATETIME NOT NULL
                )
            """)
        except Exception:
            pass
        try:
            # migrate latest zone from gecko_zone_events if exists
            async with db.execute(
                "SELECT zone, confidence, occurred_at FROM gecko_zone_events ORDER BY occurred_at DESC LIMIT 1"
            ) as cur:
                row = await cur.fetchone()
            if row:
                await db.execute(
                    "INSERT OR IGNORE INTO gecko_zone (id, zone, confidence, updated_at) VALUES (1,?,?,?)",
                    (row["zone"], row["confidence"], row["occurred_at"]),
                )
        except Exception:
            pass
        try:
            await db.execute("DROP TABLE gecko_zone_events")
        except Exception:
            pass

        # cricket_feedings table
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cricket_feedings (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    fed_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
        except Exception:
            pass

        # drop empty legacy tables
        for t in ("photos",):
            try:
                await db.execute(f"DROP TABLE IF EXISTS {t}")
            except Exception:
                pass

        await db.commit()
    await _init_media_db()


async def _init_media_db():
    async with aiosqlite.connect(MEDIA_DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS photos (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                taken_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                source   TEXT DEFAULT 'web',
                caption  TEXT,
                data     BLOB NOT NULL
            )
        """)
        await db.commit()


# ── User actions ──

_ACTION_COL = {"snapshot": "snapshots", "clip_30": "clips_30", "clip_180": "clips_3min", "stream": "streams"}


async def log_user_action(user_id: int, username: str | None, action: str):
    col = _ACTION_COL.get(action)
    if not col:
        return
    async with _db(write=True) as db:
        await db.execute(
            f"INSERT INTO user_actions (user_id, {col}) VALUES (?, 1)"
            f" ON CONFLICT(user_id) DO UPDATE SET {col} = {col} + 1",
            (user_id,),
        )


async def get_user_stats() -> list[dict]:
    async with _db() as db:
        async with db.execute("""
            SELECT u.username, a.user_id, a.snapshots, a.clips_30, a.clips_3min, a.streams
            FROM user_actions a
            LEFT JOIN allowed_users u ON u.user_id = a.user_id
            ORDER BY a.snapshots + a.clips_30 + a.clips_3min + a.streams DESC
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
            "INSERT INTO lamp_events (occurred_at, lamp_type, action, source) VALUES (?,?,?,?)",
            (datetime.now(), lamp_type, action, source),
        )


async def purge_lamp_events():
    """Удаляет lamp_events старше 2 дней."""
    cutoff = datetime.now() - timedelta(days=2)
    async with _db(write=True) as db:
        cur = await db.execute("DELETE FROM lamp_events WHERE occurred_at < ?", (cutoff,))
        if cur.rowcount:
            log.info("purged %d lamp_events older than 2 days", cur.rowcount)


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
        async with db.execute(
            "SELECT 1 FROM allowed_users WHERE user_id = ? AND revoked = 0", (user_id,)
        ) as cur:
            return await cur.fetchone() is not None


async def was_user_revoked(user_id: int) -> bool:
    """Был ли пользователь заблокировавшим бота и лишён доступа."""
    async with _db() as db:
        async with db.execute(
            "SELECT 1 FROM allowed_users WHERE user_id = ? AND revoked = 1", (user_id,)
        ) as cur:
            return await cur.fetchone() is not None


async def add_allowed_user(user_id: int, username: str = None, first_name: str = None):
    async with _db(write=True) as db:
        await db.execute(
            """INSERT INTO allowed_users (user_id, username, first_name)
               VALUES (?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                   revoked = 0, blocked_bot = 0,
                   username = excluded.username,
                   first_name = excluded.first_name""",
            (user_id, username, first_name),
        )


async def update_user_info(user_id: int, username: str | None, first_name: str | None):
    """Обновляет username и first_name."""
    async with _db(write=True) as db:
        await db.execute(
            "UPDATE allowed_users SET username = ?, first_name = ? WHERE user_id = ?",
            (username, first_name, user_id),
        )


async def remove_allowed_user(user_id: int):
    async with _db(write=True) as db:
        await db.execute("DELETE FROM allowed_users WHERE user_id = ?", (user_id,))


async def set_user_blocked(user_id: int, blocked: bool):
    async with _db(write=True) as db:
        if blocked:
            await db.execute(
                "UPDATE allowed_users SET blocked_bot = 1, blocked_at = ?, revoked = 1 WHERE user_id = ?",
                (datetime.now(), user_id),
            )
        else:
            await db.execute(
                "UPDATE allowed_users SET blocked_bot = 0 WHERE user_id = ?",
                (user_id,),
            )


async def get_blocked_user_ids() -> set[int]:
    async with _db() as db:
        async with db.execute("SELECT user_id FROM allowed_users WHERE blocked_bot = 1") as cur:
            return {r["user_id"] for r in await cur.fetchall()}


async def get_blocked_users() -> list[dict]:
    async with _db() as db:
        async with db.execute(
            "SELECT user_id, username, first_name, blocked_at FROM allowed_users WHERE blocked_bot = 1 ORDER BY blocked_at DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


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


# ── Photos (media db) ──

async def save_photo(data: bytes, source: str = "web", caption: str = None) -> int:
    async with _media_db(write=True) as db:
        cur = await db.execute(
            "INSERT INTO photos (data, source, caption) VALUES (?,?,?)", (data, source, caption)
        )
        return cur.lastrowid


async def get_photos(limit: int = 20, offset: int = 0) -> list[dict]:
    async with _media_db() as db:
        async with db.execute(
            "SELECT id, taken_at, source, caption FROM photos ORDER BY taken_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_photo_data(photo_id: int) -> bytes | None:
    async with _media_db() as db:
        async with db.execute("SELECT data FROM photos WHERE id = ?", (photo_id,)) as cur:
            row = await cur.fetchone()
            return bytes(row["data"]) if row else None


async def delete_photo(photo_id: int):
    async with _media_db(write=True) as db:
        await db.execute("DELETE FROM photos WHERE id = ?", (photo_id,))


async def purge_old_photos():
    async with _media_db(write=True) as db:
        cur = await db.execute("DELETE FROM photos WHERE taken_at < datetime('now', '-1 hour')")
        if cur.rowcount:
            log.info("purged %d photos older than 1h", cur.rowcount)


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


async def update_motion_photo(event_id: int, photo_file_id: str):
    async with _db(write=True) as db:
        await db.execute("UPDATE motion_events SET photo_file_id = ? WHERE id = ?", (photo_file_id, event_id))


# ── Feedings ──

_last_feeding_time: datetime | None = None


async def load_last_feeding():
    global _last_feeding_time
    async with _db() as db:
        async with db.execute("SELECT fed_at FROM feedings ORDER BY fed_at DESC LIMIT 1") as cur:
            row = await cur.fetchone()
            if row:
                _last_feeding_time = datetime.fromisoformat(row["fed_at"])


async def log_feeding(notes: str | None = None):
    global _last_feeding_time
    _last_feeding_time = datetime.now()
    async with _db(write=True) as db:
        await db.execute("INSERT INTO feedings (fed_at, notes) VALUES (?, ?)", (_last_feeding_time, notes))


async def append_feeding_note(note: str):
    """Добавляет заметку к сегодняшнему кормлению; если кормления нет — создаёт новое."""
    async with _db(write=True) as db:
        async with db.execute(
            "SELECT id, notes FROM feedings WHERE DATE(fed_at) = DATE('now', 'localtime') ORDER BY fed_at DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
        if row:
            existing = row["notes"] or ""
            parts = [p for p in existing.split("+") if p]
            if note not in parts:
                parts.append(note)
            await db.execute("UPDATE feedings SET notes = ? WHERE id = ?", ("+".join(parts), row["id"]))
        else:
            await log_feeding(notes=note)


def get_last_feeding_cached() -> datetime | None:
    return _last_feeding_time


async def get_last_feeding_db() -> datetime | None:
    """Читает дату последнего кормления из БД (не из кэша)."""
    async with _db() as db:
        async with db.execute("SELECT fed_at FROM feedings ORDER BY fed_at DESC LIMIT 1") as cur:
            row = await cur.fetchone()
            return datetime.fromisoformat(row["fed_at"]) if row else None


async def get_feeding_count() -> int:
    async with _db() as db:
        async with db.execute("SELECT COUNT(*) as cnt FROM feedings") as cur:
            row = await cur.fetchone()
            return row["cnt"] if row else 0


async def get_last_note_date(note: str) -> datetime | None:
    """Возвращает дату последнего кормления с указанной заметкой (например 'hornworm')."""
    async with _db() as db:
        async with db.execute(
            "SELECT fed_at FROM feedings WHERE notes LIKE ? ORDER BY fed_at DESC LIMIT 1",
            (f"%{note}%",),
        ) as cur:
            row = await cur.fetchone()
            return datetime.fromisoformat(row["fed_at"]) if row else None


async def log_cricket_purchase(count: int = 20):
    async with _db(write=True) as db:
        await db.execute("INSERT INTO cricket_batches (bought_at, count) VALUES (?, ?)", (datetime.now(), count))


async def log_cricket_feeding():
    async with _db(write=True) as db:
        await db.execute("INSERT INTO cricket_feedings (fed_at) VALUES (?)", (datetime.now(),))


async def get_last_cricket_feeding() -> datetime | None:
    async with _db() as db:
        async with db.execute(
            "SELECT fed_at FROM cricket_feedings ORDER BY fed_at DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            return datetime.fromisoformat(row["fed_at"]) if row else None


async def log_cricket_ran_out():
    """Помечает последнюю партию как закончившуюся — сдвигает bought_at так чтобы days_since >= LIFESPAN."""
    LIFESPAN = 6
    ran_out_date = datetime.now() - timedelta(days=LIFESPAN)
    async with _db(write=True) as db:
        async with db.execute("SELECT id FROM cricket_batches ORDER BY bought_at DESC LIMIT 1") as cur:
            row = await cur.fetchone()
        if row:
            await db.execute("UPDATE cricket_batches SET bought_at = ?, count = 0 WHERE id = ?",
                             (ran_out_date, row["id"]))
        else:
            await db.execute("INSERT INTO cricket_batches (bought_at, count) VALUES (?, ?)", (ran_out_date, 0))


async def get_last_cricket_purchase() -> tuple[datetime | None, int]:
    """Возвращает (дата закупки, кол-во) последней партии или (None, 0)."""
    async with _db() as db:
        async with db.execute(
            "SELECT bought_at, count FROM cricket_batches ORDER BY bought_at DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None, 0
            return datetime.fromisoformat(row["bought_at"]), row["count"]


async def get_next_feeding_supplements() -> list[str]:
    """Возвращает список добавок для следующего кормления."""
    supplements = []
    last_vitamins = await get_last_note_date("vitamins")
    if last_vitamins is None or (datetime.now() - last_vitamins).days >= 10:
        supplements.append("vitamins")
    last_hornworm = await get_last_note_date("hornworm")
    if last_hornworm is None or (datetime.now() - last_hornworm).days >= 14:
        supplements.append("hornworm")
    return supplements


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


async def get_zone_stats(hours: int = 24) -> list[dict]:
    """Заглушка — зональная история больше не хранится."""
    return []


async def get_sensor_history(hours: int = 24) -> list[dict]:
    async with _db() as db:
        async with db.execute(
            "SELECT recorded_at, temperature, humidity FROM sensor_readings "
            "WHERE recorded_at >= datetime('now', ? || ' hours') ORDER BY recorded_at",
            (f"-{hours}",),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── Lang ──

async def get_user_lang(user_id: int) -> str | None:
    async with _db() as db:
        async with db.execute("SELECT lang FROM allowed_users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return row["lang"] if row else None


async def set_user_lang(user_id: int, lang: str):
    async with _db(write=True) as db:
        await db.execute(
            "INSERT INTO allowed_users (user_id, lang) VALUES (?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET lang = excluded.lang",
            (user_id, lang),
        )


# ── Feeding history ──

async def get_feeding_history(limit: int = 10) -> list[dict]:
    async with _db() as db:
        async with db.execute(
            "SELECT fed_at, notes FROM feedings ORDER BY fed_at DESC LIMIT ?", (limit,)
        ) as cur:
            return [
                {"fed_at": datetime.fromisoformat(r["fed_at"]), "notes": r["notes"]}
                for r in await cur.fetchall()
            ]


async def get_cricket_stats() -> dict:
    """Суммарная статистика по количеству сверчков."""
    async with _db() as db:
        async with db.execute("SELECT notes FROM feedings WHERE notes LIKE '%crickets:%'") as cur:
            rows = await cur.fetchall()
    total = 0
    count = 0
    for row in rows:
        for part in (row["notes"] or "").split("+"):
            if part.startswith("crickets:"):
                try:
                    total += int(part.split(":", 1)[1])
                    count += 1
                except ValueError:
                    pass
    return {"total": total, "count": count, "avg": round(total / count, 1) if count else 0}


# ── Alert messages ──

async def get_alert_message(user_id: int, alert_type: str) -> int | None:
    async with _db() as db:
        async with db.execute(
            "SELECT message_id FROM alert_messages WHERE user_id=? AND alert_type=?",
            (user_id, alert_type),
        ) as cur:
            row = await cur.fetchone()
            return row["message_id"] if row else None


async def save_alert_message(user_id: int, alert_type: str, message_id: int):
    async with _db(write=True) as db:
        await db.execute(
            "INSERT OR REPLACE INTO alert_messages (user_id, alert_type, message_id) VALUES (?,?,?)",
            (user_id, alert_type, message_id),
        )


async def delete_alert_message(user_id: int, alert_type: str):
    async with _db(write=True) as db:
        await db.execute(
            "DELETE FROM alert_messages WHERE user_id=? AND alert_type=?",
            (user_id, alert_type),
        )
