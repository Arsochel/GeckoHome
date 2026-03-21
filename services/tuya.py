import tinytuya
from config import DEVICE_IDS, DEVICE_LOCAL


def _outlet(device_type: str):
    info = DEVICE_LOCAL.get(device_type, {})
    device_id = DEVICE_IDS.get(device_type, "")
    if not device_id or not info.get("ip") or not info.get("key"):
        return None
    try:
        d = tinytuya.OutletDevice(
            dev_id=device_id,
            address=info["ip"],
            local_key=info["key"],
            version=info.get("version", "3.4"),
        )
        d.set_socketRetryLimit(1)
        d.set_socketTimeout(3)
        return d
    except Exception as e:
        print(f"[Tuya] init {device_type} error: {e}")
        return None


def _device(device_type: str):
    info = DEVICE_LOCAL.get(device_type, {})
    device_id = DEVICE_IDS.get(device_type, "")
    if not device_id or not info.get("ip") or not info.get("key"):
        return None
    try:
        d = tinytuya.Device(
            dev_id=device_id,
            address=info["ip"],
            local_key=info["key"],
            version=info.get("version", "3.3"),
        )
        d.set_socketRetryLimit(1)
        d.set_socketTimeout(3)
        return d
    except Exception as e:
        print(f"[Tuya] init {device_type} error: {e}")
        return None


def get_lamp_status(lamp_type: str) -> dict:
    d = _outlet(f"{lamp_type}_lamp")
    if not d:
        return {"online": None, "switch": None}
    try:
        result = d.status()
        if result.get("Error"):
            print(f"[Tuya] status {lamp_type}: {result['Error']}")
            return {"online": False, "switch": None}
        switch = result.get("dps", {}).get("1")
        return {"online": True, "switch": switch}
    except Exception as e:
        print(f"[Tuya] status {lamp_type} error: {e}")
        return {"online": False, "switch": None}


_CODE_TO_DPS = {"va_temperature": "1", "va_humidity": "2"}


def get_sensor(sensor_type: str, code: str):
    d = _device(sensor_type)
    if not d:
        return None
    try:
        result = d.status()
        if result.get("Error"):
            return None
        dps_key = _CODE_TO_DPS.get(code)
        if dps_key:
            return result.get("dps", {}).get(dps_key)
    except Exception as e:
        print(f"[Tuya] sensor {sensor_type} error: {e}")
    return None


def switch_lamp(lamp_type: str, on: bool) -> bool:
    d = _outlet(f"{lamp_type}_lamp")
    if not d:
        return False
    try:
        result = d.turn_on() if on else d.turn_off()
        if result.get("Error"):
            print(f"[Tuya] switch_lamp({lamp_type}, {on}): {result['Error']}")
            return False
        print(f"[Tuya] switch_lamp({lamp_type}, {on}): OK")
        return True
    except Exception as e:
        print(f"[Tuya] switch_lamp({lamp_type}, {on}) error: {e}")
        return False
