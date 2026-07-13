"""CRICKET_CARE_HINTS: подсказки про живых сверчков выключаемы (замороженные)."""

from freezegun import freeze_time

from geckohome import config
from geckohome.database import log_feeding
from geckohome.database._core import _db
from geckohome.services.scheduler import feeding as sched_feeding


async def _captured_alert(monkeypatch) -> str:
    """Готовит просроченное кормление и возвращает текст алерта."""
    await log_feeding(crickets=5)
    async with _db(write=True) as db:
        await db.execute("UPDATE feedings SET fed_at = datetime('now', '-5 days')")

    sent: list[str] = []

    async def fake_send(uid, alert_type, text, markup):
        sent.append(text)

    monkeypatch.setattr(sched_feeding, "_send_or_edit_alert", fake_send)
    with freeze_time("2026-07-14 21:00:00"):
        await sched_feeding.check_feeding_alert()
    assert sent, "алерт должен был отправиться"
    return sent[0]


async def test_hints_present_by_default(monkeypatch):
    monkeypatch.setattr(config, "CRICKET_CARE_HINTS", True)
    text = await _captured_alert(monkeypatch)
    assert "Покорми сверчков" in text


async def test_hints_absent_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "CRICKET_CARE_HINTS", False)
    text = await _captured_alert(monkeypatch)
    assert "Покорми сверчков" not in text
    assert "Пора кормить геккона" in text  # сам алерт на месте
