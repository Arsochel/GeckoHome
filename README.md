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

Код — устанавливаемый пакет `src/geckohome/`. Точки входа: `geckohome-web`, `geckohome-bot`.

```
GeckoHome/
├── pyproject.toml          # пакет, зависимости (пиннинг), ruff/mypy/pytest config
├── src/geckohome/
│   ├── paths.py            # единый источник путей данных (якорь = CWD)
│   ├── config.py           # настройки через pydantic-settings (.env)
│   ├── logging_config.py
│   ├── database/           # слой данных (пакет по доменам), __init__ ре-экспортит API
│   │   └── _core · schema · users · schedules · lamps · sensors · photos · motion · feeding · gecko · alerts
│   ├── web/
│   │   ├── app.py          # FastAPI, WebSocket, lifespan, run()
│   │   └── routers/        # auth · admin · devices · schedules · debug · stats
│   ├── services/
│   │   ├── tuya.py         # tinytuya LAN + Tuya Cloud fallback + UDP listener
│   │   ├── camera.py · motion.py · zones.py · timelapse.py · highlights.py
│   │   ├── tunnel.py · yolo.py · debug_log.py
│   │   └── scheduler/      # APScheduler (пакет): _core · notify · lamps · sensors · feeding · backup · jobs
│   └── bot/
│       ├── main.py         # точка входа бота, run()
│       ├── handlers/       # пакет: dispatch · _helpers · access · lamps · media · schedules · feeding · motion
│       └── keyboards.py · formatters.py · access.py · i18n.py
├── tests/                  # pytest (config, database, feeding, scheduler logic, fresh-deploy)
├── templates/ · static/    # Jinja2 + статика (в корне, путь через paths.py)
├── .github/workflows/      # ci.yml (ruff/pytest/mypy) + deploy.yml
├── gecko_detect.py · motion_debug.py · timelapse_debug.py   # standalone dev-утилиты
├── .env.example            # шаблон конфигурации
└── .env                    # секреты (не коммитить)
```

## Setup

### 1. Deploy (Docker)

```bash
# Первый запуск — сборка образа и старт
docker compose up --build -d

# Логи
docker compose logs -f
```

Требует Docker. На Windows — Docker Engine в WSL2 (см. `setup.ps1`).  
При каждом пуше в `main` GitHub Actions автоматически пересобирает и перезапускает контейнеры.

### 2. Development (без Docker)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"   # пакет + dev-инструменты (ruff, mypy, pytest)
cp .env.example .env       # заполнить значения

geckohome-web    # веб-панель (порт 8000), == python -m geckohome.web.app
geckohome-bot    # Telegram бот (отдельный процесс), == python -m geckohome.bot.main

# Тесты и линт
pytest
ruff check src tests && ruff format --check src tests
```

ffmpeg должен быть в PATH.

### 3. Environment (.env)

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
MEDIAMTX_BIN=mediamtx/mediamtx.exe   # WebRTC низкая задержка (путь относительно корня проекта)

# YOLO (опционально, включает детекцию зон геккона)
YOLO_MODEL_PATH=models/gecko_yolo.pt  # путь относительно корня проекта
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
docker compose up -d          # старт
docker compose down           # стоп
docker compose logs -f app    # логи сервера
docker compose logs -f bot    # логи бота
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
- Cloudflare tunnel стартует вместе с веб-сервером (`geckohome-web`)
- Внешний доступ — HLS (~3с задержка), локальный — WebRTC (~1с)
