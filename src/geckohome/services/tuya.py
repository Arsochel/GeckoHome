import json
import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

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
# Офлайн-устройство кэшируем дольше: каждый промах — это секунды TCP-таймаутов,
# и меню бота начинает отвечать по 5-10 секунд.
_LAMP_OFFLINE_TTL = 60  # seconds

_sensor_value_cache: dict[str, dict] = {}  # "sensor_type:code" → {"value": any, "ts": float}
_SENSOR_CACHE_TTL = 120  # seconds
_SENSOR_NEGATIVE_TTL = 60  # seconds — кэш «данных нет», чтобы не долбить таймауты

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


# ── Runtime IP rediscovery ─────────────────────────────────────────────────────
# Реальный инцидент 2026-07-13: роутер сменил подсеть, IP из .env протухли,
# лампы стали Device Unreachable, и off не доходил всю ночь. Здесь два пути
# самолечения: пассивный (gwId+ip из UDP-бродкастов Tuya) и активный (при
# фейлах — скан кандидатных /24 по порту 6668 с верификацией локальным ключом).

_discovered_ips: dict[str, str] = {}  # device_id → ip, найденный в рантайме
_rediscover_lock = threading.Lock()
_rediscover_running = False
_last_rediscover = 0.0
_REDISCOVER_COOLDOWN = 600  # секунд между активными сканами
_PROBE_TIMEOUT = 0.3  # секунд на TCP-пробу одного IP


def _effective_ip(device_type: str) -> str | None:
    """IP устройства: найденный в рантайме приоритетнее сконфигуренного."""
    device_id = DEVICE_IDS.get(device_type, "")
    return _discovered_ips.get(device_id) or DEVICE_LOCAL.get(device_type, {}).get("ip") or None


def _handle_discovery_broadcast(data: bytes):
    """Парсит UDP-бродкаст Tuya (gwId+ip) и обновляет карту адресов."""
    try:
        msg = json.loads(tinytuya.decrypt_udp(data))
    except Exception:
        return
    gwid, ip = msg.get("gwId"), msg.get("ip")
    if not gwid or not ip:
        return
    known = {did: name for name, did in DEVICE_IDS.items() if did}
    if gwid not in known:
        return
    device_type = known[gwid]
    if _discovered_ips.get(gwid) == ip:
        return
    old = _effective_ip(device_type)
    _discovered_ips[gwid] = ip
    if ip != old:
        log.warning("discovery: %s moved %s → %s (broadcast)", device_type, old, ip)


def _candidate_subnets() -> set[str]:
    """/24-подсети, где могут жить устройства: LAN_SUBNETS из конфига,
    производные от IP устройств, свои интерфейсы и host.docker.internal.

    LAN_SUBNETS — главный источник в Docker: host.docker.internal на Docker
    Desktop отдаёт внутренний NAT (192.168.65.x), а не реальную LAN.
    """
    from geckohome import config

    prefixes = set()
    for raw in config.LAN_SUBNETS.split(","):
        raw = raw.strip().removesuffix("/24").removesuffix(".0")
        if raw and raw.count(".") == 2:
            prefixes.add(raw)
    for info in DEVICE_LOCAL.values():
        ip = info.get("ip", "")
        if ip and "." in ip:
            prefixes.add(ip.rsplit(".", 1)[0])
    for probe in ("host.docker.internal", None):
        try:
            if probe:
                ip = socket.gethostbyname(probe)
            else:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 53))
                ip = s.getsockname()[0]
                s.close()
            if ip and not ip.startswith("127."):
                prefixes.add(ip.rsplit(".", 1)[0])
        except OSError:
            pass
    return prefixes


def _port_open(ip: str, port: int = 6668) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=_PROBE_TIMEOUT):
            return True
    except OSError:
        return False


def _probe_device(device_type: str, ip: str) -> bool:
    """Проверяет, что по ip живёт именно это устройство (ключом)."""
    info = DEVICE_LOCAL.get(device_type, {})
    device_id = DEVICE_IDS.get(device_type, "")
    if not device_id or not info.get("key"):
        return False
    try:
        d = tinytuya.Device(
            dev_id=device_id,
            address=ip,
            local_key=info["key"],
            version=info.get("version", "3.4"),
        )
        d.set_socketRetryLimit(1)
        d.set_socketTimeout(2)
        result = d.status()
        return isinstance(result, dict) and not result.get("Error") and "dps" in result
    except Exception:
        return False


def rediscover_devices(force: bool = False) -> dict[str, str]:
    """Скан кандидатных подсетей: TCP 6668, потом верификация ключом.

    Возвращает {device_type: new_ip} для найденных. Троттлится кулдауном.
    """
    global _last_rediscover, _rediscover_running
    with _rediscover_lock:
        if _rediscover_running:
            return {}
        if not force and time.time() - _last_rediscover < _REDISCOVER_COOLDOWN:
            return {}
        _rediscover_running = True
        _last_rediscover = time.time()

    try:
        targets = [
            dt for dt, info in DEVICE_LOCAL.items() if DEVICE_IDS.get(dt) and info.get("key")
        ]
        if not targets:
            return {}
        subnets = _candidate_subnets()
        candidates = [f"{p}.{i}" for p in subnets for i in range(1, 255)]
        log.info("rediscovery: scanning %d IPs in %s", len(candidates), sorted(subnets))
        with ThreadPoolExecutor(max_workers=64) as pool:
            results = pool.map(_port_open, candidates)
            open_ips = [ip for ip, ok in zip(candidates, results, strict=True) if ok]
        log.info("rediscovery: %d hosts with 6668 open: %s", len(open_ips), open_ips)

        found: dict[str, str] = {}
        remaining = list(targets)
        for ip in open_ips:
            for dt in list(remaining):
                if _probe_device(dt, ip):
                    old = _effective_ip(dt)
                    _discovered_ips[DEVICE_IDS[dt]] = ip
                    remaining.remove(dt)
                    found[dt] = ip
                    if ip != old:
                        log.warning("rediscovery: %s moved %s → %s", dt, old, ip)
                    break
        return found
    finally:
        with _rediscover_lock:
            _rediscover_running = False


def _schedule_rediscovery():
    """Фоновое переоткрытие; no-op если кулдаун не вышел или скан уже идёт."""
    if _rediscover_running or time.time() - _last_rediscover < _REDISCOVER_COOLDOWN:
        return
    threading.Thread(target=rediscover_devices, daemon=True).start()


# ── Passive UDP listener for battery devices ──────────────────────────────────
# Кэш последних значений полученных из local broadcast
_sensor_cache: dict[str, dict] = {}  # device_id → {"temp": int, "hum": int, "ts": float}
_listener_started = False
_listener_lock = threading.Lock()


def _listener_thread():
    device_id = DEVICE_IDS.get("thermometer", "")
    local_key = DEVICE_LOCAL.get("thermometer", {}).get("key", "")

    # Device нужен только для расшифровки данных термометра (соединение не
    # открывается); discovery-парсинг бродкастов работает и без него.
    d = None
    if device_id and local_key:
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

    log.info("UDP listener started (discovery + thermometer)")
    while True:
        for s in socks:
            try:
                data, addr = s.recvfrom(4096)
                _handle_discovery_broadcast(data)
                if d is None:
                    continue
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
    ip = _effective_ip(device_type)
    if not device_id or not ip or not info.get("key"):
        return None
    try:
        d = tinytuya.OutletDevice(
            dev_id=device_id,
            address=ip,
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
    ip = _effective_ip(device_type)
    if not device_id or not ip or not info.get("key"):
        return None
    try:
        d = tinytuya.Device(
            dev_id=device_id,
            address=ip,
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
    if cached:
        ttl = _LAMP_CACHE_TTL if cached["online"] else _LAMP_OFFLINE_TTL
        if time.time() - cached["ts"] < ttl:
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
            _schedule_rediscovery()
            return {"online": False, "switch": last_switch}
        switch = result.get("dps", {}).get("1")
        _lamp_cache[lamp_type] = {"online": True, "switch": switch, "ts": time.time()}
        return {"online": True, "switch": switch}
    except Exception as e:
        log.error("status %s error: %s", lamp_type, e)
        _lamp_cache[lamp_type] = {"online": False, "switch": last_switch, "ts": time.time()}
        _schedule_rediscovery()
        return {"online": False, "switch": last_switch}


_CODE_TO_DPS = {"va_temperature": "1", "va_humidity": "2"}
_CLOUD_CODES = {
    "va_temperature": "temp_current",
    "va_humidity": "humidity_value",
}


_cloud_dead_until = 0.0
_CLOUD_DEAD_TTL = 600  # секунд не дёргать облако после ошибки (напр. истёкшая подписка)


def _get_sensor_cloud(device_id: str, code: str):
    import socket

    global _cloud_dead_until
    if time.time() < _cloud_dead_until:
        return None
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
            _cloud_dead_until = time.time() + _CLOUD_DEAD_TTL
            log.warning(
                "cloud request failed (%s) — skipping cloud for %ds", r.get("msg"), _CLOUD_DEAD_TTL
            )
            return None
        cloud_code = _CLOUD_CODES.get(code)
        for prop in r["result"]["properties"]:
            if prop["code"] == cloud_code:
                return prop["value"]
    except Exception as e:
        _cloud_dead_until = time.time() + _CLOUD_DEAD_TTL
        log.error("cloud sensor error: %s — skipping cloud for %ds", e, _CLOUD_DEAD_TTL)
    return None


def get_sensor(sensor_type: str, code: str):
    cache_key = f"{sensor_type}:{code}"
    # 0. short-term in-memory cache (заполняется каждые 30 мин планировщиком);
    # «данных нет» тоже кэшируется, но короче
    cached = _sensor_value_cache.get(cache_key)
    if cached:
        ttl = _SENSOR_CACHE_TTL if cached["value"] is not None else _SENSOR_NEGATIVE_TTL
        if time.time() - cached["ts"] < ttl:
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
    # None тоже кэшируем (negative TTL) — иначе каждый статус в боте
    # заново собирает все таймауты цепочки
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
            _schedule_rediscovery()
            return False
        log.info("switch_lamp(%s, %s): OK", lamp_type, on)
        _lamp_cache[lamp_type] = {"online": True, "switch": on, "ts": time.time()}
        return True
    except Exception as e:
        log.error("switch_lamp(%s, %s) error: %s", lamp_type, on, e)
        _schedule_rediscovery()
        return False
