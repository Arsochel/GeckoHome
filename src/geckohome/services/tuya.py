import logging
import socket
import threading
import time

import tinytuya

from geckohome.config import (
    DEVICE_IDS,
    DEVICE_LOCAL,
    TUYA_CLOUD_KEY,
    TUYA_CLOUD_REGION,
    TUYA_CLOUD_SECRET,
)

_lamp_cache: dict[
    str, dict
] = {}  # lamp_type → {"switch": bool|None, "online": bool|None, "ts": float}
_LAMP_CACHE_TTL = 15  # seconds

_sensor_value_cache: dict[str, dict] = {}  # "sensor_type:code" → {"value": any, "ts": float}
_SENSOR_CACHE_TTL = 120  # seconds — заполняется планировщиком каждые 30 мин

log = logging.getLogger(__name__)

_cloud = None


def _get_cloud():
    global _cloud
    if _cloud is None and TUYA_CLOUD_KEY and TUYA_CLOUD_SECRET:
        _cloud = tinytuya.Cloud(
            apiKey=TUYA_CLOUD_KEY,
            apiSecret=TUYA_CLOUD_SECRET,
            apiRegion=TUYA_CLOUD_REGION,
        )
    return _cloud


# ── Passive UDP listener for battery devices ──────────────────────────────────
# Кэш последних значений полученных из local broadcast
_sensor_cache: dict[str, dict] = {}  # device_id → {"temp": int, "hum": int, "ts": float}
_listener_started = False
_listener_lock = threading.Lock()


def _listener_thread():
    device_id = DEVICE_IDS.get("thermometer", "")
    local_key = DEVICE_LOCAL.get("thermometer", {}).get("key", "")
    if not device_id or not local_key:
        return

    # создаём Device только для расшифровки (соединение не открывается)
    ip = DEVICE_LOCAL.get("thermometer", {}).get("ip") or "192.168.3.16"
    d = tinytuya.Device(dev_id=device_id, address=ip, local_key=local_key, version=3.4)
    d.set_socketRetryLimit(0)
    d.set_socketTimeout(0)

    socks = []
    for port in (6666, 6667):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", port))
            s.settimeout(5)
            socks.append(s)
        except Exception as e:
            log.error("listener bind :%d error: %s", port, e)

    if not socks:
        return

    log.info("UDP listener started for thermometer")
    while True:
        for s in socks:
            try:
                data, addr = s.recvfrom(4096)
                try:
                    msg = d._decode_payload(data)
                    dps = msg.get("dps", {}) if isinstance(msg, dict) else {}
                    if not dps:
                        # попробуем через status payload
                        parsed = d.receive()
                        dps = parsed.get("dps", {}) if parsed else {}
                except Exception as e:
                    log.debug("decode broadcast: %s", e)
                    continue
                temp = dps.get("1") or dps.get(1)
                hum = dps.get("2") or dps.get(2)
                if temp is not None or hum is not None:
                    first_ever = device_id not in _sensor_cache
                    with _listener_lock:
                        entry = _sensor_cache.setdefault(device_id, {})
                        if temp is not None:
                            entry["temp"] = temp
                        if hum is not None:
                            entry["hum"] = hum
                        entry["ts"] = time.time()
                    log.debug("local broadcast: %s temp=%s hum=%s", addr[0], temp, hum)
                    if first_ever:
                        _notify_thermometer_online(temp, hum)
            except TimeoutError:
                pass
            except Exception as e:
                log.error("listener error: %s", e)
                time.sleep(1)


def _notify_thermometer_online(temp, hum):
    import threading

    import httpx

    from geckohome.config import TELEGRAM_BOT_TOKEN, TELEGRAM_SUPER_ADMINS

    if not TELEGRAM_BOT_TOKEN:
        return
    t_str = f"{temp / 10:.1f}°C" if temp is not None else "—"
    h_str = f"{hum}%" if hum is not None else "—"
    text = f"🌡 Термометр онлайн (локально)\nТемпература: *{t_str}*, влажность: *{h_str}*"

    def _send():
        for uid in TELEGRAM_SUPER_ADMINS:
            try:
                httpx.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": uid, "text": text, "parse_mode": "Markdown"},
                    timeout=10,
                )
            except Exception as e:
                log.debug("tg notify thermometer: %s", e)

    threading.Thread(target=_send, daemon=True).start()


async def warm_lamp_cache():
    """Восстанавливает последнее состояние ламп из lamp_events на старте."""
    from geckohome.database import get_last_lamp_states

    states = await get_last_lamp_states()
    for lamp, switch in states.items():
        _lamp_cache[lamp] = {"online": None, "switch": switch, "ts": 0}
    if states:
        log.info("lamp cache warmed from DB: %s", dict(states.items()))


async def warm_sensor_cache():
    """Заполняет кэш сенсоров из последней записи в БД — чтобы первый /start был мгновенным."""
    from geckohome.database import get_last_sensor_reading

    temp, hum = await get_last_sensor_reading()
    if temp is not None:
        _sensor_value_cache["thermometer:va_temperature"] = {"value": temp, "ts": time.time()}
    if hum is not None:
        _sensor_value_cache["thermometer:va_humidity"] = {"value": hum, "ts": time.time()}
        _sensor_value_cache["humidifier:va_humidity"] = {"value": hum, "ts": time.time()}
    if temp is not None or hum is not None:
        log.info("sensor cache warmed from DB: temp=%s hum=%s", temp, hum)


def set_sensor_value(sensor_type: str, code: str, value) -> None:
    """Inject a sensor value from a local non-Tuya source (ingest endpoint).

    Writes into the same short-term cache that get_sensor() reads first, so the
    value flows to /status, the WebSocket and the scheduler without any Tuya call.
    """
    _sensor_value_cache[f"{sensor_type}:{code}"] = {"value": value, "ts": time.time()}


def start_listener():
    global _listener_started
    with _listener_lock:
        if _listener_started:
            return
        _listener_started = True
    t = threading.Thread(target=_listener_thread, daemon=True)
    t.start()


def get_sensor_cached(sensor_type: str, code: str):
    """Возвращает значение из local broadcast кэша если оно не старше 1 часа."""
    device_id = DEVICE_IDS.get(sensor_type, "")
    with _listener_lock:
        entry = _sensor_cache.get(device_id, {})
    if not entry or time.time() - entry.get("ts", 0) > 3600:
        return None
    if code == "va_temperature":
        return entry.get("temp")
    if code == "va_humidity":
        return entry.get("hum")
    return None


# ─────────────────────────────────────────────────────────────────────────────


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
        d.set_socketTimeout(1)
        return d
    except Exception as e:
        log.error("init %s error: %s", device_type, e)
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
        d.set_socketTimeout(1)
        return d
    except Exception as e:
        log.error("init %s error: %s", device_type, e)
        return None


def get_lamp_status(lamp_type: str) -> dict:
    cached = _lamp_cache.get(lamp_type)
    if cached and time.time() - cached["ts"] < _LAMP_CACHE_TTL:
        return {"online": cached["online"], "switch": cached["switch"]}
    last_switch = cached.get("switch") if cached else None
    d = _outlet(f"{lamp_type}_lamp")
    if not d:
        return {"online": None, "switch": last_switch}
    try:
        result = d.status()
        if result.get("Error"):
            log.warning("status %s: %s", lamp_type, result["Error"])
            _lamp_cache[lamp_type] = {"online": False, "switch": last_switch, "ts": time.time()}
            return {"online": False, "switch": last_switch}
        switch = result.get("dps", {}).get("1")
        _lamp_cache[lamp_type] = {"online": True, "switch": switch, "ts": time.time()}
        return {"online": True, "switch": switch}
    except Exception as e:
        log.error("status %s error: %s", lamp_type, e)
        _lamp_cache[lamp_type] = {"online": False, "switch": last_switch, "ts": time.time()}
        return {"online": False, "switch": last_switch}


_CODE_TO_DPS = {"va_temperature": "1", "va_humidity": "2"}
_CLOUD_CODES = {
    "va_temperature": "temp_current",
    "va_humidity": "humidity_value",
}


def _get_sensor_cloud(device_id: str, code: str):
    import socket

    cloud = _get_cloud()
    if not cloud or not device_id:
        return None
    try:
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(10)
        try:
            r = cloud.cloudrequest(f"/v2.0/cloud/thing/{device_id}/shadow/properties")
        finally:
            socket.setdefaulttimeout(old_timeout)
        if not r.get("success"):
            return None
        cloud_code = _CLOUD_CODES.get(code)
        for prop in r["result"]["properties"]:
            if prop["code"] == cloud_code:
                return prop["value"]
    except Exception as e:
        log.error("cloud sensor error: %s", e)
    return None


def get_sensor(sensor_type: str, code: str):
    cache_key = f"{sensor_type}:{code}"
    # 0. short-term in-memory cache (заполняется каждые 30 мин планировщиком)
    cached = _sensor_value_cache.get(cache_key)
    if cached and time.time() - cached["ts"] < _SENSOR_CACHE_TTL:
        return cached["value"]
    # 1. local broadcast cache (если поймали broadcast)
    val = get_sensor_cached(sensor_type, code)
    if val is not None:
        _sensor_value_cache[cache_key] = {"value": val, "ts": time.time()}
        return val
    # 2. direct local LAN (для постоянно включённых устройств)
    d = _device(sensor_type)
    if d:
        try:
            result = d.status()
            if not result.get("Error"):
                dps_key = _CODE_TO_DPS.get(code)
                if dps_key:
                    val = result.get("dps", {}).get(dps_key)
                    if val is not None:
                        _sensor_value_cache[cache_key] = {"value": val, "ts": time.time()}
                        return val
        except Exception as e:
            log.error("sensor %s local error: %s", sensor_type, e)
    # 3. cloud (основной для батарейных устройств)
    device_id = DEVICE_IDS.get(sensor_type, "")
    val = _get_sensor_cloud(device_id, code)
    if val is not None:
        _sensor_value_cache[cache_key] = {"value": val, "ts": time.time()}
    return val


def switch_lamp(lamp_type: str, on: bool) -> bool:
    d = _outlet(f"{lamp_type}_lamp")
    if not d:
        return False
    try:
        result = d.turn_on() if on else d.turn_off()
        if result.get("Error"):
            log.warning("switch_lamp(%s, %s): %s", lamp_type, on, result["Error"])
            return False
        log.info("switch_lamp(%s, %s): OK", lamp_type, on)
        _lamp_cache[lamp_type] = {"online": True, "switch": on, "ts": time.time()}
        return True
    except Exception as e:
        log.error("switch_lamp(%s, %s) error: %s", lamp_type, on, e)
        return False
