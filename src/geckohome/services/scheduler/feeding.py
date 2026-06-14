"""Feeding/cricket/birthday alerts and the age-based feeding schedule."""

import logging
from datetime import datetime

import httpx

from geckohome.config import FEEDING_ALERT_DAYS, TELEGRAM_BOT_TOKEN, TELEGRAM_SUPER_ADMINS
from geckohome.database import (
    get_blocked_user_ids,
    get_cricket_remaining,
    get_gecko_birthday,
    get_last_feeding_db,
    get_next_feeding_supplements,
)
from geckohome.services.scheduler.notify import _delete_alert_for_all, _send_or_edit_alert

log = logging.getLogger(__name__)


# (max_months, interval_days, crickets_min, crickets_max)
# По статье: до 6 мес — ежедневно 5-7 шт; 6-12 мес — раз в 2 дня 5-6 шт; взрослые — раз в 3-4 дня 5-10 шт
_FEEDING_SCHEDULE = [
    (6,   1, 5,  7),
    (12,  2, 5,  6),
    (999, 3, 5, 10),
]


def get_feeding_schedule(birthday: str) -> tuple[int, int, int]:
    """Возвращает (interval_days, crickets_min, crickets_max) по дате рождения."""
    from datetime import date
    bday = date.fromisoformat(birthday)
    today = date.today()
    months = (today.year - bday.year) * 12 + (today.month - bday.month)
    for max_months, interval, cmin, cmax in _FEEDING_SCHEDULE:
        if months < max_months:
            return interval, cmin, cmax
    return _FEEDING_SCHEDULE[-1][1], _FEEDING_SCHEDULE[-1][2], _FEEDING_SCHEDULE[-1][3]


async def check_feeding_alert():
    last = await get_last_feeding_db()
    if last is None:
        return
    days = (datetime.now().date() - last.date()).days

    birthday = await get_gecko_birthday()
    if birthday:
        alert_days, cmin, cmax = get_feeding_schedule(birthday)
    else:
        alert_days, cmin, cmax = FEEDING_ALERT_DAYS, 0, 0

    if days < alert_days:
        return

    crickets_remaining = await get_cricket_remaining()
    now_hour = datetime.now().hour
    min_hour = 10 if crickets_remaining == 0 else 20
    if now_hour < min_hour:
        return

    supplements = await get_next_feeding_supplements()
    amount_hint = f" {cmin}–{cmax} сверчков" if cmin else ""
    text = f"🍎 *Пора кормить геккона!* (не ел *{days} д.*){amount_hint}"
    if "vitamins" in supplements:
        text += "\n💊 Это кормление *с витаминами*"
    if "hornworm" in supplements:
        text += "\n🐛 Дать *табачного бражника*"
    text += "\n🦗 Покорми сверчков сегодня — через 2 дня готовы"

    rows = [[{"text": "🍎 Покормил", "callback_data": "alert_fed"}]]
    event_row = []
    if "vitamins" in supplements:
        event_row.append({"text": "💊 Дал витамины", "callback_data": "alert_vitamins"})
    if "hornworm" in supplements:
        event_row.append({"text": "🐛 Дал бражника", "callback_data": "alert_hornworm"})
    if event_row:
        rows.append(event_row)
    if crickets_remaining == 0:
        rows.append([{"text": "🦗 Купил сверчков", "callback_data": "alert_cricket"}])

    markup = {"inline_keyboard": rows}
    blocked = await get_blocked_user_ids()
    for uid in TELEGRAM_SUPER_ADMINS - blocked:
        await _send_or_edit_alert(uid, "feeding", text, markup)


async def check_birthday():
    birthday = await get_gecko_birthday()
    if not birthday:
        return
    from datetime import date
    bday = date.fromisoformat(birthday)
    today = date.today()

    # Полный день рождения
    if today.month == bday.month and today.day == bday.day:
        age = today.year - bday.year
        text = f"🎂 *С днём рождения, геккон!*\n\nСегодня ему исполняется *{age} {'год' if age == 1 else 'года' if 2 <= age <= 4 else 'лет'}* 🦎🎉"
    else:
        # Полгода: месяц + 6, с учётом перехода года
        half_month = ((bday.month - 1 + 6) % 12) + 1
        half_year = bday.year if bday.month <= 6 else bday.year + 1
        if today.month == half_month and today.day == bday.day and today.year == half_year:
            text = "🎉 *Геккону полгода!* 🦎\n\nПоловина первого года позади!"
        else:
            return

    blocked = await get_blocked_user_ids()
    from geckohome.config import TELEGRAM_ADMINS
    recipients = (TELEGRAM_SUPER_ADMINS | TELEGRAM_ADMINS) - blocked
    if not TELEGRAM_BOT_TOKEN or not recipients:
        return
    for uid in recipients:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": uid, "text": text, "parse_mode": "Markdown"},
                )
        except Exception as e:
            log.debug("birthday send failed: %s", e)


async def check_cricket_alert():
    from geckohome.database import get_cricket_remaining
    remaining = await get_cricket_remaining()
    if remaining is None:
        return
    if remaining > 5:
        await _delete_alert_for_all("cricket")
        return
    if remaining == 0:
        text = "🔴 *Сверчки закончились!* — купи новую партию"
    else:
        text = f"🟡 *Сверчков осталось мало: {remaining} шт.* — скоро покупать"
    markup = {"inline_keyboard": [[
        {"text": "🦗 Купил сверчков", "callback_data": "alert_cricket"},
    ]]}
    blocked = await get_blocked_user_ids()
    for uid in TELEGRAM_SUPER_ADMINS - blocked:
        await _send_or_edit_alert(uid, "cricket", text, markup)
