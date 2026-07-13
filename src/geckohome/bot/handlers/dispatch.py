import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import NamedTuple

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


# ─── Callbacks: диспатч-таблица ───
#
# Каждый маршрут — Route с единой сигнатурой адаптера (query, ctx, user_id, arg):
# для exact-ключей arg = "", для префиксных arg = суффикс callback_data.
# super_admin=True без deny_msg — молчаливый отказ (как в исходной простыне).


class Route(NamedTuple):
    handler: Callable[..., Awaitable]
    super_admin: bool = False
    deny_msg: str | None = None


# — многошаговые адаптеры (лямбдой не выразить) —


async def _route_lang_toggle(query, ctx, user_id, arg):
    await toggle_lang(user_id)
    await _handle_refresh(query, ctx, user_id)


async def _route_lang_set(query, ctx, user_id, lang):
    if lang not in ("ru", "en"):
        return
    await set_lang(user_id, lang)
    text = await status_text(lang) if is_super_admin(user_id) else await user_status_text(lang)
    kb = await main_keyboard(user_id)
    try:
        await query.message.delete()
    except Exception:
        pass
    msg = await query.message.chat.send_message(text, parse_mode="Markdown", reply_markup=kb)
    ctx.user_data["status_msg_id"] = msg.message_id


async def _route_timelapse_publish(query, ctx, user_id, day):
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


async def _route_stream_link(query, ctx, user_id, arg):
    url = stream_url() or f"{STREAM_BASE_URL}/stream"
    await query.answer()
    await query.message.reply_text(f"📡 Стрим: {url}")


async def _route_feeding_menu(query, ctx, user_id, arg):
    lang = await get_lang(user_id)
    await _safe_edit(
        query,
        "🍎 *Питание*" if lang == "ru" else "🍎 *Feeding*",
        parse_mode="Markdown",
        reply_markup=await feeding_keyboard(lang),
    )


def _count_route(handler):
    """Адаптер для кнопок с числом в суффиксе; мусор в суффиксе игнорируется."""

    async def _route(query, ctx, user_id, arg):
        try:
            count = int(arg)
        except ValueError:
            return
        await handler(query, ctx, user_id, count)

    return _route


_DENY_LAMPS = "⛔ Only super admin can control lamps."
_DENY_SCHEDULES = "⛔ Only super admin can manage schedules."

_EXACT_ROUTES: dict[str, Route] = {
    # навигация и язык — все допущенные
    "back_main": Route(lambda q, c, uid, a: _handle_refresh(q, c, uid)),
    "refresh": Route(lambda q, c, uid, a: _handle_refresh(q, c, uid)),
    "lang_toggle": Route(_route_lang_toggle),
    # лампы — только супер-админ, с алертом
    "uv_on": Route(lambda q, c, uid, a: _handle_lamp(q, uid, "uv", True), True, _DENY_LAMPS),
    "uv_off": Route(lambda q, c, uid, a: _handle_lamp(q, uid, "uv", False), True, _DENY_LAMPS),
    "heat_on": Route(lambda q, c, uid, a: _handle_lamp(q, uid, "heat", True), True, _DENY_LAMPS),
    "heat_off": Route(lambda q, c, uid, a: _handle_lamp(q, uid, "heat", False), True, _DENY_LAMPS),
    # камера — все допущенные
    "cam_snap": Route(lambda q, c, uid, a: _handle_snapshot(q, uid, c)),
    "cam_clip": Route(lambda q, c, uid, a: _handle_clip(q, uid, 30, c)),
    "cam_clip3": Route(lambda q, c, uid, a: _handle_clip(q, uid, 180, c)),
    "stream_link": Route(_route_stream_link),
    # расписания
    "schedules": Route(lambda q, c, uid, a: _handle_schedules(q), True, _DENY_SCHEDULES),
    "sched_new": Route(lambda q, c, uid, a: _handle_sched_new(q, c), True),
    # питание
    "feeding_menu": Route(_route_feeding_menu, True),
    "calendar": Route(lambda q, c, uid, a: _handle_calendar(q), True),
    "fed": Route(lambda q, c, uid, a: _handle_fed(q, uid, c), True),
    "fed_hornworm": Route(
        lambda q, c, uid, a: _handle_fed_note(
            q, uid, "hornworm", "🐛 Бражник записан!", "🐛 Hornworm logged!"
        ),
        True,
    ),
    "fed_vitamins": Route(
        lambda q, c, uid, a: _handle_fed_note(
            q, uid, "vitamins", "💊 Витамины записаны!", "💊 Vitamins logged!"
        ),
        True,
    ),
    "feeding_history": Route(lambda q, c, uid, a: _handle_feeding_history(q), True),
    "cricket_stats": Route(lambda q, c, uid, a: _handle_cricket_stats(q), True),
    "cricket_bought": Route(lambda q, c, uid, a: _handle_cricket_bought(q, uid, c), True),
    "cricket_out": Route(lambda q, c, uid, a: _handle_cricket_out(q, uid, c), True),
    # алерты (кнопки в отдельном алерт-сообщении)
    "alert_fed": Route(lambda q, c, uid, a: _handle_alert_fed(q, uid), True),
    "alert_fed_cancel": Route(lambda q, c, uid, a: _handle_alert_fed_cancel(q), True),
    "alert_cricket": Route(lambda q, c, uid, a: _handle_alert_cricket(q, uid), True),
    "alert_hornworm": Route(lambda q, c, uid, a: _handle_alert_hornworm(q, uid), True),
    "alert_vitamins": Route(lambda q, c, uid, a: _handle_alert_vitamins(q, uid), True),
    # админка
    "admin": Route(lambda q, c, uid, a: _handle_admin(q), True),
    "tunnel_restart": Route(lambda q, c, uid, a: _handle_tunnel_restart(q), True),
    "debug_link": Route(lambda q, c, uid, a: _handle_debug_link(q, uid), True),
    "add_user": Route(lambda q, c, uid, a: _handle_add_user_prompt(q, c), True),
}

# Порядок важен: более специфичные префиксы раньше (alert_fed_count_ vs alert_fed
# конфликта нет — exact проверяется первым, но fed_count_ и т.п. живут только тут).
_PREFIX_ROUTES: list[tuple[str, Route]] = [
    ("lang_set_", Route(_route_lang_set)),
    ("timelapse_publish_", Route(_route_timelapse_publish, True)),
    ("sched_toggle_", Route(lambda q, c, uid, a: _handle_sched_toggle(q, a), True)),
    ("sched_del_", Route(lambda q, c, uid, a: _handle_sched_delete(q, a), True)),
    ("snew_", Route(lambda q, c, uid, a: _handle_sched_select_lamp(q, c, a), True)),
    ("fed_count_", Route(_count_route(lambda q, c, uid, n: _handle_fed_count(q, uid, c, n)), True)),
    (
        "alert_fed_count_",
        Route(_count_route(lambda q, c, uid, n: _handle_alert_fed_count(q, uid, n)), True),
    ),
    (
        "alert_cricket_count_",
        Route(_count_route(lambda q, c, uid, n: _handle_alert_cricket_count(q, uid, n)), True),
    ),
    (
        "motion_pub_",
        Route(_count_route(lambda q, c, uid, n: _handle_motion_pub(q, c, n)), True),
    ),
    (
        "motion_skip_",
        Route(_count_route(lambda q, c, uid, n: _handle_motion_skip(q, n)), True),
    ),
    (
        "rm_user_",
        Route(_count_route(lambda q, c, uid, n: _handle_remove_user(q, n)), True),
    ),
]


def _resolve_route(data: str) -> tuple[Route, str] | None:
    """(маршрут, аргумент) для callback_data или None."""
    route = _EXACT_ROUTES.get(data)
    if route:
        return route, ""
    # у fed_count_5 exact-ключа нет, но prefix fed_count_ есть; alert_fed_count_
    # длиннее alert_fed — конфликт исключён порядком списка
    for prefix, route in _PREFIX_ROUTES:
        if data.startswith(prefix):
            return route, data.removeprefix(prefix)
    return None


async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    user_id = user.id
    data = query.data
    if not data:  # у inline-кнопок data есть всегда, но PTB типизирует как Optional
        return

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

    resolved = _resolve_route(data)
    if resolved is None:
        log.debug("unknown callback: %s", data)
        return
    route, arg = resolved
    if route.super_admin and not is_super_admin(user_id):
        if route.deny_msg:
            await query.answer(route.deny_msg, show_alert=True)
        return
    await route.handler(query, ctx, user_id, arg)


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
