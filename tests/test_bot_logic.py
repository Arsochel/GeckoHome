"""Чистая логика бота: доступ, i18n, форматтеры, клавиатуры. Без Telegram API.

conftest.py задаёт TELEGRAM_SUPER_ADMIN=111,222 и TELEGRAM_ADMIN=333.
"""

import os
from datetime import datetime, timedelta

import pytest

from geckohome import paths
from geckohome.bot import formatters, keyboards
from geckohome.bot.access import check_access, is_admin, is_super_admin
from geckohome.bot.i18n import get_lang, toggle_lang
from geckohome.database import (
    add_access_request,
    add_allowed_user,
    save_schedule,
    set_gecko_state,
)

# ── access ──


def test_super_admin_roles():
    assert is_super_admin(111)
    assert is_super_admin(222)
    assert not is_super_admin(333)
    assert not is_super_admin(999)


def test_admin_includes_super_admin():
    assert is_admin(333)
    assert is_admin(111)
    assert not is_admin(999)


async def test_check_access_for_admins_and_strangers():
    assert await check_access(111) is True
    assert await check_access(333) is True
    assert await check_access(999) is False


async def test_check_access_after_allowlisting():
    await add_allowed_user(999, "someone", "Some One")
    assert await check_access(999) is True


# ── i18n ──


async def test_default_lang_is_ru():
    assert await get_lang(424242) == "ru"


async def test_toggle_lang_roundtrip():
    assert await toggle_lang(424242) == "en"
    assert await get_lang(424242) == "en"
    assert await toggle_lang(424242) == "ru"
    assert await get_lang(424242) == "ru"


# ── formatters ──


@pytest.mark.parametrize(
    ("switch", "lang", "expected"),
    [
        (True, "ru", "🟢 включена"),
        (False, "ru", "🔴 выключена"),
        (None, "ru", "⚪️ недоступна"),
        (True, "en", "🟢 on"),
        (None, "en", "⚪️ unavailable"),
    ],
)
def test_lamp_line(switch, lang, expected):
    assert formatters._lamp_line({"switch": switch}, lang) == expected


def test_ago_str():
    assert formatters._ago_str(None, "ru") == ""
    assert "только что" in formatters._ago_str(datetime.now(), "ru")
    assert "just now" in formatters._ago_str(datetime.now(), "en")
    old = datetime.now() - timedelta(minutes=7)
    assert "7 мин назад" in formatters._ago_str(old, "ru")
    assert "7 min ago" in formatters._ago_str(old, "en")


async def test_state_line_uses_labels():
    await set_gecko_state("roaming")
    line = await formatters._state_line("ru")
    assert "Шарится" in line
    line_en = await formatters._state_line("en")
    assert "Roaming" in line_en


async def test_alert_block_feeding_overdue(monkeypatch):
    three_days_ago = datetime.now() - timedelta(days=3)
    monkeypatch.setattr(formatters, "get_last_feeding_cached", lambda: three_days_ago)
    block = await formatters._alert_block("ru")
    assert "Пора кормить" in block
    assert "3 д." in block


async def test_alert_block_crickets_out(monkeypatch):
    monkeypatch.setattr(formatters, "get_last_feeding_cached", lambda: datetime.now())

    async def _no_crickets():
        return 0

    monkeypatch.setattr(formatters, "get_cricket_remaining", _no_crickets)
    block = await formatters._alert_block("ru")
    assert "Сверчки закончились" in block


async def test_alert_block_empty_when_all_good(monkeypatch):
    monkeypatch.setattr(formatters, "get_last_feeding_cached", lambda: datetime.now())

    async def _plenty():
        return 42

    monkeypatch.setattr(formatters, "get_cricket_remaining", _plenty)
    assert await formatters._alert_block("ru") == ""


async def test_user_status_text_formats_readings(monkeypatch):
    def fake_get_sensor(device, dp):
        return 253 if dp == "va_temperature" else 47

    monkeypatch.setattr(formatters.tuya, "get_sensor", fake_get_sensor)
    text = await formatters.user_status_text("ru")
    assert "25.3°C" in text
    assert "47%" in text


async def test_user_status_text_dashes_when_offline(monkeypatch):
    monkeypatch.setattr(formatters.tuya, "get_sensor", lambda device, dp: None)
    text = await formatters.user_status_text("en")
    assert "—" in text


# ── keyboards ──


@pytest.fixture(autouse=True)
def no_tunnel_file():
    try:
        os.remove(paths.TUNNEL_URL_FILE)
    except OSError:
        pass
    yield
    try:
        os.remove(paths.TUNNEL_URL_FILE)
    except OSError:
        pass


def test_stream_url_none_for_localhost_fallback():
    # tunnel_url.txt нет, STREAM_BASE_URL дефолтный localhost → кнопки нет
    assert keyboards.stream_url() is None
    assert keyboards.detect_stream_url() is None


def test_stream_url_from_tunnel_file():
    with open(paths.TUNNEL_URL_FILE, "w") as f:
        f.write("https://example.trycloudflare.com\n")
    assert keyboards.stream_url() == "https://example.trycloudflare.com/stream"
    assert keyboards.detect_stream_url() == "https://example.trycloudflare.com/stream/detect"


def _callback_datas(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def test_cricket_count_keyboard():
    markup = keyboards.cricket_count_keyboard()
    datas = _callback_datas(markup)
    assert datas == ["fed_count_3", "fed_count_4", "fed_count_5", "fed_count_6", "feeding_menu"]


async def test_feeding_keyboard_actions():
    datas = _callback_datas(await keyboards.feeding_keyboard("ru"))
    for expected in ("fed", "fed_hornworm", "fed_vitamins", "cricket_bought", "back_main"):
        assert expected in datas


async def test_schedules_keyboard_lists_saved():
    await save_schedule("sched1", "uv", 8, 0, 12)
    datas = _callback_datas(await keyboards.schedules_keyboard())
    assert "sched_toggle_sched1" in datas
    assert "sched_del_sched1" in datas
    assert "sched_new" in datas


async def test_admin_keyboard_shows_pending_requests():
    await add_access_request(777, "newbie", "New Bie")
    datas = _callback_datas(await keyboards.admin_keyboard())
    assert "approve_777" in datas
    assert "deny_777" in datas
