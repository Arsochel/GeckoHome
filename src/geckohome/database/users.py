"""Users: access control, blocking, debug tokens, action stats, language."""

from datetime import datetime, timedelta

from geckohome.database._core import _db


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
    async with (
        _db() as db,
        db.execute(
            "SELECT user_id, expires_at, revoked FROM debug_tokens WHERE token=?",
            (token,),
        ) as cur,
    ):
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


# ── User actions ──

_ACTION_COL = {
    "snapshot": "snapshots",
    "clip_30": "clips_30",
    "clip_180": "clips_3min",
    "stream": "streams",
}


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


# ── Users ──


async def get_allowed_users() -> list[dict]:
    async with _db() as db, db.execute("SELECT * FROM allowed_users") as cur:
        return [dict(r) for r in await cur.fetchall()]


async def is_user_allowed(user_id: int) -> bool:
    async with (
        _db() as db,
        db.execute(
            "SELECT 1 FROM allowed_users WHERE user_id = ? AND revoked = 0", (user_id,)
        ) as cur,
    ):
        return await cur.fetchone() is not None


async def was_user_revoked(user_id: int) -> bool:
    """Был ли пользователь заблокировавшим бота и лишён доступа."""
    async with (
        _db() as db,
        db.execute(
            "SELECT 1 FROM allowed_users WHERE user_id = ? AND revoked = 1", (user_id,)
        ) as cur,
    ):
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
    async with _db() as db, db.execute("SELECT * FROM access_requests") as cur:
        return [dict(r) for r in await cur.fetchall()]


async def remove_access_request(user_id: int):
    async with _db(write=True) as db:
        await db.execute("DELETE FROM access_requests WHERE user_id = ?", (user_id,))


async def has_pending_request(user_id: int) -> bool:
    async with _db() as db:
        async with db.execute("SELECT 1 FROM access_requests WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone() is not None


# ── Lang ──


async def get_user_lang(user_id: int) -> str | None:
    async with _db() as db:
        async with db.execute(
            "SELECT lang FROM allowed_users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row["lang"] if row else None


async def set_user_lang(user_id: int, lang: str):
    async with _db(write=True) as db:
        await db.execute(
            "INSERT INTO allowed_users (user_id, lang) VALUES (?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET lang = excluded.lang",
            (user_id, lang),
        )
