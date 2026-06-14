import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from geckohome.bot.keyboards import admin_keyboard
from geckohome.config import TELEGRAM_SUPER_ADMINS
from geckohome.database import (
    add_access_request,
    add_allowed_user,
    get_access_requests,
    has_pending_request,
    remove_access_request,
    remove_allowed_user,
)

log = logging.getLogger(__name__)


from geckohome.bot.handlers._helpers import _safe_edit


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
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("✅", callback_data=f"approve_{user.id}"),
                            InlineKeyboardButton("❌", callback_data=f"deny_{user.id}"),
                        ]
                    ]
                ),
            )
        except Exception:
            pass


async def _handle_approve(query, ctx, data):
    await query.answer()
    req_id = int(data.replace("approve_", ""))
    reqs = await get_access_requests()
    req = next((r for r in reqs if r["user_id"] == req_id), None)
    await add_allowed_user(
        req_id, req["username"] if req else None, req["first_name"] if req else None
    )
    await remove_access_request(req_id)
    name = (
        f"@{req['username']}"
        if req and req.get("username")
        else (req["first_name"] if req and req.get("first_name") else str(req_id))
    )
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


async def _handle_admin(query):
    kb = await admin_keyboard()
    await _safe_edit(
        query, "⚙️ *Управление*\n━━━━━━━━━━━━━━━", parse_mode="Markdown", reply_markup=kb
    )


async def _handle_add_user_prompt(query, ctx):
    ctx.user_data["waiting_user_id"] = True
    await _safe_edit(
        query,
        "Отправьте Telegram ID пользователя:",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀ Отмена", callback_data="admin")]]
        ),
    )


async def _handle_remove_user(query, rm_id):
    await remove_allowed_user(rm_id)
    kb = await admin_keyboard()
    try:
        await _safe_edit(
            query,
            "⚙️ *Управление*\n━━━━━━━━━━━━━━━\n\nПользователь удалён.",
            parse_mode="Markdown",
            reply_markup=kb,
        )
    except Exception:
        pass
