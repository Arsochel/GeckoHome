import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from services import tuya, camera
from database import get_schedules, get_allowed_users, get_access_requests
from bot.access import is_super_admin
from config import STREAM_BASE_URL


def detect_stream_url() -> str | None:
    url = stream_url()
    if url:
        return url.replace("/stream", "/stream/detect")
    return None


def stream_url() -> str | None:
    """None если URL локальный и не подходит для кнопки."""
    tunnel_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tunnel_url.txt")
    try:
        with open(tunnel_file) as f:
            base = f.read().strip()
        if base:
            return f"{base}/stream"
    except FileNotFoundError:
        pass
    url = f"{STREAM_BASE_URL}/stream"
    if "localhost" in url or "127.0.0.1" in url:
        return None
    return url




def _camera_rows() -> list:
    if not camera.is_configured():
        return []
    rows = [
        [
            InlineKeyboardButton("📸 Снимок", callback_data="cam_snap"),
            InlineKeyboardButton("🎬 Клип 30с", callback_data="cam_clip"),
        ],
        [
            InlineKeyboardButton("🎥 Клип 3 мин", callback_data="cam_clip3"),
        ],
    ]
    url = stream_url()
    if url:
        rows.append([InlineKeyboardButton("📡 Стрим", web_app=WebAppInfo(url=url))])
    det_url = detect_stream_url()
    if det_url:
        rows.append([InlineKeyboardButton("🔍 Стрим+детект", web_app=WebAppInfo(url=det_url))])
    return rows


def user_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(_camera_rows())


def main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    if not is_super_admin(user_id):
        return user_keyboard()

    uv   = tuya.get_lamp_status("uv")
    heat = tuya.get_lamp_status("heat")

    uv_on   = uv.get("switch") is True
    heat_on = heat.get("switch") is True

    rows = [
        [InlineKeyboardButton(
            f"🔦 UV: {'выкл ➜ вкл' if not uv_on else 'вкл ➜ выкл'}",
            callback_data="uv_on" if not uv_on else "uv_off",
        )],
        [InlineKeyboardButton(
            f"🔥 Тепловая: {'выкл ➜ вкл' if not heat_on else 'вкл ➜ выкл'}",
            callback_data="heat_on" if not heat_on else "heat_off",
        )],
        *_camera_rows(),
        [InlineKeyboardButton("🍎 Покормил", callback_data="fed")],
        [InlineKeyboardButton("📋 Расписания", callback_data="schedules")],
        [InlineKeyboardButton("⚙️ Управление", callback_data="admin")],
    ]
    return InlineKeyboardMarkup(rows)


async def schedules_keyboard() -> InlineKeyboardMarkup:
    scheds = await get_schedules()
    rows = []
    for s in scheds:
        lamp  = "🔦 UV" if s["lamp_type"] == "uv" else "🔥 Тепл."
        start = f"{s['hour']:02d}:{s['minute']:02d}"
        end   = f"{s['end_hour']:02d}:{s['end_minute']:02d}"
        icon  = "⏸" if not s["paused"] else "▶️"
        rows.append([
            InlineKeyboardButton(f"{lamp}  {start} → {end}", callback_data="noop"),
            InlineKeyboardButton(icon, callback_data=f"sched_toggle_{s['id']}"),
            InlineKeyboardButton("✕",  callback_data=f"sched_del_{s['id']}"),
        ])
    rows.append([InlineKeyboardButton("➕ Новое расписание", callback_data="sched_new")])
    rows.append([InlineKeyboardButton("◀ Назад",             callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


async def admin_keyboard() -> InlineKeyboardMarkup:
    users    = await get_allowed_users()
    requests = await get_access_requests()
    rows = []

    if requests:
        rows.append([InlineKeyboardButton("⏳ Запросы доступа", callback_data="noop")])
        for r in requests:
            name = f"@{r['username']}" if r.get("username") else r.get("first_name") or str(r["user_id"])
            rows.append([
                InlineKeyboardButton(name, callback_data="noop"),
                InlineKeyboardButton("✅", callback_data=f"approve_{r['user_id']}"),
                InlineKeyboardButton("❌", callback_data=f"deny_{r['user_id']}"),
            ])

    rows.append([InlineKeyboardButton("👥 Пользователи", callback_data="noop")])
    if users:
        for u in users:
            label = f"@{u['username']}" if u.get("username") else (u.get("first_name") or str(u["user_id"]))
            rows.append([
                InlineKeyboardButton(label,  callback_data="noop"),
                InlineKeyboardButton("🗑",   callback_data=f"rm_user_{u['user_id']}"),
            ])
    else:
        rows.append([InlineKeyboardButton("Нет пользователей", callback_data="noop")])

    rows.append([InlineKeyboardButton("➕ Добавить по ID", callback_data="add_user")])
    rows.append([InlineKeyboardButton("◀ Назад",           callback_data="back_main")])
    return InlineKeyboardMarkup(rows)
