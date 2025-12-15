"""Tests for map_viewer transformer module (pure functions)."""

import numpy as np
import pytest
from unittest.mock import MagicMock

from bosdyn.client.math_helpers import SE3Pose, Quat

from src.map_viewer.transformer import (
    se3_pose_to_position,
    compute_edge_lines,
    _compute_positions_via_bfs,
)
from src.map_viewer.loader import MapData


class TestSe3PoseToPosition:
    """Tests for se3_pose_to_position function."""

    def test_identity_pose(self):
        """Identity pose should return origin."""
        pose = SE3Pose(x=0, y=0, z=0, rot=Quat(w=1, x=0, y=0, z=0))
        result = se3_pose_to_position(pose)
        assert result == (0.0, 0.0, 0.0)

    def test_translated_pose(self):
        """Translated pose should return correct position."""
        pose = SE3Pose(x=1.5, y=2.5, z=3.5, rot=Quat(w=1, x=0, y=0, z=0))
        result = se3_pose_to_position(pose)
        assert result[0] == pytest.approx(1.5)
        assert result[1] == pytest.approx(2.5)
        assert result[2] == pytest.approx(3.5)

    def test_rotated_pose(self):
        """Rotated pose should still extract correct translation."""
        # 90 degree rotation around Z axis
        pose = SE3Pose(x=5.0, y=-3.0, z=1.0, rot=Quat(w=0.707, x=0, y=0, z=0.707))
        result = se3_pose_to_position(pose)
        assert result[0] == pytest.approx(5.0)
        assert result[1] == pytest.approx(-3.0)
        assert result[2] == pytest.approx(1.0)


class TestComputeEdgeLines:
    """Tests for compute_edge_lines function."""

    def test_empty_graph(self):
        """Empty graph should return empty list."""
        map_data = MagicMock()
        map_data.graph.edges = []

        result = compute_edge_lines(map_data, {})
        assert result == []

    def test_single_edge(self):
        """Single edge with both endpoints should create one line."""
        map_data = MagicMock()
        edge = MagicMock()
        edge.id.from_waypoint = "wp1"
        edge.id.to_waypoint = "wp2"
        map_data.graph.edges = [edge]

        positions = {
            "wp1": (0.0, 0.0, 0.0),
            "wp2": (1.0, 1.0, 0.0),
        }

        result = compute_edge_lines(map_data, positions)
        assert len(result) == 1
        assert result[0] == ((0.0, 0.0, 0.0), (1.0, 1.0, 0.0))

    def test_edge_missing_endpoint(self):
        """Edge with missing endpoint should be skipped."""
        map_data = MagicMock()
        edge = MagicMock()
        edge.id.from_waypoint = "wp1"
        edge.id.to_waypoint = "wp_missing"
        map_data.graph.edges = [edge]

        positions = {"wp1": (0.0, 0.0, 0.0)}

        result = compute_edge_lines(map_data, positions)
        assert result == []

    def test_multiple_edges(self):
        """Multiple edges should create multiple lines."""
        map_data = MagicMock()

        edge1 = MagicMock()
        edge1.id.from_waypoint = "wp1"
        edge1.id.to_waypoint = "wp2"

        edge2 = MagicMock()
        edge2.id.from_waypoint = "wp2"
        edge2.id.to_waypoint = "wp3"

        map_data.graph.edges = [edge1, edge2]

        positions = {
            "wp1": (0.0, 0.0, 0.0),
            "wp2": (1.0, 0.0, 0.0),
            "wp3": (2.0, 0.0, 0.0),
        }

        result = compute_edge_lines(map_data, positions)
        assert len(result) == 2


class TestComputePositionsViaBfs:
    """Tests for BFS-based position computation."""

    def test_empty_graph(self):
        """Empty graph should return empty dict."""
        map_data = MagicMock()
        map_data.graph.waypoints = []
        map_data.graph.edges = []

        result = _compute_positions_via_bfs(map_data)
        assert result == {}

    def test_single_waypoint(self):
        """Single waypoint should be at origin."""
        map_data = MagicMock()
        wp = MagicMock()
        wp.id = "wp1"
        map_data.graph.waypoints = [wp]
        map_data.graph.edges = []

        result = _compute_positions_via_bfs(map_data)
        assert "wp1" in result
        assert result["wp1"] == (0.0, 0.0, 0.0)

    def test_two_connected_waypoints(self):
        """Two connected waypoints should have correct relative positions."""
        map_data = MagicMock()

        wp1 = MagicMock()
        wp1.id = "wp1"
        wp2 = MagicMock()
        wp2.id = "wp2"
        map_data.graph.waypoints = [wp1, wp2]

        # Edge from wp1 to wp2 with 1m translation in X
        edge = MagicMock()
        edge.id.from_waypoint = "wp1"
        edge.id.to_waypoint = "wp2"
        edge.snapshot_id = ""

        # Create a proper SE3Pose protobuf mock
        from bosdyn.api import geometry_pb2
        transform = geometry_pb2.SE3Pose()
        transform.position.x = 1.0
        transform.position.y = 0.0
        transform.position.z = 0.0
        transform.rotation.w = 1.0
        transform.rotation.x = 0.0
        transform.rotation.y = 0.0
        transform.rotation.z = 0.0
        edge.from_tform_to = transform

        map_data.graph.edges = [edge]
        map_data.waypoints = {"wp1": wp1, "wp2": wp2}

        result = _compute_positions_via_bfs(map_data)

        assert "wp1" in result
        assert "wp2" in result
        assert result["wp1"] == (0.0, 0.0, 0.0)
        assert result["wp2"][0] == pytest.approx(1.0, abs=0.01)
        assert result["wp2"][1] == pytest.approx(0.0, abs=0.01)
