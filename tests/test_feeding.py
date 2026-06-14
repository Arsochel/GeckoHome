"""Feeding, supplements and cricket-stock domain logic."""

from datetime import datetime, timedelta

from geckohome import database as db


async def test_same_day_feeding_merges():
    await db.log_feeding(crickets=4)
    await db.log_feeding(vitamins=True)
    assert await db.get_feeding_count() == 1


async def test_supplements_on_fresh_db():
    # never fed vitamins/hornworm -> both due
    assert set(await db.get_next_feeding_supplements()) == {"vitamins", "hornworm"}


async def test_supplements_after_vitamins_feeding():
    await db.log_feeding(crickets=5, vitamins=True)
    supplements = await db.get_next_feeding_supplements()
    # vitamins were just given this feeding -> not due again yet
    assert "vitamins" not in supplements
    assert "hornworm" in supplements


async def test_feedings_count_since():
    past = datetime.now() - timedelta(days=1)
    await db.log_feeding(crickets=5)
    assert await db.get_feedings_count_since(past) == 1
    future = datetime.now() + timedelta(days=1)
    assert await db.get_feedings_count_since(future) == 0


async def test_cricket_remaining_and_deaths():
    await db.log_cricket_purchase(30)
    assert await db.get_cricket_remaining() == 30
    await db.log_cricket_deaths(5)
    _, count = await db.get_last_cricket_purchase()
    assert count == 25
    assert await db.get_cricket_remaining() == 25


async def test_cricket_remaining_none_without_purchase():
    assert await db.get_cricket_remaining() is None
