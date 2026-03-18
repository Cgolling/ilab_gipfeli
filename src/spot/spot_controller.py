"""SPOT Robot Controller for Telegram Bot integration."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import time
from typing import Awaitable, Callable, Optional

from src.logging_config import setup_logging

# Initialize logging (safe to call multiple times)
setup_logging()

import bosdyn.client
import bosdyn.client.util
from bosdyn.api import image_pb2, robot_state_pb2
from bosdyn.api.graph_nav import graph_nav_pb2, map_pb2, map_processing_pb2, nav_pb2, recording_pb2
from bosdyn.api.spot_cam import audio_pb2
from bosdyn.client.exceptions import ResponseError
from bosdyn.client.frame_helpers import get_odom_tform_body
from bosdyn.client.graph_nav import GraphNavClient
from bosdyn.client.image import ImageClient, build_image_request
from bosdyn.client.lease import LeaseClient, LeaseKeepAlive, ResourceAlreadyClaimedError
from bosdyn.client.map_processing import MapProcessingServiceClient
from bosdyn.client.power import PowerClient, power_on_motors, safe_power_off_motors
from bosdyn.client.recording import GraphNavRecordingServiceClient, NotReadyYetError
from bosdyn.client.robot_command import RobotCommandBuilder, RobotCommandClient
from bosdyn.client.robot_state import RobotStateClient
from bosdyn.client.spot_cam.audio import AudioClient
from google.protobuf import wrappers_pb2 as wrappers

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
JPEG_QUALITY_PERCENT = 85            # Snapshot JPEG quality for perception capture


@dataclass(frozen=True, slots=True)
class WebRTCContext:
    """Minimal data required to start Spot CAM WebRTC."""

    hostname: str
    token: str


@dataclass(frozen=True, slots=True)
class MapRecordingStatus:
    """Current/last status of GraphNav map recording."""

    session_name: Optional[str]
    state: str
    is_recording: bool
    has_unsaved_graph: bool
    waypoint_count: int
    edge_count: int
    last_saved_map_name: Optional[str]
    last_error: Optional[str]
    updated_at: datetime


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

    normalized = identifier.strip()
    if not normalized:
        return None

    # Route to appropriate resolver based on identifier length
    if len(normalized) == 2:
        return _resolve_short_code(normalized, graph)
    return _resolve_annotation_or_raw_id(normalized, graph, name_to_id)


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


def _resolve_annotation_or_raw_id(identifier: str, graph, name_to_id: dict) -> Optional[str]:
    """
    Resolve an annotation name from the mapping, or return as raw ID.

    Args:
        identifier: Annotation name or full waypoint ID
        graph: The loaded GraphNav graph
        name_to_id: Mapping of annotation names to waypoint IDs

    Returns:
        The resolved waypoint ID, or None if the annotation is ambiguous.
        If identifier matches a full waypoint ID in the graph, that ID is returned.
        Unknown identifiers return None.
    """
    if identifier in name_to_id:
        waypoint_id = name_to_id[identifier]
        if waypoint_id is None:
            logger.error(f"Waypoint name '{identifier}' is ambiguous (maps to multiple waypoints).")
            return None
        return waypoint_id

    for waypoint in graph.waypoints:
        if waypoint.id == identifier:
            return identifier

    logger.error("Unknown waypoint identifier '%s'.", identifier)
    return None


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
        self.audio_client = None
        self.image_client = None
        self.recording_client = None
        self.map_processing_client = None

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

        # Recording state
        self._recording_session_name: Optional[str] = None
        self._recording_has_unsaved_graph = False
        self._recording_last_saved_map_name: Optional[str] = None
        self._recording_last_error: Optional[str] = None

    @property
    def is_connected(self) -> bool:
        """Check if connected to SPOT."""
        return self._connected and self.robot is not None

    @property
    def current_map_name(self) -> str:
        """Return the current map directory name."""
        return os.path.basename(self.map_path.rstrip("/\\"))

    def set_map_path(self, map_path: str) -> None:
        """Update map path and clear loaded graph state."""
        self.map_path = map_path.rstrip("/\\")
        self._clear_loaded_graph_state()

    def get_named_waypoints(self) -> list[str]:
        """Return unique named waypoints from the currently loaded graph."""
        names = [
            name
            for name, waypoint_id in self._current_annotation_name_to_wp_id.items()
            if name and waypoint_id
        ]
        return sorted(set(names))

    def get_recording_status(self) -> MapRecordingStatus:
        """Return current/last known GraphNav recording state."""
        is_recording = False
        state = "idle"

        if self.is_connected and self.recording_client is not None:
            try:
                response = self.recording_client.get_record_status()
                is_recording = bool(getattr(response, "is_recording", False))
                state = "recording" if is_recording else (
                    "stopped" if self._recording_has_unsaved_graph else "idle"
                )
            except Exception as exc:
                self._recording_last_error = str(exc)
                state = "error"

        if not self.is_connected and self._recording_has_unsaved_graph:
            state = "stopped"

        waypoint_count = len(self._current_graph.waypoints) if self._current_graph is not None else 0
        edge_count = len(self._current_graph.edges) if self._current_graph is not None else 0
        return MapRecordingStatus(
            session_name=self._recording_session_name,
            state=state,
            is_recording=is_recording,
            has_unsaved_graph=self._recording_has_unsaved_graph,
            waypoint_count=waypoint_count,
            edge_count=edge_count,
            last_saved_map_name=self._recording_last_saved_map_name,
            last_error=self._recording_last_error,
            updated_at=datetime.now(timezone.utc),
        )

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
            "audio_available": self.audio_client is not None,
            "image_available": self.image_client is not None,
            "webrtc_available": self.is_webrtc_available(),
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
            if self.audio_client:
                await status_callback("Spot CAM audio service available")
            else:
                await status_callback("Spot CAM audio service not available")

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

    async def load_map(
        self,
        map_path: str,
        status_callback: Callable[[str], Awaitable[None]],
    ) -> bool:
        """
        Switch the active GraphNav map.

        When connected, the new map is uploaded immediately and fiducial localization is retried.
        When disconnected, the controller just stores the new path for the next /connect.
        """
        normalized_path = map_path.rstrip("/\\")
        graph_path = os.path.join(normalized_path, "graph")
        if not os.path.isfile(graph_path):
            await status_callback(f"Map folder is invalid or missing graph file: {normalized_path}")
            return False

        self.set_map_path(normalized_path)

        if not self.is_connected:
            await status_callback(f"Active map set to '{self.current_map_name}'. Connect to upload it.")
            return True

        try:
            await status_callback(f"Loading map '{self.current_map_name}'...")
            await asyncio.to_thread(self._upload_graph_and_snapshots)
            await status_callback("Map uploaded")
            await status_callback("Relocalizing robot...")
            await asyncio.to_thread(self._set_initial_localization_fiducial)
            await status_callback("Robot localized successfully!")
            return True
        except Exception as e:
            logger.exception("Failed to load map '%s': %s", normalized_path, e)
            await status_callback(f"Failed to load map: {e}")
            return False

    async def start_map_recording(
        self,
        map_name: str,
        status_callback: Callable[[str], Awaitable[None]],
    ) -> bool:
        """Start a fresh GraphNav recording session for a new map."""
        if not self.is_connected or self.recording_client is None:
            await status_callback("Not connected to SPOT")
            return False

        normalized_name = self._normalize_map_name(map_name)
        if not normalized_name:
            await status_callback("Invalid map name. Use letters, numbers, '-' or '_'.")
            return False

        target_dir = self._resolve_map_directory(normalized_name)
        if target_dir.exists():
            await status_callback(
                f"Map '{normalized_name}' already exists locally. Choose a different name."
            )
            return False

        try:
            status = self.get_recording_status()
            if status.is_recording:
                await status_callback(
                    f"A map recording is already running for '{status.session_name or '-'}'."
                )
                return False

            await status_callback("Clearing map on robot...")
            await asyncio.to_thread(self.graph_nav_client.clear_graph)

            await status_callback(f"Starting recording for '{normalized_name}'...")
            await asyncio.to_thread(
                self.recording_client.start_recording,
                recording_environment=self._make_recording_environment(normalized_name),
            )
            self._recording_session_name = normalized_name
            self._recording_has_unsaved_graph = True
            self._recording_last_error = None
            self._clear_loaded_graph_state()
            await status_callback(
                f"Recording started for '{normalized_name}'. Use /map waypoint <name> to add named waypoints."
            )
            return True
        except Exception as exc:
            self._recording_last_error = str(exc)
            logger.exception("Failed to start map recording '%s': %s", normalized_name, exc)
            await status_callback(f"Failed to start map recording: {exc}")
            return False

    async def stop_map_recording(
        self,
        status_callback: Callable[[str], Awaitable[None]],
    ) -> bool:
        """Stop the current recording session but keep it available for save/post-processing."""
        if not self.is_connected or self.recording_client is None:
            await status_callback("Not connected to SPOT")
            return False

        status = self.get_recording_status()
        if not status.is_recording:
            await status_callback("No active map recording to stop.")
            return False

        try:
            await status_callback("Stopping map recording...")
            while True:
                try:
                    await asyncio.to_thread(self.recording_client.stop_recording)
                    break
                except NotReadyYetError:
                    await asyncio.sleep(1.0)

            await asyncio.to_thread(self._refresh_current_graph_from_robot)
            self._recording_has_unsaved_graph = True
            self._recording_last_error = None
            await status_callback(
                "Recording stopped. You can now run /map record close-loops, /map record optimize, or /map record save."
            )
            return True
        except Exception as exc:
            self._recording_last_error = str(exc)
            logger.exception("Failed to stop map recording: %s", exc)
            await status_callback(f"Failed to stop map recording: {exc}")
            return False

    async def abort_map_recording(
        self,
        status_callback: Callable[[str], Awaitable[None]],
    ) -> bool:
        """Abort the current recording workflow and discard the server-side graph."""
        if not self.is_connected or self.recording_client is None or self.graph_nav_client is None:
            await status_callback("Not connected to SPOT")
            return False

        status = self.get_recording_status()
        if not status.is_recording and not status.has_unsaved_graph:
            await status_callback("No active or unsaved map recording to abort.")
            return False

        try:
            if status.is_recording:
                await status_callback("Aborting active map recording...")
                while True:
                    try:
                        await asyncio.to_thread(self.recording_client.stop_recording)
                        break
                    except NotReadyYetError:
                        await asyncio.sleep(1.0)

            await status_callback("Discarding graph on robot...")
            await asyncio.to_thread(self.graph_nav_client.clear_graph)
            self._clear_loaded_graph_state()
            self._recording_session_name = None
            self._recording_has_unsaved_graph = False
            self._recording_last_error = None
            await status_callback("Map recording aborted and discarded.")
            return True
        except Exception as exc:
            self._recording_last_error = str(exc)
            logger.exception("Failed to abort map recording: %s", exc)
            await status_callback(f"Failed to abort map recording: {exc}")
            return False

    async def create_recording_waypoint(
        self,
        waypoint_name: str,
        status_callback: Callable[[str], Awaitable[None]],
    ) -> bool:
        """Create a named waypoint at the current robot location during recording."""
        if not self.is_connected or self.recording_client is None:
            await status_callback("Not connected to SPOT")
            return False

        normalized_name = self._normalize_waypoint_name(waypoint_name)
        if not normalized_name:
            await status_callback("Invalid waypoint name. Use letters, numbers, '-' or '_'.")
            return False

        status = self.get_recording_status()
        if not status.is_recording:
            await status_callback("No active map recording. Start one with /map record start <map_name>.")
            return False

        try:
            response = await asyncio.to_thread(
                self.recording_client.create_waypoint,
                waypoint_name=normalized_name,
            )
            if response.status != recording_pb2.CreateWaypointResponse.STATUS_OK:
                await status_callback(f"Could not create waypoint '{normalized_name}'.")
                return False

            await asyncio.to_thread(self._refresh_current_graph_from_robot)
            self._recording_has_unsaved_graph = True
            await status_callback(f"Waypoint '{normalized_name}' created.")
            return True
        except Exception as exc:
            self._recording_last_error = str(exc)
            logger.exception("Failed to create recording waypoint '%s': %s", normalized_name, exc)
            await status_callback(f"Failed to create waypoint: {exc}")
            return False

    async def close_recording_loops(
        self,
        mode: str,
        status_callback: Callable[[str], Awaitable[None]],
    ) -> bool:
        """Run GraphNav topology processing to close loops."""
        if not self.is_connected or self.map_processing_client is None:
            await status_callback("Not connected to SPOT")
            return False

        normalized_mode = mode.strip().lower()
        close_fiducial = normalized_mode in {"all", "fiducial"}
        close_odometry = normalized_mode in {"all", "odometry"}
        if normalized_mode not in {"all", "fiducial", "odometry"}:
            await status_callback("Invalid loop mode. Use: all, fiducial, odometry.")
            return False

        try:
            await status_callback(f"Closing {normalized_mode} loops...")
            response = await asyncio.to_thread(
                self.map_processing_client.process_topology,
                map_processing_pb2.ProcessTopologyRequest.Params(
                    do_fiducial_loop_closure=wrappers.BoolValue(value=close_fiducial),
                    do_odometry_loop_closure=wrappers.BoolValue(value=close_odometry),
                ),
                True,
            )
            await asyncio.to_thread(self._refresh_current_graph_from_robot)
            self._recording_has_unsaved_graph = True
            await status_callback(f"Loop closure complete. Added {len(response.new_subgraph.edges)} edge(s).")
            return True
        except Exception as exc:
            self._recording_last_error = str(exc)
            logger.exception("Failed to close loops: %s", exc)
            await status_callback(f"Failed to close loops: {exc}")
            return False

    async def optimize_recording_anchoring(
        self,
        status_callback: Callable[[str], Awaitable[None]],
    ) -> bool:
        """Run GraphNav anchoring optimization on the current server-side map."""
        if not self.is_connected or self.map_processing_client is None:
            await status_callback("Not connected to SPOT")
            return False

        try:
            await status_callback("Optimizing anchoring...")
            response = await asyncio.to_thread(
                self.map_processing_client.process_anchoring,
                map_processing_pb2.ProcessAnchoringRequest.Params(),
                True,
                False,
            )
            await asyncio.to_thread(self._refresh_current_graph_from_robot)
            self._recording_has_unsaved_graph = True
            await status_callback(
                f"Anchoring optimized after {response.iteration} iteration(s)."
            )
            return True
        except Exception as exc:
            self._recording_last_error = str(exc)
            logger.exception("Failed to optimize anchoring: %s", exc)
            await status_callback(f"Failed to optimize anchoring: {exc}")
            return False

    async def save_recorded_map(
        self,
        map_name: Optional[str],
        status_callback: Callable[[str], Awaitable[None]],
    ) -> tuple[bool, Optional[str]]:
        """Download the current server-side graph to maps/<name> and switch the active controller map."""
        if not self.is_connected or self.graph_nav_client is None:
            await status_callback("Not connected to SPOT")
            return False, None

        target_name = self._normalize_map_name(map_name or self._recording_session_name or "")
        if not target_name:
            await status_callback("No map name available. Start with /map record start <map_name>.")
            return False, None

        if self.get_recording_status().is_recording:
            await status_callback("Recording is still running. Stop it first with /map record stop.")
            return False, None

        target_dir = self._resolve_map_directory(target_name)
        if target_dir.exists():
            await status_callback(
                f"Map '{target_name}' already exists locally. Choose another save name."
            )
            return False, None

        try:
            await status_callback(f"Saving map to '{target_name}'...")
            await asyncio.to_thread(self._download_graph_to_directory, target_dir)
            self.set_map_path(str(target_dir))
            await asyncio.to_thread(self._load_graph_from_disk)
            self._recording_session_name = target_name
            self._recording_last_saved_map_name = target_name
            self._recording_has_unsaved_graph = False
            self._recording_last_error = None
            await status_callback(
                f"Map '{target_name}' saved with {len(self._current_graph.waypoints)} waypoints and {len(self._current_graph.edges)} edges."
            )
            return True, str(target_dir)
        except Exception as exc:
            self._recording_last_error = str(exc)
            logger.exception("Failed to save recorded map '%s': %s", target_name, exc)
            await status_callback(f"Failed to save recorded map: {exc}")
            return False, None

    def _normalize_map_name(self, map_name: str) -> str:
        """Normalize a local map directory name."""
        text = "".join(
            ch.lower() if ch.isalnum() else "_" if ch in {" ", "-", "_"} else ""
            for ch in map_name.strip()
        )
        while "__" in text:
            text = text.replace("__", "_")
        return text.strip("_")

    def _normalize_waypoint_name(self, waypoint_name: str) -> str:
        """Normalize a waypoint annotation name."""
        text = "".join(
            ch.lower() if ch.isalnum() else "_" if ch in {" ", "-", "_"} else ""
            for ch in waypoint_name.strip()
        )
        while "__" in text:
            text = text.replace("__", "_")
        return text.strip("_")

    def _resolve_map_directory(self, map_name: str) -> Path:
        """Return absolute path for a local maps/<map_name> directory."""
        return Path(self.map_path).resolve().parent / map_name

    def _make_recording_environment(self, map_name: str) -> recording_pb2.RecordingEnvironment:
        """Build recording environment metadata for a Telegram-driven recording session."""
        client_metadata = GraphNavRecordingServiceClient.make_client_metadata(
            session_name=map_name,
            client_username=os.getenv("USERNAME") or "telegram-bot",
            client_id="telegram-bot",
            client_type="telegram",
        )
        return GraphNavRecordingServiceClient.make_recording_environment(
            name=map_name,
            waypoint_env=GraphNavRecordingServiceClient.make_waypoint_environment(
                client_metadata=client_metadata
            ),
        )

    def _refresh_current_graph_from_robot(self) -> None:
        """Download graph metadata from the robot and refresh local caches."""
        assert self.graph_nav_client is not None
        graph = self.graph_nav_client.download_graph()
        if graph is None:
            self._clear_loaded_graph_state()
            return

        self._current_graph = graph
        localization_id = self.graph_nav_client.get_localization_state().localization.waypoint_id
        self._current_annotation_name_to_wp_id, self._current_edges = update_waypoints_and_edges(
            graph, localization_id
        )

    def _load_graph_from_disk(self) -> None:
        """Load graph and snapshots from self.map_path into memory."""
        logger.info("Loading graph from %s", self.map_path)
        self._clear_loaded_graph_state()

        graph_path = Path(self.map_path) / "graph"
        with graph_path.open("rb") as graph_file:
            self._current_graph = map_pb2.Graph()
            self._current_graph.ParseFromString(graph_file.read())

        for waypoint in self._current_graph.waypoints:
            snapshot_path = Path(self.map_path) / "waypoint_snapshots" / waypoint.snapshot_id
            with snapshot_path.open("rb") as snapshot_file:
                waypoint_snapshot = map_pb2.WaypointSnapshot()
                waypoint_snapshot.ParseFromString(snapshot_file.read())
                self._current_waypoint_snapshots[waypoint_snapshot.id] = waypoint_snapshot

        for edge in self._current_graph.edges:
            if len(edge.snapshot_id) == 0:
                continue
            snapshot_path = Path(self.map_path) / "edge_snapshots" / edge.snapshot_id
            with snapshot_path.open("rb") as snapshot_file:
                edge_snapshot = map_pb2.EdgeSnapshot()
                edge_snapshot.ParseFromString(snapshot_file.read())
                self._current_edge_snapshots[edge_snapshot.id] = edge_snapshot

        localization_id = ""
        if self.graph_nav_client is not None:
            try:
                localization_id = self.graph_nav_client.get_localization_state().localization.waypoint_id
            except Exception:
                localization_id = ""

        self._current_annotation_name_to_wp_id, self._current_edges = update_waypoints_and_edges(
            self._current_graph, localization_id
        )

    def _download_graph_to_directory(self, target_dir: Path) -> None:
        """Download graph and all snapshots from the robot into target_dir."""
        assert self.graph_nav_client is not None

        graph = self.graph_nav_client.download_graph()
        if graph is None:
            raise RuntimeError("Failed to download graph from robot.")

        target_dir.mkdir(parents=True, exist_ok=False)
        (target_dir / "waypoint_snapshots").mkdir(exist_ok=True)
        (target_dir / "edge_snapshots").mkdir(exist_ok=True)
        (target_dir / "graph").write_bytes(graph.SerializeToString())

        for waypoint in graph.waypoints:
            if not waypoint.snapshot_id:
                continue
            snapshot = self.graph_nav_client.download_waypoint_snapshot(waypoint.snapshot_id)
            (target_dir / "waypoint_snapshots" / waypoint.snapshot_id).write_bytes(
                snapshot.SerializeToString()
            )

        for edge in graph.edges:
            if not edge.snapshot_id:
                continue
            snapshot = self.graph_nav_client.download_edge_snapshot(edge.snapshot_id)
            (target_dir / "edge_snapshots" / edge.snapshot_id).write_bytes(
                snapshot.SerializeToString()
            )

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

        # Check E-Stop status immediately - fail fast if robot is stopped
        if self.robot.is_estopped():
            raise Exception(
                "Robot is E-Stopped! Release the physical E-Stop or software Cut before connecting."
            )

        # Create clients
        self.robot_command_client = self.robot.ensure_client(
            RobotCommandClient.default_service_name)
        self.robot_state_client = self.robot.ensure_client(
            RobotStateClient.default_service_name)
        self.graph_nav_client = self.robot.ensure_client(
            GraphNavClient.default_service_name)
        self.power_client = self.robot.ensure_client(
            PowerClient.default_service_name)
        self.recording_client = self.robot.ensure_client(
            GraphNavRecordingServiceClient.default_service_name)
        self.map_processing_client = self.robot.ensure_client(
            MapProcessingServiceClient.default_service_name)
        self._initialize_optional_image_client()
        self._initialize_optional_audio_client()

        # Check initial power state
        power_state = self.robot_state_client.get_robot_state().power_state
        self._started_powered_on = (power_state.motor_power_state == power_state.STATE_ON)
        self._powered_on = self._started_powered_on
        logger.info(f"Initial power state: motors_on={self._started_powered_on}")

    def _initialize_optional_audio_client(self) -> None:
        """Initialize Spot CAM audio client if available on this robot."""
        try:
            assert self.robot is not None
            self.audio_client = self.robot.ensure_client(AudioClient.default_service_name)
            logger.info("Spot CAM audio service available")
        except Exception as e:
            self.audio_client = None
            logger.info(f"Spot CAM audio service unavailable: {e}")

    def _initialize_optional_image_client(self) -> None:
        """Initialize image client if available on this robot."""
        try:
            assert self.robot is not None
            self.image_client = self.robot.ensure_client(ImageClient.default_service_name)
            logger.info("Image service available")
        except Exception as e:
            self.image_client = None
            logger.info(f"Image service unavailable: {e}")

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
        assert self.lease_client is not None
        try:
            self.lease_client.return_lease(self.lease_client.lease_wallet.get_lease())
        except Exception as e:
            logger.debug(f"Could not return existing lease (expected if we don't have one): {e}")

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
        self._load_graph_from_disk()
        logger.info(
            "Loaded graph has %d waypoints and %d edges",
            len(self._current_graph.waypoints),
            len(self._current_graph.edges),
        )

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

    def _clear_loaded_graph_state(self) -> None:
        """Reset cached graph data before loading another map."""
        self._current_graph = None
        self._current_waypoint_snapshots = {}
        self._current_edge_snapshots = {}
        self._current_annotation_name_to_wp_id = {}
        self._current_edges = {}

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

        location_name = location.strip()
        if not location_name:
            await status_callback("Missing destination waypoint name.")
            return False

        identifier = WAYPOINTS.get(location_name.lower(), location_name)

        try:
            # Find the full waypoint ID
            destination_waypoint = await asyncio.to_thread(
                find_unique_waypoint_id,
                identifier,
                self._current_graph,
                self._current_annotation_name_to_wp_id
            )

            if not destination_waypoint:
                await status_callback(f"Unknown location: {location_name}")
                return False

            # Power on if needed
            await status_callback(f"Powering on robot...")
            powered_on = await asyncio.to_thread(self._toggle_power, True)
            if not powered_on:
                logger.error("Failed to power on robot motors")
                await status_callback("Failed to power on robot")
                return False

            # Navigate with heartbeat updates
            logger.info(
                "Starting navigation to %s (waypoint: %s)",
                location_name,
                destination_waypoint,
            )
            await status_callback(f"Navigating to {location_name}...")
            success = await self._navigate_to_waypoint_with_heartbeat(
                destination_waypoint,
                location_name,
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

    async def play_wav_file(
        self,
        sound_name: str,
        wav_path: str,
        status_callback: Callable[[str], Awaitable[None]],
        gain: Optional[float] = None,
    ) -> bool:
        """
        Upload a local WAV file to Spot CAM and play it immediately.

        Args:
            sound_name: Name used to store the uploaded sound on the robot.
            wav_path: Local path to a .wav file.
            status_callback: Async callback for user-facing status messages.
            gain: Optional playback gain multiplier.

        Returns:
            True on success, False on failure.
        """
        if not self.is_connected:
            await status_callback("Not connected to SPOT")
            return False

        if self.audio_client is None:
            await status_callback("Spot CAM audio service is not available on this robot.")
            return False

        if not os.path.isfile(wav_path):
            await status_callback(f"WAV file not found: {wav_path}")
            return False

        if not wav_path.lower().endswith(".wav"):
            await status_callback("Only .wav files are supported.")
            return False

        try:
            await status_callback(f"Uploading sound '{sound_name}'...")
            sound_data = await asyncio.to_thread(self._read_sound_file, wav_path)
            await asyncio.to_thread(self._upload_and_play_sound, sound_name, sound_data, gain)
            await status_callback(f"Playing '{sound_name}'")
            return True
        except Exception as e:
            logger.exception(f"Failed to play WAV file '{wav_path}': {e}")
            await status_callback(f"Failed to play sound: {e}")
            return False

    def is_webrtc_available(self) -> bool:
        """Return True when Spot CAM WebRTC can be attempted."""
        return self.is_connected and self.audio_client is not None and self.robot is not None

    def get_webrtc_context(self) -> Optional[WebRTCContext]:
        """Return hostname/token pair for Spot CAM WebRTC signaling."""
        if not self.is_webrtc_available() or self.robot is None:
            return None
        token = getattr(self.robot, "user_token", None)
        if not token:
            return None
        return WebRTCContext(hostname=self.hostname, token=str(token))

    def _read_sound_file(self, wav_path: str) -> bytes:
        """Read WAV file bytes from disk."""
        with open(wav_path, "rb") as fh:
            return fh.read()

    def _upload_and_play_sound(
        self,
        sound_name: str,
        sound_data: bytes,
        gain: Optional[float],
    ) -> None:
        """Upload sound bytes to Spot CAM and start playback."""
        assert self.audio_client is not None
        sound = audio_pb2.Sound(name=sound_name)

        # Replace existing sound with same name for predictable behavior.
        try:
            self.audio_client.delete_sound(sound)
        except Exception:
            pass

        self.audio_client.load_sound(sound, sound_data)
        if gain is not None:
            self.audio_client.play_sound(sound, gain=max(gain, 0.0))
        else:
            self.audio_client.play_sound(sound)

    async def get_audio_volume_percent(self) -> Optional[float]:
        """
        Read Spot CAM audio volume percentage (0..100).

        Returns:
            Current volume percentage, or None if unavailable.
        """
        if not self.is_connected or self.audio_client is None:
            return None

        try:
            volume = await asyncio.to_thread(self.audio_client.get_volume)
            return float(volume)
        except Exception as e:
            logger.exception(f"Failed to get audio volume: {e}")
            return None

    async def set_audio_volume_percent(self, percentage: float) -> bool:
        """
        Set Spot CAM audio volume percentage (0..100).

        Args:
            percentage: Target volume in percent.

        Returns:
            True if applied, False if unavailable or failed.
        """
        if not self.is_connected or self.audio_client is None:
            return False

        target = min(max(percentage, 0.0), 100.0)

        try:
            await asyncio.to_thread(self.audio_client.set_volume, target)
            return True
        except Exception as e:
            logger.exception(f"Failed to set audio volume to {target}: {e}")
            return False

    def list_image_sources(self) -> list[str]:
        """
        List available image sources from SPOT.

        Returns:
            Sorted list of source names. Empty list if unavailable.
        """
        if not self.is_connected or self.image_client is None:
            return []

        try:
            sources = self.image_client.list_image_sources()
            names = [source.name for source in sources if getattr(source, "name", None)]
            return sorted(set(names))
        except Exception as e:
            logger.exception(f"Failed to list image sources: {e}")
            return []

    async def capture_image_jpeg(self, source_name: str) -> Optional[bytes]:
        """
        Capture a JPEG image from the given source.

        Args:
            source_name: Image source name as reported by list_image_sources().

        Returns:
            JPEG bytes on success, None on failure.
        """
        if not self.is_connected or self.image_client is None:
            return None

        try:
            request = build_image_request(
                source_name,
                quality_percent=JPEG_QUALITY_PERCENT,
                image_format=image_pb2.Image.FORMAT_JPEG,
            )
            responses = await asyncio.to_thread(self.image_client.get_image, [request])
            if not responses:
                return None

            shot = responses[0].shot.image
            if shot.format != image_pb2.Image.FORMAT_JPEG:
                logger.warning(
                    "Image source '%s' returned non-JPEG format=%s",
                    source_name,
                    shot.format,
                )
                return None

            return bytes(shot.data)
        except Exception as e:
            logger.exception(f"Failed to capture image from source '{source_name}': {e}")
            return None

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
