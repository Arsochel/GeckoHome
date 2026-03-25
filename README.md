# Gecko Home

Система автоматизации террариума для леопардового геккона. Управление лампами, мониторинг температуры и влажности, стрим камеры — через веб-панель и Telegram бот.

## Stack

- **Backend:** FastAPI + APScheduler
- **Database:** SQLite (aiosqlite)
- **Smart devices:** tinytuya (локальный LAN, без облака)
- **Motion detection:** OpenCV
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
├── services/
│   ├── tuya.py           # tinytuya local control
│   ├── camera.py         # RTSP snapshot/clip/HLS/WebRTC
│   ├── scheduler.py      # APScheduler jobs
│   ├── motion.py         # OpenCV motion detection
│   └── highlights.py     # Gecko state machine
├── routers/
│   ├── auth.py           # Login/logout
│   ├── admin.py          # Admin page
│   ├── devices.py        # Lamp/sensor API
│   └── schedules.py      # Schedule CRUD
├── bot/
│   ├── access.py         # Access control
│   ├── formatters.py     # Message formatting
│   ├── keyboards.py      # Inline keyboards
│   └── handlers.py       # Command/button handlers
├── templates/
│   ├── admin.html        # Web panel
│   └── stream.html       # Telegram WebApp stream player
├── static/               # CSS, JS, sounds
└── .env                  # Secrets (never commit)
```

## Setup

### 1. Requirements

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

ffmpeg must be installed and available in PATH (needed for camera).

### 2. Environment

```env
# Tuya device IDs
DEVICE_UV_LAMP=your_device_id
DEVICE_HEAT_LAMP=your_device_id

# tinytuya local keys (get via: python -m tinytuya wizard)
DEVICE_UV_LAMP_IP=192.168.x.x
DEVICE_UV_LAMP_LOCAL_KEY=your_local_key
DEVICE_UV_LAMP_VERSION=3.5

DEVICE_HEAT_LAMP_IP=192.168.x.x
DEVICE_HEAT_LAMP_LOCAL_KEY=your_local_key
DEVICE_HEAT_LAMP_VERSION=3.5

# Web panel
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=   # bcrypt hash: python -c "import bcrypt; print(bcrypt.hashpw(b'pass', bcrypt.gensalt()).decode())"
SECRET_KEY=            # python -c "import secrets; print(secrets.token_hex(32))"

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_SUPER_ADMIN=123456789          # несколько через запятую: 123,456

# Sensors (optional, same pattern as lamps)
DEVICE_THERMOMETER=your_device_id
DEVICE_THERMOMETER_IP=192.168.x.x
DEVICE_THERMOMETER_LOCAL_KEY=your_local_key
DEVICE_THERMOMETER_VERSION=3.3

DEVICE_HUMIDIFIER=your_device_id
DEVICE_HUMIDIFIER_IP=192.168.x.x
DEVICE_HUMIDIFIER_LOCAL_KEY=your_local_key
DEVICE_HUMIDIFIER_VERSION=3.3

# Camera (optional)
CAMERA_RTSP_URL=rtsp://user:pass@192.168.x.x:554/stream
```

### 3. Getting local Tuya keys

```bash
python -m tinytuya wizard
```

Wizard сканирует сеть и выгружает ключи. Нужен доступ к [iot.tuya.com](https://iot.tuya.com):

1. Зарегистрируйся и создай проект
2. Подключи **IoT Core** (бесплатный триал — достаточно для получения ключей)
3. Привяжи SmartLife аккаунт: **Devices → Link Tuya App Account**
4. Запусти wizard, введи Access ID и Access Secret из проекта

### 4. Telegram Bot

1. Создай бота через [@BotFather](https://t.me/BotFather)
2. Токен → `.env` `TELEGRAM_BOT_TOKEN`
3. Свой Telegram user ID → `.env` `TELEGRAM_SUPER_ADMIN`

### 5. Camera (optional)

RTSP URL камеры → `.env` `CAMERA_RTSP_URL`. Кнопки камеры в боте появятся автоматически.

## Running

```bash
# Web panel (port 8000)
python main.py

# Telegram bot (separate process)
python -m bot
```

Веб-панель: `http://localhost:8000` → редирект на логин.

При старте автоматически поднимается Cloudflare Quick Tunnel — URL пишется в консоль и используется для Telegram WebApp стрима.

## Troubleshooting

**Лампы не отвечают:**
- Проверь IP в `.env` — мог поменяться после перезагрузки роутера
- Убедись что версия протокола совпадает: `python -m tinytuya scan`

**Бот не отвечает:**
- Telegram API заблокирован провайдером — нужен VPN или прокси

**Стрим не работает извне:**
- Cloudflare tunnel должен быть запущен (стартует вместе с `python main.py`)
- Внешний доступ идёт через HLS, локальный — через WebRTC
