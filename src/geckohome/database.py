import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import aiosqlite

from geckohome.paths import DB_PATH, MEDIA_DB_PATH

log = logging.getLogger(__name__)


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
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                fed_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                crickets INTEGER,
                vitamins INTEGER DEFAULT 0,
                hornworm INTEGER DEFAULT 0,
                notes    TEXT
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
            await db.execute("ALTER TABLE cricket_batches ADD COLUMN deaths INTEGER DEFAULT 0")
        except Exception:
            pass

        # feedings migrations (DBs created before these columns existed)
        for col in ("crickets INTEGER", "vitamins INTEGER DEFAULT 0",
                    "hornworm INTEGER DEFAULT 0", "notes TEXT"):
            try:
                await db.execute(f"ALTER TABLE feedings ADD COLUMN {col}")
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

        # drop empty legacy tables
        for t in ("photos",):
            try:
                await db.execute(f"DROP TABLE IF EXISTS {t}")
            except Exception:
                pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS debug_tokens (
                token       TEXT PRIMARY KEY,
                user_id     INTEGER NOT NULL,
                issued_at   TIMESTAMP NOT NULL,
                expires_at  TIMESTAMP NOT NULL,
                revoked     INTEGER DEFAULT 0
            )
        """)

        await db.commit()
    await _init_media_db()


async def create_debug_token(user_id: int, ttl_hours: int = 24) -> str:
    import secrets
    token = secrets.token_urlsafe(16)
    now = datetime.now()
    expires = now + timedelta(hours=ttl_hours)
    async with _db(write=True) as db:
        await db.execute(
            "INSERT INTO debug_tokens (token, user_id, issued_at, expires_at) VALUES (?,?,?,?)",
            (token, user_id, now.isoformat(), expires.isoformat()),
        )
    return token


async def validate_debug_token(token: str) -> int | None:
    if not token:
        return None
    async with _db() as db:
        async with db.execute(
            "SELECT user_id, expires_at, revoked FROM debug_tokens WHERE token=?",
            (token,),
        ) as cur:
            row = await cur.fetchone()
    if not row or row["revoked"]:
        return None
    try:
        expires = datetime.fromisoformat(row["expires_at"])
    except (TypeError, ValueError):
        return None
    if expires < datetime.now():
        return None
    return int(row["user_id"])


async def purge_expired_debug_tokens():
    async with _db(write=True) as db:
        await db.execute(
            "DELETE FROM debug_tokens WHERE expires_at < ?",
            (datetime.now().isoformat(),),
        )


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


# ── Sensor readings ──

async def log_sensor_reading(temperature: float | None, humidity: float | None):
    async with _db(write=True) as db:
        await db.execute(
            "INSERT INTO sensor_readings (temperature, humidity) VALUES (?,?)",
            (temperature, humidity),
        )


async def get_last_sensor_reading() -> tuple[float | None, float | None]:
    async with _db() as db:
        async with db.execute(
            "SELECT temperature, humidity FROM sensor_readings ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None, None
    return row["temperature"], row["humidity"]


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
                "INSERT INTO allowed_users (user_id, blocked_bot, blocked_at, revoked) VALUES (?, 1, ?, 1)"
                " ON CONFLICT(user_id) DO UPDATE SET blocked_bot=1, blocked_at=excluded.blocked_at, revoked=1",
                (user_id, datetime.now()),
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


async def get_motion_events_24h_count() -> int:
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    async with _db() as db:
        async with db.execute(
            "SELECT COUNT(*) AS n FROM motion_events WHERE created_at >= ?",
            (cutoff,),
        ) as cur:
            row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def get_recent_motion_events(limit: int = 20) -> list[dict]:
    async with _db() as db:
        async with db.execute(
            "SELECT created_at, caption, photo_file_id, status FROM motion_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    out = []
    for r in rows:
        ts = None
        try:
            ts = datetime.fromisoformat(r["created_at"]).timestamp()
        except (TypeError, ValueError):
            pass
        out.append({
            "ts": ts,
            "caption": r["caption"],
            "photo_file_id": r["photo_file_id"],
            "status": r["status"],
        })
    return out


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


async def _today_feeding(db):
    async with db.execute(
        "SELECT id, crickets, vitamins, hornworm FROM feedings"
        " WHERE DATE(fed_at) = DATE('now', 'localtime') ORDER BY fed_at DESC LIMIT 1"
    ) as cur:
        return await cur.fetchone()


async def log_feeding(crickets: int | None = None, vitamins: bool = False, hornworm: bool = False):
    global _last_feeding_time
    _last_feeding_time = datetime.now().replace(microsecond=0)
    async with _db(write=True) as db:
        row = await _today_feeding(db)
        if row:
            new_crickets = crickets if crickets is not None else row["crickets"]
            new_vitamins = int(vitamins or row["vitamins"])
            new_hornworm = int(hornworm or row["hornworm"])
            await db.execute(
                "UPDATE feedings SET crickets=?, vitamins=?, hornworm=? WHERE id=?",
                (new_crickets, new_vitamins, new_hornworm, row["id"]),
            )
        else:
            await db.execute(
                "INSERT INTO feedings (fed_at, crickets, vitamins, hornworm) VALUES (?, ?, ?, ?)",
                (_last_feeding_time, crickets, int(vitamins), int(hornworm)),
            )


async def append_feeding_note(note: str):
    """Добавляет заметку к сегодняшнему кормлению; если кормления нет — создаёт новое."""
    col = {"vitamins": "vitamins", "hornworm": "hornworm"}.get(note)
    if col:
        async with _db(write=True) as db:
            row = await _today_feeding(db)
            if row:
                await db.execute(f"UPDATE feedings SET {col}=1 WHERE id=?", (row["id"],))
            else:
                await log_feeding(**{col: True})
    else:
        # неизвестная заметка — игнорируем
        pass


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
    """Возвращает дату последнего кормления с указанной заметкой (vitamins или hornworm)."""
    col = {"vitamins": "vitamins", "hornworm": "hornworm"}.get(note)
    if not col:
        return None
    async with _db() as db:
        async with db.execute(
            f"SELECT fed_at FROM feedings WHERE {col}=1 ORDER BY fed_at DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            return datetime.fromisoformat(row["fed_at"]) if row else None


async def log_cricket_purchase(count: int = 20):
    async with _db(write=True) as db:
        await db.execute("INSERT INTO cricket_batches (bought_at, count) VALUES (?, ?)", (datetime.now(), count))


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
    """Возвращает (дата закупки, кол-во за вычетом смертей) последней партии или (None, 0)."""
    async with _db() as db:
        async with db.execute(
            "SELECT bought_at, count, COALESCE(deaths, 0) as deaths FROM cricket_batches ORDER BY bought_at DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None, 0
            return datetime.fromisoformat(row["bought_at"]), max(0, row["count"] - row["deaths"])


async def get_cricket_remaining() -> int | None:
    """Остаток сверчков: куплено минус сдохло минус съедено с момента последней закупки."""
    bought_at, total = await get_last_cricket_purchase()
    if bought_at is None or total == 0:
        return None
    async with _db() as db:
        async with db.execute(
            "SELECT COALESCE(SUM(crickets), 0) as eaten FROM feedings WHERE fed_at >= ? AND crickets IS NOT NULL",
            (bought_at,),
        ) as cur:
            row = await cur.fetchone()
    return max(0, total - (row["eaten"] or 0))


async def log_cricket_deaths(count: int):
    """Записывает гибель сверчков в текущую партию."""
    async with _db() as db:
        await db.execute(
            "UPDATE cricket_batches SET deaths = COALESCE(deaths, 0) + ? WHERE id = (SELECT id FROM cricket_batches ORDER BY bought_at DESC LIMIT 1)",
            (count,),
        )
        await db.commit()


async def get_feedings_count_since(since: datetime) -> int:
    """Количество кормлений начиная с даты since (не включая)."""
    async with _db() as db:
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM feedings WHERE fed_at > ?", (since.isoformat(),)
        ) as cur:
            row = await cur.fetchone()
            return row["cnt"] if row else 0


async def get_next_feeding_supplements() -> list[str]:
    """Возвращает список добавок для следующего кормления."""
    supplements = []
    last_vitamins = await get_last_note_date("vitamins")
    if last_vitamins is None or await get_feedings_count_since(last_vitamins) >= 1:
        # каждое 2-е кормление (1-2 раза в неделю по статье)
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
            "SELECT fed_at, crickets, vitamins, hornworm FROM feedings ORDER BY fed_at DESC LIMIT ?", (limit,)
        ) as cur:
            return [
                {
                    "fed_at": datetime.fromisoformat(r["fed_at"]),
                    "crickets": r["crickets"],
                    "vitamins": bool(r["vitamins"]),
                    "hornworm": bool(r["hornworm"]),
                }
                for r in await cur.fetchall()
            ]


async def get_cricket_stats() -> dict:
    """Суммарная статистика по количеству сверчков."""
    async with _db() as db:
        async with db.execute(
            "SELECT COALESCE(SUM(crickets), 0) as total, COUNT(*) as count"
            " FROM feedings WHERE crickets IS NOT NULL"
        ) as cur:
            row = await cur.fetchone()
    total, count = row["total"] or 0, row["count"] or 0
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
