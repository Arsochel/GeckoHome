import asyncio
import logging
import os
from datetime import datetime

log = logging.getLogger(__name__)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes

from geckohome.config import TELEGRAM_SUPER_ADMINS
from geckohome.services import tuya, camera
from geckohome.database import (
    add_access_request, get_access_requests, remove_access_request, has_pending_request,
    add_allowed_user, remove_allowed_user, update_user_info,
    get_schedules, save_schedule, delete_schedule, set_schedule_paused, log_lamp_event,
    log_feeding, get_feeding_history, get_motion_event, update_motion_status, get_allowed_users,
    log_user_action, get_user_lang, get_sensor_history,
    get_next_feeding_supplements, log_cricket_purchase,
    get_feeding_count, get_last_note_date, get_last_cricket_purchase, get_last_feeding_cached,
    delete_alert_message, get_alert_message, save_alert_message, log_cricket_ran_out,
    set_user_blocked, was_user_revoked, get_cricket_stats,
    append_feeding_note, create_debug_token,
)
from geckohome.bot.access import check_access, is_super_admin
from geckohome.bot.keyboards import main_keyboard, schedules_keyboard, admin_keyboard, feeding_keyboard, cricket_count_keyboard, stream_url
from geckohome.bot.i18n import get_lang, set_lang, toggle_lang
from geckohome.config import STREAM_BASE_URL
from geckohome.bot.formatters import status_text, user_status_text


# ─── Commands ───

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    log.info("/start from @%s (%s)", user.username or user.first_name, user.id)
    if not await check_access(user.id):
        if await was_user_revoked(user.id):
            await set_user_blocked(user.id, False)  # сбрасываем blocked_bot, доступ всё ещё revoked
            await update.message.reply_text(
                "🦎 *Gecko Home*\n\nВернулся? Гекончик подумает над твоим поведением...",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📩 Запросить доступ", callback_data="request_access")]
                ]),
            )
        elif await has_pending_request(user.id):
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
    await update_user_info(user.id, user.username, user.first_name)
    # показываем выбор языка если ещё не выбран
    existing_lang = await get_user_lang(user.id)
    if existing_lang is None:
        await update.message.reply_text(
            "🦎 *Gecko Home*\n\nChoose language / Выберите язык:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_set_ru"),
                InlineKeyboardButton("🇬🇧 English", callback_data="lang_set_en"),
            ]]),
        )
        return
    lang = existing_lang
    text_coro = status_text(lang) if is_super_admin(user.id) else user_status_text(lang)
    text, kb = await asyncio.gather(text_coro, main_keyboard(user.id))
    # удаляем команду /start из чата
    try:
        await update.message.delete()
    except Exception:
        pass
    # удаляем старое главное сообщение и шлём новое последним
    prev_id = ctx.user_data.get("status_msg_id")
    if prev_id:
        try:
            await ctx.bot.delete_message(update.effective_chat.id, prev_id)
        except Exception:
            pass
    msg = await ctx.bot.send_message(update.effective_chat.id, text, parse_mode="Markdown", reply_markup=kb)
    ctx.user_data["status_msg_id"] = msg.message_id
    from geckohome.services.scheduler import check_feeding_alert, check_cricket_alert
    asyncio.create_task(check_feeding_alert())
    asyncio.create_task(check_cricket_alert())


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await check_access(user.id):
        return
    # удаляем команду /status
    try:
        await update.message.delete()
    except Exception:
        pass
    lang = await get_lang(user.id)
    text_coro = status_text(lang) if is_super_admin(user.id) else user_status_text(lang)
    text, kb = await asyncio.gather(text_coro, main_keyboard(user.id))
    # удаляем старое главное сообщение и шлём новое последним
    prev_id = ctx.user_data.get("status_msg_id")
    if prev_id:
        try:
            await ctx.bot.delete_message(update.effective_chat.id, prev_id)
        except Exception:
            pass
    msg = await ctx.bot.send_message(update.effective_chat.id, text, parse_mode="Markdown", reply_markup=kb)
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

    await update_user_info(user_id, user.username, user.first_name)

    try:
        await query.answer()
    except Exception:
        pass

    if data == "noop":
        return

    if data.startswith("timelapse_publish_") and is_super_admin(user_id):
        day = data.removeprefix("timelapse_publish_")
        import os
        from geckohome.services.timelapse import TIMELAPSE_VIDEOS_DIR, _send_video
        path = os.path.join(TIMELAPSE_VIDEOS_DIR, f"timelapse_{day}_15fps.mp4")
        if not os.path.exists(path):
            await query.answer("Файл не найден", show_alert=True)
            return
        from geckohome.config import TELEGRAM_ADMINS
        from geckohome.database import get_allowed_users, get_blocked_user_ids
        allowed = {u["user_id"] for u in await get_allowed_users()}
        blocked = await get_blocked_user_ids()
        everyone = (TELEGRAM_SUPER_ADMINS | TELEGRAM_ADMINS | allowed) - {user_id} - blocked
        await _send_video(path, f"🎬 Таймлапс {day}", everyone)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.answer("Отправлено!")
        return

    if data == "lang_toggle":
        await toggle_lang(user_id)
        return await _handle_refresh(query, ctx, user_id)

    if data in ("lang_set_ru", "lang_set_en"):
        lang = data.replace("lang_set_", "")
        await set_lang(user_id, lang)
        text = await status_text(lang) if is_super_admin(user_id) else await user_status_text(lang)
        kb = await main_keyboard(user_id)
        try:
            await query.message.delete()
        except Exception:
            pass
        msg = await query.message.chat.send_message(text, parse_mode="Markdown", reply_markup=kb)
        ctx.user_data["status_msg_id"] = msg.message_id
        return

    # Navigation
    if data in ("back_main", "refresh"):
        return await _handle_refresh(query, ctx, user_id)

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
        return await _handle_snapshot(query, user_id, ctx)
    if data == "cam_clip":
        return await _handle_clip(query, user_id, 30, ctx)
    if data == "cam_clip3":
        return await _handle_clip(query, user_id, 180, ctx)

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

    # Feeding menu
    if data == "feeding_menu" and is_super_admin(user_id):
        lang = await get_lang(user_id)
        await _safe_edit(query, "🍎 *Питание*" if lang == "ru" else "🍎 *Feeding*",
                         parse_mode="Markdown", reply_markup=await feeding_keyboard(lang))
        return

    # Calendar
    if data == "calendar" and is_super_admin(user_id):
        return await _handle_calendar(query)

    # Feeding
    if data == "fed" and is_super_admin(user_id):
        return await _handle_fed(query, user_id, ctx)
    if data.startswith("fed_count_") and is_super_admin(user_id):
        try:
            count = int(data.split("_")[-1])
        except ValueError:
            return
        return await _handle_fed_count(query, user_id, ctx, count)
    if data == "fed_hornworm" and is_super_admin(user_id):
        return await _handle_fed_note(query, user_id, "hornworm", "🐛 Бражник записан!", "🐛 Hornworm logged!")
    if data == "fed_vitamins" and is_super_admin(user_id):
        return await _handle_fed_note(query, user_id, "vitamins", "💊 Витамины записаны!", "💊 Vitamins logged!")
    if data == "feeding_history" and is_super_admin(user_id):
        return await _handle_feeding_history(query)
    if data == "cricket_stats" and is_super_admin(user_id):
        return await _handle_cricket_stats(query)
    if data == "cricket_bought" and is_super_admin(user_id):
        return await _handle_cricket_bought(query, user_id, ctx)
    if data == "cricket_out" and is_super_admin(user_id):
        return await _handle_cricket_out(query, user_id, ctx)

    # Alert buttons (из отдельного алерт-сообщения)
    if data == "alert_fed" and is_super_admin(user_id):
        return await _handle_alert_fed(query, user_id)
    if data.startswith("alert_fed_count_") and is_super_admin(user_id):
        try:
            count = int(data.split("_")[-1])
        except ValueError:
            return
        return await _handle_alert_fed_count(query, user_id, count)
    if data == "alert_fed_cancel" and is_super_admin(user_id):
        return await _handle_alert_fed_cancel(query)
    if data == "alert_cricket" and is_super_admin(user_id):
        return await _handle_alert_cricket(query, user_id)
    if data.startswith("alert_cricket_count_") and is_super_admin(user_id):
        try:
            count = int(data.split("_")[-1])
        except ValueError:
            return
        return await _handle_alert_cricket_count(query, user_id, count)
    if data == "alert_hornworm" and is_super_admin(user_id):
        return await _handle_alert_hornworm(query, user_id)
    if data == "alert_vitamins" and is_super_admin(user_id):
        return await _handle_alert_vitamins(query, user_id)

    # Motion approval
    if data.startswith("motion_pub_") and is_super_admin(user_id):
        return await _handle_motion_pub(query, ctx, int(data.replace("motion_pub_", "")))
    if data.startswith("motion_skip_") and is_super_admin(user_id):
        return await _handle_motion_skip(query, int(data.replace("motion_skip_", "")))

    # Admin
    if data == "admin" and is_super_admin(user_id):
        return await _handle_admin(query)
    if data == "tunnel_restart" and is_super_admin(user_id):
        return await _handle_tunnel_restart(query)
    if data == "debug_link" and is_super_admin(user_id):
        return await _handle_debug_link(query, user_id)
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
            if duration_h > 16:
                await update.message.reply_text("❌ Максимум 16 часов.")
                return
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


async def _handle_refresh(query, ctx, user_id):
    try:
        lang = await get_lang(user_id)
        text_coro = status_text(lang) if is_super_admin(user_id) else user_status_text(lang)
        text, kb = await asyncio.gather(text_coro, main_keyboard(user_id))
        await _replace_main(query, ctx, user_id, text, kb)
    except Exception:
        pass


async def _handle_lamp(query, user_id, lamp, on):
    ok = await asyncio.to_thread(tuya.switch_lamp, lamp, on)
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
        lang = await get_lang(user_id)
        text, kb = await asyncio.gather(status_text(lang), main_keyboard(user_id))
        await _safe_edit(query,
            text + f"\n\n{result}",
            parse_mode="Markdown", reply_markup=kb,
        )
    except Exception:
        pass


async def _bump_alerts(ctx, user_id: int):
    """Пересылает активные алерты вниз (после главного сообщения)."""
    from geckohome.database import get_cricket_remaining, get_next_feeding_supplements
    crickets_remaining = await get_cricket_remaining()
    supplements = await get_next_feeding_supplements()

    feeding_rows = [[InlineKeyboardButton("🍎 Покормил", callback_data="alert_fed")]]
    event_row = []
    if "vitamins" in supplements:
        event_row.append(InlineKeyboardButton("💊 Дал витамины", callback_data="alert_vitamins"))
    if "hornworm" in supplements:
        event_row.append(InlineKeyboardButton("🐛 Дал бражника", callback_data="alert_hornworm"))
    if event_row:
        feeding_rows.append(event_row)
    if crickets_remaining == 0:
        feeding_rows.append([InlineKeyboardButton("🦗 Купил сверчков", callback_data="alert_cricket")])

    alert_defs = {
        "feeding": ("🔴 *Пора кормить!*", feeding_rows),
        "cricket": ("🔴 *Сверчки закончились!*\nКупи новую партию.", [
            [InlineKeyboardButton("🦗 Купил сверчков", callback_data="alert_cricket")],
        ]),
    }
    for alert_type, (text, rows) in alert_defs.items():
        msg_id = await get_alert_message(user_id, alert_type)
        if not msg_id:
            continue
        try:
            await ctx.bot.delete_message(chat_id=user_id, message_id=msg_id)
        except Exception:
            pass
        try:
            sent = await ctx.bot.send_message(
                chat_id=user_id, text=text, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(rows),
            )
            await save_alert_message(user_id, alert_type, sent.message_id)
            await set_user_blocked(user_id, False)
        except Exception as e:
            if "bot was blocked" in str(e) or "Forbidden" in str(e):
                await set_user_blocked(user_id, True)
                log.warning("user %s blocked the bot", user_id)


async def _dismiss_alert(ctx, user_id: int, alert_type: str):
    """Удаляет алерт из чата если он больше не актуален."""
    msg_id = await get_alert_message(user_id, alert_type)
    if not msg_id:
        return
    try:
        await ctx.bot.delete_message(chat_id=user_id, message_id=msg_id)
    except Exception:
        pass
    await delete_alert_message(user_id, alert_type)


async def _replace_main(query, ctx, user_id, text, kb):
    """Удаляет текущее главное сообщение и шлёт новое — чтобы оно было последним."""
    try:
        await query.message.delete()
    except Exception:
        pass
    msg = await query.message.chat.send_message(text, parse_mode="Markdown", reply_markup=kb)
    if ctx is not None:
        ctx.user_data["status_msg_id"] = msg.message_id
    await _bump_alerts(ctx, user_id)


async def _safe_edit(query, text, **kwargs):
    try:
        await query.edit_message_text(text, **kwargs)
    except Exception:
        await query.message.reply_text(text, **kwargs)


async def _handle_snapshot(query, user_id, ctx):
    if not camera.is_configured():
        await _safe_edit(query, "❌ Камера не настроена.")
        return
    u = query.from_user
    log.info("snapshot requested by @%s (%s)", u.username or u.first_name, u.id)
    await log_user_action(u.id, u.username or u.first_name, "snapshot")
    await _safe_edit(query, "📸 Делаю снимок...")
    lang, path = await asyncio.gather(get_lang(user_id), camera.snapshot())
    text_coro = status_text(lang) if is_super_admin(user_id) else user_status_text(lang)
    text, kb = await asyncio.gather(text_coro, main_keyboard(user_id))
    err_msg = "❌ Failed to take snapshot" if lang == "en" else "❌ Не удалось сделать снимок"
    if path:
        try:
            with open(path, "rb") as f:
                await query.message.reply_photo(
                    f,
                    caption=f"🦎 Gecko Cam • {datetime.now().strftime('%H:%M:%S')}",
                )
        finally:
            os.unlink(path)
        await _replace_main(query, ctx, user_id, text, kb)
    else:
        await _safe_edit(query, text + f"\n\n{err_msg}", parse_mode="Markdown", reply_markup=kb)


async def _handle_clip(query, user_id, duration: int = 30, ctx=None):
    if not camera.is_configured():
        await _safe_edit(query, "❌ Камера не настроена.")
        return
    u = query.from_user
    label = "3 мин" if duration >= 60 else f"{duration}с"
    log.info("clip %s requested by @%s (%s)", label, u.username or u.first_name, u.id)
    await log_user_action(u.id, u.username or u.first_name, f"clip_{duration}")
    await _safe_edit(query, f"🎬 Записываю {label}...")
    lang, path = await asyncio.gather(get_lang(user_id), camera.clip(duration))
    text_coro = status_text(lang) if is_super_admin(user_id) else user_status_text(lang)
    text, kb = await asyncio.gather(text_coro, main_keyboard(user_id))
    err_msg = "❌ Failed to record clip" if lang == "en" else "❌ Не удалось записать клип"
    if path:
        try:
            with open(path, "rb") as f:
                await query.message.reply_video(
                    f,
                    caption=f"🦎 Gecko Cam • {datetime.now().strftime('%H:%M:%S')}",
                    width=720, height=1280,
                    write_timeout=max(60, duration * 3),
                    read_timeout=max(60, duration * 3),
                )
        finally:
            os.unlink(path)
        await _replace_main(query, ctx, user_id, text, kb)
    else:
        await _safe_edit(query, text + f"\n\n{err_msg}", parse_mode="Markdown", reply_markup=kb)


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


async def _handle_tunnel_restart(query):
    await query.answer("🔄 Перезапуск туннеля...")
    from geckohome.services.tunnel import restart as restart_tunnel
    await asyncio.to_thread(restart_tunnel)
    kb = await admin_keyboard()
    await _safe_edit(query, "⚙️ *Управление*\n━━━━━━━━━━━━━━━\n\n🔄 Туннель перезапущен, URL обновится через ~30с",
                     parse_mode="Markdown", reply_markup=kb)


async def _handle_admin(query):
    kb = await admin_keyboard()
    await _safe_edit(query, "⚙️ *Управление*\n━━━━━━━━━━━━━━━",
                     parse_mode="Markdown", reply_markup=kb)


async def _handle_debug_link(query, user_id: int):
    from geckohome.paths import TUNNEL_URL_FILE

    tunnel_file = TUNNEL_URL_FILE
    lang = await get_lang(user_id)
    try:
        with open(tunnel_file) as f:
            tunnel = f.read().strip()
    except FileNotFoundError:
        tunnel = ""
    if not tunnel:
        msg = "🛠 Туннель ещё не готов, попробуйте через минуту" if lang == "ru" else "🛠 Tunnel not ready, try again in a minute"
        await query.answer(msg, show_alert=True)
        return
    token = await create_debug_token(user_id, ttl_hours=24)
    url = f"{tunnel}/debug?token={token}"
    if lang == "ru":
        text = f"🛠 *Дебаг (24ч)*\n{url}\n\nДействует 24 часа, ссылка одноразовая."
    else:
        text = f"🛠 *Debug access (24h)*\n{url}\n\nValid for 24 hours."
    await query.message.chat.send_message(text, parse_mode="Markdown", disable_web_page_preview=True)


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


async def _handle_calendar(query):
    from datetime import timedelta
    from geckohome.database import get_gecko_birthday, get_cricket_remaining, get_feedings_count_since
    from geckohome.services.scheduler import get_feeding_schedule
    now = datetime.now()

    last_feeding = get_last_feeding_cached()
    last_hornworm = await get_last_note_date("hornworm")
    last_vitamins = await get_last_note_date("vitamins")
    cricket_bought, cricket_total = await get_last_cricket_purchase()
    cricket_remaining = await get_cricket_remaining()

    lines = ["📅 *Календарь ухода*\n━━━━━━━━━━━━━━━\n"]

    # Определяем интервал кормления по возрасту
    birthday = await get_gecko_birthday()
    feed_interval, cmin, cmax = get_feeding_schedule(birthday) if birthday else (3, 0, 0)

    # Следующее кормление + добавки на ту же дату
    if last_feeding:
        next_feed = last_feeding + timedelta(days=feed_interval)
        delta = (next_feed.date() - now.date()).days
        if delta < 0:
            marker = "🔴"
            when = f"просрочено на {-delta} д."
        elif delta == 0:
            marker = "🟡"
            when = "сегодня"
        elif delta == 1:
            marker = "🟡"
            when = "завтра"
        else:
            marker = "🟢"
            when = f"через {delta} д."

        amount = f"{cmin}–{cmax} шт." if cmin else ""
        feed_line = f"{marker} 🍎 Кормление: *{next_feed.strftime('%d.%m')}* ({when})"
        if amount:
            feed_line += f"  _{amount}_"

        # Добавки: нужны ли к этому кормлению?
        extras = []
        feedings_since_vitamins = await get_feedings_count_since(last_vitamins) if last_vitamins else 99
        if last_vitamins is None or feedings_since_vitamins >= 1:
            extras.append("💊 витамины")
        if last_hornworm is None or (next_feed.date() - last_hornworm.date()).days >= 14:
            extras.append("🐛 бражник")
        if extras:
            feed_line += "\n   ┗ " + ", ".join(extras)

        lines.append(feed_line)
    else:
        lines.append("🔴 🍎 Кормление: *не записано*")

    # Сверчки
    if cricket_remaining is not None:
        if cricket_remaining == 0:
            marker = "🔴"
            when = "закончились"
        elif cricket_remaining <= 5:
            marker = "🟡"
            when = f"осталось {cricket_remaining} шт."
        else:
            marker = "🟢"
            when = f"осталось {cricket_remaining} шт."
        lines.append(f"{marker} 🦗 Сверчки: *{when}* (куплено {cricket_total})")
    elif cricket_bought:
        lines.append("⚪️ 🦗 Сверчки: *количество не записано*")
    else:
        lines.append("⚪️ 🦗 Сверчки: *не записаны*")

    text = "\n".join(lines)
    await _safe_edit(query, text, parse_mode="Markdown",
                     reply_markup=InlineKeyboardMarkup([[
                         InlineKeyboardButton("◀ Назад", callback_data="feeding_menu")
                     ]]))


async def _handle_cricket_bought(query, user_id, ctx):
    await log_cricket_purchase()
    lang = await get_lang(user_id)
    if lang == "en":
        msg = "🦗 Cricket batch logged! Remember to feed them today."
    else:
        msg = "🦗 Партия сверчков записана! Покорми их сегодня."
    await query.answer(msg, show_alert=True)
    await _safe_edit(query, "🍎 *Питание*" if lang == "ru" else "🍎 *Feeding*",
                     parse_mode="Markdown", reply_markup=await feeding_keyboard(lang))
    await _dismiss_alert(ctx, user_id, "cricket")
    await _bump_alerts(ctx, user_id)


async def _handle_cricket_out(query, user_id, ctx):
    lang = await get_lang(user_id)
    if lang == "en":
        msg = "🦗 Noted — crickets are out. Alert sent."
        alert_text = "🔴 *Crickets ran out!*\nTime to buy a new batch."
    else:
        msg = "🦗 Записали — сверчки закончились. Алерт отправлен."
        alert_text = "🔴 *Сверчки закончились!*\nКупи новую партию."
    await log_cricket_ran_out()
    await query.answer(msg, show_alert=True)
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("🦗 Купил сверчков", callback_data="alert_cricket"),
    ]])
    for uid in TELEGRAM_SUPER_ADMINS:
        old_id = await get_alert_message(uid, "cricket")
        if old_id:
            try:
                await ctx.bot.delete_message(chat_id=uid, message_id=old_id)
            except Exception:
                pass
        try:
            sent = await ctx.bot.send_message(chat_id=uid, text=alert_text, parse_mode="Markdown", reply_markup=markup)
            await save_alert_message(uid, "cricket", sent.message_id)
        except Exception:
            pass



async def _handle_feeding_history(query):
    history = await get_feeding_history(20)
    if not history:
        text = "🍎 *История кормления*\n━━━━━━━━━━━━━━━\n\n_Нет записей_"
    else:
        parsed = []
        for entry in history:
            parsed.append((entry["fed_at"], entry["crickets"], entry["vitamins"], entry["hornworm"]))

        show_crickets  = any(p[1] for p in parsed)
        show_vitamins  = any(p[2] for p in parsed)
        show_hornworm  = any(p[3] for p in parsed)

        lines = []
        for dt, crickets, has_vitamins, has_hornworm in parsed:
            line = f"• {dt.strftime('%d.%m.%Y  %H:%M')}"
            if show_crickets:
                line += f"  🦗×{crickets}" if crickets else "     —"
            if show_vitamins:
                line += "  💊" if has_vitamins else "   —"
            if show_hornworm:
                line += "  🐛" if has_hornworm else "   —"
            lines.append(line)

        text = "🍎 *История кормления*\n━━━━━━━━━━━━━━━\n\n" + "\n".join(lines)
    await _safe_edit(query, text, parse_mode="Markdown",
                     reply_markup=InlineKeyboardMarkup([
                         [InlineKeyboardButton("📊 Статистика", callback_data="cricket_stats")],
                         [InlineKeyboardButton("◀ Назад", callback_data="feeding_menu")],
                     ]))


async def _handle_cricket_stats(query):
    stats = await get_cricket_stats()
    total_feedings = await get_feeding_count()
    if stats["count"] == 0:
        text = "📊 *Статистика сверчков*\n━━━━━━━━━━━━━━━\n\n_Нет данных — количество не записывалось_"
    else:
        text = (
            f"📊 *Статистика сверчков*\n━━━━━━━━━━━━━━━\n\n"
            f"Всего кормлений: *{total_feedings}*\n"
            f"С подсчётом: *{stats['count']}*\n"
            f"Всего сверчков: *{stats['total']}*\n"
            f"В среднем за кормление: *{stats['avg']}*"
        )
    await _safe_edit(query, text, parse_mode="Markdown",
                     reply_markup=InlineKeyboardMarkup([[
                         InlineKeyboardButton("◀ Назад", callback_data="feeding_history")
                     ]]))


async def _handle_fed_note(query, user_id, note: str, msg_ru: str, msg_en: str):
    await append_feeding_note(note)
    lang = await get_lang(user_id)
    await query.answer(msg_ru if lang == "ru" else msg_en, show_alert=True)
    await _safe_edit(query, "🍎 *Питание*" if lang == "ru" else "🍎 *Feeding*",
                     parse_mode="Markdown", reply_markup=await feeding_keyboard(lang))


async def _handle_fed(query, user_id, ctx):
    lang = await get_lang(user_id)
    title = "🍎 *Питание*\n\nСколько сверчков дал?" if lang == "ru" else "🍎 *Feeding*\n\nHow many crickets?"
    await _safe_edit(query, title, parse_mode="Markdown",
                     reply_markup=cricket_count_keyboard(lang, prefix="fed_count_", back="feeding_menu"))


async def _handle_fed_count(query, user_id, ctx, count: int):
    supplements = await get_next_feeding_supplements()
    await log_feeding(crickets=count, vitamins="vitamins" in supplements, hornworm="hornworm" in supplements)
    lang = await get_lang(user_id)
    confirm = f"✅ Записано! Дал {count} сверчков." if lang == "ru" else f"✅ Fed {count} crickets!"
    await query.answer(confirm, show_alert=True)
    await _safe_edit(query, "🍎 *Питание*" if lang == "ru" else "🍎 *Feeding*",
                     parse_mode="Markdown", reply_markup=await feeding_keyboard(lang))
    await _dismiss_alert(ctx, user_id, "feeding")
    await _bump_alerts(ctx, user_id)


async def _remove_alert_button(query, user_id, remove_data: str, alert_type: str):
    """Убирает кнопку из алерт-сообщения. Если кнопок не осталось — удаляет сообщение."""
    markup = query.message.reply_markup
    remaining = []
    if markup:
        for row in markup.inline_keyboard:
            new_row = [btn for btn in row if btn.callback_data != remove_data]
            if new_row:
                remaining.append(new_row)

    await delete_alert_message(user_id, alert_type)

    if remaining:
        new_kb = InlineKeyboardMarkup(remaining)
        try:
            await query.edit_message_reply_markup(reply_markup=new_kb)
        except Exception:
            pass
    else:
        try:
            await query.message.delete()
        except Exception:
            pass


async def _handle_alert_fed(query, user_id):
    lang = await get_lang(user_id)
    title = "🔴 *Пора кормить!*\n\nСколько сверчков?" if lang == "ru" else "🔴 *Time to feed!*\n\nHow many crickets?"
    try:
        await query.edit_message_text(title, parse_mode="Markdown",
                                      reply_markup=cricket_count_keyboard(lang, prefix="alert_fed_count_", back="alert_fed_cancel"))
    except Exception:
        pass


async def _handle_alert_fed_count(query, user_id, count: int):
    supplements = await get_next_feeding_supplements()  # до логирования
    await log_feeding(crickets=count)
    confirm = f"✅ Записано! Дал {count} сверчков." if await get_lang(user_id) == "ru" else f"✅ Fed {count} crickets!"
    await query.answer(confirm, show_alert=True)

    from geckohome.database import get_cricket_remaining
    crickets_remaining = await get_cricket_remaining()

    event_row = []
    if "vitamins" in supplements:
        event_row.append(InlineKeyboardButton("💊 Дал витамины", callback_data="alert_vitamins"))
    if "hornworm" in supplements:
        event_row.append(InlineKeyboardButton("🐛 Дал бражника", callback_data="alert_hornworm"))

    rows = []
    if event_row:
        rows.append(event_row)
    if crickets_remaining == 0:
        rows.append([InlineKeyboardButton("🦗 Купил сверчков", callback_data="alert_cricket")])

    if rows:
        try:
            await query.edit_message_text(
                "🍎 *Покормил!*\n\nЕщё что отметить?", parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(rows),
            )
            await save_alert_message(user_id, "feeding", query.message.message_id)
        except Exception:
            await delete_alert_message(user_id, "feeding")
            try:
                await query.message.delete()
            except Exception:
                pass
    else:
        await delete_alert_message(user_id, "feeding")
        try:
            await query.message.delete()
        except Exception:
            pass


async def _handle_alert_fed_cancel(query):
    """Восстанавливаем оригинальные кнопки алерта (пользователь передумал)."""
    from geckohome.database import get_cricket_remaining, get_last_feeding_db
    supplements = await get_next_feeding_supplements()
    crickets_remaining = await get_cricket_remaining()
    last = await get_last_feeding_db()
    days = (datetime.now().date() - last.date()).days if last else 0

    text = f"🍎 *Пора кормить геккона!* (не ел *{days} д.*)"
    if "vitamins" in supplements:
        text += "\n💊 Это кормление *с витаминами*"
    if "hornworm" in supplements:
        text += "\n🐛 Дать *табачного бражника*"
    text += "\n🦗 Покорми сверчков сегодня — через 2 дня готовы"

    rows = [[InlineKeyboardButton("🍎 Покормил", callback_data="alert_fed")]]
    event_row = []
    if "vitamins" in supplements:
        event_row.append(InlineKeyboardButton("💊 Дал витамины", callback_data="alert_vitamins"))
    if "hornworm" in supplements:
        event_row.append(InlineKeyboardButton("🐛 Дал бражника", callback_data="alert_hornworm"))
    if event_row:
        rows.append(event_row)
    if crickets_remaining == 0:
        rows.append([InlineKeyboardButton("🦗 Купил сверчков", callback_data="alert_cricket")])

    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
    except Exception:
        pass


async def _handle_alert_cricket(query, user_id):
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("20 шт.", callback_data="alert_cricket_count_20"),
        InlineKeyboardButton("30 шт.", callback_data="alert_cricket_count_30"),
    ]])
    try:
        await query.edit_message_reply_markup(reply_markup=markup)
    except Exception:
        pass


async def _handle_alert_cricket_count(query, user_id, count: int):
    await log_cricket_purchase(count)
    await query.answer(f"🦗 Куплено {count} сверчков!")
    msg_id = query.message.message_id
    for alert_type in ("cricket", "feeding"):
        stored = await get_alert_message(user_id, alert_type)
        if stored == msg_id:
            await delete_alert_message(user_id, alert_type)
            break
    try:
        await query.message.delete()
    except Exception:
        pass


async def _handle_alert_hornworm(query, user_id):
    await append_feeding_note("hornworm")
    await query.answer("🐛 Бражник записан!")
    await _remove_alert_button(query, user_id, "alert_hornworm", "feeding")


async def _handle_alert_vitamins(query, user_id):
    await append_feeding_note("vitamins")
    await query.answer("💊 Витамины записаны!")
    await _remove_alert_button(query, user_id, "alert_vitamins", "feeding")


async def _handle_motion_pub(query, ctx, event_id: int):
    event = await get_motion_event(event_id)
    if not event or event["status"] != "pending":
        await query.answer("Уже обработано.")
        return

    await update_motion_status(event_id, "published")
    await _safe_edit(query, f"✅ Опубликовано\n_{event['caption']}_", parse_mode="Markdown")

    users = await get_allowed_users()
    for u in users:
        uid = u["user_id"]
        if is_super_admin(uid):
            continue  # super admins already saw it via approval
        try:
            await ctx.bot.send_photo(
                uid,
                photo=event["photo_file_id"],
                caption=f"🦎 {event['caption']}",
            )
        except Exception as e:
            log.error("motion send to %s failed: %s", uid, e)


async def _handle_motion_skip(query, event_id: int):
    event = await get_motion_event(event_id)
    if not event or event["status"] != "pending":
        await query.answer("Уже обработано.")
        return
    await update_motion_status(event_id, "skipped")
    await _safe_edit(query, f"❌ Пропущено\n_{event['caption']}_", parse_mode="Markdown")
