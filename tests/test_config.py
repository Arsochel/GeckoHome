"""Settings parsing/validation — pure, no env or .env involved."""

from geckohome.config import Settings


def _s(**kw) -> Settings:
    # _env_file=None keeps the test independent of any real .env on disk.
    return Settings(_env_file=None, **kw)


def test_super_admin_ids_drops_zero_and_dedupes():
    assert _s(telegram_super_admin="5, 6, 0, 5").super_admin_ids == {5, 6}


def test_super_admin_ids_handles_comma_and_space():
    assert _s(telegram_super_admin="10 20,30").super_admin_ids == {10, 20, 30}


def test_admin_ids_keep_nonzero():
    assert _s(telegram_admin="7, 8").admin_ids == {7, 8}


def test_region_is_normalized_lowercase():
    assert _s(tuya_cloud_region="  EU ").tuya_cloud_region == "eu"


def test_region_empty_falls_back_to_eu():
    assert _s(tuya_cloud_region="").tuya_cloud_region == "eu"


def test_motion_debug_bool_coercion():
    assert _s(motion_debug="yes").motion_debug is True
    assert _s(motion_debug="0").motion_debug is False


def test_numeric_coercion():
    s = _s(motion_threshold="30", temp_alert_min="201")
    assert s.motion_threshold == 30
    assert isinstance(s.motion_threshold, int)
    assert s.temp_alert_min == 201.0


def test_device_local_structure():
    # Versions passed explicitly so the test does not depend on ambient env/.env.
    s = _s(
        device_uv_lamp_ip="192.168.0.5", device_uv_lamp_local_key="k", device_uv_lamp_version="3.4"
    )
    assert s.device_local["uv_lamp"] == {"ip": "192.168.0.5", "key": "k", "version": "3.4"}


def test_device_local_version_defaults():
    # Field defaults (independent of the dict-assembly logic above).
    fields = Settings.model_fields
    assert fields["device_uv_lamp_version"].default == "3.4"
    assert fields["device_thermometer_version"].default == "3.3"


def test_device_ids_mapping():
    s = _s(device_uv_lamp="abc", device_heat_lamp="def")
    assert s.device_ids["uv_lamp"] == "abc"
    assert s.device_ids["heat_lamp"] == "def"
    assert s.device_ids["humidifier"] == ""
