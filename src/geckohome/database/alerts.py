"""Telegram alert message bookkeeping."""

from geckohome.database._core import _db

# ── Alert messages ──


async def get_alert_message(user_id: int, alert_type: str) -> int | None:
    async with (
        _db() as db,
        db.execute(
            "SELECT message_id FROM alert_messages WHERE user_id=? AND alert_type=?",
            (user_id, alert_type),
        ) as cur,
    ):
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
