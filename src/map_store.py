"""Map discovery and active-map persistence for GraphNav maps."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class MapInfo:
    """Resolved metadata for one stored GraphNav map."""

    name: str
    path: str
    named_waypoints: dict[str, str]


class MapStore:
    """Discover maps from disk and persist the currently active map name."""

    def __init__(
        self,
        default_map_path: str,
        state_path: str | None = None,
        waypoint_config_path: str | None = None,
    ) -> None:
        default_path = Path(default_map_path).resolve()
        self._maps_dir = default_path.parent
        self._default_map_name = default_path.name
        self._state_path = (
            Path(state_path).resolve()
            if state_path
            else self._maps_dir.parent / "config" / "map_state.local.yml"
        )
        self._waypoint_config_path = (
            Path(waypoint_config_path).resolve()
            if waypoint_config_path
            else self._maps_dir.parent / "config" / "map_waypoints.yml"
        )

    @property
    def maps_dir(self) -> str:
        """Absolute path to the directory containing stored maps."""
        return str(self._maps_dir)

    @property
    def state_path(self) -> str:
        """Absolute path to the local active-map state file."""
        return str(self._state_path)

    def list_maps(self) -> list[MapInfo]:
        """Return all valid GraphNav maps found in the maps directory."""
        if not self._maps_dir.exists():
            return []

        configured_waypoints = self._read_waypoint_config()
        maps: list[MapInfo] = []
        for child in sorted(self._maps_dir.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir():
                continue
            if not (child / "graph").is_file():
                continue
            maps.append(
                MapInfo(
                    name=child.name,
                    path=str(child),
                    named_waypoints=dict(configured_waypoints.get(child.name, {})),
                )
            )
        return maps

    def get_map(self, name: str) -> MapInfo | None:
        """Return map metadata by directory name."""
        normalized = name.strip().lower()
        for map_info in self.list_maps():
            if map_info.name.lower() == normalized:
                return map_info
        return None

    def get_active_map_name(self) -> str | None:
        """Resolve active map name from local state, then config default, then first map."""
        maps = self.list_maps()
        if not maps:
            return None

        by_name = {map_info.name: map_info for map_info in maps}
        configured = self._read_state().get("active_map")
        if isinstance(configured, str) and configured in by_name:
            return configured
        if self._default_map_name in by_name:
            return self._default_map_name
        return maps[0].name

    def get_active_map(self) -> MapInfo | None:
        """Return metadata for the currently active map."""
        active_name = self.get_active_map_name()
        if active_name is None:
            return None
        return self.get_map(active_name)

    def set_active_map(self, name: str) -> MapInfo:
        """Persist a new active map name and return its metadata."""
        map_info = self.get_map(name)
        if map_info is None:
            raise ValueError(f"Unknown map '{name}'.")

        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        with self._state_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump({"active_map": map_info.name}, fh, sort_keys=True)
        return map_info

    def list_active_waypoints(self) -> list[str]:
        """Return named waypoints for the currently active map."""
        active_map = self.get_active_map()
        if active_map is None:
            return []
        return sorted(active_map.named_waypoints.keys())

    def resolve_active_waypoint(self, name: str) -> str | None:
        """Resolve a curated waypoint name to its GraphNav identifier."""
        active_map = self.get_active_map()
        if active_map is None:
            return None
        return active_map.named_waypoints.get(name.strip().lower())

    def _read_state(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {}
        with self._state_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data if isinstance(data, dict) else {}

    def _read_waypoint_config(self) -> dict[str, dict[str, str]]:
        """Load curated waypoint names per map from YAML config."""
        if not self._waypoint_config_path.exists():
            return {}

        with self._waypoint_config_path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            return {}

        maps_raw = raw.get("maps", {})
        if not isinstance(maps_raw, dict):
            return {}

        resolved: dict[str, dict[str, str]] = {}
        for map_name, map_data in maps_raw.items():
            if not isinstance(map_name, str) or not isinstance(map_data, dict):
                continue
            named_waypoints_raw = map_data.get("named_waypoints", {})
            if not isinstance(named_waypoints_raw, dict):
                continue
            named_waypoints: dict[str, str] = {}
            for display_name, identifier in named_waypoints_raw.items():
                if not isinstance(display_name, str) or not isinstance(identifier, str):
                    continue
                key = display_name.strip().lower()
                value = identifier.strip()
                if key and value:
                    named_waypoints[key] = value
            resolved[map_name] = named_waypoints
        return resolved
