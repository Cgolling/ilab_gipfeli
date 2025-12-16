"""
Tests for SpotController async methods.

These tests demonstrate mocking external dependencies (Boston Dynamics SDK).
Key concepts for new developers:
- @pytest.fixture provides reusable test setup
- unittest.mock.patch replaces real objects with controllable fakes
- AsyncMock handles async function mocking
- @pytest.mark.asyncio marks async test functions

Educational notes:
- Always mock external dependencies (network, hardware, file I/O)
- Test behavior, not implementation details
- One test = one specific behavior
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.spot.spot_controller import SpotController


class TestSpotControllerInit:
    """Tests for SpotController initialization."""

    def test_init_sets_hostname_and_map_path(self):
        """Constructor stores configuration correctly."""
        controller = SpotController("192.168.1.100", "maps/test/")

        assert controller.hostname == "192.168.1.100"
        assert controller.map_path == "maps/test"  # Trailing slash stripped

    def test_init_strips_trailing_slash(self):
        """Map path has trailing slash removed."""
        controller = SpotController("host", "path/to/map/")
        assert controller.map_path == "path/to/map"

    def test_init_all_clients_none(self):
        """Clients are None before connect()."""
        controller = SpotController("host", "path")

        assert controller.robot is None
        assert controller.lease_client is None
        assert controller.graph_nav_client is None
        assert controller.power_client is None

    def test_is_connected_false_initially(self):
        """is_connected returns False before connect()."""
        controller = SpotController("host", "path")
        assert controller.is_connected is False

    def test_power_state_false_initially(self):
        """Power state flags are False initially."""
        controller = SpotController("host", "path")

        assert controller._powered_on is False
        assert controller._started_powered_on is False


class TestSpotControllerIsConnected:
    """Tests for the is_connected property."""

    def test_false_when_not_connected(self):
        """Returns False when _connected is False."""
        controller = SpotController("host", "path")
        controller._connected = False
        controller.robot = MagicMock()

        assert controller.is_connected is False

    def test_false_when_robot_none(self):
        """Returns False when robot is None."""
        controller = SpotController("host", "path")
        controller._connected = True
        controller.robot = None

        assert controller.is_connected is False

    def test_true_when_connected_and_robot_exists(self):
        """Returns True when both conditions met."""
        controller = SpotController("host", "path")
        controller._connected = True
        controller.robot = MagicMock()

        assert controller.is_connected is True


class TestSpotControllerConnect:
    """Tests for the connect() async method."""

    @pytest.fixture
    def controller(self):
        """Create a fresh SpotController for each test."""
        return SpotController("192.168.80.3", "maps/test_map")

    @pytest.fixture
    def mock_all_connect_steps(self):
        """Patch all SDK interactions for connect()."""
        with patch.object(
            SpotController, "_create_sdk_and_authenticate"
        ) as mock_auth, patch.object(
            SpotController, "_acquire_lease"
        ) as mock_lease, patch.object(
            SpotController, "_upload_graph_and_snapshots"
        ) as mock_upload, patch.object(
            SpotController, "_set_initial_localization_fiducial"
        ) as mock_localize:
            yield {
                "auth": mock_auth,
                "lease": mock_lease,
                "upload": mock_upload,
                "localize": mock_localize,
            }

    @pytest.mark.asyncio
    async def test_connect_success_sets_connected_flag(
        self, controller, mock_all_connect_steps, mock_status_callback
    ):
        """Successful connect() sets _connected to True."""
        result = await controller.connect(mock_status_callback)

        assert result is True
        assert controller._connected is True

    @pytest.mark.asyncio
    async def test_connect_calls_all_steps_in_order(
        self, controller, mock_all_connect_steps, mock_status_callback
    ):
        """connect() calls all initialization steps."""
        await controller.connect(mock_status_callback)

        mock_all_connect_steps["auth"].assert_called_once()
        mock_all_connect_steps["lease"].assert_called_once()
        mock_all_connect_steps["upload"].assert_called_once()
        mock_all_connect_steps["localize"].assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_sends_status_updates(
        self, controller, mock_all_connect_steps, mock_status_callback
    ):
        """connect() sends expected status messages."""
        await controller.connect(mock_status_callback)

        # Verify status messages were sent
        assert mock_status_callback.call_count >= 4
        call_args = [call[0][0] for call in mock_status_callback.call_args_list]

        assert any("Connecting" in msg for msg in call_args)
        assert any("Authenticated" in msg for msg in call_args)
        assert any("Lease" in msg for msg in call_args)
        assert any("localized" in msg.lower() for msg in call_args)

    @pytest.mark.asyncio
    async def test_connect_lease_claimed_error_returns_false(
        self, controller, mock_status_callback
    ):
        """ResourceAlreadyClaimedError is handled gracefully."""
        from bosdyn.client.lease import ResourceAlreadyClaimedError

        # Create a mock response object for the exception
        mock_response = MagicMock()
        mock_response.header.error.message = "Lease already claimed by another client"

        with patch.object(
            SpotController, "_create_sdk_and_authenticate"
        ), patch.object(
            SpotController, "_acquire_lease",
            side_effect=ResourceAlreadyClaimedError(mock_response)
        ):
            result = await controller.connect(mock_status_callback)

        assert result is False
        assert controller._connected is False

        # Check error message was sent
        final_msg = mock_status_callback.call_args[0][0]
        assert "Lease already claimed" in final_msg

    @pytest.mark.asyncio
    async def test_connect_generic_error_returns_false(
        self, controller, mock_status_callback
    ):
        """Generic exceptions are handled and return False."""
        with patch.object(
            SpotController, "_create_sdk_and_authenticate",
            side_effect=Exception("Connection refused")
        ):
            result = await controller.connect(mock_status_callback)

        assert result is False
        assert controller._connected is False


class TestSpotControllerNavigateTo:
    """Tests for navigation functionality."""

    @pytest.fixture
    def connected_controller(self, mock_graph):
        """Create a controller in connected state with mocked clients."""
        controller = SpotController("host", "path")
        controller._connected = True
        controller.robot = MagicMock()
        controller.graph_nav_client = MagicMock()
        controller._current_graph = mock_graph
        controller._current_annotation_name_to_wp_id = {}
        return controller

    @pytest.mark.asyncio
    async def test_navigate_not_connected_returns_false(self, mock_status_callback):
        """Navigation fails when not connected."""
        controller = SpotController("host", "path")

        result = await controller.navigate_to("aula", mock_status_callback)

        assert result is False
        msg = mock_status_callback.call_args[0][0]
        assert "Not connected" in msg

    @pytest.mark.asyncio
    async def test_navigate_unknown_location_returns_false(
        self, connected_controller, mock_status_callback
    ):
        """Navigation fails for unknown locations."""
        result = await connected_controller.navigate_to(
            "nonexistent_place", mock_status_callback
        )

        assert result is False
        msg = mock_status_callback.call_args[0][0]
        assert "Unknown location" in msg

    @pytest.mark.asyncio
    async def test_navigate_known_location_attempts_navigation(
        self, connected_controller, mock_status_callback
    ):
        """Known locations trigger navigation attempt."""
        # Mock the power and navigation methods
        with patch.object(
            SpotController, "_toggle_power", return_value=True
        ), patch.object(
            SpotController, "_navigate_to_waypoint_with_heartbeat",
            return_value=True
        ) as mock_nav:
            result = await connected_controller.navigate_to(
                "aula", mock_status_callback
            )

        assert result is True
        mock_nav.assert_called_once()

    @pytest.mark.asyncio
    async def test_navigate_power_failure_returns_false(
        self, connected_controller, mock_status_callback
    ):
        """Navigation fails if power-on fails."""
        with patch.object(
            SpotController, "_toggle_power", return_value=False
        ):
            result = await connected_controller.navigate_to(
                "aula", mock_status_callback
            )

        assert result is False
        msg = mock_status_callback.call_args[0][0]
        assert "power" in msg.lower()
