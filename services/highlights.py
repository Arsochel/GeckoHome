from datetime import datetime

from database import set_gecko_state
from services.motion import MOTION_TIMEOUT

_SLEEP_THRESHOLD_MIN = 3  # минут без движения → считаем что спит


async def update_gecko_state(force: bool = False):
    from services.motion import get_last_motion_time
    last_motion = get_last_motion_time()
    if last_motion is None:
        await set_gecko_state("sleeping")
        print("[State] sleeping (no motion since start)")
        return
    seconds_ago = (datetime.now() - last_motion).total_seconds()
    if seconds_ago < MOTION_TIMEOUT:
        state = "roaming"
    elif seconds_ago < _SLEEP_THRESHOLD_MIN * 60:
        state = "resting"
    else:
        state = "sleeping"
    await set_gecko_state(state)
    print(f"[State] {state} (motion {int(seconds_ago)}s ago)")
