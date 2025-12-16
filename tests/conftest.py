"""
Shared pytest fixtures for ilab_gipfeli tests.

This module provides reusable fixtures that are automatically discovered
by pytest. Fixtures here are available to all test files.

Educational notes for new developers:
- Fixtures are functions that provide test data or set up test state
- @pytest.fixture decorator marks a function as a fixture
- Fixtures can have different scopes: function (default), class, module, session
- Use 'yield' in fixtures for setup/teardown patterns
- Fixtures can depend on other fixtures (dependency injection)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_status_callback() -> AsyncMock:
    """
    Create a mock async callback for status updates.

    This fixture provides a callable that records all status messages
    sent during a test, useful for verifying user feedback behavior.

    Example:
        async def test_connect(mock_status_callback):
            await controller.connect(mock_status_callback)
            # Check what messages were sent
            assert mock_status_callback.call_count > 0
            first_msg = mock_status_callback.call_args_list[0][0][0]
            assert "Connecting" in first_msg
    """
    return AsyncMock()


@pytest.fixture
def sample_waypoints() -> dict[str, str]:
    """
    Provide the standard waypoint mapping for tests.

    Returns the same mapping used in production (WAYPOINTS constant).
    """
    return {
        "aula": "al",
        "triangle": "tv",
        "hauswart": "oh",
        "turnhalle": "cw",
    }


@pytest.fixture
def mock_graph():
    """
    Create a minimal mock GraphNav graph for testing.

    Returns a MagicMock that simulates a map_pb2.Graph with
    waypoints and edges.
    """
    graph = MagicMock()

    # Create mock waypoints
    wp1 = MagicMock()
    wp1.id = "aula-lobby-xyz-123"
    wp1.annotations.name = "entrance"
    wp1.snapshot_id = "snap1"

    wp2 = MagicMock()
    wp2.id = "triangle-vast-abc-456"
    wp2.annotations.name = "triangle"
    wp2.snapshot_id = "snap2"

    wp3 = MagicMock()
    wp3.id = "short"  # ID too short for short_code conversion
    wp3.annotations.name = ""
    wp3.snapshot_id = "snap3"

    graph.waypoints = [wp1, wp2, wp3]

    # Create mock edges
    edge1 = MagicMock()
    edge1.id.from_waypoint = "aula-lobby-xyz-123"
    edge1.id.to_waypoint = "triangle-vast-abc-456"
    edge1.snapshot_id = "edge_snap1"

    graph.edges = [edge1]
    graph.anchoring.anchors = []

    return graph


@pytest.fixture
def mock_telegram_update():
    """
    Create a mock Telegram Update object.

    Returns a MagicMock that simulates an incoming Telegram update
    with user information and message capabilities.
    """
    update = MagicMock()
    update.effective_user.mention_html.return_value = "<b>TestUser</b>"
    update.effective_user.id = 12345
    update.message.reply_text = AsyncMock()
    update.message.reply_html = AsyncMock()
    update.message.text = "test message"
    return update


@pytest.fixture
def mock_telegram_context():
    """
    Create a mock Telegram Context object.

    Returns a MagicMock that simulates the context passed to
    Telegram command handlers.
    """
    return MagicMock()


@pytest.fixture
def mock_callback_query():
    """
    Create a mock Telegram callback query for inline button presses.

    Returns a MagicMock that simulates a callback query with
    answer and edit capabilities.
    """
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.data = "goto_aula"
    return query
