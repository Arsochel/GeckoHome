from datetime import datetime

from services import tuya


def lamp_status_icon(s: dict) -> str:
    if s.get("switch") is True:
        return "\U0001f7e2 ON"
    elif s.get("switch") is False:
        return "\U0001f534 OFF"
    return "\u26aa N/A"


def status_text() -> str:
    uv = tuya.get_lamp_status("uv")
    heat = tuya.get_lamp_status("heat")
    temp = tuya.get_sensor("thermometer", "va_temperature")
    hum = tuya.get_sensor("humidifier", "va_humidity")
    now = datetime.now().strftime("%H:%M:%S")

    temp_str = f"{temp / 10:.1f}\u00b0C" if temp is not None else "N/A"
    hum_str = f"{hum}%" if hum is not None else "N/A"

    return (
        f"\U0001f98e *Gecko Home*\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        f"\n"
        f"\U0001f526 *UV Lamp:*  {lamp_status_icon(uv)}\n"
        f"\U0001f525 *Heat Lamp:*  {lamp_status_icon(heat)}\n"
        f"\n"
        f"\U0001f321 *Temp:*  {temp_str}\n"
        f"\U0001f4a7 *Humidity:*  {hum_str}\n"
        f"\n"
        f"\u23f0 _{now}_"
    )
