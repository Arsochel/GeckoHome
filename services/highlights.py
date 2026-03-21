from datetime import datetime

from database import set_gecko_state

_SLEEP_THRESHOLD_MIN = 3  # минут без движения → считаем что спит


async def update_gecko_state(force: bool = False):
    """Determine gecko state from motion timer."""
    from services.motion import get_last_motion_time
    last_motion = get_last_motion_time()
    if last_motion is None:
        return
    seconds_ago = (datetime.now() - last_motion).total_seconds()
    if seconds_ago > _SLEEP_THRESHOLD_MIN * 60:
        state = "sleeping"
    else:
        state = "resting"
    await set_gecko_state(state)
    print(f"[State] {state} (motion {int(seconds_ago // 60)}m ago)")
