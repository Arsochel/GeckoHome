import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from geckohome.database import (
    delete_alert_message,
    get_alert_message,
    get_next_feeding_supplements,
    save_alert_message,
    set_user_blocked,
)

log = logging.getLogger(__name__)


async def _bump_alerts(ctx, user_id: int):
    """Пересылает активные алерты вниз (после главного сообщения)."""
    from geckohome.database import get_cricket_remaining

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
        feeding_rows.append(
            [InlineKeyboardButton("🦗 Купил сверчков", callback_data="alert_cricket")]
        )

    alert_defs = {
        "feeding": ("🔴 *Пора кормить!*", feeding_rows),
        "cricket": (
            "🔴 *Сверчки закончились!*\nКупи новую партию.",
            [
                [InlineKeyboardButton("🦗 Купил сверчков", callback_data="alert_cricket")],
            ],
        ),
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
                chat_id=user_id,
                text=text,
                parse_mode="Markdown",
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
