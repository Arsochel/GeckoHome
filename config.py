import os
import secrets
from dotenv import load_dotenv

load_dotenv()

TUYA_ENDPOINT = os.getenv("TUYA_ENDPOINT", "https://openapi.tuyaeu.com")
TUYA_ACCESS_ID = os.getenv("TUYA_ACCESS_ID", "")
TUYA_ACCESS_KEY = os.getenv("TUYA_ACCESS_KEY", "")

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
