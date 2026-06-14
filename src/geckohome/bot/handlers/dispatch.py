import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from geckohome.bot.access import check_access, is_super_admin
from geckohome.bot.formatters import status_text, user_status_text
from geckohome.bot.i18n import get_lang, set_lang, toggle_lang
from geckohome.bot.keyboards import (
    admin_keyboard,
    feeding_keyboard,
    main_keyboard,
    schedules_keyboard,
    stream_url,
)
from geckohome.config import STREAM_BASE_URL, TELEGRAM_SUPER_ADMINS
from geckohome.database import (
    add_allowed_user,
    get_user_lang,
    has_pending_request,
    save_schedule,
    set_user_blocked,
    update_user_info,
    was_user_revoked,
)

log = logging.getLogger(__name__)


from geckohome.bot.handlers._helpers import (
    _safe_edit,
)
from geckohome.bot.handlers.access import (
    _handle_add_user_prompt,
    _handle_admin,
    _handle_approve,
    _handle_deny,
    _handle_remove_user,
    _handle_request_access,
)
from geckohome.bot.handlers.feeding import (
    _handle_alert_cricket,
    _handle_alert_cricket_count,
    _handle_alert_fed,
    _handle_alert_fed_cancel,
    _handle_alert_fed_count,
    _handle_alert_hornworm,
    _handle_alert_vitamins,
    _handle_calendar,
    _handle_cricket_bought,
    _handle_cricket_out,
    _handle_cricket_stats,
    _handle_fed,
    _handle_fed_count,
    _handle_fed_note,
    _handle_feeding_history,
)
from geckohome.bot.handlers.lamps import (
    _handle_lamp,
    _handle_refresh,
    _handle_tunnel_restart,
)
from geckohome.bot.handlers.media import (
    _handle_clip,
    _handle_debug_link,
    _handle_snapshot,
)
from geckohome.bot.handlers.motion import (
    _handle_motion_pub,
    _handle_motion_skip,
)
from geckohome.bot.handlers.schedules import (
    _handle_sched_delete,
    _handle_sched_new,
    _handle_sched_select_lamp,
    _handle_sched_toggle,
    _handle_schedules,
)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    log.info("/start from @%s (%s)", user.username or user.first_name, user.id)
    if not await check_access(user.id):
        if await was_user_revoked(user.id):
            await set_user_blocked(user.id, False)  # сбрасываем blocked_bot, доступ всё ещё revoked
            await update.message.reply_text(
                "🦎 *Gecko Home*\n\nВернулся? Гекончик подумает над твоим поведением...",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📩 Запросить доступ", callback_data="request_access")]]
                ),
            )
        elif await has_pending_request(user.id):
            await update.message.reply_text("⏳ Ваш запрос ожидает подтверждения.")
        else:
            await update.message.reply_text(
                "🦎 *Gecko Home*\n\nУ вас нет доступа к системе.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📩 Запросить доступ", callback_data="request_access")]]
                ),
            )
        return
    await update_user_info(user.id, user.username, user.first_name)
    # показываем выбор языка если ещё не выбран
    existing_lang = await get_user_lang(user.id)
    if existing_lang is None:
        await update.message.reply_text(
            "🦎 *Gecko Home*\n\nChoose language / Выберите язык:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_set_ru"),
                        InlineKeyboardButton("🇬🇧 English", callback_data="lang_set_en"),
                    ]
                ]
            ),
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
    msg = await ctx.bot.send_message(
        update.effective_chat.id, text, parse_mode="Markdown", reply_markup=kb
    )
    ctx.user_data["status_msg_id"] = msg.message_id
    from geckohome.services.scheduler import check_cricket_alert, check_feeding_alert

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
    msg = await ctx.bot.send_message(
        update.effective_chat.id, text, parse_mode="Markdown", reply_markup=kb
    )
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
        "uv_on": ("uv", True),
        "uv_off": ("uv", False),
        "heat_on": ("heat", True),
        "heat_off": ("heat", False),
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
        await _safe_edit(
            query,
            "🍎 *Питание*" if lang == "ru" else "🍎 *Feeding*",
            parse_mode="Markdown",
            reply_markup=await feeding_keyboard(lang),
        )
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
        return await _handle_fed_note(
            query, user_id, "hornworm", "🐛 Бражник записан!", "🐛 Hornworm logged!"
        )
    if data == "fed_vitamins" and is_super_admin(user_id):
        return await _handle_fed_note(
            query, user_id, "vitamins", "💊 Витамины записаны!", "💊 Vitamins logged!"
        )
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
        await update.message.reply_text(
            f"✅ Пользователь `{new_id}` добавлен.", parse_mode="Markdown", reply_markup=kb
        )
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
            await update.message.reply_text(
                "❌ Неверный формат. Пример: `08:00 20:00`", parse_mode="Markdown"
            )
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
