"""Application settings loader for shared, non-secret configuration."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class TelegramSettings:
    """Telegram bot runtime settings."""

    spot_hostname: str
    default_map_path: str
    spot_auto_connect: bool
    rbac_enabled: bool
    rbac_config_path: str
    sounds_dir: str
    snapshots_dir: str
    recordings_dir: str


@dataclass(frozen=True, slots=True)
class PerceptionSettings:
    """Perception/recording runtime settings."""

    record_backend_default: str
    webrtc_sdp_port: int
    webrtc_sdp_filename: str
    webrtc_verify_tls: bool
    webrtc_ca_cert_path: str | None
    webrtc_connect_timeout_seconds: float
    webrtc_ice_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Top-level application settings."""

    telegram: TelegramSettings
    perception: PerceptionSettings
    config_path: str
    local_override_path: str


def _as_bool(value: Any, default: bool) -> bool:
    """Coerce bool-like values from YAML/strings."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _as_int(value: Any, default: int) -> int:
    """Coerce integer values with fallback."""
    try:
        return int(value)
    except Exception:
        return default


def _as_float(value: Any, default: float) -> float:
    """Coerce float values with fallback."""
    try:
        return float(value)
    except Exception:
        return default


def _as_str(value: Any, default: str) -> str:
    """Coerce string values with fallback."""
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _as_optional_str(value: Any) -> str | None:
    """Coerce optional string values."""
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML mapping from file (empty mapping if missing/invalid)."""
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge mapping values from override into base."""
    result: dict[str, Any] = dict(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _resolve_path(project_root: Path, value: str) -> str:
    """Resolve path values relative to project root unless absolute."""
    path = Path(value)
    if not path.is_absolute():
        path = project_root / path
    return str(path)


@lru_cache(maxsize=1)
def load_app_settings() -> AppSettings:
    """
    Load shared app settings from versioned config files.

    Precedence:
    1. config/app_settings.local.yml (optional, gitignored)
    2. config/app_settings.yml (versioned defaults)
    """
    project_root = Path(__file__).resolve().parents[1]
    config_path = project_root / "config" / "app_settings.yml"
    local_path = project_root / "config" / "app_settings.local.yml"

    base = _load_yaml(config_path)
    override = _load_yaml(local_path)
    merged = _deep_merge(base, override)

    telegram_raw = merged.get("telegram", {}) if isinstance(merged.get("telegram"), dict) else {}
    perception_raw = (
        merged.get("perception", {}) if isinstance(merged.get("perception"), dict) else {}
    )

    telegram = TelegramSettings(
        spot_hostname=_as_str(telegram_raw.get("spot_hostname"), "192.168.80.3"),
        default_map_path=_resolve_path(
            project_root,
            _as_str(telegram_raw.get("default_map_path"), "maps/map_catacombs_01"),
        ),
        spot_auto_connect=_as_bool(telegram_raw.get("spot_auto_connect"), True),
        rbac_enabled=_as_bool(telegram_raw.get("rbac_enabled"), True),
        rbac_config_path=_resolve_path(
            project_root,
            _as_str(telegram_raw.get("rbac_config_path"), "config/telegram_rbac.yml"),
        ),
        sounds_dir=_resolve_path(
            project_root,
            _as_str(telegram_raw.get("sounds_dir"), "sounds"),
        ),
        snapshots_dir=_resolve_path(
            project_root,
            _as_str(telegram_raw.get("snapshots_dir"), "logs/perception_snapshots"),
        ),
        recordings_dir=_resolve_path(
            project_root,
            _as_str(telegram_raw.get("recordings_dir"), "logs/perception_recordings"),
        ),
    )

    perception = PerceptionSettings(
        record_backend_default=_as_str(perception_raw.get("record_backend_default"), "hybrid"),
        webrtc_sdp_port=_as_int(perception_raw.get("webrtc_sdp_port"), 31102),
        webrtc_sdp_filename=_as_str(perception_raw.get("webrtc_sdp_filename"), "h264.sdp"),
        webrtc_verify_tls=_as_bool(perception_raw.get("webrtc_verify_tls"), False),
        webrtc_ca_cert_path=_as_optional_str(perception_raw.get("webrtc_ca_cert_path")),
        webrtc_connect_timeout_seconds=_as_float(
            perception_raw.get("webrtc_connect_timeout_seconds"), 10.0
        ),
        webrtc_ice_timeout_seconds=_as_float(
            perception_raw.get("webrtc_ice_timeout_seconds"), 15.0
        ),
    )

    return AppSettings(
        telegram=telegram,
        perception=perception,
        config_path=str(config_path),
        local_override_path=str(local_path),
    )
