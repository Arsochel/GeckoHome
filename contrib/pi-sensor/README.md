# GeckoHome: проводной сенсор на Raspberry Pi

Читает температуру/влажность с проводного датчика и шлёт в GeckoHome через
`POST /api/sensor/ingest` — без Tuya Cloud и его 30-минутных задержек.

Поддерживается:

- **DS18B20** (только температура) — водонепроницаемый, копеечный, 1-Wire, stdlib-only
- **DHT22 / AM2302** (температура + влажность) — нужна `adafruit-circuitpython-dht`

Если подключены оба — температура берётся с DS18B20, влажность с DHT22.

## Подключение DS18B20

| Провод DS18B20 | Pi (физический пин) |
|---|---|
| VCC (красный)  | 3.3V (pin 1) |
| GND (чёрный)   | GND (pin 6) |
| DATA (жёлтый)  | GPIO4 (pin 7) + резистор 4.7 кОм между DATA и VCC |

Включить 1-Wire:

```bash
echo "dtoverlay=w1-gpio" | sudo tee -a /boot/firmware/config.txt
sudo reboot
# проверить: должен появиться каталог 28-*
ls /sys/bus/w1/devices/
```

## Установка

```bash
sudo mkdir -p /opt/gecko-sensor
sudo cp pi_sensor.py /opt/gecko-sensor/
sudo cp gecko-sensor.service /etc/systemd/system/
sudo nano /etc/systemd/system/gecko-sensor.service   # URL и токен!
sudo systemctl daemon-reload
sudo systemctl enable --now gecko-sensor
journalctl -u gecko-sensor -f
```

Токен — тот же, что `SENSOR_INGEST_TOKEN` в `.env` сервера GeckoHome
(пустой токен = эндпоинт выключен).

## DHT22 (опционально, для влажности)

```bash
sudo apt install python3-pip
sudo pip3 install adafruit-circuitpython-dht --break-system-packages
# в unit-файле раскомментировать DHT22_PIN (BCM-номер, обычно 4)
sudo systemctl restart gecko-sensor
```

## Проверка руками

```bash
GECKO_INGEST_URL=http://<host>:8000/api/sensor/ingest \
SENSOR_INGEST_TOKEN=<token> \
python3 pi_sensor.py
```

В веб-панели и боте показания появятся сразу (кэш сенсоров обновляется
при каждом ingest).
