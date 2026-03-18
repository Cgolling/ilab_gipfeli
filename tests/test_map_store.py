"""Tests for on-disk GraphNav map discovery and curated waypoint persistence."""

from pathlib import Path

import yaml

from src.map_store import MapStore


def _write_graph(map_dir: Path) -> None:
    map_dir.mkdir(parents=True, exist_ok=True)
    (map_dir / "waypoint_snapshots").mkdir(exist_ok=True)
    (map_dir / "edge_snapshots").mkdir(exist_ok=True)
    (map_dir / "graph").write_bytes(b"graph")


def test_list_maps_discovers_graphnav_maps(tmp_path: Path) -> None:
    maps_dir = tmp_path / "maps"
    _write_graph(maps_dir / "map_one")
    _write_graph(maps_dir / "map_two")
    waypoint_config = tmp_path / "config" / "map_waypoints.yml"
    waypoint_config.parent.mkdir(parents=True, exist_ok=True)
    waypoint_config.write_text(
        yaml.safe_dump(
            {
                "maps": {
                    "map_one": {
                        "named_waypoints": {
                            "aula": "aula",
                            "triangle": "triangle",
                        }
                    },
                    "map_two": {"named_waypoints": {}},
                }
            }
        ),
        encoding="utf-8",
    )

    store = MapStore(
        str(maps_dir / "map_one"),
        state_path=str(tmp_path / "config" / "map_state.local.yml"),
        waypoint_config_path=str(waypoint_config),
    )

    maps = store.list_maps()
    assert [map_info.name for map_info in maps] == ["map_one", "map_two"]
    assert maps[0].named_waypoints == {"aula": "aula", "triangle": "triangle"}


def test_active_map_falls_back_to_default(tmp_path: Path) -> None:
    maps_dir = tmp_path / "maps"
    _write_graph(maps_dir / "map_one")
    _write_graph(maps_dir / "map_two")
    waypoint_config = tmp_path / "config" / "map_waypoints.yml"
    waypoint_config.parent.mkdir(parents=True, exist_ok=True)
    waypoint_config.write_text(
        yaml.safe_dump({"maps": {"map_two": {"named_waypoints": {"triangle": "triangle"}}}}),
        encoding="utf-8",
    )

    store = MapStore(
        str(maps_dir / "map_two"),
        state_path=str(tmp_path / "config" / "map_state.local.yml"),
        waypoint_config_path=str(waypoint_config),
    )

    active_map = store.get_active_map()
    assert active_map is not None
    assert active_map.name == "map_two"
    assert active_map.named_waypoints == {"triangle": "triangle"}


def test_set_active_map_persists_state(tmp_path: Path) -> None:
    maps_dir = tmp_path / "maps"
    _write_graph(maps_dir / "map_one")
    _write_graph(maps_dir / "map_two")
    state_path = tmp_path / "config" / "map_state.local.yml"
    waypoint_config = tmp_path / "config" / "map_waypoints.yml"
    waypoint_config.parent.mkdir(parents=True, exist_ok=True)
    waypoint_config.write_text(
        yaml.safe_dump({"maps": {"map_two": {"named_waypoints": {"triangle": "triangle"}}}}),
        encoding="utf-8",
    )

    store = MapStore(
        str(maps_dir / "map_one"),
        state_path=str(state_path),
        waypoint_config_path=str(waypoint_config),
    )
    store.set_active_map("map_two")

    reloaded = MapStore(
        str(maps_dir / "map_one"),
        state_path=str(state_path),
        waypoint_config_path=str(waypoint_config),
    )
    active_map = reloaded.get_active_map()
    assert active_map is not None
    assert active_map.name == "map_two"


def test_resolve_active_waypoint_uses_curated_mapping(tmp_path: Path) -> None:
    maps_dir = tmp_path / "maps"
    _write_graph(maps_dir / "map_one")
    waypoint_config = tmp_path / "config" / "map_waypoints.yml"
    waypoint_config.parent.mkdir(parents=True, exist_ok=True)
    waypoint_config.write_text(
        yaml.safe_dump({"maps": {"map_one": {"named_waypoints": {"aula": "al"}}}}),
        encoding="utf-8",
    )

    store = MapStore(
        str(maps_dir / "map_one"),
        state_path=str(tmp_path / "config" / "map_state.local.yml"),
        waypoint_config_path=str(waypoint_config),
    )

    assert store.list_active_waypoints() == ["aula"]
    assert store.resolve_active_waypoint("aula") == "al"
