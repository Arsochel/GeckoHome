# Gecko Home

Система автоматизации террариума для леопардового геккона. Управление лампами, мониторинг температуры/влажности, детекция движения + YOLO, таймлапс, стрим камеры — через веб-панель и Telegram бот.

## Stack

- **Backend:** FastAPI + APScheduler
- **Database:** SQLite × 2 — `gecko.db` (основная) + `gecko_media.db` (фото BLOB)
- **Smart devices:** tinytuya (локальный LAN) + Tuya Cloud API (батарейные сенсоры)
- **Motion detection:** OpenCV + YOLOv8 (зональная детекция геккона)
- **Timelapse:** автозахват кадров при движении, фильтрация дублей, сборка MP4 через ffmpeg
- **Camera:** RTSP → HLS (ffmpeg) + WebRTC (mediamtx)
- **Tunnel:** Cloudflare Quick Tunnel (внешний доступ)
- **Bot:** python-telegram-bot + Telegram WebApp (стрим)

## Structure

```
GeckoHome/
├── main.py               # FastAPI, WebSocket, Cloudflare tunnel
├── bot.py                # Telegram bot entry point
├── config.py             # Config from .env
├── database.py           # DB schema and CRUD
├── logging_config.py     # Centralized logging setup
├── services/
│   ├── tuya.py           # tinytuya LAN + Tuya Cloud API + UDP listener
│   ├── camera.py         # RTSP snapshot/clip/HLS/WebRTC
│   ├── scheduler.py      # APScheduler: лампы, сенсоры, алерты, бэкап
│   ├── motion.py         # OpenCV motion detection + YOLO зоны
│   ├── zones.py          # Зоны террариума (skull/water/sauna)
│   ├── timelapse.py      # Захват кадров, прунинг, сборка MP4
│   └── highlights.py     # Состояние геккона (roaming/resting/sleeping)
├── routers/
│   ├── auth.py           # Login/logout, CSRF
│   ├── admin.py          # Веб-панель
│   ├── devices.py        # Lamp/sensor/camera API + sensor history
│   └── schedules.py      # Schedule CRUD
├── bot/
│   ├── access.py         # Контроль доступа
│   ├── formatters.py     # Форматирование статуса (RU/EN)
│   ├── keyboards.py      # Inline keyboards
│   ├── i18n.py           # Язык пользователя
│   └── handlers.py       # Обработчики команд и кнопок
├── templates/
│   ├── admin.html        # Веб-панель (лампы, камера, расписания, графики)
│   ├── login.html
│   └── stream.html       # Telegram WebApp стрим
├── static/               # CSS, JS
├── gecko_detect.py       # Standalone: YOLO live + калибровка зон
└── .env                  # Секреты (не коммитить)
```

## Setup

### 1. Requirements

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

ffmpeg должен быть в PATH (нужен для камеры и таймлапса).

### 2. Environment (.env)

```env
# Tuya device IDs (из приложения SmartLife)
DEVICE_UV_LAMP=your_device_id
DEVICE_HEAT_LAMP=your_device_id
DEVICE_THERMOMETER=your_device_id

# Локальные ключи для ламп (постоянно онлайн — LAN достаточно)
DEVICE_UV_LAMP_IP=192.168.x.x
DEVICE_UV_LAMP_LOCAL_KEY=your_local_key
DEVICE_UV_LAMP_VERSION=3.5

DEVICE_HEAT_LAMP_IP=192.168.x.x
DEVICE_HEAT_LAMP_LOCAL_KEY=your_local_key
DEVICE_HEAT_LAMP_VERSION=3.5

# Батарейный термометр — нужен Tuya Cloud API (LAN только как бонус)
DEVICE_THERMOMETER_IP=192.168.x.x
DEVICE_THERMOMETER_LOCAL_KEY=your_local_key
DEVICE_THERMOMETER_VERSION=3.4
TUYA_CLOUD_KEY=your_access_id
TUYA_CLOUD_SECRET=your_access_secret
TUYA_CLOUD_REGION=eu   # eu / us / cn / in

# Веб-панель
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=   # bcrypt: python -c "import bcrypt; print(bcrypt.hashpw(b'pass', bcrypt.gensalt()).decode())"
SECRET_KEY=            # python -c "import secrets; print(secrets.token_hex(32))"

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_SUPER_ADMIN=123456789   # несколько через запятую: 123,456

# Камера (опционально)
CAMERA_RTSP_URL=rtsp://user:pass@192.168.x.x:554/stream
MEDIAMTX_BIN=C:\path\to\mediamtx.exe   # WebRTC низкая задержка

# YOLO (опционально, включает детекцию зон геккона)
YOLO_MODEL_PATH=C:\path\to\best.pt
```

### 3. Получить локальные ключи Tuya

```bash
python -m tinytuya wizard
```

Нужен аккаунт на [iot.tuya.com](https://iot.tuya.com):
1. Создай проект, подключи **IoT Core**
2. Привяжи SmartLife: **Devices → Link Tuya App Account**
3. Запусти wizard с Access ID и Access Secret

> **Батарейные устройства** (термометры, гигрометры) почти всегда спят и не доступны по LAN. Для них нужен Tuya Cloud API — данные приходят с задержкой до 30 мин, но зато стабильно.

### 4. Telegram Bot

1. [@BotFather](https://t.me/BotFather) → создай бота → токен в `.env`
2. Свой Telegram user ID → `TELEGRAM_SUPER_ADMIN`

### 5. Camera (опционально)

RTSP URL → `CAMERA_RTSP_URL`. Кнопки Snapshot / Clip появятся в боте и на веб-панели автоматически.

## Running

```bash
# Веб-панель (порт 8000)
python main.py

# Telegram бот (отдельный процесс)
python -m bot
```

Веб-панель: `http://localhost:8000/admin`

При старте поднимается Cloudflare Quick Tunnel — URL пишется в лог и используется для Telegram WebApp стрима.

## Features

### Веб-панель (`/admin`)
- Live MJPEG стрим + WebRTC (низкая задержка через mediamtx)
- Ручное управление лампами
- График температуры и влажности (24h / 3d / 7d)
- Расписания ламп с паузой и удалением
- Галерея снимков при движении
- Статус устройств

### Telegram бот
- Статус террариума (температура, влажность, состояние геккона, зона)
- Снимок, клип 30с, клип 3 мин
- Стрим через Telegram WebApp (если настроен Cloudflare tunnel)
- Управление лампами и расписаниями (супер-админ)
- Алерты: пора кормить, сверчки заканчиваются, температура/влажность вне нормы
- Дневник кормления с витаминами, бражником, статистикой сверчков
- Язык: RU / EN

### Автоматика
- Расписания ламп с восстановлением после перезапуска
- Запись показаний сенсоров каждые 30 мин
- Ежедневный бэкап БД (хранится 7 копий)
- Детекция движения → клип → Telegram
- Зональная детекция геккона через YOLO (skull / water / sauna)
- Таймлапс: захват кадров при движении → генерация MP4 в 12:00

## Troubleshooting

**Лампы не отвечают:**
- Проверь IP в `.env` — мог поменяться после перезагрузки роутера
- Проверь версию протокола: `python -m tinytuya scan`

**Термометр показывает `—`:**
- Батарейные устройства — нужен Tuya Cloud API (`TUYA_CLOUD_KEY` / `TUYA_CLOUD_SECRET`)
- Данные обновляются раз в 30 мин, это нормально

**Бот не отвечает:**
- Telegram API заблокирован провайдером — нужен VPN

**Стрим не работает извне:**
- Cloudflare tunnel стартует вместе с `python main.py`
- Внешний доступ — HLS (~3с задержка), локальный — WebRTC (~1с)
