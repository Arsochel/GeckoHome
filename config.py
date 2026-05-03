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
TELEGRAM_SUPER_ADMINS: set[int] = {
    int(x) for x in os.getenv("TELEGRAM_SUPER_ADMIN", "0").replace(",", " ").split()
    if x.strip().isdigit() and int(x) != 0
}
TELEGRAM_ADMINS: set[int] = {
    int(x) for x in os.getenv("TELEGRAM_ADMIN", "").replace(",", " ").split()
    if x.strip().isdigit()
}
TIMELAPSE_OWNER_ID: int = int(next(iter(TELEGRAM_SUPER_ADMINS), 0))

CAMERA_RTSP_URL = os.getenv("CAMERA_RTSP_URL", "")
MEDIAMTX_BIN = os.getenv("MEDIAMTX_BIN", "mediamtx")
YOLO_MODEL_PATH = os.getenv("YOLO_MODEL_PATH", "")

STREAM_BASE_URL = os.getenv("STREAM_BASE_URL", "http://localhost:8080")
APP_INTERNAL_URL = os.getenv("APP_INTERNAL_URL", "")

# Motion detection
MOTION_THRESHOLD = int(os.getenv("MOTION_THRESHOLD", "25"))
MOTION_MIN_AREA  = int(os.getenv("MOTION_MIN_AREA", "1342"))
MOTION_TIMEOUT   = int(os.getenv("MOTION_TIMEOUT", "45"))
MOTION_DEBUG     = os.getenv("MOTION_DEBUG", "true").lower() in ("1", "true", "yes")

# Sensor alerts
TEMP_ALERT_MIN = float(os.getenv("TEMP_ALERT_MIN", "200"))   # ×10, т.е. 20.0°C
TEMP_ALERT_MAX = float(os.getenv("TEMP_ALERT_MAX", "350"))   # ×10, т.е. 35.0°C
HUM_ALERT_MIN  = float(os.getenv("HUM_ALERT_MIN", "30"))
HUM_ALERT_MAX  = float(os.getenv("HUM_ALERT_MAX", "60"))

# Feeding alert
FEEDING_ALERT_DAYS = int(os.getenv("FEEDING_ALERT_DAYS", "2"))

# Tuya Cloud API (для батарейных устройств без локального LAN)
TUYA_CLOUD_KEY    = os.getenv("TUYA_CLOUD_KEY", "")
TUYA_CLOUD_SECRET = os.getenv("TUYA_CLOUD_SECRET", "")
TUYA_CLOUD_REGION = os.getenv("TUYA_CLOUD_REGION", "eu")

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
