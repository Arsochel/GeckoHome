import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from services import tuya
from services.highlights import update_gecko_state
from config import DEVICE_IDS
from database import get_schedules, save_schedule, log_lamp_event, log_sensor_reading

scheduler = AsyncIOScheduler()


async def lamp_schedule(lamp_type: str, duration_h: float):
    device_id = DEVICE_IDS[f"{lamp_type}_lamp"]
    tuya.switch_lamp(lamp_type, True)
    await log_lamp_event(lamp_type, "on", "scheduler")
    await asyncio.sleep(duration_h * 3600)
    tuya.switch_lamp(lamp_type, False)
    await log_lamp_event(lamp_type, "off", "scheduler")


async def record_sensor_readings():
    temp = tuya.get_sensor("thermometer", "va_temperature")
    hum = tuya.get_sensor("humidifier", "va_humidity")
    await log_sensor_reading(temp, hum)


async def load_schedules():
    saved = await get_schedules()
    if not saved:
        default_id = "uv_lamp_midnight"
        await save_schedule(default_id, "uv", 0, 0, 60)
        saved = await get_schedules()

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


def start():
    scheduler.start()


def shutdown():
    scheduler.shutdown()
