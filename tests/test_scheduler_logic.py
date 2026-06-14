"""Age-based feeding schedule (pure logic in services.scheduler)."""

from freezegun import freeze_time

from geckohome.services.scheduler import get_feeding_schedule


@freeze_time("2026-06-14")
def test_juvenile_under_6_months_daily():
    # ~2 months old -> daily, 5-7 crickets
    assert get_feeding_schedule("2026-04-01") == (1, 5, 7)


@freeze_time("2026-06-14")
def test_subadult_6_to_12_months_every_2_days():
    # ~8 months old -> every 2 days, 5-6 crickets
    assert get_feeding_schedule("2025-10-01") == (2, 5, 6)


@freeze_time("2026-06-14")
def test_adult_every_3_days():
    # >12 months -> every 3 days, 5-10 crickets
    assert get_feeding_schedule("2024-01-01") == (3, 5, 10)
