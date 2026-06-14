import logging


from geckohome.database import (
    get_motion_event, update_motion_status, get_allowed_users,
)
from geckohome.bot.access import is_super_admin

log = logging.getLogger(__name__)


from geckohome.bot.handlers._helpers import _safe_edit


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
