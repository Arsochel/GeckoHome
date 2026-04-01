import asyncio
import os
import sqlite3
import glob
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from services import tuya
from services.highlights import update_gecko_state
from database import get_schedules, save_schedule, log_lamp_event, log_sensor_reading

_DB_PATH     = os.path.join(os.path.dirname(os.path.dirname(__file__)), "gecko.db")
_BACKUP_DIR  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backups")
_KEEP_BACKUPS = 7

scheduler = AsyncIOScheduler()


async def lamp_schedule(lamp_type: str, duration_h: float):
    tuya.switch_lamp(lamp_type, True)
    await log_lamp_event(lamp_type, "on", "scheduler")
    await asyncio.sleep(duration_h * 3600)
    tuya.switch_lamp(lamp_type, False)
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


async def record_sensor_readings():
    temp = tuya.get_sensor("thermometer", "va_temperature")
    hum = tuya.get_sensor("humidifier", "va_humidity")
    await log_sensor_reading(temp, hum)


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
        await save_schedule(default_id, "uv", 0, 0, 60)
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


def start():
    scheduler.start()


def shutdown():
    scheduler.shutdown()
