#!/usr/bin/env python3
"""Проводной сенсор на Raspberry Pi → GeckoHome /api/sensor/ingest.

Только stdlib — на Pi ничего ставить не нужно. Поддерживается:
  - DS18B20 (температура) через 1-Wire: /sys/bus/w1/devices/28-*/w1_slave
  - DHT22 (температура + влажность) через adafruit-circuitpython-dht,
    если библиотека установлена (pip install adafruit-circuitpython-dht)

Конфигурация через переменные окружения (см. gecko-sensor.service):
  GECKO_INGEST_URL     http://<host>:8000/api/sensor/ingest
  SENSOR_INGEST_TOKEN  тот же токен, что в .env сервера
  SENSOR_INTERVAL      секунд между отправками (default 300)
  DHT22_PIN            BCM-номер пина DHT22 (default: не используется)
"""

import glob
import json
import logging
import os
import time
import urllib.error
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pi-sensor")

INGEST_URL = os.environ.get("GECKO_INGEST_URL", "")
TOKEN = os.environ.get("SENSOR_INGEST_TOKEN", "")
INTERVAL = int(os.environ.get("SENSOR_INTERVAL", "300"))
DHT22_PIN = os.environ.get("DHT22_PIN", "")

RETRIES = 3
RETRY_DELAY = 10  # секунд между ретраями внутри одного цикла


def read_ds18b20() -> float | None:
    """Температура с первого найденного DS18B20, °C."""
    for path in glob.glob("/sys/bus/w1/devices/28-*/w1_slave"):
        try:
            with open(path) as f:
                data = f.read()
        except OSError as e:
            log.warning("w1 read failed: %s", e)
            continue
        # строка 1: "... crc=xx YES", строка 2: "... t=25312"
        if "YES" not in data.splitlines()[0]:
            log.warning("w1 crc check failed for %s", path)
            continue
        _, _, t = data.rpartition("t=")
        try:
            return int(t.strip()) / 1000.0
        except ValueError:
            continue
    return None


_dht = None


def read_dht22() -> tuple[float | None, float | None]:
    """(температура, влажность) с DHT22, если настроен и библиотека доступна."""
    global _dht
    if not DHT22_PIN:
        return None, None
    try:
        if _dht is None:
            import adafruit_dht
            import board

            _dht = adafruit_dht.DHT22(getattr(board, f"D{DHT22_PIN}"))
        return _dht.temperature, _dht.humidity
    except Exception as e:
        # DHT22 регулярно фейлит чтение — это нормально, попробуем в следующий раз
        log.debug("dht22 read failed: %s", e)
        return None, None


def post_reading(temperature: float | None, humidity: float | None) -> bool:
    body = json.dumps({"temperature": temperature, "humidity": humidity}).encode()
    req = urllib.request.Request(
        INGEST_URL,
        data=body,
        headers={"Content-Type": "application/json", "X-Token": TOKEN},
        method="POST",
    )
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                log.info("sent temp=%s hum=%s → %s", temperature, humidity, resp.status)
                return True
        except (urllib.error.URLError, OSError) as e:
            log.warning("post failed (%d/%d): %s", attempt, RETRIES, e)
            if attempt < RETRIES:
                time.sleep(RETRY_DELAY)
    return False


def main():
    if not INGEST_URL or not TOKEN:
        raise SystemExit("GECKO_INGEST_URL и SENSOR_INGEST_TOKEN обязательны")
    log.info("started: %s, every %ds", INGEST_URL, INTERVAL)
    while True:
        temp = read_ds18b20()
        dht_temp, hum = read_dht22()
        if temp is None:
            temp = dht_temp  # DS18B20 приоритетнее, DHT22 — запасной
        if temp is None and hum is None:
            log.warning("no sensor data this cycle")
        else:
            post_reading(temp, hum)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
