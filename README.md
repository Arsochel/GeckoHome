# Gecko Home

Smart home system for leopard gecko. Controls UV/heat lamps, monitors temperature and humidity, streams camera — via web panel and Telegram bot.

## Stack

- **Backend:** FastAPI + APScheduler
- **Database:** SQLite (aiosqlite)
- **Smart devices:** Tuya Cloud API
- **Camera:** RTSP via ffmpeg
- **Bot:** python-telegram-bot

## Project Structure

```
GeckoHome/
├── main.py               # FastAPI entry point
├── bot.py                # Telegram bot entry point
├── config.py             # Config from .env
├── database.py           # DB schema and CRUD
├── services/
│   ├── tuya.py           # Tuya API wrapper
│   ├── camera.py         # RTSP snapshot/clip
│   └── scheduler.py      # APScheduler jobs
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
├── templates/            # Jinja2 HTML
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

Copy `.env` and fill in your values:

```env
TUYA_ENDPOINT=https://openapi.tuyaeu.com
TUYA_ACCESS_ID=your_access_id
TUYA_ACCESS_KEY=your_access_key

DEVICE_UV_LAMP=your_device_id
DEVICE_HEAT_LAMP=
DEVICE_THERMOMETER=
DEVICE_HUMIDIFIER=

ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=   # sha256 hash of your password
SECRET_KEY=            # random hex string

TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_SUPER_ADMIN=your_telegram_user_id

CAMERA_RTSP_URL=       # rtsp://user:pass@ip:554/stream
```

**Generate password hash:**
```bash
python -c "from hashlib import sha256; print(sha256(b'your_password').hexdigest())"
```

**Generate SECRET_KEY:**
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 3. Tuya IoT Platform

1. Register at [iot.tuya.com](https://iot.tuya.com)
2. Create a project, pick the correct **Data Center** for your region:
   - Europe: `https://openapi.tuyaeu.com`
   - USA: `https://openapi.tuyaus.com`
   - China: `https://openapi.tuyacn.com`
   - India: `https://openapi.tuyain.com`
3. Link your SmartLife account under **Devices → Link Tuya App Account**
4. Copy **Access ID** and **Access Secret** to `.env`

> The region in your Tuya IoT project must match the region of your SmartLife account, otherwise devices will appear offline.

### 4. Telegram Bot

1. Create a bot via [@BotFather](https://t.me/BotFather)
2. Put the token in `.env` → `TELEGRAM_BOT_TOKEN`
3. Get your Telegram user ID (send `/start` to the bot, it prints to console)
4. Put your ID in `.env` → `TELEGRAM_SUPER_ADMIN`

### 5. Camera (optional, Ezviz H1C or similar)

Find your RTSP URL (usually in camera settings):
```
rtsp://admin:password@192.168.x.x:554/h264_stream
```
Put it in `.env` → `CAMERA_RTSP_URL`. Camera buttons appear in the bot automatically.

## Running

**Web panel:**
```bash
python main.py
# or
uvicorn main:app --host 0.0.0.0 --port 80
```

**Telegram bot:**
```bash
python bot.py
```

Web panel: `http://localhost` → redirects to login
API docs: `http://localhost/docs`

## API Endpoints

All endpoints except `/login` require authentication (session cookie).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Redirect to login |
| GET/POST | `/login` | Auth |
| GET | `/logout` | Logout |
| GET | `/admin` | Admin panel |
| POST | `/control/{action}` | Web lamp control (`uv_on`, `uv_off`, `heat_on`, `heat_off`) |
| GET | `/temperature` | Current temperature |
| GET | `/humidity` | Current humidity |
| POST | `/lamp/{type}/on` | Turn lamp on |
| POST | `/lamp/{type}/off` | Turn lamp off |
| GET | `/device/{id}/status` | Device status |
| GET | `/device/{id}/functions` | Device functions |
| POST | `/api/schedules` | Create schedule |
| DELETE | `/api/schedules/{id}` | Delete schedule |
| POST | `/api/schedules/{id}/toggle` | Pause/resume schedule |

## Troubleshooting

**Token invalid:**
- Check that `TUYA_ENDPOINT` matches the region of your Tuya IoT project
- Ensure the SmartLife account is linked in the Tuya IoT Platform project
- Restart the server to force re-authentication

**Device offline:**
- Device is online in SmartLife but offline via API → region mismatch between IoT project and SmartLife account
- Check that the device appears under **Cloud → Devices** in your Tuya IoT project

**Bot timeout:**
- Telegram API is blocked by your ISP — use a VPN or proxy
