import asyncio
import os
import sqlite3
import glob
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import httpx

from services import tuya
from services.highlights import update_gecko_state
from database import get_schedules, save_schedule, log_lamp_event, log_sensor_reading, get_last_feeding_cached
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
    print(f"[Backup] saved {dest} ({len(files)} total → kept {_KEEP_BACKUPS})")


_last_alert_time: float = 0


async def _send_alert(text: str):
    global _last_alert_time
    now = datetime.now().timestamp()
    if now - _last_alert_time < 1800:  # не чаще раза в 30 мин
        return
    _last_alert_time = now
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_SUPER_ADMINS:
        return
    for admin_id in TELEGRAM_SUPER_ADMINS:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": admin_id, "text": text, "parse_mode": "Markdown"},
                )
        except Exception:
            pass


async def record_sensor_readings():
    temp = tuya.get_sensor("thermometer", "va_temperature")
    hum = tuya.get_sensor("humidifier", "va_humidity")
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


async def check_feeding_alert():
    last = get_last_feeding_cached()
    if last is None:
        return
    days = (datetime.now() - last).days
    if days >= FEEDING_ALERT_DAYS:
        await _send_alert(f"🍎 Геккон не кормлен *{days}* дней!")


def _is_lamp_on_now(hour: int, minute: int, duration_h: float) -> bool:
    """Должна ли лампа сейчас гореть по расписанию."""
    now = datetime.now()
    start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    end = start + timedelta(hours=duration_h)
    if end.day == start.day:
        return start <= now < end
    # переход через полночь
    return now >= start or now < end


async def _recover_lamps(schedules: list[dict]):
    """При старте выключает лампы которые должны быть выключены."""
    lamps_should_be_on: dict[str, bool] = {}
    for s in schedules:
        if s.get("paused"):
            continue
        if _is_lamp_on_now(s["hour"], s["minute"], s["duration_h"]):
            lamps_should_be_on[s["lamp_type"]] = True

    for lamp in ("uv", "heat"):
        status = tuya.get_lamp_status(lamp)
        if status.get("switch") is True and not lamps_should_be_on.get(lamp):
            print(f"[Scheduler] recovery: turning off {lamp} lamp (outside schedule window)")
            tuya.switch_lamp(lamp, False)
            await log_lamp_event(lamp, "off", "scheduler:recovery")


async def load_schedules():
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
    scheduler.add_job(check_feeding_alert, "cron", hour=12, minute=0, id="feeding_alert")


def start():
    scheduler.start()


def shutdown():
    scheduler.shutdown()
