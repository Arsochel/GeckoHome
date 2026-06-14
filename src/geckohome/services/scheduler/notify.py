"""Telegram alert sending helpers shared by scheduler jobs."""

import logging
from datetime import datetime

import httpx

from geckohome.config import TELEGRAM_BOT_TOKEN, TELEGRAM_SUPER_ADMINS
from geckohome.database import (
    delete_alert_message,
    get_alert_message,
    get_blocked_user_ids,
    save_alert_message,
    set_user_blocked,
)

log = logging.getLogger(__name__)


_last_alert_time: float = 0


async def _send_alert(text: str):
    global _last_alert_time
    now = datetime.now().timestamp()
    if now - _last_alert_time < 1800:  # не чаще раза в 30 мин
        return
    _last_alert_time = now
    from geckohome.config import TELEGRAM_ADMINS
    blocked = await get_blocked_user_ids()
    _alert_recipients = (TELEGRAM_SUPER_ADMINS | TELEGRAM_ADMINS) - blocked
    if not TELEGRAM_BOT_TOKEN or not _alert_recipients:
        return
    for admin_id in _alert_recipients:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": admin_id, "text": text, "parse_mode": "Markdown"},
                )
            data = r.json()
            if data.get("ok"):
                await set_user_blocked(admin_id, False)
            elif "blocked" in data.get("description", "").lower():
                await set_user_blocked(admin_id, True)
                log.warning("user %s blocked the bot", admin_id)
        except Exception as e:
            log.debug("alert send failed: %s", e)


async def _send_or_edit_alert(user_id: int, alert_type: str, text: str, markup: dict):
    """Удаляет старое алерт-сообщение и шлёт новое (чтобы оставалось внизу чата)."""
    if not TELEGRAM_BOT_TOKEN:
        return
    existing_msg_id = await get_alert_message(user_id, alert_type)
    async with httpx.AsyncClient(timeout=10) as client:
        # удаляем старое если есть
        if existing_msg_id:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage",
                    json={"chat_id": user_id, "message_id": existing_msg_id},
                )
            except Exception as e:
                log.debug("delete old alert failed: %s", e)
        # шлём новое
        try:
            r = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": user_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "reply_markup": markup,
                },
            )
            data = r.json()
            if data.get("ok"):
                msg_id = data["result"]["message_id"]
                await save_alert_message(user_id, alert_type, msg_id)
                await set_user_blocked(user_id, False)
            elif "blocked" in data.get("description", "").lower():
                await set_user_blocked(user_id, True)
                log.warning("user %s blocked the bot", user_id)
        except Exception as e:
            log.debug("alert send failed: %s", e)


async def _delete_alert_for_all(alert_type: str):
    """Удаляет алерт-сообщение у всех супер-админов если оно есть."""
    if not TELEGRAM_BOT_TOKEN:
        return
    for uid in TELEGRAM_SUPER_ADMINS:
        msg_id = await get_alert_message(uid, alert_type)
        if not msg_id:
            continue
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage",
                    json={"chat_id": uid, "message_id": msg_id},
                )
        except Exception as e:
            log.debug("delete alert failed: %s", e)
        await delete_alert_message(uid, alert_type)
