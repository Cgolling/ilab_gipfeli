"""Coordinate transforms for GraphNav map visualization."""

import logging
from collections import deque
from typing import TYPE_CHECKING

import numpy as np
from bosdyn.client.frame_helpers import ODOM_FRAME_NAME, get_a_tform_b
from bosdyn.client.math_helpers import SE3Pose

if TYPE_CHECKING:
    from src.map_viewer.loader import MapData

logger = logging.getLogger(__name__)


def se3_pose_to_position(se3_pose: SE3Pose) -> tuple[float, float, float]:
    """
    Extract (x, y, z) position from an SE3Pose.

    Args:
        se3_pose: Boston Dynamics SE3Pose object

    Returns:
        Tuple of (x, y, z) coordinates
    """
    mat = se3_pose.to_matrix()
    return (float(mat[0, 3]), float(mat[1, 3]), float(mat[2, 3]))


def compute_waypoint_positions(
    map_data: "MapData",
    use_anchoring: bool = True,
) -> dict[str, tuple[float, float, float]]:
    """
    Compute world positions for all waypoints.

    Args:
        map_data: Loaded map data
        use_anchoring: If True and anchors exist, use seed frame positions.
            Otherwise, use BFS traversal from first waypoint.

    Returns:
        Dict mapping waypoint_id to (x, y, z) position
    """
    # Try anchoring first if requested
    if use_anchoring and map_data.anchors:
        return _compute_positions_from_anchors(map_data)

    # Fall back to BFS traversal
    return _compute_positions_via_bfs(map_data)


def _compute_positions_from_anchors(
    map_data: "MapData",
) -> dict[str, tuple[float, float, float]]:
    """Compute positions using anchor seed frame transforms."""
    positions: dict[str, tuple[float, float, float]] = {}

    for waypoint in map_data.graph.waypoints:
        if waypoint.id not in map_data.anchors:
            continue

        anchor = map_data.anchors[waypoint.id]
        se3_pose = SE3Pose.from_proto(anchor.seed_tform_waypoint)
        positions[waypoint.id] = se3_pose_to_position(se3_pose)

    return positions


def _compute_positions_via_bfs(
    map_data: "MapData",
) -> dict[str, tuple[float, float, float]]:
    """
    Compute positions via BFS traversal from first waypoint.

    GraphNav graphs have no global reference frame. This places the first
    waypoint at the origin and computes all other positions by traversing
    edges and concatenating transforms.
    """
    if not map_data.graph.waypoints:
        return {}

    positions: dict[str, tuple[float, float, float]] = {}
    visited: set[str] = set()

    # Start BFS from first waypoint at origin
    first_waypoint = map_data.graph.waypoints[0]
    queue: deque[tuple[str, np.ndarray]] = deque()
    queue.append((first_waypoint.id, np.eye(4)))

    # Build edge lookup for efficient traversal
    edges_from: dict[str, list] = {}
    edges_to: dict[str, list] = {}
    for edge in map_data.graph.edges:
        from_id = edge.id.from_waypoint
        to_id = edge.id.to_waypoint
        if from_id not in edges_from:
            edges_from[from_id] = []
        if to_id not in edges_to:
            edges_to[to_id] = []
        edges_from[from_id].append(edge)
        edges_to[to_id].append(edge)

    while queue:
        waypoint_id, world_tform_waypoint = queue.popleft()

        if waypoint_id in visited:
            continue
        visited.add(waypoint_id)

        # Extract position from transform matrix
        positions[waypoint_id] = (
            float(world_tform_waypoint[0, 3]),
            float(world_tform_waypoint[1, 3]),
            float(world_tform_waypoint[2, 3]),
        )

        # Traverse outgoing edges (from this waypoint)
        for edge in edges_from.get(waypoint_id, []):
            neighbor_id = edge.id.to_waypoint
            if neighbor_id in visited:
                continue

            # Transform: world -> current -> neighbor
            current_tform_neighbor = SE3Pose.from_proto(edge.from_tform_to).to_matrix()
            world_tform_neighbor = np.dot(world_tform_waypoint, current_tform_neighbor)
            queue.append((neighbor_id, world_tform_neighbor))

        # Traverse incoming edges (to this waypoint)
        for edge in edges_to.get(waypoint_id, []):
            neighbor_id = edge.id.from_waypoint
            if neighbor_id in visited:
                continue

            # Transform: world -> current -> neighbor (inverse of edge transform)
            neighbor_tform_current = SE3Pose.from_proto(edge.from_tform_to).to_matrix()
            current_tform_neighbor = np.linalg.inv(neighbor_tform_current)
            world_tform_neighbor = np.dot(world_tform_waypoint, current_tform_neighbor)
            queue.append((neighbor_id, world_tform_neighbor))

    return positions


def compute_edge_lines(
    map_data: "MapData",
    positions: dict[str, tuple[float, float, float]],
) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    """
    Compute edge line segments for visualization.

    Args:
        map_data: Loaded map data
        positions: Dict mapping waypoint_id to (x, y, z) position

    Returns:
        List of (start_pos, end_pos) tuples for each edge
    """
    edges: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []

    for edge in map_data.graph.edges:
        from_id = edge.id.from_waypoint
        to_id = edge.id.to_waypoint

        if from_id not in positions or to_id not in positions:
            continue

        edges.append((positions[from_id], positions[to_id]))

    return edges


def compute_fiducial_positions(
    map_data: "MapData",
    waypoint_positions: dict[str, tuple[float, float, float]],
) -> dict[str, tuple[float, float, float]]:
    """
    Compute fiducial (AprilTag) positions in world frame.

    Args:
        map_data: Loaded map data
        waypoint_positions: Dict mapping waypoint_id to position

    Returns:
        Dict mapping fiducial_id to (x, y, z) position
    """
    fiducial_positions: dict[str, tuple[float, float, float]] = {}

    # For anchored fiducials, use the seed frame directly
    for fiducial_id, data in map_data.anchored_world_objects.items():
        if len(data) >= 1:
            anchored_wo = data[0]
            if hasattr(anchored_wo, "seed_tform_object"):
                se3_pose = SE3Pose.from_proto(anchored_wo.seed_tform_object)
                fiducial_positions[fiducial_id] = se3_pose_to_position(se3_pose)

    return fiducial_positions


def compute_point_clouds(
    map_data: "MapData",
    use_anchoring: bool = True,
    max_points_per_waypoint: int = 500,
    max_total_points: int = 50000,
) -> np.ndarray:
    """
    Extract and transform point cloud data from waypoint snapshots.

    Point clouds are visual feature clouds captured at each waypoint.
    Points are transformed to world coordinates and sampled for performance.

    Args:
        map_data: Loaded map data
        use_anchoring: Use anchor transforms if available
        max_points_per_waypoint: Max points to sample from each waypoint
        max_total_points: Max total points to return

    Returns:
        Nx4 numpy array of (x, y, z, height) where height is used for coloring
    """
    all_points: list[np.ndarray] = []

    # Get world transforms for waypoints
    if use_anchoring and map_data.anchors:
        waypoint_transforms = _compute_transforms_from_anchors(map_data)
    else:
        waypoint_transforms = _compute_transforms_via_bfs(map_data)

    for waypoint_id, world_tform_waypoint in waypoint_transforms.items():
        waypoint = map_data.waypoints.get(waypoint_id)
        if waypoint is None:
            continue

        snapshot = map_data.waypoint_snapshots.get(waypoint.snapshot_id)
        if snapshot is None:
            continue

        if not hasattr(snapshot, "point_cloud") or snapshot.point_cloud.num_points == 0:
            continue

        cloud = snapshot.point_cloud
        try:
            # Parse raw point data
            points = np.frombuffer(cloud.data, dtype=np.float32).reshape(
                int(cloud.num_points), 3
            )

            # Get transform from waypoint to point cloud sensor
            odom_tform_cloud = get_a_tform_b(
                cloud.source.transforms_snapshot,
                ODOM_FRAME_NAME,
                cloud.source.frame_name_sensor,
            )
            waypoint_tform_odom = SE3Pose.from_proto(waypoint.waypoint_tform_ko)
            waypoint_tform_cloud = (waypoint_tform_odom * odom_tform_cloud).to_matrix()

            # Transform points: world <- waypoint <- cloud
            world_tform_cloud = np.dot(world_tform_waypoint, waypoint_tform_cloud)

            # Apply transform to points (homogeneous coordinates)
            ones = np.ones((points.shape[0], 1), dtype=np.float32)
            points_homogeneous = np.hstack([points, ones])
            world_points = np.dot(points_homogeneous, world_tform_cloud.T)[:, :3]

            # Sample points if too many
            if len(world_points) > max_points_per_waypoint:
                indices = np.random.choice(
                    len(world_points), max_points_per_waypoint, replace=False
                )
                world_points = world_points[indices]

            # Add height (z) as 4th column for coloring
            heights = world_points[:, 2:3]
            points_with_height = np.hstack([world_points, heights])
            all_points.append(points_with_height)

        except Exception as e:
            logger.debug(f"Failed to process point cloud for {waypoint_id}: {e}")
            continue

    if not all_points:
        return np.array([]).reshape(0, 4)

    combined = np.vstack(all_points)

    # Sample if exceeds total limit
    if len(combined) > max_total_points:
        indices = np.random.choice(len(combined), max_total_points, replace=False)
        combined = combined[indices]

    logger.info(f"Extracted {len(combined):,} point cloud points")
    return combined


def _compute_transforms_from_anchors(
    map_data: "MapData",
) -> dict[str, np.ndarray]:
    """Compute 4x4 transform matrices using anchor seed frame."""
    transforms: dict[str, np.ndarray] = {}

    for waypoint in map_data.graph.waypoints:
        if waypoint.id not in map_data.anchors:
            continue

        anchor = map_data.anchors[waypoint.id]
        se3_pose = SE3Pose.from_proto(anchor.seed_tform_waypoint)
        transforms[waypoint.id] = se3_pose.to_matrix()

    return transforms


def _compute_transforms_via_bfs(
    map_data: "MapData",
) -> dict[str, np.ndarray]:
    """Compute 4x4 transform matrices via BFS traversal."""
    if not map_data.graph.waypoints:
        return {}

    transforms: dict[str, np.ndarray] = {}
    visited: set[str] = set()

    first_waypoint = map_data.graph.waypoints[0]
    queue: deque[tuple[str, np.ndarray]] = deque()
    queue.append((first_waypoint.id, np.eye(4)))

    edges_from: dict[str, list] = {}
    edges_to: dict[str, list] = {}
    for edge in map_data.graph.edges:
        from_id = edge.id.from_waypoint
        to_id = edge.id.to_waypoint
        if from_id not in edges_from:
            edges_from[from_id] = []
        if to_id not in edges_to:
            edges_to[to_id] = []
        edges_from[from_id].append(edge)
        edges_to[to_id].append(edge)

    while queue:
        waypoint_id, world_tform_waypoint = queue.popleft()

        if waypoint_id in visited:
            continue
        visited.add(waypoint_id)
        transforms[waypoint_id] = world_tform_waypoint

        for edge in edges_from.get(waypoint_id, []):
            neighbor_id = edge.id.to_waypoint
            if neighbor_id in visited:
                continue
            current_tform_neighbor = SE3Pose.from_proto(edge.from_tform_to).to_matrix()
            world_tform_neighbor = np.dot(world_tform_waypoint, current_tform_neighbor)
            queue.append((neighbor_id, world_tform_neighbor))

        for edge in edges_to.get(waypoint_id, []):
            neighbor_id = edge.id.from_waypoint
            if neighbor_id in visited:
                continue
            neighbor_tform_current = SE3Pose.from_proto(edge.from_tform_to).to_matrix()
            current_tform_neighbor = np.linalg.inv(neighbor_tform_current)
            world_tform_neighbor = np.dot(world_tform_waypoint, current_tform_neighbor)
            queue.append((neighbor_id, world_tform_neighbor))

    return transforms
