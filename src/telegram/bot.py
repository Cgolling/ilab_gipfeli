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
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.error import BadRequest, NetworkError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

from src.spot import SpotController
from src.logging_config import setup_logging
from src.telegram.control_queue import ControlQueue
from src.telegram.security import (
    SecurityConfig,
    can_execute_command,
    describe_access_denied,
    get_user_role,
    load_security_config,
)

# Initialize logging (safe to call multiple times)
setup_logging()

logger = logging.getLogger(__name__)

# Configuration constants
DEFAULT_SPOT_HOSTNAME = "192.168.8.200"
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
DEFAULT_MAP_PATH = os.path.join(PROJECT_ROOT, "maps/ilabZi9_withWP")
DEFAULT_SECURITY_CONFIG_PATH = Path(PROJECT_ROOT) / "config" / "telegram_rbac.toml"
CALLBACK_DATA_PREFIX = "goto_"
WAYPOINTS = {
    "aula": "Aula",
    "turnhalle": "Turnhalle",
    "zimmer9": "Zimmer 9",
    "home": "Home",
}

# Reply keyboard button labels
BTN_START = "Start"
BTN_STOP = "Stop"
BTN_STATUS = "Status"
BTN_HELP = "Help"
BTN_MY_ID = "My ID"
BTN_GOTO = "Go To"
BTN_STANDUP = "Stand Up"
BTN_SITDOWN = "Sit Down"

# Keyboards for different states
KB_INITIAL = ReplyKeyboardMarkup(
    [[BTN_START, BTN_STATUS], [BTN_HELP, BTN_MY_ID]],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Tap a button to get started",
)

KB_HAS_CONTROL = ReplyKeyboardMarkup(
    [[BTN_GOTO], [BTN_STANDUP, BTN_SITDOWN], [BTN_STATUS, BTN_STOP]],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="You have control of SPOT",
)

KB_IN_QUEUE = ReplyKeyboardMarkup(
    [[BTN_STATUS, BTN_STOP]],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Waiting in queue...",
)


def _keyboard_for_user(user_id: int | None, context: ContextTypes.DEFAULT_TYPE) -> ReplyKeyboardMarkup:
    """Return the appropriate reply keyboard based on the user's queue state."""
    if user_id is None:
        return KB_INITIAL
    control_queue = _get_control_queue(context)
    if control_queue.has_control(user_id):
        return KB_HAS_CONTROL
    if control_queue.status_message_for(user_id) is not None:
        return KB_IN_QUEUE
    return KB_INITIAL


def _get_security_config(context: ContextTypes.DEFAULT_TYPE) -> SecurityConfig:
    """Return the loaded security configuration."""
    return context.bot_data["security_config"]


def _get_control_queue(context: ContextTypes.DEFAULT_TYPE) -> ControlQueue:
    """Return the current control queue."""
    return context.bot_data["control_queue"]


async def _reply_access_denied(update: Update, command_name: str) -> None:
    """Send an access denied message for a command."""
    message = describe_access_denied(command_name)

    if update.message:
        await update.message.reply_text(message)
        return

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(message)


async def _authorize_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, command_name: str
) -> bool:
    """Check whether the effective user may execute the command."""
    user = update.effective_user
    config = _get_security_config(context)

    if can_execute_command(command_name, getattr(user, "id", None), config):
        return True

    logger.warning(
        "Denied command /%s for Telegram user %s",
        command_name,
        getattr(user, "id", None),
    )
    await _reply_access_denied(update, command_name)
    return False


async def _notify_queue_updates(
    context: ContextTypes.DEFAULT_TYPE, previous_statuses: dict[int, str]
) -> None:
    """Notify queued users if their queue status changed."""
    control_queue = _get_control_queue(context)
    current_statuses = control_queue.user_statuses()

    for user_id, message in current_statuses.items():
        if previous_statuses.get(user_id) == message:
            continue

        chat_id = control_queue.chat_id_for(user_id)
        if chat_id is None:
            continue

        keyboard = _keyboard_for_user(user_id, context)
        await context.bot.send_message(chat_id=chat_id, text=message, reply_markup=keyboard)


async def _ensure_control(
    update: Update, context: ContextTypes.DEFAULT_TYPE, command_name: str
) -> bool:
    """Require that the current user holds robot control before proceeding."""
    user = update.effective_user
    control_queue = _get_control_queue(context)

    if control_queue.has_control(getattr(user, "id", None)):
        return True

    status_message = control_queue.status_message_for(getattr(user, "id", None))
    if status_message is None:
        message = (
            f"`/{command_name}` requires control.\n"
            "Use /start to join the queue."
        )
    else:
        message = (
            f"`/{command_name}` requires control.\n"
            f"{status_message}"
        )

    if update.message:
        await update.message.reply_text(message)
        return False

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(message)
        return False

    return False

# Define a few command handlers. These usually take the two arguments update and
# context.
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Join the robot control queue or show current queue status."""
    user = update.effective_user
    chat = update.effective_chat
    if not update.message or not user or not chat:
        return

    control_queue = _get_control_queue(context)
    previous_statuses = control_queue.user_statuses()
    _, added = control_queue.join(
        user_id=user.id,
        chat_id=chat.id,
        display_name=user.full_name,
    )

    if added:
        keyboard = _keyboard_for_user(user.id, context)
        status_message = control_queue.status_message_for(user.id)
        if status_message:
            await update.message.reply_text(status_message, reply_markup=keyboard)
        await _notify_queue_updates(context, previous_statuses)
        return

    status_message = control_queue.status_message_for(user.id)
    if status_message:
        keyboard = _keyboard_for_user(user.id, context)
        await update.message.reply_text(status_message, reply_markup=keyboard)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
     
    if not update.message:
        return
    
    user = update.effective_user
    keyboard = _keyboard_for_user(getattr(user, "id", None), context)
    await update.message.reply_text(
        "SPOT Robot Control Bot\n\n"
        "Use the buttons below to control SPOT.\n\n"
        "Admin commands (type manually):\n"
        "/connect - Connect to SPOT robot\n"
        "/disconnect - Disconnect and release lease\n"
        "/forceconnect - Force take control\n\n"
        "All commands:\n"
        "Start - Join the control queue\n"
        "Stop - Release control or leave the queue\n"
        "Go To - Navigate to a location\n"
        "Stand Up / Sit Down - Control posture\n"
        "Status - Show robot status\n"
        "Help - Show this help message\n"
        "My ID - Show your Telegram user ID",
        reply_markup=keyboard,
    )


async def _handle_connection(update: Update, context: ContextTypes.DEFAULT_TYPE, force: bool) -> None:
    """Helper to handle connection logic (connect or forceconnect)."""
    if not update.message:
        return

    hostname = os.getenv("SPOT_HOSTNAME", DEFAULT_SPOT_HOSTNAME)
    map_path = DEFAULT_MAP_PATH
    action_desc = "forceconnect" if force else "connect"

    logger.info(f"User initiated /{action_desc} to SPOT at {hostname}")
    
    if force:
        await update.message.reply_text(
            "FORCE CONNECT: Taking control from any other client...\n"
            "(This will disconnect tablet or other scripts!)"
        )
        status_message = await update.message.reply_text("Initializing force connection...")
    else:
        await update.message.reply_text("Starting SPOT connection procedure...")
        status_message = await update.message.reply_text("Starting SPOT connection procedure...")

    # Disconnect existing controller if any to ensure clean slate
    current_controller = context.bot_data.get("spot_controller")
    if current_controller and current_controller.is_connected:
        try:
            await current_controller.disconnect()
        except Exception:
            pass

    # Create new controller
    spot_controller = SpotController(hostname, map_path)
    context.bot_data["spot_controller"] = spot_controller

    async def send_status(msg: str):
        if not update.message:
            return
        await update.message.reply_text(msg)
        try:
            await status_message.edit_text(msg)
        except BadRequest:
            pass

    success = await spot_controller.connect(send_status, force_acquire=force)

    if success:
        logger.info(f"SPOT {action_desc} successful")
        msg = "SPOT is ready! Lease forcefully acquired." if force else "SPOT is ready! Use /goto to navigate."
        await update.message.reply_text(msg)
    else:
        logger.warning(f"SPOT {action_desc} failed")
        msg = "Force connection failed. Check logs." if force else "SPOT connection failed."
        await update.message.reply_text(msg)


async def connect_spot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Connect to SPOT robot."""
    if not await _authorize_command(update, context, "connect"):
        return
    await _handle_connection(update, context, force=False)


async def forceconnect_spot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Force connect to SPOT robot, taking the lease from any other client."""
    if not await _authorize_command(update, context, "forceconnect"):
        return
    await _handle_connection(update, context, force=True)


async def disconnect_spot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Disconnect from SPOT and release the lease."""
    if not update.message:
        return
    if not await _authorize_command(update, context, "disconnect"):
        return

    spot_controller = context.bot_data.get("spot_controller")
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
    if not update.message:
        return
    
    user = update.effective_user
    keyboard = _keyboard_for_user(getattr(user, "id", None), context)

    spot_controller = context.bot_data.get("spot_controller")
    if spot_controller is None:
        await update.message.reply_text(
            "SPOT Status: Not initialized\n\n"
            "Use /connect to connect to the robot.",
            reply_markup=keyboard,
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

        await update.message.reply_text("\n".join(lines), reply_markup=keyboard)

    except Exception as e:
        logger.exception(f"Error getting status: {e}")
        await update.message.reply_text(f"Error getting status: {e}", reply_markup=keyboard)


async def goto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send inline keyboard with location options."""
    if not update.message:
        return
    if not await _authorize_command(update, context, "goto"):
        return
    if not await _ensure_control(update, context, "goto"):
        return
    
    spot_controller = context.bot_data.get("spot_controller")
    if spot_controller is None or not spot_controller.is_connected:
        await update.message.reply_text("SPOT not connected. Use /connect first.")
        return
    
    keyboard = []
    row = []
    for key, name in WAYPOINTS.items():
        row.append(InlineKeyboardButton(name, callback_data=f"{CALLBACK_DATA_PREFIX}{key}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Where do you want to go?", reply_markup=reply_markup)


async def goto_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle goto button presses and navigate SPOT to the selected location."""
    if not update.callback_query:
        return
    if not await _authorize_command(update, context, "goto"):
        return
    if not await _ensure_control(update, context, "goto"):
        return
    
    query = update.callback_query
    await query.answer()

    if not query.data:
        return

    location = query.data.replace(CALLBACK_DATA_PREFIX, "")
    location_name = WAYPOINTS.get(location, location.title())

    spot_controller = context.bot_data.get("spot_controller")
    # Check if SPOT is connected
    if spot_controller is None or not spot_controller.is_connected:
        await query.edit_message_text("SPOT not connected. Use /connect first.")
        return

    # Replace the inline keyboard message with a confirmation, then send a
    # separate status message that we edit with heartbeat updates.  Editing a
    # standalone message is more reliable than editing the inline-keyboard
    # message repeatedly (Telegram can reject edits on callback messages).
    await query.edit_message_text(f"Navigating to {location_name}...")
    chat_id = query.message.chat.id if query.message else None
    status_message = None
    if chat_id:
        status_message = await context.bot.send_message(
            chat_id=chat_id, text=f"Navigating to {location_name}..."
        )

    async def send_status(msg: str):
        nonlocal status_message
        if status_message is None:
            return
        try:
            await status_message.edit_text(msg)
        except BadRequest as e:
            # Expected: message not modified, deleted, or user blocked bot
            logger.debug(f"Could not update status message: {e}")
        except Exception as e:
            # Unexpected error - log for debugging
            logger.warning(f"Unexpected error updating status message: {e}")

    logger.info(f"User requested navigation to: {location} ({location_name})")
    try:
        success = await spot_controller.navigate_to(location, send_status)
    except Exception as e:
        logger.exception(f"Navigation error: {e}")
        await send_status(f"Error during navigation: {e}")
        return

    if success:
        logger.info(f"Navigation to {location_name} completed successfully")
        await send_status(f"Arrived at {location_name}!")
    else:
        logger.warning(f"Navigation to {location_name} failed")
        await send_status(f"Failed to navigate to {location_name}")


async def standup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Make SPOT stand up."""
    if not update.message:
        return
    if not await _ensure_control(update, context, "standup"):
        return

    spot_controller = context.bot_data.get("spot_controller")
    if spot_controller is None or not spot_controller.is_connected:
        await update.message.reply_text("SPOT not connected. Use /connect first.")
        return

    try:
        await spot_controller.stand()
        await update.message.reply_text("SPOT is standing.")
    except Exception as e:
        logger.exception(f"Stand failed: {e}")
        await update.message.reply_text(f"Failed to stand: {e}")


async def sitdown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Make SPOT sit down."""
    if not update.message:
        return
    if not await _ensure_control(update, context, "sitdown"):
        return

    spot_controller = context.bot_data.get("spot_controller")
    if spot_controller is None or not spot_controller.is_connected:
        await update.message.reply_text("SPOT not connected. Use /connect first.")
        return

    try:
        await spot_controller.sit()
        await update.message.reply_text("SPOT is sitting.")
    except Exception as e:
        logger.exception(f"Sit failed: {e}")
        await update.message.reply_text(f"Failed to sit: {e}")


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the Telegram user ID and effective role."""
    if not update.message or not update.effective_user:
        return

    user = update.effective_user
    role = get_user_role(user.id, _get_security_config(context))
    keyboard = _keyboard_for_user(user.id, context)
    await update.message.reply_text(
        f"Your Telegram user ID is {user.id}.\nRole: {role}",
        reply_markup=keyboard,
    )


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Release robot control or leave the queue."""
    user = update.effective_user
    if not update.message or not user:
        return

    control_queue = _get_control_queue(context)
    previous_statuses = control_queue.user_statuses()
    _, had_control = control_queue.leave(user.id)

    if user.id not in previous_statuses:
        await update.message.reply_text("You are not in the queue.", reply_markup=KB_INITIAL)
        return

    if had_control:
        await update.message.reply_text("You released control.", reply_markup=KB_INITIAL)
    else:
        await update.message.reply_text("You left the queue.", reply_markup=KB_INITIAL)

    await _notify_queue_updates(context, previous_statuses)


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inform the user that the command was not found."""
    if not update.message:
        return
    user = update.effective_user
    keyboard = _keyboard_for_user(getattr(user, "id", None), context)
    await update.message.reply_text(
        "Sorry, I didn't understand that command.\n"
        "Use the buttons below or /help to see available commands.",
        reply_markup=keyboard,
    )


async def post_init(application: Application) -> None:
    """Try to connect to SPOT once on startup."""
    hostname = os.getenv("SPOT_HOSTNAME", DEFAULT_SPOT_HOSTNAME)
    map_path = DEFAULT_MAP_PATH

    logger.info(f"Attempting auto-connect to SPOT at {hostname}...")
    spot_controller = SpotController(hostname, map_path)
    application.bot_data["spot_controller"] = spot_controller

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
    spot_controller = application.bot_data.get("spot_controller")

    logger.info("Bot shutting down - releasing SPOT resources...")

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
            application.bot_data["spot_controller"] = None

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
    application.bot_data["security_config"] = load_security_config(DEFAULT_SECURITY_CONFIG_PATH)
    application.bot_data["control_queue"] = ControlQueue()

    # Connection commands
    application.add_handler(CommandHandler("connect", connect_spot))
    application.add_handler(CommandHandler("forceconnect", forceconnect_spot))
    application.add_handler(CommandHandler("disconnect", disconnect_spot))
    application.add_handler(CommandHandler("status", status_spot))

    # Posture commands
    application.add_handler(CommandHandler("standup", standup))
    application.add_handler(CommandHandler("sitdown", sitdown))

    # Navigation commands
    application.add_handler(CommandHandler("goto", goto))
    application.add_handler(CallbackQueryHandler(goto_callback, pattern=f"^{CALLBACK_DATA_PREFIX}"))

    # General commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("id", id_command))
    application.add_handler(CommandHandler("help", help_command))

    # Reply keyboard button handlers (text messages matching button labels)
    application.add_handler(MessageHandler(filters.Text([BTN_START]), start))
    application.add_handler(MessageHandler(filters.Text([BTN_STOP]), stop_command))
    application.add_handler(MessageHandler(filters.Text([BTN_STATUS]), status_spot))
    application.add_handler(MessageHandler(filters.Text([BTN_HELP]), help_command))
    application.add_handler(MessageHandler(filters.Text([BTN_MY_ID]), id_command))
    application.add_handler(MessageHandler(filters.Text([BTN_GOTO]), goto))
    application.add_handler(MessageHandler(filters.Text([BTN_STANDUP]), standup))
    application.add_handler(MessageHandler(filters.Text([BTN_SITDOWN]), sitdown))

    # handle unknown commands
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    # Error handler for cleaner logging
    application.add_error_handler(error_handler)

    logger.info("Starting bot with graceful shutdown enabled...")

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
