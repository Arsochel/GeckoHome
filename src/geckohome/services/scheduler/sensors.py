"""Periodic sensor readings with threshold alerts."""

import asyncio
import logging

from geckohome.config import HUM_ALERT_MAX, HUM_ALERT_MIN, TEMP_ALERT_MAX, TEMP_ALERT_MIN
from geckohome.database import log_sensor_reading
from geckohome.services import tuya
from geckohome.services.scheduler.notify import _send_alert

log = logging.getLogger(__name__)


async def record_sensor_readings():
    try:
        temp = await asyncio.wait_for(
            asyncio.to_thread(tuya.get_sensor, "thermometer", "va_temperature"),
            timeout=20,
        )
    except asyncio.TimeoutError:
        log.warning("record_sensor_readings: temperature read timeout")
        temp = None
    try:
        hum = await asyncio.wait_for(
            asyncio.to_thread(tuya.get_sensor, "humidifier", "va_humidity"),
            timeout=20,
        ) or await asyncio.wait_for(
            asyncio.to_thread(tuya.get_sensor, "thermometer", "va_humidity"),
            timeout=20,
        )
    except asyncio.TimeoutError:
        log.warning("record_sensor_readings: humidity read timeout")
        hum = None
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
