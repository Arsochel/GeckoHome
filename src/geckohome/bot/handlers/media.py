import asyncio
import logging
import os
from datetime import datetime

from geckohome.bot.access import is_super_admin
from geckohome.bot.formatters import status_text, user_status_text
from geckohome.bot.i18n import get_lang
from geckohome.bot.keyboards import main_keyboard
from geckohome.database import (
    create_debug_token,
    log_user_action,
)
from geckohome.services import camera

log = logging.getLogger(__name__)


from geckohome.bot.handlers._helpers import _replace_main, _safe_edit


async def _handle_snapshot(query, user_id, ctx):
    if not camera.is_configured():
        await _safe_edit(query, "❌ Камера не настроена.")
        return
    u = query.from_user
    log.info("snapshot requested by @%s (%s)", u.username or u.first_name, u.id)
    await log_user_action(u.id, u.username or u.first_name, "snapshot")
    await _safe_edit(query, "📸 Делаю снимок...")
    lang, path = await asyncio.gather(get_lang(user_id), camera.snapshot())
    text_coro = status_text(lang) if is_super_admin(user_id) else user_status_text(lang)
    text, kb = await asyncio.gather(text_coro, main_keyboard(user_id))
    err_msg = "❌ Failed to take snapshot" if lang == "en" else "❌ Не удалось сделать снимок"
    if path:
        try:
            with open(path, "rb") as f:
                await query.message.reply_photo(
                    f,
                    caption=f"🦎 Gecko Cam • {datetime.now().strftime('%H:%M:%S')}",
                )
        finally:
            os.unlink(path)
        await _replace_main(query, ctx, user_id, text, kb)
    else:
        await _safe_edit(query, text + f"\n\n{err_msg}", parse_mode="Markdown", reply_markup=kb)


async def _handle_clip(query, user_id, duration: int = 30, ctx=None):
    if not camera.is_configured():
        await _safe_edit(query, "❌ Камера не настроена.")
        return
    u = query.from_user
    label = "3 мин" if duration >= 60 else f"{duration}с"
    log.info("clip %s requested by @%s (%s)", label, u.username or u.first_name, u.id)
    await log_user_action(u.id, u.username or u.first_name, f"clip_{duration}")
    await _safe_edit(query, f"🎬 Записываю {label}...")
    lang, path = await asyncio.gather(get_lang(user_id), camera.clip(duration))
    text_coro = status_text(lang) if is_super_admin(user_id) else user_status_text(lang)
    text, kb = await asyncio.gather(text_coro, main_keyboard(user_id))
    err_msg = "❌ Failed to record clip" if lang == "en" else "❌ Не удалось записать клип"
    if path:
        try:
            with open(path, "rb") as f:
                await query.message.reply_video(
                    f,
                    caption=f"🦎 Gecko Cam • {datetime.now().strftime('%H:%M:%S')}",
                    width=720,
                    height=1280,
                    write_timeout=max(60, duration * 3),
                    read_timeout=max(60, duration * 3),
                )
        finally:
            os.unlink(path)
        await _replace_main(query, ctx, user_id, text, kb)
    else:
        await _safe_edit(query, text + f"\n\n{err_msg}", parse_mode="Markdown", reply_markup=kb)


async def _handle_debug_link(query, user_id: int):
    from geckohome.paths import TUNNEL_URL_FILE

    tunnel_file = TUNNEL_URL_FILE
    lang = await get_lang(user_id)
    try:
        with open(tunnel_file) as f:
            tunnel = f.read().strip()
    except FileNotFoundError:
        tunnel = ""
    if not tunnel:
        msg = (
            "🛠 Туннель ещё не готов, попробуйте через минуту"
            if lang == "ru"
            else "🛠 Tunnel not ready, try again in a minute"
        )
        await query.answer(msg, show_alert=True)
        return
    token = await create_debug_token(user_id, ttl_hours=24)
    url = f"{tunnel}/debug?token={token}"
    if lang == "ru":
        text = f"🛠 *Дебаг (24ч)*\n{url}\n\nДействует 24 часа, ссылка одноразовая."
    else:
        text = f"🛠 *Debug access (24h)*\n{url}\n\nValid for 24 hours."
    await query.message.chat.send_message(
        text, parse_mode="Markdown", disable_web_page_preview=True
    )
