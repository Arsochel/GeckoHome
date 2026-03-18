import asyncio
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import TELEGRAM_SUPER_ADMIN
from services import tuya, camera
from database import (
    add_access_request, get_access_requests, remove_access_request, has_pending_request,
    add_allowed_user, remove_allowed_user,
    get_schedules, save_schedule, delete_schedule, set_schedule_paused, log_lamp_event,
)
from bot.access import check_access, is_super_admin
from bot.keyboards import main_keyboard, schedules_keyboard, admin_keyboard
from bot.formatters import status_text


# ─── Commands ───

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await check_access(user.id):
        if await has_pending_request(user.id):
            await update.message.reply_text("\u23f3 Your request is pending approval.")
        else:
            await update.message.reply_text(
                "\U0001f98e *Gecko Home*\n\nYou don't have access yet.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("\U0001f4e9 Request Access", callback_data="request_access")]
                ]),
            )
        return
    text = status_text()
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard(user.id))


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await check_access(user.id):
        return
    text = status_text()
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard(user.id))


# ─── Callbacks ───

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    user_id = user.id
    data = query.data

    # Public
    if data == "request_access":
        return await _handle_request_access(query, user)

    # Admin-only (before general access check)
    if data.startswith("approve_") and is_super_admin(user_id):
        return await _handle_approve(query, ctx, data)
    if data.startswith("deny_") and is_super_admin(user_id):
        return await _handle_deny(query, ctx, data)

    # Access gate
    if not await check_access(user_id):
        await query.answer("Access denied.", show_alert=True)
        return

    await query.answer()

    if data == "noop":
        return

    # Navigation
    if data in ("back_main", "refresh"):
        return await _handle_refresh(query, user_id)

    # Lamps
    action_map = {
        "uv_on": ("uv", True), "uv_off": ("uv", False),
        "heat_on": ("heat", True), "heat_off": ("heat", False),
    }
    if data in action_map:
        return await _handle_lamp(query, user_id, *action_map[data])

    # Camera
    if data == "cam_snap":
        return await _handle_snapshot(query, user_id)
    if data == "cam_clip":
        return await _handle_clip(query, user_id)

    # Schedules
    if data == "schedules":
        return await _handle_schedules(query)
    if data.startswith("sched_toggle_"):
        return await _handle_sched_toggle(query, data.replace("sched_toggle_", ""))
    if data.startswith("sched_del_"):
        return await _handle_sched_delete(query, data.replace("sched_del_", ""))
    if data == "sched_new":
        return await _handle_sched_new(query, ctx)
    if data.startswith("snew_"):
        return await _handle_sched_select_lamp(query, ctx, data.replace("snew_", ""))

    # Admin
    if data == "admin" and is_super_admin(user_id):
        return await _handle_admin(query)
    if data == "add_user" and is_super_admin(user_id):
        return await _handle_add_user_prompt(query, ctx)
    if data.startswith("rm_user_") and is_super_admin(user_id):
        return await _handle_remove_user(query, int(data.replace("rm_user_", "")))


async def message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await check_access(user.id):
        return

    text = update.message.text.strip()

    if is_super_admin(user.id) and ctx.user_data.get("waiting_user_id"):
        ctx.user_data["waiting_user_id"] = False
        try:
            new_id = int(text)
        except ValueError:
            await update.message.reply_text("\u274c Invalid ID.")
            return
        await add_allowed_user(new_id)
        kb = await admin_keyboard()
        await update.message.reply_text(f"\u2705 User `{new_id}` added.", parse_mode="Markdown", reply_markup=kb)
        return

    if ctx.user_data.get("sched_step") == "time":
        ctx.user_data["sched_step"] = None
        lamp = ctx.user_data.get("sched_lamp", "uv")
        try:
            parts = text.split()
            sh, sm = map(int, parts[0].split(":"))
            eh, em = map(int, parts[1].split(":"))
            assert 0 <= sh <= 23 and 0 <= sm <= 59
            assert 0 <= eh <= 23 and 0 <= em <= 59
            duration_h = ((eh * 60 + em) - (sh * 60 + sm)) / 60
            if duration_h <= 0:
                duration_h += 24  # crosses midnight
        except (ValueError, IndexError, AssertionError):
            await update.message.reply_text("\u274c Format: `HH:MM HH:MM`\nExample: `08:00 20:00`", parse_mode="Markdown")
            return
        sched_id = f"{lamp}_{sh:02d}{sm:02d}"
        await save_schedule(sched_id, lamp, sh, sm, duration_h, eh, em)
        kb = await schedules_keyboard()
        await update.message.reply_text(
            f"\u2705 {lamp.upper()} {sh:02d}:{sm:02d} \u2192 {eh:02d}:{em:02d}", reply_markup=kb
        )
        return


# ─── Private handlers ───

async def _handle_request_access(query, user):
    await query.answer()
    if await has_pending_request(user.id):
        await query.edit_message_text("\u23f3 Already pending.")
        return
    await add_access_request(user.id, user.username, user.first_name)
    await query.edit_message_text("\u2705 Request sent!")
    name = f"@{user.username}" if user.username else user.first_name or str(user.id)
    try:
        await query.get_bot().send_message(
            TELEGRAM_SUPER_ADMIN,
            f"\U0001f514 *Access request*\n{name} (`{user.id}`)",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("\u2705", callback_data=f"approve_{user.id}"),
                InlineKeyboardButton("\u274c", callback_data=f"deny_{user.id}"),
            ]]),
        )
    except Exception:
        pass


async def _handle_approve(query, ctx, data):
    await query.answer()
    req_id = int(data.replace("approve_", ""))
    reqs = await get_access_requests()
    req = next((r for r in reqs if r["user_id"] == req_id), None)
    await add_allowed_user(req_id, req["username"] if req else None)
    await remove_access_request(req_id)
    await query.edit_message_text(f"\u2705 User `{req_id}` approved.", parse_mode="Markdown")
    try:
        await ctx.bot.send_message(req_id, "\U0001f389 Access approved! Send /start")
    except Exception:
        pass


async def _handle_deny(query, ctx, data):
    await query.answer()
    req_id = int(data.replace("deny_", ""))
    await remove_access_request(req_id)
    await query.edit_message_text(f"\u274c User `{req_id}` denied.", parse_mode="Markdown")
    try:
        await ctx.bot.send_message(req_id, "\u274c Access denied.")
    except Exception:
        pass


async def _handle_refresh(query, user_id):
    try:
        await query.edit_message_text(status_text(), parse_mode="Markdown", reply_markup=main_keyboard(user_id))
    except Exception:
        pass


async def _handle_lamp(query, user_id, lamp, on):
    ok = tuya.switch_lamp(lamp, on)
    word = "ON" if on else "OFF"
    if ok:
        await log_lamp_event(lamp, word, f"tg:{user_id}")
        result = f"\u2705 {lamp.upper()} \u2192 {word}"
    else:
        result = f"\u274c Failed: {lamp.upper()} \u2192 {word}"
    await asyncio.sleep(1)
    try:
        await query.edit_message_text(
            status_text() + f"\n\n{result}",
            parse_mode="Markdown", reply_markup=main_keyboard(user_id),
        )
    except Exception:
        pass


async def _handle_snapshot(query, user_id):
    if not camera.is_configured():
        await query.edit_message_text("\u274c Camera not configured.")
        return
    await query.edit_message_text("\U0001f4f8 Capturing...")
    path = await camera.snapshot()
    if path:
        with open(path, "rb") as f:
            await query.message.reply_photo(
                f,
                caption=f"\U0001f98e Gecko Cam \u2022 {datetime.now().strftime('%H:%M:%S')}",
                reply_markup=main_keyboard(user_id),
            )
        try:
            await query.message.delete()
        except Exception:
            pass
    else:
        await query.edit_message_text(
            status_text() + "\n\n\u274c Snapshot failed",
            parse_mode="Markdown", reply_markup=main_keyboard(user_id),
        )


async def _handle_clip(query, user_id):
    if not camera.is_configured():
        await query.edit_message_text("\u274c Camera not configured.")
        return
    await query.edit_message_text("\U0001f3ac Recording 15s...")
    path = await camera.clip(15)
    if path:
        with open(path, "rb") as f:
            await query.message.reply_video(
                f,
                caption=f"\U0001f98e Gecko Cam \u2022 {datetime.now().strftime('%H:%M:%S')}",
                reply_markup=main_keyboard(user_id),
            )
        try:
            await query.message.delete()
        except Exception:
            pass
    else:
        await query.edit_message_text(
            status_text() + "\n\n\u274c Clip failed",
            parse_mode="Markdown", reply_markup=main_keyboard(user_id),
        )


async def _handle_schedules(query):
    kb = await schedules_keyboard()
    await query.edit_message_text(
        "\U0001f4cb *Schedules*\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
        parse_mode="Markdown", reply_markup=kb,
    )


async def _handle_sched_toggle(query, sched_id):
    scheds = await get_schedules()
    sched = next((s for s in scheds if s["id"] == sched_id), None)
    if sched:
        await set_schedule_paused(sched_id, not sched["paused"])
    kb = await schedules_keyboard()
    try:
        await query.edit_message_text(
            "\U0001f4cb *Schedules*\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
            parse_mode="Markdown", reply_markup=kb,
        )
    except Exception:
        pass


async def _handle_sched_delete(query, sched_id):
    await delete_schedule(sched_id)
    kb = await schedules_keyboard()
    try:
        await query.edit_message_text(
            "\U0001f4cb *Schedules*\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
            parse_mode="Markdown", reply_markup=kb,
        )
    except Exception:
        pass


async def _handle_sched_new(query, ctx):
    ctx.user_data["sched_step"] = "lamp"
    await query.edit_message_text(
        "\u2795 *New Schedule*\n\nChoose lamp:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("\U0001f526 UV", callback_data="snew_uv"),
                InlineKeyboardButton("\U0001f525 Heat", callback_data="snew_heat"),
            ],
            [InlineKeyboardButton("\u25c0 Cancel", callback_data="schedules")],
        ]),
    )


async def _handle_sched_select_lamp(query, ctx, lamp):
    ctx.user_data["sched_lamp"] = lamp
    ctx.user_data["sched_step"] = "time"
    await query.edit_message_text(
        f"\u2795 *New Schedule*\n\nLamp: {lamp.upper()}\n\n"
        f"Send start and end time:\n`HH:MM HH:MM`\n\nExample: `08:00 20:00`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("\u25c0 Cancel", callback_data="schedules")],
        ]),
    )


async def _handle_admin(query):
    kb = await admin_keyboard()
    await query.edit_message_text(
        "\u2699\ufe0f *Admin Panel*\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
        parse_mode="Markdown", reply_markup=kb,
    )


async def _handle_add_user_prompt(query, ctx):
    ctx.user_data["waiting_user_id"] = True
    await query.edit_message_text(
        "Send user ID:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u25c0 Cancel", callback_data="admin")]]),
    )


async def _handle_remove_user(query, rm_id):
    await remove_allowed_user(rm_id)
    kb = await admin_keyboard()
    try:
        await query.edit_message_text(
            "\u2699\ufe0f *Admin Panel*\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\nUser removed.",
            parse_mode="Markdown", reply_markup=kb,
        )
    except Exception:
        pass
