"""Local sensor ingest: token guard, °C→×10 conversion, value reaches get_sensor."""

import pytest
from fastapi import HTTPException

from geckohome.services import tuya
from geckohome.web.routers import ingest as ing


async def test_ingest_writes_value_reachable_via_get_sensor(monkeypatch):
    monkeypatch.setattr(ing, "SENSOR_INGEST_TOKEN", "secret")
    r = await ing.ingest_sensor(
        ing.SensorReading(temperature=25.3, humidity=48), x_token="secret"
    )
    assert r["ok"] is True
    assert r["temperature_x10"] == 253
    assert r["humidity"] == 48
    # the same cache get_sensor() reads first — no Tuya call involved
    assert tuya.get_sensor("thermometer", "va_temperature") == 253
    assert tuya.get_sensor("thermometer", "va_humidity") == 48
    assert tuya.get_sensor("humidifier", "va_humidity") == 48


async def test_ingest_rejects_bad_token(monkeypatch):
    monkeypatch.setattr(ing, "SENSOR_INGEST_TOKEN", "secret")
    with pytest.raises(HTTPException) as exc:
        await ing.ingest_sensor(ing.SensorReading(temperature=20.0), x_token="wrong")
    assert exc.value.status_code == 401


async def test_ingest_rejects_empty_token_config(monkeypatch):
    monkeypatch.setattr(ing, "SENSOR_INGEST_TOKEN", "")
    with pytest.raises(HTTPException) as exc:
        await ing.ingest_sensor(ing.SensorReading(temperature=20.0), x_token="")
    assert exc.value.status_code == 401


async def test_ingest_requires_some_reading(monkeypatch):
    monkeypatch.setattr(ing, "SENSOR_INGEST_TOKEN", "secret")
    with pytest.raises(HTTPException) as exc:
        await ing.ingest_sensor(ing.SensorReading(), x_token="secret")
    assert exc.value.status_code == 400
