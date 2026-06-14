"""Local, cloud-free sensor ingest.

Any LAN source (ESP32/ESPHome, a Zigbee2MQTT bridge, a host-side script) can POST
readings here; they land in the same cache that /status, the WebSocket and the
scheduler read, fully bypassing Tuya. Guarded by a shared token.

    curl -X POST http://host:8000/api/sensor/ingest \
         -H "X-Token: $SENSOR_INGEST_TOKEN" \
         -H "Content-Type: application/json" \
         -d '{"temperature": 25.3, "humidity": 48}'
"""

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from geckohome.config import SENSOR_INGEST_TOKEN
from geckohome.database import log_sensor_reading
from geckohome.services import tuya

log = logging.getLogger(__name__)

router = APIRouter()


class SensorReading(BaseModel):
    temperature: float | None = Field(default=None, description="°C")
    humidity: float | None = Field(default=None, description="%")


@router.post("/api/sensor/ingest")
async def ingest_sensor(reading: SensorReading, x_token: str = Header(default="")):
    if not SENSOR_INGEST_TOKEN or x_token != SENSOR_INGEST_TOKEN:
        raise HTTPException(status_code=401, detail="invalid ingest token")
    if reading.temperature is None and reading.humidity is None:
        raise HTTPException(status_code=400, detail="temperature or humidity required")

    # Temperature is stored ×10 across the app (e.g. 253 == 25.3 °C).
    temp_x10 = round(reading.temperature * 10) if reading.temperature is not None else None
    hum = round(reading.humidity) if reading.humidity is not None else None

    if temp_x10 is not None:
        tuya.set_sensor_value("thermometer", "va_temperature", temp_x10)
    if hum is not None:
        tuya.set_sensor_value("thermometer", "va_humidity", hum)
        tuya.set_sensor_value("humidifier", "va_humidity", hum)

    await log_sensor_reading(temp_x10, hum)
    log.info("sensor ingest: temp=%s hum=%s", temp_x10, hum)
    return {"ok": True, "temperature_x10": temp_x10, "humidity": hum}
