from datetime import datetime

from services import tuya
from database import get_last_feeding_cached, get_gecko_state

_STATE_LABELS = {
    "sleeping":    "💀 Спит",
    "resting":     "💀 Отдыхает",
    "roaming":     "🏃 Шарится",
    "basking":     "🌡 Греется",
    "watching":    "👀 Смотрит в камеру",
    "eating":      "🍽 Ест",
    "drinking":    "💧 Пьёт",
    "hiding":      "🫥 Прячется",
    "weird":       "🤔 Ёрничает",
    "not_visible": "❓ Не видно",
}


def _lamp_line(s: dict) -> str:
    if s.get("switch") is True:
        return "🟢 включена"
    elif s.get("switch") is False:
        return "🔴 выключена"
    return "⚪️ недоступна"


async def _state_line() -> str:
    state, updated = await get_gecko_state()
    if not state:
        return ""
    ago = ""
    if updated:
        diff = int((datetime.now() - updated).total_seconds() // 60)
        ago = f" _({diff} мин назад)_" if diff > 0 else " _(только что)_"
    label = _STATE_LABELS.get(state, state)
    return f"🦎 Состояние:     *{label}*{ago}\n"


def _feeding_line() -> str:
    dt = get_last_feeding_cached()
    if dt is None:
        return "🍎 Кормление:     *не записано*"
    return f"🍎 Кормление:     *{dt.strftime('%d.%m %H:%M')}*"


async def user_status_text() -> str:
    temp = tuya.get_sensor("thermometer", "va_temperature")
    hum  = tuya.get_sensor("humidifier",  "va_humidity")
    now  = datetime.now().strftime("%H:%M:%S")

    temp_str = f"{temp / 10:.1f}°C" if temp is not None else "—"
    hum_str  = f"{hum}%"            if hum  is not None else "—"

    return (
        f"🦎 *Gecko Home*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"\n"
        f"{await _state_line()}"
        f"🌡 Температура:  *{temp_str}*\n"
        f"💧 Влажность:      *{hum_str}*\n"
        f"\n"
        f"{_feeding_line()}\n"
        f"\n"
        f"🕐 _{now}_"
    )


async def status_text() -> str:
    uv   = tuya.get_lamp_status("uv")
    heat = tuya.get_lamp_status("heat")
    temp = tuya.get_sensor("thermometer", "va_temperature")
    hum  = tuya.get_sensor("humidifier",  "va_humidity")
    now  = datetime.now().strftime("%H:%M:%S")

    temp_str = f"{temp / 10:.1f}°C" if temp is not None else "—"
    hum_str  = f"{hum}%"            if hum  is not None else "—"

    return (
        f"🦎 *Gecko Home*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"\n"
        f"{await _state_line()}"
        f"🔦 UV-лампа:       {_lamp_line(uv)}\n"
        f"🔥 Тепловая:       {_lamp_line(heat)}\n"
        f"\n"
        f"🌡 Температура:  *{temp_str}*\n"
        f"💧 Влажность:      *{hum_str}*\n"
        f"\n"
        f"{_feeding_line()}\n"
        f"\n"
        f"🕐 _{now}_"
    )
