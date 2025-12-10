"""SPOT Robot Controller for Telegram Bot integration."""

import asyncio
import logging
import os
import time
from typing import Awaitable, Callable, Optional

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


def id_to_short_code(waypoint_id: str) -> Optional[str]:
    """Convert a unique id to a 2 letter short code."""
    tokens = waypoint_id.split('-')
    if len(tokens) > 2:
        return f'{tokens[0][0]}{tokens[1][0]}'
    return None


def find_unique_waypoint_id(short_code: str, graph, name_to_id: dict) -> Optional[str]:
    """Convert either a 2 letter short code or an annotation name into the associated unique id."""
    if graph is None:
        logger.error("Graph not loaded. Cannot find waypoint.")
        return None

    if len(short_code) != 2:
        # Not a short code, check if it is an annotation name
        if short_code in name_to_id:
            if name_to_id[short_code] is not None:
                return name_to_id[short_code]
            else:
                logger.error(f"Waypoint name {short_code} is used for multiple waypoints.")
                return None
        # Assume it's a unique waypoint id
        return short_code

    ret = short_code
    for waypoint in graph.waypoints:
        if short_code == id_to_short_code(waypoint.id):
            if ret != short_code:
                return short_code  # Multiple waypoints with same short code
            ret = waypoint.id
    return ret


def update_waypoints_and_edges(graph, localization_id: str) -> tuple[dict, dict]:
    """Update waypoint names to ids mapping and edges."""
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

    def __init__(self, hostname: str, map_path: str):
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

        # Power state
        self._powered_on = False
        self._started_powered_on = False

        # Connection state
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Check if connected to SPOT."""
        return self._connected and self.robot is not None

    async def connect(self, status_callback: Callable[[str], Awaitable[None]]) -> bool:
        """
        Connect to SPOT and initialize for navigation.

        Args:
            status_callback: Async function to report status updates

        Returns:
            True if connection successful, False otherwise
        """
        try:
            # Step 1: Create SDK and authenticate
            await status_callback("Connecting to SPOT...")
            await asyncio.to_thread(self._create_sdk_and_authenticate)
            await status_callback("Authenticated with SPOT")

            # Step 2: Acquire lease
            await status_callback("Acquiring lease...")
            await asyncio.to_thread(self._acquire_lease)
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

        except ResourceAlreadyClaimedError:
            await status_callback("Lease already claimed. Check for tablet connection.")
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

    def _acquire_lease(self):
        """Acquire lease for robot control."""
        self.lease_client = self.robot.ensure_client(LeaseClient.default_service_name)
        self.lease_keepalive = LeaseKeepAlive(
            self.lease_client, must_acquire=True, return_at_exit=True
        )

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
                await status_callback("Failed to power on robot")
                return False

            # Navigate with heartbeat updates
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

            # Send heartbeat update every 3 seconds
            if elapsed - last_update >= 3:
                await status_callback(f"Navigating to {location.title()}... ({elapsed}s)")
                last_update = elapsed

            try:
                # Issue navigation command
                nav_to_cmd_id = await asyncio.to_thread(
                    self.graph_nav_client.navigate_to,
                    destination_waypoint,
                    1.0,
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

            await asyncio.sleep(0.5)

    def _toggle_power(self, should_power_on: bool) -> bool:
        """Power the robot on/off."""
        is_powered_on = self._check_is_powered_on()

        if not is_powered_on and should_power_on:
            power_on_motors(self.power_client)
            # Wait for motors to power on
            while True:
                state = self.robot_state_client.get_robot_state()
                if state.power_state.motor_power_state == robot_state_pb2.PowerState.STATE_ON:
                    break
                time.sleep(0.25)

        elif is_powered_on and not should_power_on:
            safe_power_off_motors(self.robot_command_client, self.robot_state_client)

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
            return True, None  # Success
        elif status.status == graph_nav_pb2.NavigationFeedbackResponse.STATUS_LOST:
            return True, "Robot got lost during navigation"
        elif status.status == graph_nav_pb2.NavigationFeedbackResponse.STATUS_STUCK:
            return True, "Robot got stuck during navigation"
        elif status.status == graph_nav_pb2.NavigationFeedbackResponse.STATUS_ROBOT_IMPAIRED:
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
