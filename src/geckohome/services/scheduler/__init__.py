"""Scheduler package.

Split by responsibility (notify, lamps, sensors, feeding, backup, jobs) around a
shared APScheduler singleton. Re-exports the names used elsewhere so
``from geckohome.services.scheduler import <name>`` keeps working.
"""

from geckohome.services.scheduler._core import scheduler
from geckohome.services.scheduler.feeding import (
    check_cricket_alert,
    check_feeding_alert,
    get_feeding_schedule,
)
from geckohome.services.scheduler.jobs import load_schedules, shutdown, start
from geckohome.services.scheduler.lamps import lamp_schedule

__all__ = [
    "scheduler",
    "lamp_schedule",
    "load_schedules",
    "start",
    "shutdown",
    "check_feeding_alert",
    "check_cricket_alert",
    "get_feeding_schedule",
]
