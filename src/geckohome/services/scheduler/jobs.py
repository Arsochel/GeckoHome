"""Scheduler wiring: register all jobs, start/shutdown."""

import asyncio
import logging

from geckohome.config import MEDIAMTX_BIN
from geckohome.database import (
    get_schedules,
    purge_expired_debug_tokens,
    purge_lamp_events,
    purge_old_photos,
    save_schedule,
)
from geckohome.services import camera, tuya
from geckohome.services.highlights import update_gecko_state
from geckohome.services.scheduler._core import scheduler
from geckohome.services.scheduler.backup import backup_db
from geckohome.services.scheduler.feeding import (
    check_birthday,
    check_cricket_alert,
    check_feeding_alert,
)
from geckohome.services.scheduler.lamps import (
    _recover_lamps,
    check_lamp_temperature,
    lamp_schedule,
    sync_lamp_schedules,
)
from geckohome.services.scheduler.sensors import record_sensor_readings
from geckohome.services.timelapse import (
    capture_timelapse_frame,
    generate_and_send_timelapse,
    generate_and_send_timelapse_preview,
)

log = logging.getLogger(__name__)


async def load_schedules():
    await tuya.warm_lamp_cache()
    await tuya.warm_sensor_cache()
    saved = await get_schedules()
    if not saved:
        default_id = "uv_lamp_midnight"
        await save_schedule(default_id, "uv", 8, 0, 12)
        saved = await get_schedules()

    await _recover_lamps(saved)

    for s in saved:
        scheduler.add_job(
            lamp_schedule,
            "cron",
            hour=s["hour"],
            minute=s["minute"],
            kwargs={"lamp_type": s["lamp_type"], "duration_h": s["duration_h"]},
            id=s["id"],
            replace_existing=True,
        )
        if s["paused"]:
            scheduler.get_job(s["id"]).pause()

    scheduler.add_job(record_sensor_readings, "interval", minutes=30, id="sensor_readings")
    scheduler.add_job(sync_lamp_schedules, "interval", minutes=5, id="lamp_sync")
    scheduler.add_job(check_lamp_temperature, "interval", minutes=5, id="temp_guard")
    scheduler.add_job(update_gecko_state, "interval", minutes=2, id="gecko_state")
    scheduler.add_job(backup_db, "cron", hour=3, minute=0, id="db_backup")
    scheduler.add_job(check_feeding_alert, "cron", hour=20, minute=0, id="feeding_alert")
    scheduler.add_job(check_feeding_alert, "interval", hours=6, id="feeding_alert_interval")
    scheduler.add_job(check_cricket_alert, "cron", hour=18, minute=0, id="cricket_alert")
    scheduler.add_job(check_cricket_alert, "interval", hours=6, id="cricket_alert_interval")
    scheduler.add_job(purge_old_photos, "interval", minutes=30, id="purge_photos")
    scheduler.add_job(purge_lamp_events, "interval", hours=12, id="purge_lamp_events")
    scheduler.add_job(capture_timelapse_frame, "interval", seconds=5, id="timelapse_capture")
    scheduler.add_job(
        generate_and_send_timelapse, "cron", hour=12, minute=0, id="timelapse_generate"
    )
    scheduler.add_job(
        generate_and_send_timelapse_preview, "cron", hour=0, minute=0, id="timelapse_preview"
    )
    scheduler.add_job(purge_expired_debug_tokens, "cron", hour=4, minute=0, id="purge_debug_tokens")
    scheduler.add_job(check_birthday, "cron", hour=10, minute=0, id="birthday_check")
    if camera.is_configured():
        scheduler.add_job(
            camera.ensure_alive,
            "interval",
            seconds=30,
            kwargs={"bin_path": MEDIAMTX_BIN},
            id="camera_watchdog",
        )


async def _startup_alert_check():
    await asyncio.sleep(30)
    await check_feeding_alert()
    await check_cricket_alert()


def start():
    scheduler.start()
    asyncio.create_task(_startup_alert_check())


def shutdown():
    scheduler.shutdown()
