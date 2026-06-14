"""Opportunistic thermometer poll: caches on a caught wake, no-op when asleep."""

from geckohome.services import tuya


def _clear():
    for k in ("thermometer:va_temperature", "thermometer:va_humidity", "humidifier:va_humidity"):
        tuya._sensor_value_cache.pop(k, None)


def test_poll_caches_when_device_awake(monkeypatch):
    _clear()

    class AwakeDevice:
        def status(self):
            return {"dps": {"1": 251, "2": 47}}

    monkeypatch.setattr(tuya, "_device", lambda _type: AwakeDevice())
    assert tuya.poll_thermometer() is True
    # reachable via the same cache get_sensor() reads first
    assert tuya.get_sensor("thermometer", "va_temperature") == 251
    assert tuya.get_sensor("thermometer", "va_humidity") == 47
    assert tuya.get_sensor("humidifier", "va_humidity") == 47


def test_poll_noop_when_asleep(monkeypatch):
    _clear()

    class SleepingDevice:
        def status(self):
            return {"Error": "Network Error: Device Unreachable", "Err": "905"}

    monkeypatch.setattr(tuya, "_device", lambda _type: SleepingDevice())
    assert tuya.poll_thermometer() is False
    assert "thermometer:va_temperature" not in tuya._sensor_value_cache


def test_poll_handles_no_device(monkeypatch):
    _clear()
    monkeypatch.setattr(tuya, "_device", lambda _type: None)
    assert tuya.poll_thermometer() is False
