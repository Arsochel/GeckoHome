"""Диспатч callback-кнопок бота: таблица маршрутов и гейты доступа.

conftest.py: TELEGRAM_SUPER_ADMIN=111,222; TELEGRAM_ADMIN=333.
"""

from types import SimpleNamespace

import pytest

from geckohome.bot.handlers import dispatch

# ── _resolve_route ──


def test_exact_route_resolves_without_arg():
    route, arg = dispatch._resolve_route("cam_snap")
    assert arg == ""
    assert not route.super_admin


def test_prefix_route_resolves_with_suffix():
    route, arg = dispatch._resolve_route("sched_toggle_uv_0800")
    assert arg == "uv_0800"
    assert route.super_admin


def test_exact_wins_over_prefix():
    # alert_fed — exact, alert_fed_count_5 — префиксный: не путаются
    exact, arg1 = dispatch._resolve_route("alert_fed")
    prefixed, arg2 = dispatch._resolve_route("alert_fed_count_5")
    assert arg1 == ""
    assert arg2 == "5"
    assert exact.handler is not prefixed.handler


def test_unknown_callback_resolves_to_none():
    assert dispatch._resolve_route("bogus_button") is None


def test_all_lamp_routes_require_super_admin_with_alert():
    for key in ("uv_on", "uv_off", "heat_on", "heat_off"):
        route, _ = dispatch._resolve_route(key)
        assert route.super_admin
        assert route.deny_msg


# ── button_handler: поведение через фейковые PTB-объекты ──


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


def _update(data, user_id):
    return SimpleNamespace(
        callback_query=FakeQuery(data),
        effective_user=SimpleNamespace(id=user_id, username="u", first_name="F"),
    )


def _ctx():
    return SimpleNamespace(user_data={}, bot=None)


async def test_super_admin_route_dispatches(monkeypatch):
    calls = []

    async def fake_lamp(query, user_id, lamp, on):
        calls.append((user_id, lamp, on))

    monkeypatch.setattr(dispatch, "_handle_lamp", fake_lamp)
    upd = _update("uv_on", 111)
    await dispatch.button_handler(upd, _ctx())
    assert calls == [(111, "uv", True)]


async def test_non_admin_gets_lamp_denial(monkeypatch):
    monkeypatch.setattr(dispatch, "_handle_lamp", lambda *a: pytest.fail("не должен вызываться"))
    upd = _update("uv_on", 333)  # обычный админ, не супер
    await dispatch.button_handler(upd, _ctx())
    denials = [a for a in upd.callback_query.answers if a[1]]
    assert denials and "super admin" in denials[0][0]


async def test_non_admin_silent_deny_for_feeding(monkeypatch):
    monkeypatch.setattr(dispatch, "_handle_fed", lambda *a: pytest.fail("не должен вызываться"))
    upd = _update("fed", 333)
    await dispatch.button_handler(upd, _ctx())
    assert all(not a[1] for a in upd.callback_query.answers)  # без алертов, молча


async def test_stranger_blocked_at_access_gate(monkeypatch):
    monkeypatch.setattr(
        dispatch, "_handle_snapshot", lambda *a: pytest.fail("не должен вызываться")
    )
    upd = _update("cam_snap", 999999)
    await dispatch.button_handler(upd, _ctx())
    assert upd.callback_query.answers == [("⛔ Нет доступа.", True)]


async def test_count_route_ignores_garbage(monkeypatch):
    monkeypatch.setattr(
        dispatch, "_handle_fed_count", lambda *a: pytest.fail("не должен вызываться")
    )
    upd = _update("fed_count_garbage", 111)
    await dispatch.button_handler(upd, _ctx())  # не падает и не вызывает хэндлер


async def test_count_route_parses_number(monkeypatch):
    calls = []

    async def fake_fed_count(query, user_id, ctx, count):
        calls.append(count)

    monkeypatch.setattr(dispatch, "_handle_fed_count", fake_fed_count)
    upd = _update("fed_count_5", 111)
    await dispatch.button_handler(upd, _ctx())
    assert calls == [5]


async def test_unknown_callback_is_noop(monkeypatch):
    upd = _update("bogus_button", 111)
    await dispatch.button_handler(upd, _ctx())  # просто ничего не происходит
