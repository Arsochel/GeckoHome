"""End-to-end schema sanity: a brand-new database must support every core op.

This is the regression guard for the bug where init_db produced a `feedings`
table without the crickets/vitamins/hornworm columns, crashing fresh installs.
The autouse `fresh_db` fixture already runs init_db on an empty database.
"""

from geckohome import database as db


async def test_core_operations_on_fresh_database():
    # feeding
    await db.log_feeding(crickets=6, vitamins=True)
    assert await db.get_feeding_count() == 1

    # same-day feeding updates the existing row instead of inserting
    await db.log_feeding(hornworm=True)
    assert await db.get_feeding_count() == 1

    # lamps
    await db.log_lamp_event("heat", "on", "test")
    assert (await db.get_last_lamp_states())["heat"] is True

    # sensors
    await db.log_sensor_reading(245.0, 50.0)
    assert await db.get_last_sensor_reading() == (245.0, 50.0)

    # crickets
    await db.log_cricket_purchase(30)
    _, count = await db.get_last_cricket_purchase()
    assert count == 30

    # photos (separate media DB)
    photo_id = await db.save_photo(b"\xff\xd8\xff", source="test")
    assert await db.get_photo_data(photo_id) == b"\xff\xd8\xff"

    # gecko state
    await db.set_gecko_state("roaming")
    state, _ = await db.get_gecko_state()
    assert state == "roaming"
