"""Application configuration.

Values are loaded from the environment (and a local ``.env`` for development) and
validated/coerced by pydantic-settings. The typed :class:`Settings` object is the
canonical source; the module-level constants below are kept as a backward-compatible
surface so existing ``from geckohome.config import SECRET_KEY`` style imports keep working.
"""

from __future__ import annotations

import secrets

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Populate os.environ from .env so modules that read os.getenv directly
# (e.g. SERVER_PORT in main.py, OpenCV/FFmpeg knobs in services) work in local dev.
load_dotenv()


class Settings(BaseSettings):
    """Typed application settings, read case-insensitively from env / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Web auth ──────────────────────────────────────────────────────────────
    admin_username: str = "admin"
    admin_password_hash: str = ""
    secret_key: str = ""

    # ── Telegram ─────────────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_super_admin: str = "0"  # comma/space separated user ids
    telegram_admin: str = ""

    # ── Tuya device ids ─────────────────────────────────────────────────────────
    device_uv_lamp: str = ""
    device_heat_lamp: str = ""
    device_thermometer: str = ""
    device_humidifier: str = ""

    # ── Tuya local LAN (tinytuya) ────────────────────────────────────────────────
    device_uv_lamp_ip: str = ""
    device_uv_lamp_local_key: str = ""
    device_uv_lamp_version: str = "3.4"
    device_heat_lamp_ip: str = ""
    device_heat_lamp_local_key: str = ""
    device_heat_lamp_version: str = "3.4"
    device_thermometer_ip: str = ""
    device_thermometer_local_key: str = ""
    device_thermometer_version: str = "3.3"
    device_humidifier_ip: str = ""
    device_humidifier_local_key: str = ""
    device_humidifier_version: str = "3.3"

    # ── Tuya Cloud (fallback for battery devices) ────────────────────────────────
    tuya_cloud_key: str = ""
    tuya_cloud_secret: str = ""
    tuya_cloud_region: str = "eu"

    # ── Camera / streaming ───────────────────────────────────────────────────────
    camera_rtsp_url: str = ""
    mediamtx_bin: str = "mediamtx"
    stream_base_url: str = "http://localhost:8080"
    app_internal_url: str = ""
    yolo_model_path: str = ""
    server_port: int = 8000

    # ── Motion detection ─────────────────────────────────────────────────────────
    motion_threshold: int = 25
    motion_min_area: int = 1342
    motion_timeout: int = 45
    motion_debug: bool = True

    # ── Sensor alert thresholds (temperature stored ×10) ─────────────────────────
    temp_alert_min: float = 200
    temp_alert_max: float = 350
    hum_alert_min: float = 30
    hum_alert_max: float = 60

    # ── Feeding ──────────────────────────────────────────────────────────────────
    feeding_alert_days: int = 2

    # ── Local sensor ingest (cloud-free readings pushed from any LAN source) ─────
    sensor_ingest_token: str = ""

    @field_validator("tuya_cloud_region")
    @classmethod
    def _normalize_region(cls, v: str) -> str:
        return v.strip().lower() or "eu"

    @staticmethod
    def _parse_ids(raw: str, *, drop_zero: bool) -> set[int]:
        ids = {int(x) for x in raw.replace(",", " ").split() if x.strip().isdigit()}
        return {i for i in ids if i != 0} if drop_zero else ids

    @property
    def super_admin_ids(self) -> set[int]:
        return self._parse_ids(self.telegram_super_admin, drop_zero=True)

    @property
    def admin_ids(self) -> set[int]:
        return self._parse_ids(self.telegram_admin, drop_zero=False)

    @property
    def device_ids(self) -> dict[str, str]:
        return {
            "uv_lamp": self.device_uv_lamp,
            "heat_lamp": self.device_heat_lamp,
            "thermometer": self.device_thermometer,
            "humidifier": self.device_humidifier,
        }

    @property
    def device_local(self) -> dict[str, dict[str, str]]:
        return {
            "uv_lamp": {
                "ip": self.device_uv_lamp_ip,
                "key": self.device_uv_lamp_local_key,
                "version": self.device_uv_lamp_version,
            },
            "heat_lamp": {
                "ip": self.device_heat_lamp_ip,
                "key": self.device_heat_lamp_local_key,
                "version": self.device_heat_lamp_version,
            },
            "thermometer": {
                "ip": self.device_thermometer_ip,
                "key": self.device_thermometer_local_key,
                "version": self.device_thermometer_version,
            },
            "humidifier": {
                "ip": self.device_humidifier_ip,
                "key": self.device_humidifier_local_key,
                "version": self.device_humidifier_version,
            },
        }


settings = Settings()

# ── Backward-compatible module-level constants ────────────────────────────────
DEVICE_IDS = settings.device_ids

ADMIN_USERNAME = settings.admin_username
ADMIN_PASSWORD_HASH = settings.admin_password_hash
# A fixed SECRET_KEY keeps sessions valid across restarts; a random fallback
# (logs everyone out on restart) is used only when none is configured.
SECRET_KEY = settings.secret_key or secrets.token_hex(32)

TELEGRAM_BOT_TOKEN = settings.telegram_bot_token
TELEGRAM_SUPER_ADMINS: set[int] = settings.super_admin_ids
TELEGRAM_ADMINS: set[int] = settings.admin_ids
TIMELAPSE_OWNER_ID: int = int(next(iter(TELEGRAM_SUPER_ADMINS), 0))

CAMERA_RTSP_URL = settings.camera_rtsp_url
MEDIAMTX_BIN = settings.mediamtx_bin
YOLO_MODEL_PATH = settings.yolo_model_path

STREAM_BASE_URL = settings.stream_base_url
APP_INTERNAL_URL = settings.app_internal_url

MOTION_THRESHOLD = settings.motion_threshold
MOTION_MIN_AREA = settings.motion_min_area
MOTION_TIMEOUT = settings.motion_timeout
MOTION_DEBUG = settings.motion_debug

TEMP_ALERT_MIN = settings.temp_alert_min
TEMP_ALERT_MAX = settings.temp_alert_max
HUM_ALERT_MIN = settings.hum_alert_min
HUM_ALERT_MAX = settings.hum_alert_max

FEEDING_ALERT_DAYS = settings.feeding_alert_days

SENSOR_INGEST_TOKEN = settings.sensor_ingest_token

TUYA_CLOUD_KEY = settings.tuya_cloud_key
TUYA_CLOUD_SECRET = settings.tuya_cloud_secret
TUYA_CLOUD_REGION = settings.tuya_cloud_region

DEVICE_LOCAL = settings.device_local
