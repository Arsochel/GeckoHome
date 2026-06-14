"""Database schema creation and in-place migrations."""

import aiosqlite

from geckohome.paths import DB_PATH, MEDIA_DB_PATH


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
        for col in (
            "end_hour INTEGER NOT NULL DEFAULT 0",
            "end_minute INTEGER NOT NULL DEFAULT 0",
            "duration_h REAL NOT NULL DEFAULT 0",
        ):
            try:
                await db.execute(f"ALTER TABLE schedules ADD COLUMN {col}")
            except Exception:
                pass

        # allowed_users migrations
        for col in (
            "first_name TEXT",
            "lang TEXT",
            "blocked_bot INTEGER DEFAULT 0",
            "blocked_at DATETIME",
            "revoked INTEGER DEFAULT 0",
        ):
            try:
                await db.execute(f"ALTER TABLE allowed_users ADD COLUMN {col}")
            except Exception:
                pass
        try:
            await db.execute("ALTER TABLE cricket_batches ADD COLUMN deaths INTEGER DEFAULT 0")
        except Exception:
            pass

        # feedings migrations (DBs created before these columns existed)
        for col in (
            "crickets INTEGER",
            "vitamins INTEGER DEFAULT 0",
            "hornworm INTEGER DEFAULT 0",
            "notes TEXT",
        ):
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
