"""Lamp scheduling: cron action, window math, sync, temp guard, recovery."""

import asyncio
import logging
from datetime import datetime, timedelta

from geckohome.database import get_schedules, log_lamp_event
from geckohome.services import tuya
from geckohome.services.scheduler.notify import _send_alert

log = logging.getLogger(__name__)


async def lamp_schedule(lamp_type: str, duration_h: float):
    await asyncio.to_thread(tuya.switch_lamp, lamp_type, True)
    await log_lamp_event(lamp_type, "on", "scheduler")
    # Выключение — через sync_lamp_schedules каждые 15 мин (не sleep, чтобы не было проблем с прерыванием)


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


async def sync_lamp_schedules():
    """Каждые 15 мин проверяет что лампы соответствуют расписанию.
    Фиксирует пропущенные cron job'ы при restart loop'ах.
    Не включает лампы если температура > 34°C (temp_guard отключил)."""
    try:
        temp_raw = await asyncio.wait_for(
            asyncio.to_thread(tuya.get_sensor, "thermometer", "va_temperature"),
            timeout=15,
        )
    except asyncio.TimeoutError:
        temp_raw = None
    temp_c = temp_raw / 10.0 if temp_raw is not None else None

    saved = await get_schedules()
    for lamp in ("uv", "heat"):
        should_be_on = any(
            not s.get("paused") and s["lamp_type"] == lamp and _is_lamp_on_now(s["hour"], s["minute"], s["duration_h"])
            for s in saved
        )
        status = tuya.get_lamp_status(lamp)
        currently_on = status.get("switch") is True
        if should_be_on and not currently_on:
            if temp_c is not None and temp_c > 34:
                log.debug("sync_lamps: %s should be ON but temp=%.1f°C > 34, skipping", lamp, temp_c)
                continue
            log.info("sync_lamps: %s should be ON (schedule), turning on", lamp)
            await asyncio.to_thread(tuya.switch_lamp, lamp, True)
            await log_lamp_event(lamp, "on", "sync")
        elif not should_be_on and currently_on:
            log.info("sync_lamps: %s should be OFF (outside window), turning off", lamp)
            await asyncio.to_thread(tuya.switch_lamp, lamp, False)
            await log_lamp_event(lamp, "off", "sync")


async def check_lamp_temperature():
    """Выключает лампы при перегреве (>34°C) и включает обратно при остывании (≤30°C).
    Работает только внутри активного окна расписания."""
    try:
        temp = await asyncio.wait_for(
            asyncio.to_thread(tuya.get_sensor, "thermometer", "va_temperature"),
            timeout=20,
        )
    except asyncio.TimeoutError:
        log.warning("temp_guard: sensor timeout, skipping")
        return
    if temp is None:
        return
    temp_c = temp / 10.0

    saved = await get_schedules()
    for s in saved:
        if s.get("paused"):
            continue
        if not _is_lamp_on_now(s["hour"], s["minute"], s["duration_h"]):
            continue

        lamp = s["lamp_type"]
        status = tuya.get_lamp_status(lamp)
        currently_on = status.get("switch") is True

        if temp_c > 34 and currently_on:
            log.warning("temp_guard: %.1f°C > 34 — выключаю %s лампу", temp_c, lamp)
            await asyncio.to_thread(tuya.switch_lamp, lamp, False)
            await log_lamp_event(lamp, "off", "temp_guard")
            await _send_alert(f"🌡 *Перегрев {temp_c:.1f}°C* — автоматически выключена {lamp.upper()} лампа")
        elif temp_c <= 30 and not currently_on:
            log.info("temp_guard: %.1f°C ≤ 30 — включаю %s лампу", temp_c, lamp)
            await asyncio.to_thread(tuya.switch_lamp, lamp, True)
            await log_lamp_event(lamp, "on", "temp_guard")
            await _send_alert(f"🌡 *Остыло до {temp_c:.1f}°C* — автоматически включена {lamp.upper()} лампа")



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

