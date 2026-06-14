"""Telegram bot handlers.

Split by feature (access, lamps, media, schedules, feeding, motion) around a
shared ``_helpers`` module and a ``dispatch`` module holding the entry points.
Re-exports the four handlers registered in bot.main.
"""

from geckohome.bot.handlers.dispatch import (
    button_handler,
    cmd_start,
    cmd_status,
    message_handler,
)

__all__ = ["cmd_start", "cmd_status", "button_handler", "message_handler"]
