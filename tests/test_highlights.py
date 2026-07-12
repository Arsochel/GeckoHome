"""State machine состояния геккона: roaming / resting / sleeping."""

from datetime import datetime, timedelta

import pytest

from geckohome.database import get_gecko_state
from geckohome.services import motion
from geckohome.services.highlights import _SLEEP_THRESHOLD_MIN, update_gecko_state


async def _state_for(monkeypatch, last_motion: datetime | None) -> str | None:
    monkeypatch.setattr(motion, "get_last_motion_time", lambda: last_motion)
    await update_gecko_state()
    state, _ = await get_gecko_state()
    return state


async def test_no_motion_since_start_means_sleeping(monkeypatch):
    assert await _state_for(monkeypatch, None) == "sleeping"


async def test_recent_motion_means_roaming(monkeypatch):
    last = datetime.now() - timedelta(seconds=motion.MOTION_TIMEOUT - 5)
    assert await _state_for(monkeypatch, last) == "roaming"


async def test_stale_motion_means_resting(monkeypatch):
    last = datetime.now() - timedelta(seconds=motion.MOTION_TIMEOUT + 5)
    assert await _state_for(monkeypatch, last) == "resting"


async def test_long_idle_means_sleeping(monkeypatch):
    last = datetime.now() - timedelta(minutes=_SLEEP_THRESHOLD_MIN, seconds=5)
    assert await _state_for(monkeypatch, last) == "sleeping"


@pytest.mark.parametrize("state", ["roaming", "sleeping"])
async def test_state_is_persisted_with_timestamp(monkeypatch, state):
    last = None if state == "sleeping" else datetime.now()
    await _state_for(monkeypatch, last)
    saved, updated_at = await get_gecko_state()
    assert saved == state
    assert updated_at is not None
