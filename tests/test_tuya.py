"""Логика tuya.py без железа: кэши ламп, fallback-цепочка сенсоров.

Цепочка get_sensor: value-кэш → broadcast-кэш → локальный LAN → облако.
"""

import time

import pytest

from geckohome.database import log_sensor_reading
from geckohome.services import tuya


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch):
    monkeypatch.setattr(tuya, "_lamp_cache", {})
    monkeypatch.setattr(tuya, "_sensor_value_cache", {})
    monkeypatch.setattr(tuya, "_sensor_cache", {})
    monkeypatch.setattr(tuya, "_discovered_ips", {})
    monkeypatch.setattr(tuya, "_last_rediscover", 0.0)
    monkeypatch.setattr(tuya, "_rediscover_running", False)
    monkeypatch.setattr(tuya, "_cloud_dead_until", 0.0)


class FakeOutlet:
    def __init__(self, status=None, error=None, raises=False):
        self._status = status or {}
        self._error = error
        self._raises = raises
        self.status_calls = 0

    def status(self):
        self.status_calls += 1
        if self._raises:
            raise OSError("network unreachable")
        if self._error:
            return {"Error": self._error}
        return self._status

    def turn_on(self):
        if self._raises:
            raise OSError("network unreachable")
        return {"Error": self._error} if self._error else {"dps": {"1": True}}

    def turn_off(self):
        if self._raises:
            raise OSError("network unreachable")
        return {"Error": self._error} if self._error else {"dps": {"1": False}}


# ── get_lamp_status ──


def test_lamp_status_without_device_config(monkeypatch):
    monkeypatch.setattr(tuya, "_outlet", lambda t: None)
    assert tuya.get_lamp_status("uv") == {"online": None, "switch": None}


def test_lamp_status_reads_device_and_caches(monkeypatch):
    outlet = FakeOutlet(status={"dps": {"1": True}})
    monkeypatch.setattr(tuya, "_outlet", lambda t: outlet)

    assert tuya.get_lamp_status("uv") == {"online": True, "switch": True}
    assert tuya.get_lamp_status("uv") == {"online": True, "switch": True}
    assert outlet.status_calls == 1  # второй ответ из кэша (TTL 15с)


def test_lamp_status_error_keeps_last_known_switch(monkeypatch):
    # сначала успешный опрос
    monkeypatch.setattr(tuya, "_outlet", lambda t: FakeOutlet(status={"dps": {"1": True}}))
    tuya.get_lamp_status("uv")
    tuya._lamp_cache["uv"]["ts"] = 0  # протухание кэша

    # затем устройство отвечает ошибкой — switch сохраняется, online=False
    monkeypatch.setattr(tuya, "_outlet", lambda t: FakeOutlet(error="905: device offline"))
    assert tuya.get_lamp_status("uv") == {"online": False, "switch": True}


def test_lamp_status_exception_keeps_last_known_switch(monkeypatch):
    monkeypatch.setattr(tuya, "_outlet", lambda t: FakeOutlet(status={"dps": {"1": False}}))
    tuya.get_lamp_status("heat")
    tuya._lamp_cache["heat"]["ts"] = 0

    monkeypatch.setattr(tuya, "_outlet", lambda t: FakeOutlet(raises=True))
    assert tuya.get_lamp_status("heat") == {"online": False, "switch": False}


# ── switch_lamp ──


def test_switch_lamp_success_updates_cache(monkeypatch):
    outlet = FakeOutlet()
    monkeypatch.setattr(tuya, "_outlet", lambda t: outlet)

    assert tuya.switch_lamp("uv", True) is True
    # статус берётся из кэша, без запроса к устройству
    assert tuya.get_lamp_status("uv") == {"online": True, "switch": True}
    assert outlet.status_calls == 0


def test_switch_lamp_failures(monkeypatch):
    monkeypatch.setattr(tuya, "_outlet", lambda t: None)
    assert tuya.switch_lamp("uv", True) is False

    monkeypatch.setattr(tuya, "_outlet", lambda t: FakeOutlet(error="offline"))
    assert tuya.switch_lamp("uv", True) is False

    monkeypatch.setattr(tuya, "_outlet", lambda t: FakeOutlet(raises=True))
    assert tuya.switch_lamp("uv", False) is False


# ── get_sensor: цепочка фолбэков ──


def test_sensor_value_cache_wins(monkeypatch):
    monkeypatch.setattr(
        tuya, "_device", lambda t: pytest.fail("не должен лезть в LAN при живом кэше")
    )
    tuya.set_sensor_value("thermometer", "va_temperature", 253)
    assert tuya.get_sensor("thermometer", "va_temperature") == 253


def test_sensor_broadcast_cache_is_second(monkeypatch):
    monkeypatch.setitem(tuya.DEVICE_IDS, "thermometer", "dev123")
    tuya._sensor_cache["dev123"] = {"temp": 261, "hum": 44, "ts": time.time()}
    monkeypatch.setattr(tuya, "_device", lambda t: pytest.fail("broadcast-кэш должен победить"))

    assert tuya.get_sensor("thermometer", "va_temperature") == 261
    assert tuya.get_sensor("thermometer", "va_humidity") == 44


def test_sensor_stale_broadcast_ignored(monkeypatch):
    monkeypatch.setitem(tuya.DEVICE_IDS, "thermometer", "dev123")
    tuya._sensor_cache["dev123"] = {"temp": 261, "ts": time.time() - 7200}  # старше часа
    monkeypatch.setattr(tuya, "_device", lambda t: None)
    monkeypatch.setattr(tuya, "_get_sensor_cloud", lambda d, c: None)

    assert tuya.get_sensor("thermometer", "va_temperature") is None


def test_sensor_local_lan_is_third(monkeypatch):
    monkeypatch.setattr(tuya, "_device", lambda t: FakeOutlet(status={"dps": {"1": 249}}))
    assert tuya.get_sensor("thermometer", "va_temperature") == 249
    # значение осело в value-кэше
    assert tuya._sensor_value_cache["thermometer:va_temperature"]["value"] == 249


def test_sensor_cloud_is_last_resort(monkeypatch):
    monkeypatch.setitem(tuya.DEVICE_IDS, "thermometer", "dev123")
    monkeypatch.setattr(tuya, "_device", lambda t: None)
    monkeypatch.setattr(
        tuya, "_get_sensor_cloud", lambda dev, code: 245 if dev == "dev123" else None
    )
    assert tuya.get_sensor("thermometer", "va_temperature") == 245
    assert tuya._sensor_value_cache["thermometer:va_temperature"]["value"] == 245


def test_sensor_all_sources_dead(monkeypatch):
    monkeypatch.setattr(tuya, "_device", lambda t: None)
    monkeypatch.setattr(tuya, "_get_sensor_cloud", lambda d, c: None)
    assert tuya.get_sensor("thermometer", "va_temperature") is None


# ── negative-кэши: офлайн-железо не должно тормозить бота ──


def test_offline_lamp_cached_longer(monkeypatch):
    outlet = FakeOutlet(raises=True)
    monkeypatch.setattr(tuya, "_outlet", lambda t: outlet)

    tuya.get_lamp_status("uv")
    assert outlet.status_calls == 1
    # 20с прошло: онлайн-TTL (15с) истёк, но офлайн-TTL (60с) держит кэш
    tuya._lamp_cache["uv"]["ts"] = time.time() - 20
    tuya.get_lamp_status("uv")
    assert outlet.status_calls == 1  # повторного похода в сеть нет


def test_sensor_none_is_negative_cached(monkeypatch):
    calls = []

    def fake_device(t):
        calls.append(t)
        return None

    monkeypatch.setattr(tuya, "_device", fake_device)
    monkeypatch.setattr(tuya, "_get_sensor_cloud", lambda d, c: None)

    assert tuya.get_sensor("thermometer", "va_temperature") is None
    assert tuya.get_sensor("thermometer", "va_temperature") is None
    assert len(calls) == 1  # второй раз ответ из negative-кэша


def test_cloud_failure_disables_cloud_temporarily(monkeypatch):
    calls = []

    class _FailingCloud:
        def cloudrequest(self, url):
            calls.append(url)
            return {"success": False, "msg": "IoT Core service subscription has expired."}

    monkeypatch.setattr(tuya, "_get_cloud", lambda: _FailingCloud())
    assert tuya._get_sensor_cloud("dev123", "va_temperature") is None
    assert tuya._get_sensor_cloud("dev123", "va_temperature") is None
    assert len(calls) == 1  # второй вызов даже не пошёл в облако


# ── _get_sensor_cloud ──


class FakeCloud:
    def __init__(self, response):
        self._response = response

    def cloudrequest(self, url):
        return self._response


def test_cloud_parses_shadow_properties(monkeypatch):
    resp = {
        "success": True,
        "result": {
            "properties": [
                {"code": "battery_percentage", "value": 80},
                {"code": "temp_current", "value": 252},
                {"code": "humidity_value", "value": 41},
            ]
        },
    }
    monkeypatch.setattr(tuya, "_get_cloud", lambda: FakeCloud(resp))
    assert tuya._get_sensor_cloud("dev123", "va_temperature") == 252
    assert tuya._get_sensor_cloud("dev123", "va_humidity") == 41


def test_cloud_failure_returns_none(monkeypatch):
    monkeypatch.setattr(tuya, "_get_cloud", lambda: FakeCloud({"success": False}))
    assert tuya._get_sensor_cloud("dev123", "va_temperature") is None
    monkeypatch.setattr(tuya, "_get_cloud", lambda: None)
    assert tuya._get_sensor_cloud("dev123", "va_temperature") is None


# ── rediscovery: самолечение после смены IP (инцидент 2026-07-13) ──


def _configure_uv(monkeypatch):
    monkeypatch.setitem(tuya.DEVICE_IDS, "uv_lamp", "uvid123")
    monkeypatch.setitem(
        tuya.DEVICE_LOCAL, "uv_lamp", {"ip": "192.168.3.6", "key": "k", "version": "3.5"}
    )


def test_effective_ip_prefers_discovered(monkeypatch):
    _configure_uv(monkeypatch)
    assert tuya._effective_ip("uv_lamp") == "192.168.3.6"
    tuya._discovered_ips["uvid123"] = "192.169.3.20"
    assert tuya._effective_ip("uv_lamp") == "192.169.3.20"


def test_broadcast_updates_known_device(monkeypatch):
    import tinytuya

    _configure_uv(monkeypatch)
    monkeypatch.setattr(
        tinytuya,
        "decrypt_udp",
        lambda data: '{"gwId": "uvid123", "ip": "192.169.3.20", "version": "3.5"}',
    )
    tuya._handle_discovery_broadcast(b"whatever")
    assert tuya._effective_ip("uv_lamp") == "192.169.3.20"


def test_broadcast_ignores_unknown_and_garbage(monkeypatch):
    import tinytuya

    _configure_uv(monkeypatch)
    monkeypatch.setattr(
        tinytuya, "decrypt_udp", lambda data: '{"gwId": "stranger", "ip": "1.2.3.4"}'
    )
    tuya._handle_discovery_broadcast(b"x")
    assert tuya._discovered_ips == {}

    def _boom(data):
        raise ValueError("not tuya")

    monkeypatch.setattr(tinytuya, "decrypt_udp", _boom)
    tuya._handle_discovery_broadcast(b"x")  # не падает
    assert tuya._discovered_ips == {}


def test_candidate_subnets_include_lan_subnets_env(monkeypatch):
    from geckohome import config

    _configure_uv(monkeypatch)
    monkeypatch.setattr(config, "LAN_SUBNETS", "192.169.3.0/24, 10.0.5")
    subnets = tuya._candidate_subnets()
    assert "192.169.3" in subnets
    assert "10.0.5" in subnets
    assert "192.168.3" in subnets  # производная от сконфигуренного IP лампы


def test_rediscover_finds_moved_device(monkeypatch):
    _configure_uv(monkeypatch)
    monkeypatch.setattr(tuya, "_candidate_subnets", lambda: {"192.169.3"})
    monkeypatch.setattr(tuya, "_port_open", lambda ip: ip == "192.169.3.20")
    monkeypatch.setattr(
        tuya, "_probe_device", lambda dt, ip: dt == "uv_lamp" and ip == "192.169.3.20"
    )

    found = tuya.rediscover_devices(force=True)

    assert found == {"uv_lamp": "192.169.3.20"}
    assert tuya._effective_ip("uv_lamp") == "192.169.3.20"


def test_rediscover_respects_cooldown(monkeypatch):
    _configure_uv(monkeypatch)
    monkeypatch.setattr(tuya, "_candidate_subnets", lambda: {"192.169.3"})
    monkeypatch.setattr(tuya, "_port_open", lambda ip: False)
    monkeypatch.setattr(tuya, "_probe_device", lambda dt, ip: False)

    assert tuya.rediscover_devices(force=True) == {}
    # кулдаун не вышел — без force скан не запускается
    monkeypatch.setattr(
        tuya, "_candidate_subnets", lambda: pytest.fail("скан не должен был начаться")
    )
    assert tuya.rediscover_devices() == {}


def test_switch_failure_schedules_rediscovery(monkeypatch):
    calls = []
    monkeypatch.setattr(tuya, "_schedule_rediscovery", lambda: calls.append(1))
    monkeypatch.setattr(tuya, "_outlet", lambda t: FakeOutlet(error="unreachable"))
    tuya.switch_lamp("uv", False)
    assert calls  # фейл переключения запускает переоткрытие


# ── warm-кэши из БД ──


async def test_warm_sensor_cache_from_db(monkeypatch):
    await log_sensor_reading(257, 48)
    await tuya.warm_sensor_cache()
    monkeypatch.setattr(tuya, "_device", lambda t: pytest.fail("кэш тёплый, LAN не нужен"))
    assert tuya.get_sensor("thermometer", "va_temperature") == 257
    assert tuya.get_sensor("humidifier", "va_humidity") == 48
