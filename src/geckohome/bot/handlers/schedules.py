import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from geckohome.database import (
    get_schedules, delete_schedule, set_schedule_paused,
)
from geckohome.bot.keyboards import schedules_keyboard

log = logging.getLogger(__name__)


from geckohome.bot.handlers._helpers import _safe_edit


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


