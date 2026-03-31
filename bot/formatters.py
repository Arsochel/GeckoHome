from datetime import datetime

from services import tuya
from database import get_last_feeding_cached, get_gecko_state, get_gecko_zone

_ZONE_LABELS = {
    "ru": {
        "skull":          "на черепе",
        "water":          "у поилки",
        "hammock":        "на гамаке",
        "left of skull":  "слева от черепа",
        "right of skull": "справа от черепа",
        "below skull":    "под черепом",
        "above skull":    "над черепом",
    },
    "en": {
        "skull":          "on the skull",
        "water":          "at the water bowl",
        "hammock":        "on the hammock",
        "left of skull":  "left of skull",
        "right of skull": "right of skull",
        "below skull":    "below skull",
        "above skull":    "above skull",
    },
}

_STATE_LABELS = {
    "ru": {
        "sleeping":    "😴 Спит",
        "resting":     "🛌 Отдыхает",
        "roaming":     "🏃 Шарится",
        "basking":     "🌡 Греется",
        "watching":    "👀 Смотрит в камеру",
        "eating":      "🍽 Ест",
        "drinking":    "💧 Пьёт",
        "hiding":      "🫥 Прячется",
        "weird":       "🤔 Ёрничает",
        "not_visible": "❓ Не видно",
    },
    "en": {
        "sleeping":    "😴 Sleeping",
        "resting":     "🛌 Resting",
        "roaming":     "🏃 Roaming",
        "basking":     "🌡 Basking",
        "watching":    "👀 Watching the camera",
        "eating":      "🍽 Eating",
        "drinking":    "💧 Drinking",
        "hiding":      "🫥 Hiding",
        "weird":       "🤔 Being weird",
        "not_visible": "❓ Not visible",
    },
}


def _lamp_line(s: dict, lang: str) -> str:
    if s.get("switch") is True:
        return "🟢 on" if lang == "en" else "🟢 включена"
    elif s.get("switch") is False:
        return "🔴 off" if lang == "en" else "🔴 выключена"
    return "⚪️ unavailable" if lang == "en" else "⚪️ недоступна"


def _ago_str(updated: datetime | None, lang: str) -> str:
    if not updated:
        return ""
    diff = int((datetime.now() - updated).total_seconds() // 60)
    if lang == "en":
        return f" _({diff} min ago)_" if diff > 0 else " _(just now)_"
    return f" _({diff} мин назад)_" if diff > 0 else " _(только что)_"


async def _state_line(lang: str) -> str:
    state, updated = await get_gecko_state()
    if not state:
        return ""
    label = _STATE_LABELS[lang].get(state, state)
    key = "State" if lang == "en" else "Состояние"
    return f"🦎 {key}:     *{label}*{_ago_str(updated, lang)}\n"


async def _zone_line(lang: str) -> str:
    zone, updated = await get_gecko_zone()
    if not zone:
        return ""
    label = _ZONE_LABELS[lang].get(zone, zone)
    key = "Location" if lang == "en" else "Место"
    return f"📍 {key}:            *{label}*{_ago_str(updated, lang)}\n"


def _feeding_line(lang: str) -> str:
    dt = get_last_feeding_cached()
    if lang == "en":
        if dt is None:
            return "🍎 Feeding:     *not recorded*"
        return f"🍎 Feeding:     *{dt.strftime('%d.%m %H:%M')}*"
    if dt is None:
        return "🍎 Кормление:     *не записано*"
    return f"🍎 Кормление:     *{dt.strftime('%d.%m %H:%M')}*"


async def user_status_text(lang: str = "ru") -> str:
    temp = tuya.get_sensor("thermometer", "va_temperature")
    hum  = tuya.get_sensor("humidifier",  "va_humidity")
    now  = datetime.now().strftime("%H:%M:%S")

    temp_str = f"{temp / 10:.1f}°C" if temp is not None else "—"
    hum_str  = f"{hum}%"            if hum  is not None else "—"

    if lang == "en":
        return (
            f"🦎 *Gecko Home*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"\n"
            f"{await _state_line(lang)}"
            f"{await _zone_line(lang)}"
            f"🌡 Temperature:  *{temp_str}*\n"
            f"💧 Humidity:        *{hum_str}*\n"
            f"\n"
            f"{_feeding_line(lang)}\n"
            f"\n"
            f"🕐 _{now}_"
        )
    return (
        f"🦎 *Gecko Home*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"\n"
        f"{await _state_line(lang)}"
        f"{await _zone_line(lang)}"
        f"🌡 Температура:  *{temp_str}*\n"
        f"💧 Влажность:      *{hum_str}*\n"
        f"\n"
        f"{_feeding_line(lang)}\n"
        f"\n"
        f"🕐 _{now}_"
    )


async def status_text(lang: str = "ru") -> str:
    uv   = tuya.get_lamp_status("uv")
    heat = tuya.get_lamp_status("heat")
    temp = tuya.get_sensor("thermometer", "va_temperature")
    hum  = tuya.get_sensor("humidifier",  "va_humidity")
    now  = datetime.now().strftime("%H:%M:%S")

    temp_str = f"{temp / 10:.1f}°C" if temp is not None else "—"
    hum_str  = f"{hum}%"            if hum  is not None else "—"

    if lang == "en":
        return (
            f"🦎 *Gecko Home*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"\n"
            f"{await _state_line(lang)}"
            f"{await _zone_line(lang)}"
            f"🔦 UV Lamp:       {_lamp_line(uv, lang)}\n"
            f"🔥 Heat Lamp:    {_lamp_line(heat, lang)}\n"
            f"\n"
            f"🌡 Temperature:  *{temp_str}*\n"
            f"💧 Humidity:        *{hum_str}*\n"
            f"\n"
            f"{_feeding_line(lang)}\n"
            f"\n"
            f"🕐 _{now}_"
        )
    return (
        f"🦎 *Gecko Home*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"\n"
        f"{await _state_line(lang)}"
        f"{await _zone_line(lang)}"
        f"🔦 UV-лампа:       {_lamp_line(uv, lang)}\n"
        f"🔥 Тепловая:       {_lamp_line(heat, lang)}\n"
        f"\n"
        f"🌡 Температура:  *{temp_str}*\n"
        f"💧 Влажность:      *{hum_str}*\n"
        f"\n"
        f"{_feeding_line(lang)}\n"
        f"\n"
        f"🕐 _{now}_"
    )
