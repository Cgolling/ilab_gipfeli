"""Load GraphNav maps from protobuf files."""

import logging
import os
from dataclasses import dataclass
from typing import Any

from bosdyn.api.graph_nav import map_pb2

logger = logging.getLogger(__name__)


@dataclass
class MapData:
    """Container for loaded GraphNav map data."""

    graph: map_pb2.Graph
    waypoints: dict[str, Any]  # waypoint_id -> Waypoint
    waypoint_snapshots: dict[str, Any]  # snapshot_id -> WaypointSnapshot
    edge_snapshots: dict[str, Any]  # snapshot_id -> EdgeSnapshot
    anchors: dict[str, Any]  # waypoint_id -> Anchor
    anchored_world_objects: dict[str, tuple]  # fiducial_id -> (object, waypoint, fiducial)


def load_map(path: str) -> MapData:
    """
    Load a GraphNav map from disk.

    Args:
        path: Path to map directory containing 'graph', 'waypoint_snapshots/', 'edge_snapshots/'

    Returns:
        MapData containing all loaded structures

    Raises:
        FileNotFoundError: If the graph file doesn't exist
        ValueError: If the graph file cannot be parsed
    """
    graph_path = os.path.join(path, "graph")
    if not os.path.exists(graph_path):
        raise FileNotFoundError(f"Graph file not found: {graph_path}")

    with open(graph_path, "rb") as graph_file:
        data = graph_file.read()
        graph = map_pb2.Graph()
        try:
            graph.ParseFromString(data)
        except Exception as e:
            raise ValueError(f"Failed to parse graph file: {e}") from e

    waypoints: dict[str, Any] = {}
    waypoint_snapshots: dict[str, Any] = {}
    edge_snapshots: dict[str, Any] = {}
    anchors: dict[str, Any] = {}
    anchored_world_objects: dict[str, tuple] = {}

    # Load anchored world objects first (for fiducial tracking)
    for anchored_wo in graph.anchoring.objects:
        anchored_world_objects[anchored_wo.id] = (anchored_wo,)

    # Load waypoints and their snapshots
    for waypoint in graph.waypoints:
        waypoints[waypoint.id] = waypoint

        if not waypoint.snapshot_id:
            continue

        snapshot_path = os.path.join(path, "waypoint_snapshots", waypoint.snapshot_id)
        if not os.path.exists(snapshot_path):
            logger.warning(f"Waypoint snapshot not found: {snapshot_path}")
            continue

        with open(snapshot_path, "rb") as snapshot_file:
            snapshot = map_pb2.WaypointSnapshot()
            try:
                snapshot.ParseFromString(snapshot_file.read())
                waypoint_snapshots[snapshot.id] = snapshot
            except Exception as e:
                logger.warning(f"Failed to parse waypoint snapshot {snapshot_path}: {e}")
                continue

            # Track fiducials in this snapshot
            for world_object in snapshot.objects:
                if not world_object.HasField("apriltag_properties"):
                    continue

                tag_id = str(world_object.apriltag_properties.tag_id)
                if tag_id in anchored_world_objects and len(anchored_world_objects[tag_id]) == 1:
                    anchored_wo = anchored_world_objects[tag_id][0]
                    anchored_world_objects[tag_id] = (anchored_wo, waypoint, world_object)

    # Load edge snapshots
    for edge in graph.edges:
        if not edge.snapshot_id:
            continue

        snapshot_path = os.path.join(path, "edge_snapshots", edge.snapshot_id)
        if not os.path.exists(snapshot_path):
            logger.warning(f"Edge snapshot not found: {snapshot_path}")
            continue

        with open(snapshot_path, "rb") as snapshot_file:
            snapshot = map_pb2.EdgeSnapshot()
            try:
                snapshot.ParseFromString(snapshot_file.read())
                edge_snapshots[snapshot.id] = snapshot
            except Exception as e:
                logger.warning(f"Failed to parse edge snapshot {snapshot_path}: {e}")

    # Load anchors
    for anchor in graph.anchoring.anchors:
        anchors[anchor.id] = anchor

    logger.info(
        f"Loaded map: {len(waypoints)} waypoints, {len(graph.edges)} edges, "
        f"{len(anchors)} anchors, {len(anchored_world_objects)} fiducials"
    )

    return MapData(
        graph=graph,
        waypoints=waypoints,
        waypoint_snapshots=waypoint_snapshots,
        edge_snapshots=edge_snapshots,
        anchors=anchors,
        anchored_world_objects=anchored_world_objects,
    )
