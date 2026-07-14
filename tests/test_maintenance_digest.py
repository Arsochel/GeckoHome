"""UVB-трекер и еженедельный дайджест."""

from datetime import date, timedelta

import pytest

from geckohome import config
from geckohome.database import (
    get_profile_value,
    log_feeding,
    log_gecko_zone,
    log_sensor_reading,
    set_profile_value,
)
from geckohome.services.scheduler import maintenance
from geckohome.services.scheduler.digest import build_weekly_digest

# ── UVB age ──


@pytest.fixture
def uvb_alerts(monkeypatch):
    sent: list[tuple[int, str]] = []
    deleted: list[str] = []

    async def fake_send(uid, alert_type, text, markup):
        sent.append((uid, text))

    async def fake_delete(alert_type):
        deleted.append(alert_type)

    monkeypatch.setattr(maintenance, "_send_or_edit_alert", fake_send)
    monkeypatch.setattr(maintenance, "_delete_alert_for_all", fake_delete)
    return sent, deleted


async def test_uvb_no_date_is_silent(uvb_alerts):
    sent, deleted = uvb_alerts
    await maintenance.check_uvb_age()
    assert sent == [] and deleted == []


async def test_uvb_fresh_lamp_clears_alert(uvb_alerts):
    sent, deleted = uvb_alerts
    await set_profile_value("uvb_installed_at", date.today().isoformat())
    await maintenance.check_uvb_age()
    assert sent == []
    assert deleted == ["uvb"]


async def test_uvb_old_lamp_alerts(uvb_alerts, monkeypatch):
    sent, _ = uvb_alerts
    monkeypatch.setattr(config, "UVB_REPLACE_MONTHS", 6)
    old = (date.today() - timedelta(days=7 * 30)).isoformat()  # ~7 месяцев
    await set_profile_value("uvb_installed_at", old)
    await maintenance.check_uvb_age()
    assert sent, "алерт должен уйти супер-админам"
    assert "UVB-лампе уже" in sent[0][1]


async def test_profile_roundtrip():
    await set_profile_value("uvb_installed_at", "2026-01-15")
    assert await get_profile_value("uvb_installed_at") == "2026-01-15"
    await set_profile_value("uvb_installed_at", "2026-07-14")  # upsert
    assert await get_profile_value("uvb_installed_at") == "2026-07-14"


# ── weekly digest ──


async def test_digest_with_data():
    from geckohome.database._core import _db

    await log_feeding(crickets=5)
    async with _db(write=True) as db:  # log_feeding мержит записи одного дня
        await db.execute("UPDATE feedings SET fed_at = datetime('now', '-2 days')")
    await log_feeding(crickets=6)
    await log_gecko_zone("skull", 0.9, seconds=3600)
    await log_gecko_zone("water", 0.9, seconds=600)
    await log_sensor_reading(253, 45)
    await log_sensor_reading(311, 52)

    text = await build_weekly_digest()

    assert "Кормлений: *2* (11 🦗)" in text
    assert "💀 череп 1ч 00м" in text
    assert "💧 поилка 10м" in text
    assert "25.3–31.1°C" in text
    assert "45–52%" in text


async def test_digest_on_empty_db_does_not_crash():
    text = await build_weekly_digest()
    assert "Неделя у геккона" in text
    assert "Кормлений: *0*" in text
