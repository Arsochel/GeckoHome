import os
import secrets
from dotenv import load_dotenv

load_dotenv()

DEVICE_IDS = {
    "uv_lamp": os.getenv("DEVICE_UV_LAMP", ""),
    "heat_lamp": os.getenv("DEVICE_HEAT_LAMP", ""),
    "thermometer": os.getenv("DEVICE_THERMOMETER", ""),
    "humidifier": os.getenv("DEVICE_HUMIDIFIER", ""),
}

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")
SECRET_KEY = os.getenv("SECRET_KEY", "") or secrets.token_hex(32)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_SUPER_ADMIN = int(os.getenv("TELEGRAM_SUPER_ADMIN", "0"))

CAMERA_RTSP_URL = os.getenv("CAMERA_RTSP_URL", "")
MEDIAMTX_BIN = os.getenv("MEDIAMTX_BIN", "mediamtx")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llava:7b")
HIGHLIGHTS_INTERVAL_MIN = int(os.getenv("HIGHLIGHTS_INTERVAL_MIN", "15"))

STREAM_BASE_URL = os.getenv("STREAM_BASE_URL", "http://localhost:8080")

# tinytuya local keys (optional, enables local LAN control without cloud)
DEVICE_LOCAL = {
    "uv_lamp": {
        "ip":      os.getenv("DEVICE_UV_LAMP_IP", ""),
        "key":     os.getenv("DEVICE_UV_LAMP_LOCAL_KEY", ""),
        "version": os.getenv("DEVICE_UV_LAMP_VERSION", "3.4"),
    },
    "heat_lamp": {
        "ip":      os.getenv("DEVICE_HEAT_LAMP_IP", ""),
        "key":     os.getenv("DEVICE_HEAT_LAMP_LOCAL_KEY", ""),
        "version": os.getenv("DEVICE_HEAT_LAMP_VERSION", "3.4"),
    },
    "thermometer": {
        "ip":      os.getenv("DEVICE_THERMOMETER_IP", ""),
        "key":     os.getenv("DEVICE_THERMOMETER_LOCAL_KEY", ""),
        "version": os.getenv("DEVICE_THERMOMETER_VERSION", "3.3"),
    },
    "humidifier": {
        "ip":      os.getenv("DEVICE_HUMIDIFIER_IP", ""),
        "key":     os.getenv("DEVICE_HUMIDIFIER_LOCAL_KEY", ""),
        "version": os.getenv("DEVICE_HUMIDIFIER_VERSION", "3.3"),
    },
}
