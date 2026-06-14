import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from geckohome.bot.i18n import get_lang
from geckohome.bot.keyboards import cricket_count_keyboard, feeding_keyboard
from geckohome.config import TELEGRAM_SUPER_ADMINS
from geckohome.database import (
    append_feeding_note,
    delete_alert_message,
    get_alert_message,
    get_cricket_stats,
    get_feeding_count,
    get_feeding_history,
    get_last_cricket_purchase,
    get_last_feeding_cached,
    get_last_note_date,
    get_next_feeding_supplements,
    log_cricket_purchase,
    log_cricket_ran_out,
    log_feeding,
    save_alert_message,
)

log = logging.getLogger(__name__)


from geckohome.bot.handlers._helpers import (
    _bump_alerts,
    _dismiss_alert,
    _remove_alert_button,
    _safe_edit,
)


async def _handle_calendar(query):
    from datetime import timedelta

    from geckohome.database import (
        get_cricket_remaining,
        get_feedings_count_since,
        get_gecko_birthday,
    )
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
        feedings_since_vitamins = (
            await get_feedings_count_since(last_vitamins) if last_vitamins else 99
        )
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
    await _safe_edit(
        query,
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Назад", callback_data="feeding_menu")]]
        ),
    )


async def _handle_cricket_bought(query, user_id, ctx):
    await log_cricket_purchase()
    lang = await get_lang(user_id)
    if lang == "en":
        msg = "🦗 Cricket batch logged! Remember to feed them today."
    else:
        msg = "🦗 Партия сверчков записана! Покорми их сегодня."
    await query.answer(msg, show_alert=True)
    await _safe_edit(
        query,
        "🍎 *Питание*" if lang == "ru" else "🍎 *Feeding*",
        parse_mode="Markdown",
        reply_markup=await feeding_keyboard(lang),
    )
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
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🦗 Купил сверчков", callback_data="alert_cricket"),
            ]
        ]
    )
    for uid in TELEGRAM_SUPER_ADMINS:
        old_id = await get_alert_message(uid, "cricket")
        if old_id:
            try:
                await ctx.bot.delete_message(chat_id=uid, message_id=old_id)
            except Exception:
                pass
        try:
            sent = await ctx.bot.send_message(
                chat_id=uid, text=alert_text, parse_mode="Markdown", reply_markup=markup
            )
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
            parsed.append(
                (entry["fed_at"], entry["crickets"], entry["vitamins"], entry["hornworm"])
            )

        show_crickets = any(p[1] for p in parsed)
        show_vitamins = any(p[2] for p in parsed)
        show_hornworm = any(p[3] for p in parsed)

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
    await _safe_edit(
        query,
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📊 Статистика", callback_data="cricket_stats")],
                [InlineKeyboardButton("◀ Назад", callback_data="feeding_menu")],
            ]
        ),
    )


async def _handle_cricket_stats(query):
    stats = await get_cricket_stats()
    total_feedings = await get_feeding_count()
    if stats["count"] == 0:
        text = (
            "📊 *Статистика сверчков*\n━━━━━━━━━━━━━━━\n\n_Нет данных — количество не записывалось_"
        )
    else:
        text = (
            f"📊 *Статистика сверчков*\n━━━━━━━━━━━━━━━\n\n"
            f"Всего кормлений: *{total_feedings}*\n"
            f"С подсчётом: *{stats['count']}*\n"
            f"Всего сверчков: *{stats['total']}*\n"
            f"В среднем за кормление: *{stats['avg']}*"
        )
    await _safe_edit(
        query,
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Назад", callback_data="feeding_history")]]
        ),
    )


async def _handle_fed_note(query, user_id, note: str, msg_ru: str, msg_en: str):
    await append_feeding_note(note)
    lang = await get_lang(user_id)
    await query.answer(msg_ru if lang == "ru" else msg_en, show_alert=True)
    await _safe_edit(
        query,
        "🍎 *Питание*" if lang == "ru" else "🍎 *Feeding*",
        parse_mode="Markdown",
        reply_markup=await feeding_keyboard(lang),
    )


async def _handle_fed(query, user_id, ctx):
    lang = await get_lang(user_id)
    title = (
        "🍎 *Питание*\n\nСколько сверчков дал?"
        if lang == "ru"
        else "🍎 *Feeding*\n\nHow many crickets?"
    )
    await _safe_edit(
        query,
        title,
        parse_mode="Markdown",
        reply_markup=cricket_count_keyboard(lang, prefix="fed_count_", back="feeding_menu"),
    )


async def _handle_fed_count(query, user_id, ctx, count: int):
    supplements = await get_next_feeding_supplements()
    await log_feeding(
        crickets=count, vitamins="vitamins" in supplements, hornworm="hornworm" in supplements
    )
    lang = await get_lang(user_id)
    confirm = f"✅ Записано! Дал {count} сверчков." if lang == "ru" else f"✅ Fed {count} crickets!"
    await query.answer(confirm, show_alert=True)
    await _safe_edit(
        query,
        "🍎 *Питание*" if lang == "ru" else "🍎 *Feeding*",
        parse_mode="Markdown",
        reply_markup=await feeding_keyboard(lang),
    )
    await _dismiss_alert(ctx, user_id, "feeding")
    await _bump_alerts(ctx, user_id)


async def _handle_alert_fed(query, user_id):
    lang = await get_lang(user_id)
    title = (
        "🔴 *Пора кормить!*\n\nСколько сверчков?"
        if lang == "ru"
        else "🔴 *Time to feed!*\n\nHow many crickets?"
    )
    try:
        await query.edit_message_text(
            title,
            parse_mode="Markdown",
            reply_markup=cricket_count_keyboard(
                lang, prefix="alert_fed_count_", back="alert_fed_cancel"
            ),
        )
    except Exception:
        pass


async def _handle_alert_fed_count(query, user_id, count: int):
    supplements = await get_next_feeding_supplements()  # до логирования
    await log_feeding(crickets=count)
    confirm = (
        f"✅ Записано! Дал {count} сверчков."
        if await get_lang(user_id) == "ru"
        else f"✅ Fed {count} crickets!"
    )
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
                "🍎 *Покормил!*\n\nЕщё что отметить?",
                parse_mode="Markdown",
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
        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows)
        )
    except Exception:
        pass


async def _handle_alert_cricket(query, user_id):
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("20 шт.", callback_data="alert_cricket_count_20"),
                InlineKeyboardButton("30 шт.", callback_data="alert_cricket_count_30"),
            ]
        ]
    )
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
