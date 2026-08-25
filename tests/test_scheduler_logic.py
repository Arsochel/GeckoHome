"""Age-based feeding schedule (pure logic in services.scheduler)."""

from freezegun import freeze_time

from geckohome.services.scheduler import get_feeding_schedule


@freeze_time("2026-06-14")
def test_hatchling_under_2_months_daily():
    # ~1 month old -> daily, 5-7 crickets
    assert get_feeding_schedule("2026-05-01") == (1, 5, 7)


@freeze_time("2026-06-14")
def test_juvenile_2_to_8_months_every_2_days():
    # ~6 months old -> every 2 days, 5-8 crickets
    assert get_feeding_schedule("2025-12-01") == (2, 5, 8)


@freeze_time("2026-06-14")
def test_adult_over_8_months_every_3_days():
    # >8 months -> every 3 days, 4-8 crickets
    assert get_feeding_schedule("2024-01-01") == (3, 4, 8)
