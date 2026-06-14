"""Database access layer.

Split into domain modules (schema, users, schedules, lamps, sensors, photos,
motion, feeding, gecko, alerts); this package re-exports their public API so
``from geckohome.database import <name>`` keeps working unchanged.
"""

from geckohome.database._core import DB_PATH, MEDIA_DB_PATH, _db, _media_db
from geckohome.database.alerts import (
    delete_alert_message,
    get_alert_message,
    save_alert_message,
)
from geckohome.database.feeding import (
    append_feeding_note,
    get_cricket_remaining,
    get_cricket_stats,
    get_feeding_count,
    get_feeding_history,
    get_feedings_count_since,
    get_last_cricket_purchase,
    get_last_feeding_cached,
    get_last_feeding_db,
    get_last_note_date,
    get_next_feeding_supplements,
    load_last_feeding,
    log_cricket_deaths,
    log_cricket_purchase,
    log_cricket_ran_out,
    log_feeding,
)
from geckohome.database.gecko import (
    get_gecko_birthday,
    get_gecko_state,
    get_gecko_zone,
    log_gecko_zone,
    set_gecko_state,
)
from geckohome.database.lamps import (
    get_last_lamp_states,
    log_lamp_event,
    purge_lamp_events,
)
from geckohome.database.motion import (
    add_motion_event,
    get_motion_event,
    get_motion_events_24h_count,
    get_recent_motion_events,
    update_motion_photo,
    update_motion_status,
)
from geckohome.database.photos import (
    delete_photo,
    get_photo_data,
    get_photos,
    purge_old_photos,
    save_photo,
)
from geckohome.database.schedules import (
    delete_schedule,
    get_schedules,
    save_schedule,
    set_schedule_paused,
)
from geckohome.database.schema import _init_media_db, init_db
from geckohome.database.sensors import (
    get_last_sensor_reading,
    get_sensor_history,
    log_sensor_reading,
)
from geckohome.database.users import (
    add_access_request,
    add_allowed_user,
    create_debug_token,
    get_access_requests,
    get_allowed_users,
    get_blocked_user_ids,
    get_blocked_users,
    get_user_lang,
    has_pending_request,
    is_user_allowed,
    log_user_action,
    purge_expired_debug_tokens,
    remove_access_request,
    remove_allowed_user,
    set_user_blocked,
    set_user_lang,
    update_user_info,
    validate_debug_token,
    was_user_revoked,
)

__all__ = [
    "DB_PATH",
    "MEDIA_DB_PATH",
    "_db",
    "_media_db",
    "_init_media_db",
    "init_db",
    # users
    "create_debug_token",
    "validate_debug_token",
    "purge_expired_debug_tokens",
    "log_user_action",
    "get_allowed_users",
    "is_user_allowed",
    "was_user_revoked",
    "add_allowed_user",
    "update_user_info",
    "remove_allowed_user",
    "set_user_blocked",
    "get_blocked_user_ids",
    "get_blocked_users",
    "add_access_request",
    "get_access_requests",
    "remove_access_request",
    "has_pending_request",
    "get_user_lang",
    "set_user_lang",
    # schedules
    "get_schedules",
    "save_schedule",
    "delete_schedule",
    "set_schedule_paused",
    # lamps
    "log_lamp_event",
    "get_last_lamp_states",
    "purge_lamp_events",
    # sensors
    "log_sensor_reading",
    "get_last_sensor_reading",
    "get_sensor_history",
    # photos
    "save_photo",
    "get_photos",
    "get_photo_data",
    "delete_photo",
    "purge_old_photos",
    # motion
    "add_motion_event",
    "get_motion_event",
    "get_motion_events_24h_count",
    "get_recent_motion_events",
    "update_motion_status",
    "update_motion_photo",
    # feeding
    "load_last_feeding",
    "log_feeding",
    "append_feeding_note",
    "get_last_feeding_cached",
    "get_last_feeding_db",
    "get_feeding_count",
    "get_last_note_date",
    "log_cricket_purchase",
    "log_cricket_ran_out",
    "get_last_cricket_purchase",
    "get_cricket_remaining",
    "log_cricket_deaths",
    "get_feedings_count_since",
    "get_next_feeding_supplements",
    "get_feeding_history",
    "get_cricket_stats",
    # gecko
    "set_gecko_state",
    "get_gecko_birthday",
    "get_gecko_state",
    "log_gecko_zone",
    "get_gecko_zone",
    # alerts
    "get_alert_message",
    "save_alert_message",
    "delete_alert_message",
]
