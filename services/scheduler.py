import asyncio
import logging
import os
import sqlite3
import glob
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import httpx

from services import tuya
from services.highlights import update_gecko_state
from services.timelapse import capture_timelapse_frame, generate_and_send_timelapse, generate_and_send_timelapse_preview
from database import get_schedules, save_schedule, log_lamp_event, log_sensor_reading, get_last_feeding_cached, purge_old_photos, purge_lamp_events, get_next_feeding_supplements, get_last_cricket_purchase, get_alert_message, save_alert_message, delete_alert_message, set_user_blocked, get_blocked_user_ids, get_last_feeding_db, purge_expired_debug_tokens, get_gecko_birthday, get_cricket_remaining
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_SUPER_ADMINS,
    TEMP_ALERT_MIN, TEMP_ALERT_MAX, HUM_ALERT_MIN, HUM_ALERT_MAX, FEEDING_ALERT_DAYS,
)

_DB_PATH     = os.path.join(os.path.dirname(os.path.dirname(__file__)), "gecko.db")
_BACKUP_DIR  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backups")
_KEEP_BACKUPS = 7

scheduler = AsyncIOScheduler()


async def lamp_schedule(lamp_type: str, duration_h: float):
    await asyncio.to_thread(tuya.switch_lamp, lamp_type, True)
    await log_lamp_event(lamp_type, "on", "scheduler")
    await asyncio.sleep(duration_h * 3600)
    await asyncio.to_thread(tuya.switch_lamp, lamp_type, False)
    await log_lamp_event(lamp_type, "off", "scheduler")


def backup_db():
    os.makedirs(_BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(_BACKUP_DIR, f"gecko_{stamp}.db")
    src  = sqlite3.connect(_DB_PATH)
    dst  = sqlite3.connect(dest)
    src.backup(dst)
    src.close()
    dst.close()
    # удаляем старые бэкапы, оставляем _KEEP_BACKUPS
    files = sorted(glob.glob(os.path.join(_BACKUP_DIR, "gecko_*.db")))
    for old in files[:-_KEEP_BACKUPS]:
        os.remove(old)
    log.info("backup saved: %s (%d total, kept %d)", dest, len(files), _KEEP_BACKUPS)


_last_alert_time: float = 0


async def _send_alert(text: str):
    global _last_alert_time
    now = datetime.now().timestamp()
    if now - _last_alert_time < 1800:  # не чаще раза в 30 мин
        return
    _last_alert_time = now
    from config import TELEGRAM_ADMINS
    blocked = await get_blocked_user_ids()
    _alert_recipients = (TELEGRAM_SUPER_ADMINS | TELEGRAM_ADMINS) - blocked
    if not TELEGRAM_BOT_TOKEN or not _alert_recipients:
        return
    for admin_id in _alert_recipients:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": admin_id, "text": text, "parse_mode": "Markdown"},
                )
        except Exception as e:
            log.debug("alert send failed: %s", e)


async def record_sensor_readings():
    temp = await asyncio.to_thread(tuya.get_sensor, "thermometer", "va_temperature")
    hum = await asyncio.to_thread(tuya.get_sensor, "humidifier", "va_humidity") or \
          await asyncio.to_thread(tuya.get_sensor, "thermometer", "va_humidity")
    await log_sensor_reading(temp, hum)
    # alerts
    alerts = []
    if temp is not None:
        if temp < TEMP_ALERT_MIN:
            alerts.append(f"🥶 Температура низкая: *{temp/10:.1f}°C*")
        elif temp > TEMP_ALERT_MAX:
            alerts.append(f"🔥 Температура высокая: *{temp/10:.1f}°C*")
    if hum is not None:
        if hum < HUM_ALERT_MIN:
            alerts.append(f"🏜 Влажность низкая: *{hum}%*")
        elif hum > HUM_ALERT_MAX:
            alerts.append(f"💦 Влажность высокая: *{hum}%*")
    if alerts:
        await _send_alert("⚠️ *Gecko Home Alert*\n" + "\n".join(alerts))


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


async def check_feeding_alert():
    last = await get_last_feeding_db()
    if last is None:
        return
    days = (datetime.now().date() - last.date()).days
    if days < FEEDING_ALERT_DAYS:
        return

    crickets_remaining = await get_cricket_remaining()
    now_hour = datetime.now().hour
    min_hour = 10 if crickets_remaining == 0 else 20
    if now_hour < min_hour:
        return

    supplements = await get_next_feeding_supplements()
    text = f"🍎 *Пора кормить геккона!* (не ел *{days} д.*)"
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
    from config import TELEGRAM_ADMINS
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
    from database import get_cricket_remaining
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


def _lamp_window(hour: int, minute: int, duration_h: float) -> tuple[datetime, datetime]:
    """Returns (start, end) datetime of the current/upcoming lamp window. End may be next-day for midnight crossings."""
    now = datetime.now()
    start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    end = start + timedelta(hours=duration_h)
    return start, end


def _is_lamp_on_now(hour: int, minute: int, duration_h: float) -> bool:
    """Должна ли лампа сейчас гореть по расписанию."""
    start, end = _lamp_window(hour, minute, duration_h)
    now = datetime.now()
    if end.day == start.day:
        return start <= now < end
    return now >= start or now < end


def _remaining_seconds(hour: int, minute: int, duration_h: float) -> float:
    """Сколько секунд осталось до конца окна горения лампы. 0 если окно закончилось."""
    _, end = _lamp_window(hour, minute, duration_h)
    return max(0.0, (end - datetime.now()).total_seconds())


async def _lamp_off_after(lamp_type: str, seconds: float):
    """Вспомогательная: ждёт seconds секунд и выключает лампу."""
    log.info("recovery: will turn off %s in %.0fs", lamp_type, seconds)
    await asyncio.sleep(seconds)
    await asyncio.to_thread(tuya.switch_lamp, lamp_type, False)
    await log_lamp_event(lamp_type, "off", "scheduler:recovery")
    log.info("recovery: turned off %s", lamp_type)


async def _recover_lamps(schedules: list[dict]):
    """При старте выключает лампы вне окна; для ламп внутри окна планирует выключение."""
    lamps_in_window: dict[str, float] = {}  # lamp_type → remaining_seconds
    for s in schedules:
        if s.get("paused"):
            continue
        if _is_lamp_on_now(s["hour"], s["minute"], s["duration_h"]):
            remaining = _remaining_seconds(s["hour"], s["minute"], s["duration_h"])
            lamps_in_window[s["lamp_type"]] = remaining

    for lamp in ("uv", "heat"):
        status = tuya.get_lamp_status(lamp)
        currently_on = status.get("switch") is True
        if lamp in lamps_in_window:
            remaining = lamps_in_window[lamp]
            if not currently_on:
                log.info("recovery: turning on %s lamp (inside schedule window, was off)", lamp)
                tuya.switch_lamp(lamp, True)
                await log_lamp_event(lamp, "on", "scheduler:recovery")
            log.info("recovery: %s lamp is in window, scheduling off in %.0fs", lamp, remaining)
            asyncio.create_task(_lamp_off_after(lamp, remaining))
        elif currently_on:
            log.info("recovery: turning off %s lamp (outside schedule window)", lamp)
            tuya.switch_lamp(lamp, False)
            await log_lamp_event(lamp, "off", "scheduler:recovery")


async def load_schedules():
    await tuya.warm_sensor_cache()
    saved = await get_schedules()
    if not saved:
        default_id = "uv_lamp_midnight"
        await save_schedule(default_id, "uv", 8, 0, 12)
        saved = await get_schedules()

    await _recover_lamps(saved)

    for s in saved:
        scheduler.add_job(
            lamp_schedule, "cron",
            hour=s["hour"], minute=s["minute"],
            kwargs={"lamp_type": s["lamp_type"], "duration_h": s["duration_h"]},
            id=s["id"], replace_existing=True,
        )
        if s["paused"]:
            scheduler.get_job(s["id"]).pause()

    scheduler.add_job(record_sensor_readings, "interval", minutes=30, id="sensor_readings")
    scheduler.add_job(update_gecko_state, "interval", minutes=2, id="gecko_state")
    scheduler.add_job(backup_db, "cron", hour=3, minute=0, id="db_backup")
    scheduler.add_job(check_feeding_alert, "cron", hour=20, minute=0, id="feeding_alert")
    scheduler.add_job(check_feeding_alert, "interval", hours=6, id="feeding_alert_interval")
    scheduler.add_job(check_cricket_alert, "cron", hour=18, minute=0, id="cricket_alert")
    scheduler.add_job(check_cricket_alert, "interval", hours=6, id="cricket_alert_interval")
    scheduler.add_job(purge_old_photos, "interval", minutes=30, id="purge_photos")
    scheduler.add_job(purge_lamp_events, "interval", hours=12, id="purge_lamp_events")
    scheduler.add_job(capture_timelapse_frame, "interval", seconds=5, id="timelapse_capture")
    scheduler.add_job(generate_and_send_timelapse, "cron", hour=12, minute=0, id="timelapse_generate")
    scheduler.add_job(generate_and_send_timelapse_preview, "cron", hour=0, minute=0, id="timelapse_preview")
    scheduler.add_job(purge_expired_debug_tokens, "cron", hour=4, minute=0, id="purge_debug_tokens")
    scheduler.add_job(check_birthday, "cron", hour=10, minute=0, id="birthday_check")


async def _startup_alert_check():
    await asyncio.sleep(30)
    await check_feeding_alert()
    await check_cricket_alert()


def start():
    scheduler.start()
    asyncio.create_task(_startup_alert_check())


def shutdown():
    scheduler.shutdown()
