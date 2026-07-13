"""Фейлы переключения ламп: честный лог событий + алерт после N неудач.

Регрессия на реальный инцидент 2026-07-13: роутер сменил подсеть, обе лампы
стали Device Unreachable, sync писал фантомные 'off' каждые 5 минут всю ночь,
свет горел, алертов не было.
"""

import pytest

from geckohome.database._core import _db
from geckohome.services import tuya
from geckohome.services.scheduler import lamps


@pytest.fixture(autouse=True)
def fresh_failures(monkeypatch):
    monkeypatch.setattr(lamps, "_switch_failures", {})


@pytest.fixture
def alerts(monkeypatch):
    sent: list[str] = []

    async def fake_alert(text):
        sent.append(text)

    monkeypatch.setattr(lamps, "_send_alert", fake_alert)
    return sent


async def _lamp_events() -> list[tuple[str, str, str]]:
    async with _db() as db, db.execute("SELECT lamp_type, action, source FROM lamp_events") as cur:
        return [(r["lamp_type"], r["action"], r["source"]) for r in await cur.fetchall()]


async def test_success_logs_event_and_resets_counter(monkeypatch, alerts):
    monkeypatch.setattr(tuya, "switch_lamp", lambda lamp, on: True)
    lamps._switch_failures["uv"] = 2  # были фейлы — успех сбрасывает

    assert await lamps._switch_and_log("uv", False, "sync") is True
    assert await _lamp_events() == [("uv", "off", "sync")]
    assert lamps._switch_failures["uv"] == 0
    assert alerts == []


async def test_failure_writes_no_phantom_event(monkeypatch, alerts):
    monkeypatch.setattr(tuya, "switch_lamp", lambda lamp, on: False)

    assert await lamps._switch_and_log("uv", False, "sync") is False
    assert await _lamp_events() == []  # раньше тут был фантомный 'off'


async def test_alert_fires_after_threshold(monkeypatch, alerts):
    monkeypatch.setattr(tuya, "switch_lamp", lambda lamp, on: False)

    for _ in range(lamps._FAILURE_ALERT_THRESHOLD - 1):
        await lamps._switch_and_log("heat", False, "sync")
    assert alerts == []  # ещё рано

    await lamps._switch_and_log("heat", False, "sync")
    assert len(alerts) == 1
    assert "HEAT" in alerts[0]
    assert "выключить" in alerts[0]


async def test_failure_counters_are_per_lamp(monkeypatch, alerts):
    monkeypatch.setattr(tuya, "switch_lamp", lambda lamp, on: lamp == "uv")

    for _ in range(lamps._FAILURE_ALERT_THRESHOLD):
        await lamps._switch_and_log("uv", True, "sync")
        await lamps._switch_and_log("heat", True, "sync")

    assert lamps._switch_failures["uv"] == 0
    assert lamps._switch_failures["heat"] == lamps._FAILURE_ALERT_THRESHOLD
    assert len(alerts) == 1  # только про heat


async def test_temp_guard_alerts_only_on_real_switch(monkeypatch, alerts):
    # активное окно на текущий час, лампа «включена», перегрев, но железо не отвечает
    from datetime import datetime

    from geckohome.database import save_schedule

    now = datetime.now()
    await save_schedule("s1", "uv", now.hour, 0, 2)
    monkeypatch.setattr(tuya, "get_sensor", lambda t, c: 360)  # 36.0°C
    monkeypatch.setattr(tuya, "get_lamp_status", lambda lamp: {"online": True, "switch": True})
    monkeypatch.setattr(tuya, "switch_lamp", lambda lamp, on: False)

    await lamps.check_lamp_temperature()

    # алерт «перегрев, выключил» НЕ отправлен — выключить не удалось
    assert all("Перегрев" not in a for a in alerts)
    assert await _lamp_events() == []
