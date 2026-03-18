from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from services import tuya, camera
from database import get_schedules, get_allowed_users, get_access_requests
from bot.access import is_super_admin


def _lamp_status_icon(s: dict) -> str:
    if s.get("switch") is True:
        return "\U0001f7e2 ON"
    elif s.get("switch") is False:
        return "\U0001f534 OFF"
    return "\u26aa N/A"


def main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    uv = tuya.get_lamp_status("uv")
    heat = tuya.get_lamp_status("heat")

    uv_btn = "\U0001f526 UV: OFF" if uv.get("switch") else "\U0001f526 UV: ON"
    uv_data = "uv_off" if uv.get("switch") else "uv_on"
    heat_btn = "\U0001f525 Heat: OFF" if heat.get("switch") else "\U0001f525 Heat: ON"
    heat_data = "heat_off" if heat.get("switch") else "heat_on"

    rows = [
        [InlineKeyboardButton(uv_btn, callback_data=uv_data)],
        [InlineKeyboardButton(heat_btn, callback_data=heat_data)],
    ]
    if camera.is_configured():
        rows.append([
            InlineKeyboardButton("\U0001f4f8 Snapshot", callback_data="cam_snap"),
            InlineKeyboardButton("\U0001f3ac Clip 15s", callback_data="cam_clip"),
        ])
    rows.append([
        InlineKeyboardButton("\U0001f4cb Schedules", callback_data="schedules"),
        InlineKeyboardButton("\U0001f504 Refresh", callback_data="refresh"),
    ])
    if is_super_admin(user_id):
        rows.append([InlineKeyboardButton("\u2699\ufe0f Admin", callback_data="admin")])
    return InlineKeyboardMarkup(rows)


async def schedules_keyboard() -> InlineKeyboardMarkup:
    scheds = await get_schedules()
    rows = []
    for s in scheds:
        lamp = s["lamp_type"].upper()
        start = f"{s['hour']:02d}:{s['minute']:02d}"
        end = f"{s['end_hour']:02d}:{s['end_minute']:02d}"
        icon = "\u23f8" if s["paused"] else "\u25b6"
        rows.append([
            InlineKeyboardButton(f"{icon} {lamp} {start}\u2192{end}", callback_data="noop"),
            InlineKeyboardButton("\u23ef", callback_data=f"sched_toggle_{s['id']}"),
            InlineKeyboardButton("\u274c", callback_data=f"sched_del_{s['id']}"),
        ])
    rows.append([InlineKeyboardButton("\u2795 New Schedule", callback_data="sched_new")])
    rows.append([InlineKeyboardButton("\u25c0 Back", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


async def admin_keyboard() -> InlineKeyboardMarkup:
    users = await get_allowed_users()
    requests = await get_access_requests()
    rows = []

    if requests:
        rows.append([InlineKeyboardButton("\u23f3 Pending Requests", callback_data="noop")])
        for r in requests:
            name = f"@{r['username']}" if r.get("username") else r.get("first_name") or str(r["user_id"])
            rows.append([
                InlineKeyboardButton(name, callback_data="noop"),
                InlineKeyboardButton("\u2705", callback_data=f"approve_{r['user_id']}"),
                InlineKeyboardButton("\u274c", callback_data=f"deny_{r['user_id']}"),
            ])

    rows.append([InlineKeyboardButton("\U0001f465 Allowed Users", callback_data="noop")])
    if users:
        for u in users:
            label = f"@{u['username']}" if u.get("username") else str(u["user_id"])
            rows.append([
                InlineKeyboardButton(label, callback_data="noop"),
                InlineKeyboardButton("\U0001f5d1", callback_data=f"rm_user_{u['user_id']}"),
            ])
    else:
        rows.append([InlineKeyboardButton("No users yet", callback_data="noop")])

    rows.append([InlineKeyboardButton("\u2795 Add by ID", callback_data="add_user")])
    rows.append([InlineKeyboardButton("\u25c0 Back", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)
