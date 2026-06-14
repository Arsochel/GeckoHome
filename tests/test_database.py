"""Database CRUD against a real (temporary) SQLite database."""

from datetime import datetime

from geckohome import database as db


async def test_feeding_log_and_count():
    assert await db.get_feeding_count() == 0
    await db.log_feeding(crickets=5)
    assert await db.get_feeding_count() == 1
    last = await db.get_last_feeding_db()
    assert isinstance(last, datetime)


async def test_cricket_purchase_roundtrip():
    await db.log_cricket_purchase(20)
    when, count = await db.get_last_cricket_purchase()
    assert isinstance(when, datetime)
    assert count == 20


async def test_allowed_user_lifecycle():
    assert await db.is_user_allowed(123) is False
    await db.add_allowed_user(123, username="bob", first_name="Bob")
    assert await db.is_user_allowed(123) is True
    await db.remove_allowed_user(123)
    assert await db.is_user_allowed(123) is False


async def test_blocked_user_set():
    await db.add_allowed_user(555, username="x")
    await db.set_user_blocked(555, True)
    assert 555 in await db.get_blocked_user_ids()
    await db.set_user_blocked(555, False)
    assert 555 not in await db.get_blocked_user_ids()


async def test_lamp_event_last_state():
    await db.log_lamp_event("uv", "on", "test")
    states = await db.get_last_lamp_states()
    assert states.get("uv") is True
    await db.log_lamp_event("uv", "off", "test")
    states = await db.get_last_lamp_states()
    assert states.get("uv") is False


async def test_sensor_reading_roundtrip():
    await db.log_sensor_reading(250.0, 45.0)
    temp, hum = await db.get_last_sensor_reading()
    assert temp == 250.0
    assert hum == 45.0
