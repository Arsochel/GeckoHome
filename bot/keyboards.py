import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from services import tuya, camera
from database import get_schedules, get_access_requests
from bot.access import is_super_admin
from bot.i18n import get_lang
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




def _camera_rows(lang: str = "ru", super_admin: bool = False) -> list:
    if not camera.is_configured():
        return []
    if lang == "en":
        rows = [
            [
                InlineKeyboardButton("📸 Snapshot", callback_data="cam_snap"),
                InlineKeyboardButton("🎬 Clip 30s", callback_data="cam_clip"),
            ],
            [
                InlineKeyboardButton("🎥 Clip 3 min", callback_data="cam_clip3"),
            ],
        ]
        url = stream_url()
        if url:
            rows.append([InlineKeyboardButton("📡 Stream", web_app=WebAppInfo(url=url))])
        if super_admin:
            det_url = detect_stream_url()
            if det_url:
                rows.append([InlineKeyboardButton("🔍 Stream+detect", web_app=WebAppInfo(url=det_url))])
    else:
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
        if super_admin:
            det_url = detect_stream_url()
            if det_url:
                rows.append([InlineKeyboardButton("🔍 Стрим+детект", web_app=WebAppInfo(url=det_url))])
    return rows


async def _lang_button(user_id: int) -> InlineKeyboardButton:
    lang = await get_lang(user_id)
    label = "🌐 English" if lang == "ru" else "🌐 Русский"
    return InlineKeyboardButton(label, callback_data="lang_toggle")


async def user_keyboard(user_id: int) -> InlineKeyboardMarkup:
    lang = await get_lang(user_id)
    rows = _camera_rows(lang, super_admin=False)
    rows.append([await _lang_button(user_id)])
    return InlineKeyboardMarkup(rows)


async def main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    if not is_super_admin(user_id):
        return await user_keyboard(user_id)

    lang = await get_lang(user_id)
    uv   = tuya.get_lamp_status("uv")
    heat = tuya.get_lamp_status("heat")

    uv_on   = uv.get("switch") is True
    heat_on = heat.get("switch") is True

    requests = await get_access_requests()
    if lang == "en":
        admin_label = f"⚙️ Settings 🔴 {len(requests)}" if requests else "⚙️ Settings"
        rows = [
            [InlineKeyboardButton(
                f"🔦 UV: {'off ➜ on' if not uv_on else 'on ➜ off'}",
                callback_data="uv_on" if not uv_on else "uv_off",
            )],
            [InlineKeyboardButton(
                f"🔥 Heat: {'off ➜ on' if not heat_on else 'on ➜ off'}",
                callback_data="heat_on" if not heat_on else "heat_off",
            )],
            *_camera_rows(lang, super_admin=True),
            [InlineKeyboardButton("🍎 Fed", callback_data="fed")],
            [InlineKeyboardButton("📋 Schedules", callback_data="schedules")],
            [InlineKeyboardButton(admin_label, callback_data="admin")],
            [await _lang_button(user_id)],
        ]
    else:
        admin_label = f"⚙️ Управление 🔴 {len(requests)}" if requests else "⚙️ Управление"
        rows = [
            [InlineKeyboardButton(
                f"🔦 UV: {'выкл ➜ вкл' if not uv_on else 'вкл ➜ выкл'}",
                callback_data="uv_on" if not uv_on else "uv_off",
            )],
            [InlineKeyboardButton(
                f"🔥 Тепловая: {'выкл ➜ вкл' if not heat_on else 'вкл ➜ выкл'}",
                callback_data="heat_on" if not heat_on else "heat_off",
            )],
            *_camera_rows(lang, super_admin=True),
            [InlineKeyboardButton("🍎 Покормил", callback_data="fed")],
            [InlineKeyboardButton("📋 Расписания", callback_data="schedules")],
            [InlineKeyboardButton(admin_label, callback_data="admin")],
            [await _lang_button(user_id)],
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
    requests = await get_access_requests()
    rows = []

    if requests:
        rows.append([InlineKeyboardButton(f"⏳ Запросы доступа ({len(requests)})", callback_data="noop")])
        for r in requests:
            name = f"@{r['username']}" if r.get("username") else r.get("first_name") or str(r["user_id"])
            rows.append([
                InlineKeyboardButton(name, callback_data="noop"),
                InlineKeyboardButton("✅", callback_data=f"approve_{r['user_id']}"),
                InlineKeyboardButton("❌", callback_data=f"deny_{r['user_id']}"),
            ])

    rows.append([InlineKeyboardButton("➕ Добавить по ID", callback_data="add_user")])
    rows.append([InlineKeyboardButton("🔄 Перезапустить туннель", callback_data="tunnel_restart")])
    rows.append([InlineKeyboardButton("◀ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)
