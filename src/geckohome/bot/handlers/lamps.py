import asyncio
import logging

from geckohome.bot.access import is_super_admin
from geckohome.bot.formatters import status_text, user_status_text
from geckohome.bot.i18n import get_lang
from geckohome.bot.keyboards import admin_keyboard, main_keyboard
from geckohome.database import (
    log_lamp_event,
)
from geckohome.services import tuya

log = logging.getLogger(__name__)


from geckohome.bot.handlers._helpers import _replace_main, _safe_edit


async def _handle_refresh(query, ctx, user_id):
    try:
        lang = await get_lang(user_id)
        text_coro = status_text(lang) if is_super_admin(user_id) else user_status_text(lang)
        text, kb = await asyncio.gather(text_coro, main_keyboard(user_id))
        await _replace_main(query, ctx, user_id, text, kb)
    except Exception:
        pass


async def _handle_lamp(query, user_id, lamp, on):
    ok = await asyncio.to_thread(tuya.switch_lamp, lamp, on)
    word = "ON" if on else "OFF"
    lamp_name = "UV" if lamp == "uv" else "Тепловая"
    state = "включена" if on else "выключена"
    if ok:
        await log_lamp_event(lamp, word, f"tg:{user_id}")
        result = f"✅ {lamp_name} → {state}"
    else:
        result = f"❌ Ошибка: {lamp_name} не отвечает"
    await asyncio.sleep(1)
    try:
        lang = await get_lang(user_id)
        text, kb = await asyncio.gather(status_text(lang), main_keyboard(user_id))
        await _safe_edit(
            query,
            text + f"\n\n{result}",
            parse_mode="Markdown",
            reply_markup=kb,
        )
    except Exception:
        pass


async def _handle_tunnel_restart(query):
    await query.answer("🔄 Перезапуск туннеля...")
    from geckohome.services.tunnel import restart as restart_tunnel

    await asyncio.to_thread(restart_tunnel)
    kb = await admin_keyboard()
    await _safe_edit(
        query,
        "⚙️ *Управление*\n━━━━━━━━━━━━━━━\n\n🔄 Туннель перезапущен, URL обновится через ~30с",
        parse_mode="Markdown",
        reply_markup=kb,
    )
