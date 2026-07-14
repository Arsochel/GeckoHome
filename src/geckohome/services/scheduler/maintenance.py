"""Обслуживание железа террариума: возраст UVB-лампы.

UVB-трубка деградирует тихо: через 6-12 месяцев она всё ещё светит, но UVB
почти не даёт — глазами не увидеть, а для геккона это метаболическая болезнь
костей. Дата замены хранится в gecko_profile (key='uvb_installed_at').
"""

import logging
from datetime import date

from geckohome import config
from geckohome.config import TELEGRAM_SUPER_ADMINS
from geckohome.database import get_blocked_user_ids, get_profile_value
from geckohome.services.scheduler.notify import _delete_alert_for_all, _send_or_edit_alert

log = logging.getLogger(__name__)

UVB_PROFILE_KEY = "uvb_installed_at"


def _months_since(installed: str) -> float:
    return (date.today() - date.fromisoformat(installed)).days / 30.44


async def check_uvb_age():
    installed = await get_profile_value(UVB_PROFILE_KEY)
    if not installed:
        return
    try:
        months = _months_since(installed)
    except ValueError:
        log.warning("uvb_installed_at не парсится: %r", installed)
        return
    if months < config.UVB_REPLACE_MONTHS:
        await _delete_alert_for_all("uvb")
        return

    text = (
        f"🔆 *UVB-лампе уже {months:.0f} мес.* — UVB-выход упал, "
        "пора менять трубку, даже если она светит."
    )
    markup = {
        "inline_keyboard": [[{"text": "🔆 Заменил лампу", "callback_data": "alert_uvb_replaced"}]]
    }
    blocked = await get_blocked_user_ids()
    for uid in TELEGRAM_SUPER_ADMINS - blocked:
        await _send_or_edit_alert(uid, "uvb", text, markup)
