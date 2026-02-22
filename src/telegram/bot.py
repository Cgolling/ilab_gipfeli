#!/usr/bin/env python
# pylint: disable=unused-argument
# This program is dedicated to the public domain under the CC0 license.

"""
Telegram Bot for SPOT Robot Control.

This bot allows users to control the Boston Dynamics SPOT robot
via Telegram, including navigation to predefined waypoints.
"""

import logging
import os
from pathlib import Path
import sys
from typing import Optional

# Add project root to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dotenv import load_dotenv
from telegram import ForceReply, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, NetworkError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

from src.app_settings import load_app_settings
from src.perception import RecordingManager, RecordingState, SnapshotManager
from src.spot import SpotController
from src.spot.spot_controller import WAYPOINTS
from src.logging_config import setup_logging
from src.tasks import (
    GIPFELI_PICKUP_WAYPOINT,
    MIN_BATTERY_PERCENT,
    TaskManager,
    TaskState,
)
from src.telegram.security import (
    CRITICAL_COMMANDS,
    RbacConfig,
    format_deny_message,
    is_allowed,
    is_private_chat,
    load_rbac_config,
    required_role_for_command,
    resolve_user_role,
)

# Initialize logging (safe to call multiple times)
setup_logging()

logger = logging.getLogger(__name__)

# Configuration constants
APP_SETTINGS = load_app_settings()
CALLBACK_DATA_PREFIX = "goto_"
SOUND_CALLBACK_DATA_PREFIX = "sound_"
DEFAULT_SPOT_HOSTNAME = APP_SETTINGS.telegram.spot_hostname
DEFAULT_MAP_PATH = APP_SETTINGS.telegram.default_map_path
SOUNDS_DIR = APP_SETTINGS.telegram.sounds_dir
SNAPSHOTS_DIR = APP_SETTINGS.telegram.snapshots_dir
RECORDINGS_DIR = APP_SETTINGS.telegram.recordings_dir

# Global SPOT controller instance
# Thread-safety note: python-telegram-bot uses a single-threaded async model,
# so concurrent access to this variable is safe within Telegram handlers.
# The SpotController itself wraps blocking SDK calls with asyncio.to_thread(),
# which is also safe as those calls don't share mutable state.
# Do NOT access this from external threads without proper synchronization.
spot_controller: Optional[SpotController] = None
rbac_config: Optional[RbacConfig] = None
rbac_enabled: bool = False
task_manager = TaskManager(
    lambda: spot_controller,
    pickup_waypoint=GIPFELI_PICKUP_WAYPOINT,
    min_battery_percent=MIN_BATTERY_PERCENT,
)
snapshot_manager = SnapshotManager(
    lambda: spot_controller,
    output_dir=SNAPSHOTS_DIR,
)
recording_manager = RecordingManager(
    lambda: spot_controller,
    output_dir=RECORDINGS_DIR,
)


def env_var_is_true(name: str, default: bool = True) -> bool:
    """Parse common truthy/falsey env var values."""
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def initialize_rbac() -> None:
    """
    Initialize RBAC settings from environment and YAML configuration.

    Raises:
        RuntimeError: If RBAC is enabled but configuration cannot be loaded.
    """
    global rbac_enabled, rbac_config

    rbac_enabled = APP_SETTINGS.telegram.rbac_enabled
    rbac_config = None

    if not rbac_enabled:
        logger.warning(
            "RBAC disabled via app settings (%s)",
            APP_SETTINGS.config_path,
        )
        return

    config_path = APP_SETTINGS.telegram.rbac_config_path
    try:
        rbac_config = load_rbac_config(config_path)
    except Exception as e:
        raise RuntimeError(
            f"RBAC enabled, but config loading failed ({config_path}): {e}"
        ) from e

    logger.info(
        "RBAC loaded from %s (private_only=%s, admins=%d, users=%d)",
        config_path,
        rbac_config.private_only,
        len(rbac_config.admin_user_ids),
        len(rbac_config.users),
    )


def extract_command_name(text: str) -> str:
    """Extract command name from raw Telegram command text (without '/')."""
    if not text.startswith("/"):
        return ""
    token = text.split(" ", 1)[0]
    token = token[1:]  # remove leading slash
    return token.split("@", 1)[0].strip().lower()


async def _reply_access_denied(update: Update, message: str) -> None:
    """Reply access denied via message or callback query context."""
    if update.message:
        await update.message.reply_text(message)
        return

    if update.callback_query:
        try:
            await update.callback_query.answer(message, show_alert=True)
        except Exception:
            pass
        return


async def authorize(update: Update, command_name: str) -> bool:
    """
    Check whether current user is authorized for a command.

    Unknown commands default to viewer permission requirement.
    """
    if not rbac_enabled:
        return True

    if rbac_config is None:
        await _reply_access_denied(update, "Access control is not configured.")
        logger.error("RBAC enabled but config not initialized.")
        return False

    required_role = required_role_for_command(command_name) or "viewer"
    user = update.effective_user

    if user is None:
        await _reply_access_denied(update, format_deny_message(required_role))
        logger.warning("RBAC denied (missing user) for command=%s", command_name)
        return False

    if rbac_config.private_only and not is_private_chat(update):
        await _reply_access_denied(
            update,
            "No access. This bot accepts commands only in private chat.",
        )
        logger.warning(
            "RBAC denied (non-private chat): user_id=%s command=%s",
            user.id,
            command_name,
        )
        return False

    actual_role = resolve_user_role(user.id, rbac_config)
    if not is_allowed(actual_role, required_role):
        await _reply_access_denied(update, format_deny_message(required_role))
        logger.warning(
            "RBAC denied: user_id=%s username=%s command=%s required=%s actual=%s",
            user.id,
            user.username,
            command_name,
            required_role,
            actual_role,
        )
        return False

    if command_name in CRITICAL_COMMANDS:
        logger.info(
            "RBAC allow critical: user_id=%s username=%s command=%s role=%s",
            user.id,
            user.username,
            command_name,
            actual_role,
        )

    return True


async def authorize_operator(update: Update) -> bool:
    """Require operator role for task actions that mutate robot behavior."""
    if not rbac_enabled:
        return True

    if rbac_config is None:
        await _reply_access_denied(update, "Access control is not configured.")
        logger.error("RBAC enabled but config not initialized.")
        return False

    user = update.effective_user
    if user is None:
        await _reply_access_denied(update, format_deny_message("operator"))
        return False

    actual_role = resolve_user_role(user.id, rbac_config)
    if not is_allowed(actual_role, "operator"):
        await _reply_access_denied(update, format_deny_message("operator"))
        logger.warning(
            "RBAC denied task action: user_id=%s username=%s actual=%s required=operator",
            user.id,
            user.username,
            actual_role,
        )
        return False

    return True


def format_task_status_message(status) -> str:
    """Build a compact status message for /task status."""
    if status.state == TaskState.IDLE:
        return "No task has been started yet."

    lines = [
        f"Task: {status.task_name or '-'}",
        f"State: {status.state.value}",
        f"Step: {status.step.value if status.step else '-'}",
        f"Progress: {status.progress_current}/{status.progress_total}",
    ]
    if status.started_at:
        lines.append(f"Started: {status.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"Updated: {status.updated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    if status.error:
        lines.append(f"Last error: {status.error}")
    return "\n".join(lines)


def get_gipfeli_destinations() -> list[str]:
    """Return destinations accepted by the gipfeli task."""
    return sorted(WAYPOINTS.keys())


def gipfeli_usage() -> str:
    """Usage text for gipfeli-specific task actions."""
    destinations = ", ".join(get_gipfeli_destinations())
    return (
        "Gipfeli task usage:\n"
        "/task gipfeli <destination>\n"
        "/task gipfeli status\n"
        "/task gipfeli cancel\n\n"
        f"Available destinations: {destinations}"
    )


def task_usage() -> str:
    """Usage text for /task command."""
    return (
        "Task command usage:\n"
        "/task gipfeli - Show gipfeli usage and destinations\n"
        "/task gipfeli <destination> - Run delivery task\n"
        "/task gipfeli status - Show gipfeli task status\n"
        "/task status - Show global task state and progress\n"
        "/task cancel - Cancel active task"
    )


def snapshot_usage() -> str:
    """Usage text for snapshot command."""
    return (
        "Snapshot command usage:\n"
        "/snapshot\n"
        "/snapshot <source>\n\n"
        "Use /snapshot without args to list available image sources."
    )


def record_usage() -> str:
    """Usage text for record command."""
    return (
        "Record command usage:\n"
        "/record start [source]\n"
        "/record stop\n"
        "/record abort\n"
        "/record status\n\n"
        "Without source, /record start tries Spot CAM WebRTC.\n"
        "Use /snapshot to list image sources for timelapse."
    )


def format_record_status(status) -> str:
    """Build human-readable recording status text."""
    lines = [
        f"State: {status.state.value}",
        f"Backend: {status.backend or '-'}",
        f"Source: {status.source or '-'}",
        f"Frames: {status.frame_count}",
        f"Duration: {status.duration_seconds:.1f}s",
        f"Audio: {'yes' if getattr(status, 'has_audio', False) else 'no'}",
    ]
    if status.started_at:
        lines.append(f"Started: {status.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"Updated: {status.updated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    if status.output_path:
        lines.append(f"Output: {status.output_path}")
    if status.error:
        lines.append(f"Last error: {status.error}")
    return "\n".join(lines)


def get_effective_user_role(update: Update) -> str:
    """Resolve effective user role for role-specific response behavior."""
    if not rbac_enabled or rbac_config is None:
        return "operator"

    user = update.effective_user
    if user is None:
        return "viewer"

    return resolve_user_role(user.id, rbac_config)


# Define a few command handlers. These usually take the two arguments update and
# context.
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    if not await authorize(update, "start"):
        return

    user = update.effective_user
    if not update.message or not user:
        return
    await update.message.reply_html(
        rf"Hi {user.mention_html()}!",
        reply_markup=ForceReply(selective=True),
    )


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current Telegram user ID and chat ID."""
    if not await authorize(update, "id"):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not update.message or user is None or chat is None:
        return

    await update.message.reply_text(
        f"Your Telegram user ID: {user.id}\n"
        f"This chat ID: {chat.id}"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    if not await authorize(update, "help"):
        return
     
    if not update.message:
        return
    
    await update.message.reply_text(
        "SPOT Robot Control Bot\n\n"
        "Connection:\n"
        "/connect - Connect to SPOT robot\n"
        "/disconnect - Disconnect and release lease\n"
        "/forceconnect - Force take control (use if stuck!)\n"
        "/status - Show robot status\n\n"
        "Navigation:\n"
        "/goto - Navigate to a location\n\n"
        "Audio:\n"
        "/sound - Play a WAV sound from sounds/ (optional gain)\n\n"
        "/volume - Get/set Spot CAM volume (0-100)\n\n"
        "Perception:\n"
        "/snapshot - Snapshot commands (sources, capture)\n"
        "/record - Recording commands (start, stop, abort, status)\n\n"
        "Tasks:\n"
        "/task - Task commands and status\n\n"
        "Other:\n"
        "/start - Show welcome message\n"
        "/id - Show your Telegram ID\n"
        "/help - Show this help message"
    )


def get_available_sounds() -> dict[str, str]:
    """
    Discover available WAV files in SOUNDS_DIR.

    Returns:
        Mapping of sound name (filename without extension, lower-case)
        to full file path.
    """
    if not os.path.isdir(SOUNDS_DIR):
        return {}

    sounds: dict[str, str] = {}
    for file_name in sorted(os.listdir(SOUNDS_DIR)):
        full_path = os.path.join(SOUNDS_DIR, file_name)
        if not os.path.isfile(full_path):
            continue
        stem, ext = os.path.splitext(file_name)
        if ext.lower() != ".wav" or not stem:
            continue
        sounds[stem.lower()] = full_path
    return sounds


def _build_sound_keyboard(sound_names: list[str]) -> InlineKeyboardMarkup:
    """Build a 2-column inline keyboard for sound selection."""
    keyboard: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []

    for index, name in enumerate(sound_names, start=1):
        row.append(
            InlineKeyboardButton(
                name,
                callback_data=f"{SOUND_CALLBACK_DATA_PREFIX}{name}",
            )
        )
        if index % 2 == 0:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    return InlineKeyboardMarkup(keyboard)


async def connect_spot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Connect to SPOT robot."""
    global spot_controller

    if not await authorize(update, "connect"):
        return
    
    if not update.message:
        return

    hostname = DEFAULT_SPOT_HOSTNAME
    map_path = DEFAULT_MAP_PATH

    logger.info(f"User initiated /connect to SPOT at {hostname}")
    await update.message.reply_text("Starting SPOT connection procedure...")

    spot_controller = SpotController(hostname, map_path)

    async def send_status(msg: str):
        if not update.message:
            return
        await update.message.reply_text(msg)

    success = await spot_controller.connect(send_status)

    if success:
        logger.info("SPOT connection successful via /connect command")
        await update.message.reply_text("SPOT is ready! Use /goto to navigate.")
    else:
        logger.warning("SPOT connection failed via /connect command")


async def forceconnect_spot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Force connect to SPOT robot, taking the lease from any other client."""
    global spot_controller

    if not await authorize(update, "forceconnect"):
        return

    if not update.message:
        return

    hostname = DEFAULT_SPOT_HOSTNAME
    map_path = DEFAULT_MAP_PATH

    logger.warning(f"User initiated /forceconnect to SPOT at {hostname}")
    await update.message.reply_text(
        "FORCE CONNECT: Taking control from any other client...\n"
        "(This will disconnect tablet or other scripts!)"
    )

    # Disconnect existing controller if any
    if spot_controller and spot_controller.is_connected:
        try:
            await spot_controller.disconnect()
        except Exception:
            pass

    spot_controller = SpotController(hostname, map_path)

    async def send_status(msg: str):
        if not update.message:
            return
        await update.message.reply_text(msg)

    success = await spot_controller.connect(send_status, force_acquire=True)

    if success:
        logger.info("SPOT force-connection successful")
        await update.message.reply_text("SPOT is ready! Lease forcefully acquired.")
    else:
        logger.warning("SPOT force-connection failed")
        await update.message.reply_text("Force connection failed. Check logs.")


async def disconnect_spot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Disconnect from SPOT and release the lease."""
    global spot_controller

    if not await authorize(update, "disconnect"):
        return

    if not update.message:
        return

    if spot_controller is None:
        await update.message.reply_text("Not connected to SPOT.")
        return

    logger.info("User initiated /disconnect")
    await update.message.reply_text("Disconnecting from SPOT...")

    try:
        await spot_controller.disconnect()
        await update.message.reply_text(
            "Disconnected from SPOT. Lease released.\n"
            "Use /connect to reconnect."
        )
        logger.info("Successfully disconnected from SPOT")
    except Exception as e:
        logger.exception(f"Error during disconnect: {e}")
        await update.message.reply_text(f"Error disconnecting: {e}")


async def status_spot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current SPOT robot status."""
    if not await authorize(update, "status"):
        return

    if not update.message:
        return
    
    if spot_controller is None:
        await update.message.reply_text(
            "SPOT Status: Not initialized\n\n"
            "Use /connect to connect to the robot."
        )
        return

    try:
        status = spot_controller.get_status()

        # Build status message
        lines = ["SPOT Status:\n"]

        # Connection
        if status["connected"]:
            lines.append("Connected: Yes")
        else:
            lines.append("Connected: No")

        lines.append(f"Hostname: {status['hostname']}")

        # Power state
        if status["powered_on"] is not None:
            power_str = "Standing" if status["powered_on"] else "Sitting"
            lines.append(f"Motors: {power_str}")

        # Battery
        if status["battery_percent"] is not None:
            lines.append(f"Battery: {status['battery_percent']:.0f}%")

        # E-stop
        if status["estop_status"]:
            lines.append(f"E-Stop: {status['estop_status']}")

        # Lease owner
        if status["lease_owner"]:
            lines.append(f"Lease Owner: {status['lease_owner']}")

        # Spot CAM audio
        lines.append(
            "Spot CAM Audio: Available"
            if status.get("audio_available", False)
            else "Spot CAM Audio: Not available"
        )

        await update.message.reply_text("\n".join(lines))

    except Exception as e:
        logger.exception(f"Error getting status: {e}")
        await update.message.reply_text(f"Error getting status: {e}")


async def goto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send inline keyboard with location options."""
    if not await authorize(update, "goto"):
        return

    if not update.message:
        return
    keyboard = [
        [
            InlineKeyboardButton("Aula", callback_data=f"{CALLBACK_DATA_PREFIX}aula"),
            InlineKeyboardButton("Triangle", callback_data=f"{CALLBACK_DATA_PREFIX}triangle"),
        ],
        [
            InlineKeyboardButton("Hauswart", callback_data=f"{CALLBACK_DATA_PREFIX}hauswart"),
            InlineKeyboardButton("Turnhalle", callback_data=f"{CALLBACK_DATA_PREFIX}turnhalle"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Where do you want to go?", reply_markup=reply_markup)


async def goto_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle goto button presses and navigate SPOT to the selected location."""
    if not await authorize(update, "goto"):
        return

    if not update.callback_query:
        return
    
    query = update.callback_query
    await query.answer()

    if not query.data:
        return

    location = query.data.replace(CALLBACK_DATA_PREFIX, "")

    # Check if SPOT is connected
    if spot_controller is None or not spot_controller.is_connected:
        await query.edit_message_text("SPOT not connected. Use /connect first.")
        return

    # Navigate with heartbeat updates
    async def send_status(msg: str):
        try:
            await query.edit_message_text(msg)
        except BadRequest as e:
            # Expected: message not modified, deleted, or user blocked bot
            logger.debug(f"Could not update status message: {e}")
        except Exception as e:
            # Unexpected error - log for debugging
            logger.warning(f"Unexpected error updating status message: {e}")

    logger.info(f"User requested navigation to: {location}")
    success = await spot_controller.navigate_to(location, send_status)

    if success:
        logger.info(f"Navigation to {location} completed successfully")
        await query.edit_message_text(f"Arrived at {location.title()}!")
    else:
        logger.warning(f"Navigation to {location} failed")
        await query.edit_message_text(f"Failed to navigate to {location.title()}")


async def sound(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Play a sound from local WAV files via Spot CAM audio service."""
    if not await authorize(update, "sound"):
        return

    if not update.message:
        return

    sounds = get_available_sounds()
    if not sounds:
        await update.message.reply_text(
            "No WAV files found.\n"
            "Add .wav files to the 'sounds/' folder first."
        )
        return

    raw_args = getattr(context, "args", [])
    args = raw_args if isinstance(raw_args, list) else []

    # Direct play mode: /sound <name> [gain]
    if args:
        if len(args) > 2:
            await update.message.reply_text("Usage: /sound <name> [gain]")
            return

        sound_name = args[0].strip().lower()
        if sound_name.endswith(".wav"):
            sound_name = sound_name[:-4]

        gain: Optional[float] = None
        if len(args) == 2:
            try:
                gain = float(args[1])
            except ValueError:
                await update.message.reply_text("Gain must be a number, e.g. /sound beep 0.8")
                return
            if gain < 0.0:
                await update.message.reply_text("Gain must be >= 0.0")
                return

        if sound_name not in sounds:
            available = ", ".join(sorted(sounds.keys()))
            await update.message.reply_text(
                f"Unknown sound '{sound_name}'. Available: {available}"
            )
            return

        if spot_controller is None:
            await update.message.reply_text("SPOT not connected. Use /connect first.")
            return

        async def send_status(msg: str):
            if not update.message:
                return
            await update.message.reply_text(msg)

        success = await spot_controller.play_wav_file(
            sound_name=sound_name,
            wav_path=sounds[sound_name],
            status_callback=send_status,
            gain=gain,
        )
        if success:
            if gain is None:
                await update.message.reply_text(f"Sound '{sound_name}' playback started.")
            else:
                await update.message.reply_text(
                    f"Sound '{sound_name}' playback started with gain {gain:.2f}."
                )
        return

    # Selection mode: /sound -> inline buttons
    reply_markup = _build_sound_keyboard(sorted(sounds.keys()))
    await update.message.reply_text("Choose a sound:", reply_markup=reply_markup)


async def sound_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle sound button presses and play selected WAV file."""
    if not await authorize(update, "sound"):
        return

    if not update.callback_query:
        return

    query = update.callback_query
    await query.answer()

    if not query.data:
        return

    sound_name = query.data
    if sound_name.startswith(SOUND_CALLBACK_DATA_PREFIX):
        sound_name = sound_name[len(SOUND_CALLBACK_DATA_PREFIX):]

    sounds = get_available_sounds()
    if sound_name not in sounds:
        await query.edit_message_text(
            f"Sound '{sound_name}' not found in sounds/ folder anymore."
        )
        return

    if spot_controller is None or not spot_controller.is_connected:
        await query.edit_message_text("SPOT not connected. Use /connect first.")
        return

    async def send_status(msg: str):
        try:
            await query.edit_message_text(msg)
        except BadRequest as e:
            logger.debug(f"Could not update status message: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error updating status message: {e}")

    success = await spot_controller.play_wav_file(
        sound_name=sound_name,
        wav_path=sounds[sound_name],
        status_callback=send_status,
    )

    if success:
        await query.edit_message_text(f"Playing '{sound_name}'")
    else:
        await query.edit_message_text(f"Failed to play '{sound_name}'")


async def volume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Get or set Spot CAM audio volume.

    Usage:
    - /volume
    - /volume <0..100>
    """
    if not await authorize(update, "volume"):
        return

    if not update.message:
        return

    if spot_controller is None or not spot_controller.is_connected:
        await update.message.reply_text("SPOT not connected. Use /connect first.")
        return

    raw_args = getattr(context, "args", [])
    args = raw_args if isinstance(raw_args, list) else []

    if len(args) > 1:
        await update.message.reply_text("Usage: /volume [0-100]")
        return

    # Read current volume
    if not args:
        current = await spot_controller.get_audio_volume_percent()
        if current is None:
            await update.message.reply_text("Spot CAM audio service is not available.")
            return
        await update.message.reply_text(f"Spot CAM volume: {current:.1f}%")
        return

    # Set volume
    try:
        target = float(args[0])
    except ValueError:
        await update.message.reply_text("Volume must be a number between 0 and 100.")
        return

    if target < 0.0 or target > 100.0:
        await update.message.reply_text("Volume must be between 0 and 100.")
        return

    success = await spot_controller.set_audio_volume_percent(target)
    if not success:
        await update.message.reply_text("Could not set volume (Spot CAM audio unavailable).")
        return

    current = await spot_controller.get_audio_volume_percent()
    if current is None:
        await update.message.reply_text(f"Spot CAM volume set to {target:.1f}%.")
    else:
        await update.message.reply_text(f"Spot CAM volume set to {current:.1f}%.")


async def snapshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List image sources or capture a snapshot from a selected source."""
    if not await authorize(update, "snapshot"):
        return

    if not update.message:
        return

    raw_args = getattr(context, "args", [])
    args = raw_args if isinstance(raw_args, list) else []
    args = [arg.strip() for arg in args if arg and arg.strip()]
    is_admin_request = get_effective_user_role(update) == "admin"

    if not args:
        ok, message, sources = await snapshot_manager.list_sources()
        if not ok:
            await update.message.reply_text(f"{message}\n\n{snapshot_usage()}")
            return

        await update.message.reply_text(
            "Available image sources:\n"
            + "\n".join(f"- {source}" for source in sources)
            + "\n\n"
            + snapshot_usage()
        )
        return

    source = " ".join(args)
    await update.message.reply_text(f"Capturing snapshot from '{source}'...")
    ok, message, result = await snapshot_manager.capture_snapshot(source)
    if not ok or result is None:
        await update.message.reply_text(f"{message}\n\n{snapshot_usage()}")
        return

    try:
        image_path = Path(result.saved_path)
        with image_path.open("rb") as image_file:
            if is_admin_request:
                await update.message.reply_photo(
                    photo=image_file,
                    caption=f"Snapshot from '{result.source}'",
                )
            else:
                await update.message.reply_photo(photo=image_file)
    except Exception as exc:
        logger.warning("Could not send snapshot image in chat: %s", exc)
        await update.message.reply_text(
            "Snapshot captured, but image upload to chat failed."
        )
        return

    if is_admin_request:
        await update.message.reply_text(
            "Snapshot captured.\n"
            f"Source: {result.source}\n"
            f"Saved to: {result.saved_path}\n"
            f"Size: {result.byte_size} bytes\n"
            f"Captured at: {result.captured_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )


async def record(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manage WebRTC/timelapse recordings."""
    if not await authorize(update, "record"):
        return

    if not update.message:
        return

    raw_args = getattr(context, "args", [])
    args = raw_args if isinstance(raw_args, list) else []
    args = [arg.strip() for arg in args if arg and arg.strip()]

    if not args:
        await update.message.reply_text(record_usage())
        return

    action = args[0].lower()

    if action == "status":
        status = await recording_manager.get_status()
        await update.message.reply_text("Recording status:\n" + format_record_status(status))
        return

    if action == "start":
        source: Optional[str] = " ".join(args[1:]).strip() if len(args) > 1 else None
        if source == "":
            source = None
        ok, message = await recording_manager.start_recording(source)
        if ok:
            await update.message.reply_text(message)
        else:
            await update.message.reply_text(f"{message}\n\n{record_usage()}")
        return

    if action in {"stop", "end"}:
        await update.message.reply_text("Finalizing recording...")
        ok, message, result = await recording_manager.stop_recording()
        if not ok or result is None:
            await update.message.reply_text(f"{message}\n\n{record_usage()}")
            return

        try:
            video_path = Path(result.video_path)
            with video_path.open("rb") as video_file:
                await update.message.reply_video(
                    video=video_file,
                    caption=(
                        f"Recording ({result.backend}) from '{result.source}'\n"
                        f"Frames: {result.frame_count}\n"
                        f"Duration: {result.duration_seconds:.1f}s\n"
                        f"Audio: {'yes' if result.has_audio else 'no'}"
                    ),
                )
        except Exception as exc:
            logger.warning("Could not send recording video in chat: %s", exc)
            await update.message.reply_text(
                "Recording finalized, but video upload failed.\n"
                f"Saved to: {result.video_path}"
            )
            return

        await update.message.reply_text(
            "Recording finalized.\n"
            f"Saved to: {result.video_path}\n"
            f"Backend: {result.backend}\n"
            f"Frames: {result.frame_count}\n"
            f"Duration: {result.duration_seconds:.1f}s\n"
            f"Audio: {'yes' if result.has_audio else 'no'}"
        )
        return

    if action in {"abort", "terminate", "cancel"}:
        ok, message = await recording_manager.abort_recording()
        await update.message.reply_text(message if ok else f"{message}\n\n{record_usage()}")
        return

    await update.message.reply_text(record_usage())


async def task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manage high-level tasks like gipfeli delivery."""
    if not await authorize(update, "task"):
        return

    if not update.message:
        return

    raw_args = getattr(context, "args", [])
    args = raw_args if isinstance(raw_args, list) else []
    args = [arg.strip() for arg in args if arg and arg.strip()]

    if not args:
        await update.message.reply_text(task_usage())
        return

    command = args[0].lower()

    if command == "status":
        status = await task_manager.get_status()
        await update.message.reply_text(
            "Global task status:\n" + format_task_status_message(status)
        )
        return

    if command == "cancel":
        if not await authorize_operator(update):
            return
        _, message = await task_manager.cancel_active_task()
        await update.message.reply_text(message)
        return

    if command != "gipfeli":
        await update.message.reply_text(task_usage())
        return

    if len(args) == 1:
        await update.message.reply_text(gipfeli_usage())
        return

    sub_or_destination = args[1].lower()
    if sub_or_destination == "status":
        status = await task_manager.get_status()
        if status.task_name != "gipfeli" or status.state == TaskState.IDLE:
            await update.message.reply_text(
                "No gipfeli task has been started yet.\n"
                "Use /task gipfeli <destination> to start one.\n"
                "Use /task status for global task status."
            )
            return

        await update.message.reply_text(
            "Gipfeli task status:\n" + format_task_status_message(status)
        )
        return
    if sub_or_destination == "cancel":
        if not await authorize_operator(update):
            return
        _, message = await task_manager.cancel_active_task()
        await update.message.reply_text(message)
        return

    if not await authorize_operator(update):
        return

    destination = " ".join(args[1:]).strip()
    if not destination:
        await update.message.reply_text(gipfeli_usage())
        return

    available_destinations = get_gipfeli_destinations()
    destination_key = destination.lower()
    if destination_key not in available_destinations:
        await update.message.reply_text(
            f"Unknown gipfeli destination '{destination}'.\n"
            f"Available destinations: {', '.join(available_destinations)}\n"
            "Use /task gipfeli to list usage and destinations."
        )
        return

    async def send_status(msg: str) -> None:
        if update.message:
            await update.message.reply_text(msg)

    ok, message = await task_manager.start_gipfeli_task(destination_key, send_status)
    await update.message.reply_text(message)


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user message."""
    if not (update.message and update.message.text):
        return
    await update.message.reply_text(update.message.text)


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inform the user that the command was not found."""
    if not update.message:
        return

    command_name = extract_command_name(update.message.text or "")
    if not await authorize(update, command_name):
        return

    await update.message.reply_text(
        "Sorry, I didn't understand that command.\n\n"
        "Available commands:\n"
        "/start - Show welcome message\n"
        "/id - Show your Telegram ID\n"
        "/help - Get help\n"
        "/connect - Connect to SPOT robot\n"
        "/goto - Go to a location\n"
        "/sound - Play a sound\n"
        "/volume - Get/set volume\n"
        "/snapshot - List/capture camera snapshots\n"
        "/record - Start/stop/abort recordings\n"
        "/task - Run task commands"
    )


async def post_init(application: Application) -> None:
    """Try to connect to SPOT once on startup."""
    global spot_controller

    if not APP_SETTINGS.telegram.spot_auto_connect:
        logger.info(
            "SPOT auto-connect disabled via app settings (%s). "
            "Telegram bot running in Telegram-only mode.",
            APP_SETTINGS.config_path,
        )
        return

    hostname = DEFAULT_SPOT_HOSTNAME
    map_path = DEFAULT_MAP_PATH

    logger.info(f"Attempting auto-connect to SPOT at {hostname}...")
    spot_controller = SpotController(hostname, map_path)

    async def log_status(msg: str):
        logger.info(f"SPOT: {msg}")

    try:
        success = await spot_controller.connect(log_status)
        if success:
            logger.info("SPOT connected successfully on startup")
        else:
            logger.warning("SPOT auto-connect failed. Use /connect to retry.")
    except Exception as e:
        logger.warning(f"SPOT auto-connect failed: {e}. Use /connect to retry.")


async def post_shutdown(application: Application) -> None:
    """
    Gracefully disconnect from SPOT when the bot shuts down.

    This is called when the bot receives SIGINT (Ctrl+C) or SIGTERM,
    ensuring the lease is properly released so reconnection is possible.
    """
    global spot_controller

    logger.info("Bot shutting down - releasing SPOT resources...")

    cancelled, _ = await task_manager.cancel_active_task()
    if cancelled:
        logger.info("Cancelled active task during shutdown")

    aborted, _ = await recording_manager.abort_recording()
    if aborted:
        logger.info("Aborted active recording during shutdown")

    if spot_controller is not None:
        try:
            if spot_controller.is_connected:
                await spot_controller.disconnect()
                logger.info("SPOT disconnected successfully during shutdown")
            else:
                logger.info("SPOT was not connected, nothing to disconnect")
        except Exception as e:
            logger.error(f"Error disconnecting SPOT during shutdown: {e}")
        finally:
            spot_controller = None

    logger.info("Shutdown complete")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors - log network errors concisely, others with full traceback."""
    if isinstance(context.error, NetworkError):
        logger.warning(f"Network error (will retry): {context.error}")
    else:
        logger.exception("Unhandled exception:", exc_info=context.error)


def main() -> None:
    """Start the bot."""
    load_dotenv()
    initialize_rbac()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not found in environment variables")

    # Create the Application with lifecycle hooks
    application = (
        Application.builder()
        .token(token)
        .post_init(post_init)        # Auto-connect on startup
        .post_shutdown(post_shutdown) # Release lease on shutdown
        .build()
    )

    # Connection commands
    application.add_handler(CommandHandler("connect", connect_spot))
    application.add_handler(CommandHandler("forceconnect", forceconnect_spot))
    application.add_handler(CommandHandler("disconnect", disconnect_spot))
    application.add_handler(CommandHandler("status", status_spot))

    # Navigation commands
    application.add_handler(CommandHandler("goto", goto))
    application.add_handler(CallbackQueryHandler(goto_callback, pattern=f"^{CALLBACK_DATA_PREFIX}"))
    application.add_handler(CommandHandler("sound", sound))
    application.add_handler(CommandHandler("volume", volume))
    application.add_handler(CommandHandler("snapshot", snapshot))
    application.add_handler(CommandHandler("record", record))
    application.add_handler(CommandHandler("task", task))
    application.add_handler(
        CallbackQueryHandler(sound_callback, pattern=f"^{SOUND_CALLBACK_DATA_PREFIX}")
    )

    # General commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("id", id_command))
    application.add_handler(CommandHandler("help", help_command))

    # on non command i.e message - echo the message on Telegram
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # handle unknown commands
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    # Error handler for cleaner logging
    application.add_error_handler(error_handler)

    logger.info("Starting bot with graceful shutdown enabled...")

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
