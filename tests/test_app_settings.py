"""Tests for shared app settings loader."""

from pathlib import Path

from src.app_settings import load_app_settings


def test_load_app_settings_has_expected_sections():
    settings = load_app_settings()

    assert settings.telegram is not None
    assert settings.perception is not None
    assert settings.telegram.spot_hostname
    assert settings.perception.record_backend_default in {"hybrid", "webrtc", "timelapse"}


def test_load_app_settings_resolves_paths_to_absolute():
    settings = load_app_settings()

    assert Path(settings.telegram.default_map_path).is_absolute()
    assert Path(settings.telegram.rbac_config_path).is_absolute()
    assert Path(settings.telegram.sounds_dir).is_absolute()
