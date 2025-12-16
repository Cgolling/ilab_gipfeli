"""
Tests for pure functions in spot_controller.py.

These tests require NO mocking because the functions under test:
- Have no side effects
- Don't depend on external state
- Return deterministic outputs for given inputs

This is the ideal place to start when learning testing!

Educational notes:
- Each test function should test ONE specific behavior
- Test names should describe what is being tested
- Use descriptive assertions with clear expected values
- Arrange-Act-Assert pattern helps structure tests
"""

import pytest
from unittest.mock import MagicMock

from src.spot.spot_controller import (
    id_to_short_code,
    find_unique_waypoint_id,
    update_waypoints_and_edges,
    _resolve_short_code,
    _resolve_annotation_or_raw_id,
)


class TestIdToShortCode:
    """Tests for the id_to_short_code function."""

    def test_valid_waypoint_id_returns_two_letter_code(self):
        """Standard waypoint IDs produce 2-letter codes from first chars."""
        result = id_to_short_code("aula-vast-something-else")
        assert result == "av"

    def test_extracts_first_char_of_first_two_tokens(self):
        """Verify extraction logic: first char of tokens 0 and 1."""
        result = id_to_short_code("Xavier-Yellow-Zebra")
        assert result == "XY"

    def test_three_token_minimum(self):
        """IDs with exactly 3 tokens should work."""
        result = id_to_short_code("one-two-three")
        assert result == "ot"

    def test_two_tokens_returns_none(self):
        """IDs with only 2 tokens return None."""
        assert id_to_short_code("two-parts") is None

    def test_single_token_returns_none(self):
        """IDs with only 1 token return None."""
        assert id_to_short_code("single") is None

    def test_empty_string_returns_none(self):
        """Empty string returns None."""
        assert id_to_short_code("") is None


class TestFindUniqueWaypointId:
    """Tests for the main waypoint ID resolution function."""

    def test_none_graph_returns_none(self):
        """When graph is None, return None with error log."""
        result = find_unique_waypoint_id("al", None, {})
        assert result is None

    def test_routes_two_letter_to_short_code_resolver(self, mock_graph):
        """Two-letter identifiers are routed to short code resolution."""
        # "al" is 2 chars, should search graph
        result = find_unique_waypoint_id("al", mock_graph, {})
        # mock_graph has "aula-lobby-xyz-123" which gives "al"
        assert result == "aula-lobby-xyz-123"

    def test_routes_longer_to_annotation_resolver(self, mock_graph):
        """Longer identifiers are routed to annotation resolution."""
        name_to_id = {"entrance": "aula-lobby-xyz-123"}
        result = find_unique_waypoint_id("entrance", mock_graph, name_to_id)
        assert result == "aula-lobby-xyz-123"


class TestResolveShortCode:
    """Tests for the short code resolver helper."""

    def test_finds_matching_waypoint(self, mock_graph):
        """Short code that matches one waypoint returns its ID."""
        # mock_graph has "aula-lobby-xyz-123" -> short code "al"
        result = _resolve_short_code("al", mock_graph)
        assert result == "aula-lobby-xyz-123"

    def test_finds_another_waypoint(self, mock_graph):
        """Test finding a different waypoint."""
        # mock_graph has "triangle-vast-abc-456" -> short code "tv"
        result = _resolve_short_code("tv", mock_graph)
        assert result == "triangle-vast-abc-456"

    def test_no_match_returns_original(self, mock_graph):
        """Short code with no match returns the original code."""
        result = _resolve_short_code("xx", mock_graph)
        assert result == "xx"

    def test_multiple_matches_returns_original(self):
        """Multiple matches return original to surface ambiguity."""
        # Create graph with duplicate short codes
        graph = MagicMock()
        wp1 = MagicMock()
        wp1.id = "alpha-beta-123"  # short code "ab"
        wp2 = MagicMock()
        wp2.id = "alpha-bravo-456"  # short code "ab" (same!)
        graph.waypoints = [wp1, wp2]

        result = _resolve_short_code("ab", graph)
        assert result == "ab"  # Returns original due to ambiguity


class TestResolveAnnotationOrRawId:
    """Tests for the annotation/raw ID resolver helper."""

    def test_annotation_name_resolves_to_id(self):
        """Annotation names in mapping resolve to waypoint ID."""
        name_to_id = {"aula": "aula-full-waypoint-id"}
        result = _resolve_annotation_or_raw_id("aula", name_to_id)
        assert result == "aula-full-waypoint-id"

    def test_ambiguous_annotation_returns_none(self):
        """Ambiguous annotations (None value) return None."""
        name_to_id = {"duplicate": None}  # None indicates ambiguity
        result = _resolve_annotation_or_raw_id("duplicate", name_to_id)
        assert result is None

    def test_unknown_identifier_returned_as_raw_id(self):
        """Unknown identifiers are assumed to be raw IDs."""
        name_to_id = {"known": "known-id"}
        result = _resolve_annotation_or_raw_id("raw-waypoint-id", name_to_id)
        assert result == "raw-waypoint-id"


class TestUpdateWaypointsAndEdges:
    """Tests for graph processing function."""

    def test_builds_name_to_id_mapping(self, mock_graph):
        """Named waypoints are mapped correctly."""
        name_to_id, _ = update_waypoints_and_edges(mock_graph, "localization-id")

        assert name_to_id["entrance"] == "aula-lobby-xyz-123"
        assert name_to_id["triangle"] == "triangle-vast-abc-456"

    def test_empty_names_excluded(self, mock_graph):
        """Waypoints with empty names are not in the mapping."""
        name_to_id, _ = update_waypoints_and_edges(mock_graph, "localization-id")

        # Empty string should not be a key
        assert "" not in name_to_id

    def test_duplicate_names_map_to_none(self):
        """Duplicate annotation names result in None value."""
        graph = MagicMock()
        wp1 = MagicMock()
        wp1.id = "wp-1"
        wp1.annotations.name = "duplicate"
        wp2 = MagicMock()
        wp2.id = "wp-2"
        wp2.annotations.name = "duplicate"
        graph.waypoints = [wp1, wp2]
        graph.edges = []

        name_to_id, _ = update_waypoints_and_edges(graph, "loc-id")
        assert name_to_id["duplicate"] is None

    def test_builds_edge_mapping(self, mock_graph):
        """Edge connectivity is mapped correctly (reverse lookup)."""
        _, edges = update_waypoints_and_edges(mock_graph, "localization-id")

        # Edge goes from "aula-lobby-xyz-123" to "triangle-vast-abc-456"
        # So edges["triangle-vast-abc-456"] should contain "aula-lobby-xyz-123"
        assert "triangle-vast-abc-456" in edges
        assert "aula-lobby-xyz-123" in edges["triangle-vast-abc-456"]

    def test_empty_graph_returns_empty_dicts(self):
        """Empty graph returns empty mappings."""
        graph = MagicMock()
        graph.waypoints = []
        graph.edges = []

        name_to_id, edges = update_waypoints_and_edges(graph, "loc-id")

        assert name_to_id == {}
        assert edges == {}
