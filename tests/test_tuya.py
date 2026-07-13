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


# ── warm-кэши из БД ──


async def test_warm_sensor_cache_from_db(monkeypatch):
    await log_sensor_reading(257, 48)
    await tuya.warm_sensor_cache()
    monkeypatch.setattr(tuya, "_device", lambda t: pytest.fail("кэш тёплый, LAN не нужен"))
    assert tuya.get_sensor("thermometer", "va_temperature") == 257
    assert tuya.get_sensor("humidifier", "va_humidity") == 48
