"""SPOT Robot Controller for Telegram Bot integration."""

import asyncio
import logging
import os
import time
from typing import Awaitable, Callable, Optional

from src.logging_config import setup_logging

# Initialize logging (safe to call multiple times)
setup_logging()

import bosdyn.client
import bosdyn.client.util
from bosdyn.api import robot_state_pb2
from bosdyn.api.graph_nav import graph_nav_pb2, map_pb2, nav_pb2
from bosdyn.client.exceptions import ResponseError
from bosdyn.client.frame_helpers import get_odom_tform_body
from bosdyn.client.graph_nav import GraphNavClient
from bosdyn.client.lease import LeaseClient, LeaseKeepAlive, ResourceAlreadyClaimedError
from bosdyn.client.power import PowerClient, power_on_motors, safe_power_off_motors
from bosdyn.client.robot_command import RobotCommandBuilder, RobotCommandClient
from bosdyn.client.robot_state import RobotStateClient

logger = logging.getLogger(__name__)

# Waypoint mapping: location name -> waypoint short code
WAYPOINTS = {
    "aula": "al",
    "triangle": "tv",
    "hauswart": "oh",
    "turnhalle": "cw",
}

# Timing constants (seconds)
HEARTBEAT_INTERVAL_SECONDS = 3       # How often to send status updates during navigation
NAVIGATION_VELOCITY_LIMIT = 1.0      # Max velocity limit passed to navigate_to (m/s)
NAVIGATION_POLL_INTERVAL = 0.5       # How often to poll navigation status
POWER_STATE_POLL_INTERVAL = 0.25     # How often to poll power state during power-on


def id_to_short_code(waypoint_id: str) -> Optional[str]:
    """
    Convert a waypoint ID to a 2-letter short code.

    Short codes are derived from the first character of the first two
    hyphen-separated tokens in the waypoint ID.

    Args:
        waypoint_id: Full waypoint ID (e.g., "aula-vast-xyz-123")

    Returns:
        Two-letter short code (e.g., "av"), or None if the ID has
        fewer than 3 hyphen-separated tokens.

    Example:
        >>> id_to_short_code("aula-vast-xyz-123")
        "av"
        >>> id_to_short_code("short")
        None
    """
    tokens = waypoint_id.split('-')
    if len(tokens) > 2:
        return f'{tokens[0][0]}{tokens[1][0]}'
    return None


def find_unique_waypoint_id(identifier: str, graph, name_to_id: dict) -> Optional[str]:
    """
    Resolve a waypoint identifier to its full unique ID.

    This function handles three types of identifiers:
    1. Two-letter short codes (e.g., "al") - derived from waypoint IDs
    2. Annotation names (e.g., "aula") - human-readable names from map recording
    3. Full waypoint IDs - returned as-is if not matching the above

    Args:
        identifier: A short code, annotation name, or full waypoint ID
        graph: The loaded GraphNav graph (map_pb2.Graph)
        name_to_id: Mapping of annotation names to waypoint IDs.
            Value is None if the name is ambiguous (used by multiple waypoints).

    Returns:
        The full waypoint ID string, or None if:
        - Graph is not loaded
        - Annotation name is ambiguous (maps to multiple waypoints)
    """
    if graph is None:
        logger.error("Graph not loaded. Cannot find waypoint.")
        return None

    # Route to appropriate resolver based on identifier length
    if len(identifier) == 2:
        return _resolve_short_code(identifier, graph)
    return _resolve_annotation_or_raw_id(identifier, name_to_id)


def _resolve_short_code(short_code: str, graph) -> str:
    """
    Resolve a 2-letter short code to a full waypoint ID.

    Searches all waypoints in the graph for one whose ID produces
    the given short code.

    Args:
        short_code: Two-letter code to resolve
        graph: The loaded GraphNav graph

    Returns:
        The full waypoint ID if exactly one match is found.
        The original short_code if no match or multiple matches
        (caller should handle as navigation error).
    """
    matched_id = None
    for waypoint in graph.waypoints:
        if short_code == id_to_short_code(waypoint.id):
            if matched_id is not None:
                # Multiple matches found - return original to surface ambiguity
                logger.warning(f"Short code '{short_code}' matches multiple waypoints")
                return short_code
            matched_id = waypoint.id

    return matched_id if matched_id else short_code


def _resolve_annotation_or_raw_id(identifier: str, name_to_id: dict) -> Optional[str]:
    """
    Resolve an annotation name from the mapping, or return as raw ID.

    Args:
        identifier: Annotation name or full waypoint ID
        name_to_id: Mapping of annotation names to waypoint IDs

    Returns:
        The resolved waypoint ID, or None if the annotation is ambiguous.
        If identifier is not in the mapping, it's assumed to be a raw
        waypoint ID and returned as-is.
    """
    if identifier in name_to_id:
        waypoint_id = name_to_id[identifier]
        if waypoint_id is None:
            logger.error(f"Waypoint name '{identifier}' is ambiguous (maps to multiple waypoints).")
            return None
        return waypoint_id
    # Assume it's already a full waypoint ID
    return identifier


def update_waypoints_and_edges(graph, localization_id: str) -> tuple[dict, dict]:
    """
    Build mappings from graph for waypoint name lookup and edge connectivity.

    Args:
        graph: The loaded GraphNav graph (map_pb2.Graph)
        localization_id: Current localization waypoint ID (not used but kept for API)

    Returns:
        Tuple of (name_to_id, edges) where:
        - name_to_id: Dict mapping annotation names to waypoint IDs.
          Value is None if the name appears on multiple waypoints (ambiguous).
        - edges: Dict mapping destination waypoint IDs to lists of source
          waypoint IDs (reverse edge lookup for pathfinding).
    """
    name_to_id = {}
    edges = {}

    for waypoint in graph.waypoints:
        waypoint_name = waypoint.annotations.name
        if waypoint_name:
            if waypoint_name in name_to_id:
                name_to_id[waypoint_name] = None  # Duplicate name
            else:
                name_to_id[waypoint_name] = waypoint.id

    for edge in graph.edges:
        if edge.id.to_waypoint in edges:
            if edge.id.from_waypoint not in edges[edge.id.to_waypoint]:
                edges[edge.id.to_waypoint].append(edge.id.from_waypoint)
        else:
            edges[edge.id.to_waypoint] = [edge.id.from_waypoint]

    return name_to_id, edges


class SpotController:
    """Controller for Boston Dynamics SPOT robot using GraphNav."""

    def __init__(self, hostname: str, map_path: str) -> None:
        """
        Initialize a SpotController for robot navigation.

        Args:
            hostname: IP address or hostname of the SPOT robot
                (e.g., "192.168.80.3")
            map_path: Path to the GraphNav map directory containing
                'graph', 'waypoint_snapshots/', and 'edge_snapshots/'

        Attributes:
            robot: Boston Dynamics Robot client (None until connect())
            lease_client: Client for lease management
            lease_keepalive: Automatic lease renewal handler
            graph_nav_client: Client for GraphNav operations
            robot_command_client: Client for robot commands
            robot_state_client: Client for querying robot state
            power_client: Client for power management

        Example:
            controller = SpotController("192.168.80.3", "maps/school_v1")
            await controller.connect(status_callback)
            await controller.navigate_to("aula", status_callback)
        """
        self.hostname = hostname
        self.map_path = map_path.rstrip('/')

        # Robot clients (initialized on connect)
        self.robot = None
        self.lease_client = None
        self.lease_keepalive = None
        self.graph_nav_client = None
        self.robot_command_client = None
        self.robot_state_client = None
        self.power_client = None

        # Graph state
        self._current_graph = None
        self._current_waypoint_snapshots = {}
        self._current_edge_snapshots = {}
        self._current_annotation_name_to_wp_id = {}
        self._current_edges = {}

        # Power state tracking:
        # - _powered_on: Current motor power state, updated by _check_is_powered_on()
        # - _started_powered_on: Captured at connection time. If we powered on the
        #   robot during navigation, we power it off after. If it was already on
        #   (e.g., user had it standing), we leave it on. This prevents unexpected
        #   sit-downs.
        self._powered_on = False
        self._started_powered_on = False

        # Connection state
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Check if connected to SPOT."""
        return self._connected and self.robot is not None

    def get_status(self) -> dict:
        """
        Get current status of the robot connection and state.

        Returns:
            Dictionary with status information:
            - connected: bool
            - hostname: str
            - powered_on: bool or None if not connected
            - battery_percent: float or None
            - lease_owner: str or None
        """
        status = {
            "connected": self._connected,
            "hostname": self.hostname,
            "powered_on": None,
            "battery_percent": None,
            "lease_owner": None,
            "estop_status": None,
        }

        if not self.robot:
            return status

        try:
            # Get robot state
            if self.robot_state_client:
                robot_state = self.robot_state_client.get_robot_state()
                power_state = robot_state.power_state
                status["powered_on"] = (
                    power_state.motor_power_state == robot_state_pb2.PowerState.STATE_ON
                )

                # Battery info
                for battery in robot_state.battery_states:
                    status["battery_percent"] = battery.charge_percentage.value

                # E-stop status
                for estop in robot_state.estop_states:
                    if estop.state != robot_state_pb2.EStopState.STATE_ESTOPPED:
                        status["estop_status"] = "OK"
                    else:
                        status["estop_status"] = "ESTOPPED"
                        break

            # Get lease info
            if self.lease_client:
                lease_info = self.lease_client.list_leases()
                for resource in lease_info:
                    if resource.resource == "body":
                        if resource.lease_owner.client_name:
                            status["lease_owner"] = resource.lease_owner.client_name

        except Exception as e:
            logger.debug(f"Error getting status: {e}")

        return status

    async def connect(
        self,
        status_callback: Callable[[str], Awaitable[None]],
        force_acquire: bool = False
    ) -> bool:
        """
        Connect to SPOT and initialize for navigation.

        Args:
            status_callback: Async function to report status updates
            force_acquire: If True, forcefully take the lease from any other client.
                Use with caution - this will disconnect tablet or other controllers!

        Returns:
            True if connection successful, False otherwise
        """
        try:
            # Step 1: Create SDK and authenticate
            await status_callback("Connecting to SPOT...")
            await asyncio.to_thread(self._create_sdk_and_authenticate)
            await status_callback("Authenticated with SPOT")

            # Step 2: Acquire lease
            if force_acquire:
                await status_callback("Force-acquiring lease (taking control)...")
                await asyncio.to_thread(self._force_acquire_lease)
                logger.warning("Lease force-acquired - other clients disconnected")
            else:
                await status_callback("Acquiring lease...")
                await asyncio.to_thread(self._acquire_lease)
            logger.info("Lease acquired successfully, keepalive started")
            await status_callback("Lease acquired")

            # Step 3: Upload map
            await status_callback("Uploading map...")
            await asyncio.to_thread(self._upload_graph_and_snapshots)
            await status_callback("Map uploaded")

            # Step 4: Localize to fiducial
            await status_callback("Localizing robot (look for a fiducial)...")
            await asyncio.to_thread(self._set_initial_localization_fiducial)
            await status_callback("Robot localized successfully!")

            self._connected = True
            return True

        except ResourceAlreadyClaimedError as e:
            logger.error(f"Lease already claimed: {e}")
            await status_callback(
                "Lease already claimed by another client!\n\n"
                "Options:\n"
                "1. Use /disconnect on the other client\n"
                "2. Release control from the tablet\n"
                "3. Use /forceconnect to take over (use with caution!)"
            )
            return False
        except ConnectionRefusedError:
            logger.error(f"Connection refused to {self.hostname}")
            await status_callback(
                f"Cannot reach SPOT at {self.hostname}\n\n"
                "Check:\n"
                "1. Is the robot powered on?\n"
                "2. Is the IP address correct?\n"
                "3. Are you on the robot's network?"
            )
            return False
        except Exception as e:
            logger.exception("Failed to connect to SPOT")
            await status_callback(f"Connection failed: {e}")
            return False

    def _create_sdk_and_authenticate(self):
        """Create SDK and authenticate with the robot."""
        sdk = bosdyn.client.create_standard_sdk('TelegramSpotClient')
        self.robot = sdk.create_robot(self.hostname)

        # Authenticate using environment variables
        username = os.getenv("BOSDYN_CLIENT_USERNAME")
        password = os.getenv("BOSDYN_CLIENT_PASSWORD")
        if username and password:
            self.robot.authenticate(username, password)
        else:
            bosdyn.client.util.authenticate(self.robot)

        # Force trigger timesync
        self.robot.time_sync.wait_for_sync()

        # Create clients
        self.robot_command_client = self.robot.ensure_client(
            RobotCommandClient.default_service_name)
        self.robot_state_client = self.robot.ensure_client(
            RobotStateClient.default_service_name)
        self.graph_nav_client = self.robot.ensure_client(
            GraphNavClient.default_service_name)
        self.power_client = self.robot.ensure_client(
            PowerClient.default_service_name)

        # Check initial power state
        power_state = self.robot_state_client.get_robot_state().power_state
        self._started_powered_on = (power_state.motor_power_state == power_state.STATE_ON)
        self._powered_on = self._started_powered_on
        logger.info(f"Initial power state: motors_on={self._started_powered_on}")

    def _acquire_lease(self) -> None:
        """
        Acquire exclusive control lease for the SPOT robot.

        Creates a LeaseKeepAlive that automatically maintains the lease
        with periodic heartbeats. The lease is returned when the
        keepalive is shutdown or the program exits (return_at_exit=True).

        Raises:
            ResourceAlreadyClaimedError: If another client (e.g., tablet
                controller, another script) already holds the lease.
                Check for tablet connections if this fails.

        Note:
            Only one client can hold the robot lease at a time. The lease
            grants exclusive control over the robot's movement and power.
        """
        self.lease_client = self.robot.ensure_client(LeaseClient.default_service_name)
        self.lease_keepalive = LeaseKeepAlive(
            self.lease_client, must_acquire=True, return_at_exit=True
        )

    def _force_acquire_lease(self) -> None:
        """
        Forcefully acquire the lease, taking it from any other client.

        This will disconnect any other client (tablet, other script) that
        currently holds the lease. Use with caution!

        Note:
            This uses take() instead of acquire(), which doesn't fail if
            the lease is already held by another client.
        """
        self.lease_client = self.robot.ensure_client(LeaseClient.default_service_name)

        # First, try to return any existing lease we might have
        try:
            self.lease_client.return_lease(self.lease_client.lease_wallet.get_lease())
        except Exception:
            pass  # No lease to return, that's fine

        # Take the lease forcefully
        self.lease_keepalive = LeaseKeepAlive(
            self.lease_client,
            must_acquire=True,
            return_at_exit=True,
        )
        # The LeaseKeepAlive with must_acquire=True will take the lease
        # We need to explicitly take it first
        self.lease_client.take()
        logger.warning("Forcefully took lease from previous owner")

    def _upload_graph_and_snapshots(self):
        """Upload the graph and snapshots to the robot."""
        logger.info(f"Loading graph from {self.map_path}")

        # Load graph from disk
        with open(f'{self.map_path}/graph', 'rb') as graph_file:
            data = graph_file.read()
            self._current_graph = map_pb2.Graph()
            self._current_graph.ParseFromString(data)
            logger.info(
                f"Loaded graph has {len(self._current_graph.waypoints)} waypoints "
                f"and {len(self._current_graph.edges)} edges"
            )

        # Load waypoint snapshots
        for waypoint in self._current_graph.waypoints:
            snapshot_path = f'{self.map_path}/waypoint_snapshots/{waypoint.snapshot_id}'
            with open(snapshot_path, 'rb') as snapshot_file:
                waypoint_snapshot = map_pb2.WaypointSnapshot()
                waypoint_snapshot.ParseFromString(snapshot_file.read())
                self._current_waypoint_snapshots[waypoint_snapshot.id] = waypoint_snapshot

        # Load edge snapshots
        for edge in self._current_graph.edges:
            if len(edge.snapshot_id) == 0:
                continue
            snapshot_path = f'{self.map_path}/edge_snapshots/{edge.snapshot_id}'
            with open(snapshot_path, 'rb') as snapshot_file:
                edge_snapshot = map_pb2.EdgeSnapshot()
                edge_snapshot.ParseFromString(snapshot_file.read())
                self._current_edge_snapshots[edge_snapshot.id] = edge_snapshot

        # Upload graph to robot
        logger.info("Uploading graph to robot...")
        true_if_empty = not len(self._current_graph.anchoring.anchors)
        response = self.graph_nav_client.upload_graph(
            graph=self._current_graph,
            generate_new_anchoring=true_if_empty
        )

        # Upload waypoint snapshots
        for snapshot_id in response.unknown_waypoint_snapshot_ids:
            waypoint_snapshot = self._current_waypoint_snapshots[snapshot_id]
            self.graph_nav_client.upload_waypoint_snapshot(waypoint_snapshot)
            logger.debug(f"Uploaded waypoint snapshot {snapshot_id}")

        # Upload edge snapshots
        for snapshot_id in response.unknown_edge_snapshot_ids:
            edge_snapshot = self._current_edge_snapshots[snapshot_id]
            self.graph_nav_client.upload_edge_snapshot(edge_snapshot)
            logger.debug(f"Uploaded edge snapshot {snapshot_id}")

        # Update waypoint name to id mapping
        localization_id = self.graph_nav_client.get_localization_state().localization.waypoint_id
        self._current_annotation_name_to_wp_id, self._current_edges = update_waypoints_and_edges(
            self._current_graph, localization_id
        )

    def _set_initial_localization_fiducial(self):
        """Trigger localization based on nearest fiducial."""
        robot_state = self.robot_state_client.get_robot_state()
        current_odom_tform_body = get_odom_tform_body(
            robot_state.kinematic_state.transforms_snapshot
        ).to_proto()

        # Create empty localization to request fiducial-based localization
        localization = nav_pb2.Localization()
        self.graph_nav_client.set_localization(
            initial_guess_localization=localization,
            ko_tform_body=current_odom_tform_body
        )

    async def navigate_to(
        self,
        location: str,
        status_callback: Callable[[str], Awaitable[None]]
    ) -> bool:
        """
        Navigate to a named location.

        Args:
            location: Location name (e.g., "aula", "triangle")
            status_callback: Async function to report status updates

        Returns:
            True if navigation successful, False otherwise
        """
        if not self.is_connected:
            await status_callback("Not connected to SPOT")
            return False

        # Get waypoint short code
        short_code = WAYPOINTS.get(location.lower())
        if not short_code:
            await status_callback(f"Unknown location: {location}")
            return False

        try:
            # Find the full waypoint ID
            destination_waypoint = await asyncio.to_thread(
                find_unique_waypoint_id,
                short_code,
                self._current_graph,
                self._current_annotation_name_to_wp_id
            )

            if not destination_waypoint:
                await status_callback(f"Could not find waypoint for {location}")
                return False

            # Power on if needed
            await status_callback(f"Powering on robot...")
            powered_on = await asyncio.to_thread(self._toggle_power, True)
            if not powered_on:
                logger.error("Failed to power on robot motors")
                await status_callback("Failed to power on robot")
                return False

            # Navigate with heartbeat updates
            logger.info(f"Starting navigation to {location} (waypoint: {destination_waypoint})")
            await status_callback(f"Navigating to {location.title()}...")
            success = await self._navigate_to_waypoint_with_heartbeat(
                destination_waypoint,
                location,
                status_callback
            )

            # Power off if we powered it on
            if self._powered_on and not self._started_powered_on:
                await asyncio.to_thread(self._toggle_power, False)

            return success

        except Exception as e:
            logger.exception(f"Navigation failed: {e}")
            await status_callback(f"Navigation error: {e}")
            return False

    async def _navigate_to_waypoint_with_heartbeat(
        self,
        destination_waypoint: str,
        location: str,
        status_callback: Callable[[str], Awaitable[None]]
    ) -> bool:
        """Navigate to waypoint with periodic status updates."""
        nav_to_cmd_id = None
        start_time = time.time()
        last_update = 0

        while True:
            elapsed = int(time.time() - start_time)

            # Send heartbeat update every HEARTBEAT_INTERVAL_SECONDS
            if elapsed - last_update >= HEARTBEAT_INTERVAL_SECONDS:
                logger.debug(f"Navigation heartbeat: {location} ({elapsed}s elapsed)")
                await status_callback(f"Navigating to {location.title()}... ({elapsed}s)")
                last_update = elapsed

            try:
                # Issue navigation command
                nav_to_cmd_id = await asyncio.to_thread(
                    self.graph_nav_client.navigate_to,
                    destination_waypoint,
                    NAVIGATION_VELOCITY_LIMIT,
                    command_id=nav_to_cmd_id
                )
            except ResponseError as e:
                logger.error(f"Navigation error: {e}")
                return False

            # Check if navigation is complete
            is_finished, status_msg = await asyncio.to_thread(
                self._check_success, nav_to_cmd_id
            )

            if is_finished:
                if status_msg:
                    await status_callback(status_msg)
                return status_msg is None  # None means success

            await asyncio.sleep(NAVIGATION_POLL_INTERVAL)

    def _toggle_power(self, should_power_on: bool) -> bool:
        """Power the robot on/off."""
        is_powered_on = self._check_is_powered_on()
        logger.debug(f"Power toggle: current={is_powered_on}, target={should_power_on}")

        if not is_powered_on and should_power_on:
            logger.info("Powering on motors...")
            power_on_motors(self.power_client)
            # Wait for motors to power on
            start_time = time.time()
            while True:
                state = self.robot_state_client.get_robot_state()
                if state.power_state.motor_power_state == robot_state_pb2.PowerState.STATE_ON:
                    logger.info(f"Motors powered on in {time.time() - start_time:.2f}s")
                    break
                time.sleep(POWER_STATE_POLL_INTERVAL)

        elif is_powered_on and not should_power_on:
            logger.info("Powering off motors...")
            safe_power_off_motors(self.robot_command_client, self.robot_state_client)
            logger.info("Motors powered off")

        self._check_is_powered_on()
        return self._powered_on

    def _check_is_powered_on(self) -> bool:
        """Check if robot motors are powered on."""
        power_state = self.robot_state_client.get_robot_state().power_state
        self._powered_on = (power_state.motor_power_state == power_state.STATE_ON)
        return self._powered_on

    def _check_success(self, command_id) -> tuple[bool, Optional[str]]:
        """
        Check navigation command status.

        Returns:
            Tuple of (is_finished, error_message). error_message is None on success.
        """
        if command_id == -1:
            return False, None

        status = self.graph_nav_client.navigation_feedback(command_id)

        if status.status == graph_nav_pb2.NavigationFeedbackResponse.STATUS_REACHED_GOAL:
            logger.info("Navigation completed: reached goal")
            return True, None  # Success
        elif status.status == graph_nav_pb2.NavigationFeedbackResponse.STATUS_LOST:
            logger.warning("Navigation failed: robot got lost")
            return True, "Robot got lost during navigation"
        elif status.status == graph_nav_pb2.NavigationFeedbackResponse.STATUS_STUCK:
            logger.warning("Navigation failed: robot got stuck")
            return True, "Robot got stuck during navigation"
        elif status.status == graph_nav_pb2.NavigationFeedbackResponse.STATUS_ROBOT_IMPAIRED:
            logger.warning("Navigation failed: robot is impaired")
            return True, "Robot is impaired"
        else:
            return False, None  # Still navigating

    async def disconnect(self):
        """Disconnect from SPOT and cleanup."""
        try:
            if self._powered_on and not self._started_powered_on:
                await asyncio.to_thread(
                    self.robot_command_client.robot_command,
                    RobotCommandBuilder.safe_power_off_command(),
                    end_time_secs=time.time()
                )

            if self.lease_keepalive:
                self.lease_keepalive.shutdown()
                self.lease_keepalive = None

            self._connected = False
            logger.info("Disconnected from SPOT")

        except Exception as e:
            logger.exception(f"Error during disconnect: {e}")
