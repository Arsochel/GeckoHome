import asyncio
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from geckohome.services import tuya, camera
from geckohome.database import get_schedules, get_access_requests
from geckohome.bot.access import is_super_admin
from geckohome.bot.i18n import get_lang
from geckohome.config import STREAM_BASE_URL


_CAM_LABELS = {
    "en": {
        "snap": "📸 Snapshot",
        "clip": "🎬 Clip 30s",
        "clip3": "🎥 Clip 3 min",
        "stream": "📡 Stream",
        "detect": "🔍 Stream+detect",
    },
    "ru": {
        "snap": "📸 Снимок",
        "clip": "🎬 Клип 30с",
        "clip3": "🎥 Клип 3 мин",
        "stream": "📡 Стрим",
        "detect": "🔍 Стрим+детект",
    },
}

_MAIN_LABELS = {
    "en": {
        "uv_on": "🔦 UV: off ➜ on",
        "uv_off": "🔦 UV: on ➜ off",
        "heat_on": "🔥 Heat: off ➜ on",
        "heat_off": "🔥 Heat: on ➜ off",
        "feeding": "🍎 Feeding",
        "schedules": "📋 Schedules",
        "debug": "🛠 Debug",
        "settings": "⚙️ Settings",
        "settings_pending": "⚙️ Settings 🔴 {n}",
    },
    "ru": {
        "uv_on": "🔦 UV: выкл ➜ вкл",
        "uv_off": "🔦 UV: вкл ➜ выкл",
        "heat_on": "🔥 Тепловая: выкл ➜ вкл",
        "heat_off": "🔥 Тепловая: вкл ➜ выкл",
        "feeding": "🍎 Питание",
        "schedules": "📋 Расписания",
        "debug": "🛠 Дебаг",
        "settings": "⚙️ Управление",
        "settings_pending": "⚙️ Управление 🔴 {n}",
    },
}

_FEEDING_LABELS = {
    "en": {
        "fed": "🍎 Fed",
        "hornworm": "🐛 Hornworm",
        "vitamins": "💊 Vitamins",
        "cricket_bought": "🦗 Crickets bought",
        "cricket_out": "🦗 Ran out",
        "calendar": "📅 Calendar",
        "history": "📋 Feeding history",
        "back": "◀ Back",
    },
    "ru": {
        "fed": "🍎 Покормил",
        "hornworm": "🐛 Бражник",
        "vitamins": "💊 Витамины",
        "cricket_bought": "🦗 Купил сверчков",
        "cricket_out": "🦗 Закончились",
        "calendar": "📅 Календарь",
        "history": "📋 История кормления",
        "back": "◀ Назад",
    },
}


def detect_stream_url() -> str | None:
    url = stream_url()
    if url:
        return url.replace("/stream", "/stream/detect")
    return None


def stream_url() -> str | None:
    """None если URL локальный и не подходит для кнопки."""
    from geckohome.paths import TUNNEL_URL_FILE

    tunnel_file = TUNNEL_URL_FILE
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
    L = _CAM_LABELS.get(lang, _CAM_LABELS["ru"])
    rows = [
        [
            InlineKeyboardButton(L["snap"], callback_data="cam_snap"),
            InlineKeyboardButton(L["clip"], callback_data="cam_clip"),
        ],
        [
            InlineKeyboardButton(L["clip3"], callback_data="cam_clip3"),
        ],
    ]
    url = stream_url()
    if url:
        rows.append([InlineKeyboardButton(L["stream"], web_app=WebAppInfo(url=url))])
    if super_admin:
        det_url = detect_stream_url()
        if det_url:
            rows.append([InlineKeyboardButton(L["detect"], web_app=WebAppInfo(url=det_url))])
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
    uv, heat = await asyncio.gather(
        asyncio.to_thread(tuya.get_lamp_status, "uv"),
        asyncio.to_thread(tuya.get_lamp_status, "heat"),
    )

    uv_on   = uv.get("switch") is True
    heat_on = heat.get("switch") is True

    requests = await get_access_requests()
    L = _MAIN_LABELS.get(lang, _MAIN_LABELS["ru"])
    admin_label = L["settings_pending"].format(n=len(requests)) if requests else L["settings"]
    rows = [
        [InlineKeyboardButton(
            L["uv_off"] if uv_on else L["uv_on"],
            callback_data="uv_off" if uv_on else "uv_on",
        )],
        [InlineKeyboardButton(
            L["heat_off"] if heat_on else L["heat_on"],
            callback_data="heat_off" if heat_on else "heat_on",
        )],
        *_camera_rows(lang, super_admin=True),
        [InlineKeyboardButton(L["feeding"], callback_data="feeding_menu")],
        [InlineKeyboardButton(L["schedules"], callback_data="schedules")],
        [InlineKeyboardButton(L["debug"], callback_data="debug_link")],
        [InlineKeyboardButton(admin_label, callback_data="admin")],
        [await _lang_button(user_id)],
    ]
    return InlineKeyboardMarkup(rows)


def cricket_count_keyboard(lang: str = "ru", prefix: str = "fed_count_", back: str = "feeding_menu") -> InlineKeyboardMarkup:
    counts = [3, 4, 5, 6]
    buttons = [InlineKeyboardButton(f"{n}🦗", callback_data=f"{prefix}{n}") for n in counts]
    back_label = "◀ Back" if lang == "en" else "◀ Назад"
    return InlineKeyboardMarkup([buttons, [InlineKeyboardButton(back_label, callback_data=back)]])


async def feeding_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    L = _FEEDING_LABELS.get(lang, _FEEDING_LABELS["ru"])
    rows = [
        [InlineKeyboardButton(L["fed"], callback_data="fed")],
        [InlineKeyboardButton(L["hornworm"], callback_data="fed_hornworm"),
         InlineKeyboardButton(L["vitamins"], callback_data="fed_vitamins")],
        [InlineKeyboardButton(L["cricket_bought"], callback_data="cricket_bought"),
         InlineKeyboardButton(L["cricket_out"], callback_data="cricket_out")],
        [InlineKeyboardButton(L["calendar"], callback_data="calendar")],
        [InlineKeyboardButton(L["history"], callback_data="feeding_history")],
        [InlineKeyboardButton(L["back"], callback_data="back_main")],
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
