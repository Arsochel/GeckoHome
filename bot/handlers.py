import asyncio
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes

from config import TELEGRAM_SUPER_ADMINS
from services import tuya, camera
from database import (
    add_access_request, get_access_requests, remove_access_request, has_pending_request,
    add_allowed_user, remove_allowed_user,
    get_schedules, save_schedule, delete_schedule, set_schedule_paused, log_lamp_event,
    log_feeding, get_feeding_history, get_motion_event, update_motion_status, get_allowed_users,
    log_user_action,
)
from bot.access import check_access, is_super_admin
from bot.keyboards import main_keyboard, schedules_keyboard, admin_keyboard, stream_url
from config import STREAM_BASE_URL
from bot.formatters import status_text, user_status_text


# ─── Commands ───

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await check_access(user.id):
        if await has_pending_request(user.id):
            await update.message.reply_text("⏳ Ваш запрос ожидает подтверждения.")
        else:
            await update.message.reply_text(
                "🦎 *Gecko Home*\n\nУ вас нет доступа к системе.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📩 Запросить доступ", callback_data="request_access")]
                ]),
            )
        return
    text = await status_text() if is_super_admin(user.id) else await user_status_text()
    msg = await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard(user.id))
    ctx.user_data["status_msg_id"] = msg.message_id
    # убираем reply keyboard если была
    try:
        tmp = await update.message.reply_text(".", reply_markup=ReplyKeyboardRemove())
        await tmp.delete()
    except Exception:
        pass


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await check_access(user.id):
        return
    text = await status_text() if is_super_admin(user.id) else await user_status_text()
    prev_id = ctx.user_data.get("status_msg_id")
    if prev_id:
        try:
            await ctx.bot.edit_message_text(
                text, chat_id=update.effective_chat.id, message_id=prev_id,
                parse_mode="Markdown", reply_markup=main_keyboard(user.id),
            )
            return
        except Exception:
            pass
    msg = await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard(user.id))
    ctx.user_data["status_msg_id"] = msg.message_id


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
        await query.answer("⛔ Нет доступа.", show_alert=True)
        return

    try:
        await query.answer()
    except Exception:
        pass

    if data == "noop":
        return

    # Navigation
    if data in ("back_main", "refresh"):
        return await _handle_refresh(query, user_id)

    # Lamps
    # Lamps — super admin only
    action_map = {
        "uv_on": ("uv", True), "uv_off": ("uv", False),
        "heat_on": ("heat", True), "heat_off": ("heat", False),
    }
    if data in action_map:
        if not is_super_admin(user_id):
            await query.answer("⛔ Only super admin can control lamps.", show_alert=True)
            return
        return await _handle_lamp(query, user_id, *action_map[data])

    # Camera — all allowed users
    if data == "cam_snap":
        return await _handle_snapshot(query, user_id)
    if data == "cam_clip":
        return await _handle_clip(query, user_id, 30)
    if data == "cam_clip3":
        return await _handle_clip(query, user_id, 180)

    # Schedules — super admin only
    if data == "schedules":
        if not is_super_admin(user_id):
            await query.answer("⛔ Only super admin can manage schedules.", show_alert=True)
            return
        return await _handle_schedules(query)
    if data.startswith("sched_toggle_"):
        if not is_super_admin(user_id):
            return
        return await _handle_sched_toggle(query, data.replace("sched_toggle_", ""))
    if data.startswith("sched_del_"):
        if not is_super_admin(user_id):
            return
        return await _handle_sched_delete(query, data.replace("sched_del_", ""))
    if data == "sched_new":
        if not is_super_admin(user_id):
            return
        return await _handle_sched_new(query, ctx)
    if data.startswith("snew_"):
        if not is_super_admin(user_id):
            return
        return await _handle_sched_select_lamp(query, ctx, data.replace("snew_", ""))

    # Stream
    if data == "stream_link":
        url = stream_url() or f"{STREAM_BASE_URL}/stream"
        await query.answer()
        await query.message.reply_text(f"📡 Стрим: {url}")
        return

    # Feeding
    if data == "fed" and is_super_admin(user_id):
        return await _handle_fed(query, user_id)
    if data == "feeding_history" and is_super_admin(user_id):
        return await _handle_feeding_history(query)

    # Motion approval
    if data.startswith("motion_pub_") and is_super_admin(user_id):
        return await _handle_motion_pub(query, ctx, int(data.replace("motion_pub_", "")))
    if data.startswith("motion_skip_") and is_super_admin(user_id):
        return await _handle_motion_skip(query, int(data.replace("motion_skip_", "")))

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
            await update.message.reply_text("❌ Неверный ID. Введите числовой Telegram ID.")
            return
        await add_allowed_user(new_id)
        kb = await admin_keyboard()
        await update.message.reply_text(f"✅ Пользователь `{new_id}` добавлен.", parse_mode="Markdown", reply_markup=kb)
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
            await update.message.reply_text("❌ Неверный формат. Пример: `08:00 20:00`", parse_mode="Markdown")
            return
        sched_id = f"{lamp}_{sh:02d}{sm:02d}"
        await save_schedule(sched_id, lamp, sh, sm, duration_h, eh, em)
        kb = await schedules_keyboard()
        lamp_name = "🔦 UV" if lamp == "uv" else "🔥 Тепловая"
        await update.message.reply_text(
            f"✅ {lamp_name}  {sh:02d}:{sm:02d} → {eh:02d}:{em:02d}", reply_markup=kb
        )
        return


# ─── Private handlers ───

async def _handle_request_access(query, user):
    await query.answer()
    if await has_pending_request(user.id):
        await _safe_edit(query, "⏳ Запрос уже отправлен, ожидайте.")
        return
    await add_access_request(user.id, user.username, user.first_name)
    await _safe_edit(query, "✅ Запрос отправлен!")
    name = f"@{user.username}" if user.username else user.first_name or str(user.id)
    for admin_id in TELEGRAM_SUPER_ADMINS:
        try:
            await query.get_bot().send_message(
                admin_id,
                f"🔔 *Запрос доступа*\n{name} (`{user.id}`)",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅", callback_data=f"approve_{user.id}"),
                    InlineKeyboardButton("❌", callback_data=f"deny_{user.id}"),
                ]]),
            )
        except Exception:
            pass


async def _handle_approve(query, ctx, data):
    await query.answer()
    req_id = int(data.replace("approve_", ""))
    reqs = await get_access_requests()
    req = next((r for r in reqs if r["user_id"] == req_id), None)
    await add_allowed_user(req_id, req["username"] if req else None, req["first_name"] if req else None)
    await remove_access_request(req_id)
    name = f"@{req['username']}" if req and req.get("username") else (req["first_name"] if req and req.get("first_name") else str(req_id))
    await _safe_edit(query, f"✅ {name} одобрен.")
    try:
        await ctx.bot.send_message(req_id, "🎉 Доступ открыт! Напишите /start")
    except Exception:
        pass


async def _handle_deny(query, ctx, data):
    await query.answer()
    req_id = int(data.replace("deny_", ""))
    await remove_access_request(req_id)
    await _safe_edit(query, f"❌ Пользователь `{req_id}` отклонён.", parse_mode="Markdown")
    try:
        await ctx.bot.send_message(req_id, "❌ В доступе отказано.")
    except Exception:
        pass


async def _handle_refresh(query, user_id):
    try:
        text = await status_text() if is_super_admin(user_id) else await user_status_text()
        await _safe_edit(query, text, parse_mode="Markdown", reply_markup=main_keyboard(user_id))
    except Exception:
        pass


async def _handle_lamp(query, user_id, lamp, on):
    ok = tuya.switch_lamp(lamp, on)
    word = "ON" if on else "OFF"
    lamp_name = "UV" if lamp == "uv" else "Тепловая"
    state = "включена" if on else "выключена"
    if ok:
        await log_lamp_event(lamp, word, f"tg:{user_id}")
        result = f"✅ {lamp_name} → {state}"
    else:
        result = f"❌ Ошибка: {lamp_name} не отвечает"
    await asyncio.sleep(1)
    try:
        await _safe_edit(query,
            await status_text() + f"\n\n{result}",
            parse_mode="Markdown", reply_markup=main_keyboard(user_id),
        )
    except Exception:
        pass


async def _safe_edit(query, text, **kwargs):
    try:
        await query.edit_message_text(text, **kwargs)
    except Exception:
        await query.message.reply_text(text, **kwargs)


async def _handle_snapshot(query, user_id):
    if not camera.is_configured():
        await _safe_edit(query, "❌ Камера не настроена.")
        return
    u = query.from_user
    print(f"[Bot] [{datetime.now().strftime('%H:%M:%S')}] Snapshot requested by @{u.username or u.first_name} ({u.id})")
    await log_user_action(u.id, u.username or u.first_name, "snapshot")
    await _safe_edit(query, "📸 Делаю снимок...")
    path = await camera.snapshot()
    if path:
        with open(path, "rb") as f:
            await query.message.reply_photo(
                f,
                caption=f"🦎 Gecko Cam • {datetime.now().strftime('%H:%M:%S')}",
                reply_markup=main_keyboard(user_id),
            )
    else:
        text = await status_text() if is_super_admin(user_id) else await user_status_text()
        await _safe_edit(query, text + "\n\n❌ Не удалось сделать снимок",
                         parse_mode="Markdown", reply_markup=main_keyboard(user_id))


async def _handle_clip(query, user_id, duration: int = 30):
    if not camera.is_configured():
        await _safe_edit(query, "❌ Камера не настроена.")
        return
    u = query.from_user
    label = "3 мин" if duration >= 60 else f"{duration}с"
    print(f"[Bot] [{datetime.now().strftime('%H:%M:%S')}] Clip {label} requested by @{u.username or u.first_name} ({u.id})")
    await log_user_action(u.id, u.username or u.first_name, f"clip_{duration}")
    await _safe_edit(query, f"🎬 Записываю {label}...")
    path = await camera.clip(duration)
    if path:
        with open(path, "rb") as f:
            await query.message.reply_video(
                f,
                caption=f"🦎 Gecko Cam • {datetime.now().strftime('%H:%M:%S')}",
                reply_markup=main_keyboard(user_id),
                width=720, height=1280,
                write_timeout=max(60, duration * 3),
                read_timeout=max(60, duration * 3),
            )
        try:
            await query.message.delete()
        except Exception:
            pass
    else:
        text = await status_text() if is_super_admin(user_id) else await user_status_text()
        await _safe_edit(query, text + "\n\n❌ Не удалось записать клип",
                         parse_mode="Markdown", reply_markup=main_keyboard(user_id))


async def _handle_schedules(query):
    kb = await schedules_keyboard()
    await _safe_edit(query, "📋 *Расписания*\n━━━━━━━━━━━━━━━",
                     parse_mode="Markdown", reply_markup=kb)


async def _handle_sched_toggle(query, sched_id):
    scheds = await get_schedules()
    sched = next((s for s in scheds if s["id"] == sched_id), None)
    if sched:
        await set_schedule_paused(sched_id, not sched["paused"])
    kb = await schedules_keyboard()
    try:
        await _safe_edit(query, "📋 *Расписания*\n━━━━━━━━━━━━━━━",
                         parse_mode="Markdown", reply_markup=kb)
    except Exception:
        pass


async def _handle_sched_delete(query, sched_id):
    await delete_schedule(sched_id)
    kb = await schedules_keyboard()
    try:
        await _safe_edit(query, "📋 *Расписания*\n━━━━━━━━━━━━━━━",
                         parse_mode="Markdown", reply_markup=kb)
    except Exception:
        pass


async def _handle_sched_new(query, ctx):
    ctx.user_data["sched_step"] = "lamp"
    await _safe_edit(query,
        "➕ *Новое расписание*\n\nВыберите лампу:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔦 UV",       callback_data="snew_uv"),
                InlineKeyboardButton("🔥 Тепловая", callback_data="snew_heat"),
            ],
            [InlineKeyboardButton("◀ Отмена", callback_data="schedules")],
        ]),
    )


async def _handle_sched_select_lamp(query, ctx, lamp):
    ctx.user_data["sched_lamp"] = lamp
    ctx.user_data["sched_step"] = "time"
    lamp_name = "🔦 UV" if lamp == "uv" else "🔥 Тепловая"
    await _safe_edit(query,
        f"➕ *Новое расписание*\n\nЛампа: {lamp_name}\n\n"
        f"Отправьте время включения и выключения:\n`ЧЧ:ММ ЧЧ:ММ`\n\nПример: `08:00 20:00`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀ Отмена", callback_data="schedules")],
        ]),
    )


async def _handle_admin(query):
    kb = await admin_keyboard()
    await _safe_edit(query, "⚙️ *Управление*\n━━━━━━━━━━━━━━━",
                     parse_mode="Markdown", reply_markup=kb)


async def _handle_add_user_prompt(query, ctx):
    ctx.user_data["waiting_user_id"] = True
    await _safe_edit(query,
        "Отправьте Telegram ID пользователя:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀ Отмена", callback_data="admin")]]),
    )


async def _handle_remove_user(query, rm_id):
    await remove_allowed_user(rm_id)
    kb = await admin_keyboard()
    try:
        await _safe_edit(query, "⚙️ *Управление*\n━━━━━━━━━━━━━━━\n\nПользователь удалён.",
                         parse_mode="Markdown", reply_markup=kb)
    except Exception:
        pass


async def _handle_feeding_history(query):
    history = await get_feeding_history(20)
    if not history:
        text = "🍎 *История кормления*\n━━━━━━━━━━━━━━━\n\n_Нет записей_"
    else:
        lines = "\n".join(f"• {dt.strftime('%d.%m.%Y  %H:%M')}" for dt in history)
        text = f"🍎 *История кормления*\n━━━━━━━━━━━━━━━\n\n{lines}"
    await _safe_edit(query, text, parse_mode="Markdown",
                     reply_markup=InlineKeyboardMarkup([[
                         InlineKeyboardButton("◀ Назад", callback_data="back_main")
                     ]]))


async def _handle_fed(query, user_id):
    await log_feeding()
    text = await status_text()
    await _safe_edit(query, text, parse_mode="Markdown", reply_markup=main_keyboard(user_id))


_PUBLISH_USERS = [8563910503]  # тестовый аккаунт; позже заменить на get_allowed_users()


async def _handle_motion_pub(query, ctx, event_id: int):
    event = await get_motion_event(event_id)
    if not event or event["status"] != "pending":
        await query.answer("Уже обработано.")
        return

    await update_motion_status(event_id, "published")
    await _safe_edit(query, f"✅ Опубликовано\n_{event['caption']}_", parse_mode="Markdown")

    for uid in _PUBLISH_USERS:
        try:
            await ctx.bot.send_photo(
                uid,
                photo=event["photo_file_id"],
                caption=f"🦎 {event['caption']}",
            )
        except Exception as e:
            print(f"[Motion] send to {uid} failed: {e}")


async def _handle_motion_skip(query, event_id: int):
    event = await get_motion_event(event_id)
    if not event or event["status"] != "pending":
        await query.answer("Уже обработано.")
        return
    await update_motion_status(event_id, "skipped")
    await _safe_edit(query, f"❌ Пропущено\n_{event['caption']}_", parse_mode="Markdown")
