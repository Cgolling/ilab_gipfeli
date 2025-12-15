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
from typing import Optional

# Add project root to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dotenv import load_dotenv
from telegram import ForceReply, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

from src.spot import SpotController
from src.logging_config import setup_logging

# Initialize logging (safe to call multiple times)
setup_logging()

logger = logging.getLogger(__name__)

# Configuration constants
DEFAULT_SPOT_HOSTNAME = "192.168.80.3"
DEFAULT_MAP_PATH = "maps/map_catacombs_01"
CALLBACK_DATA_PREFIX = "goto_"

# Global SPOT controller instance
# Thread-safety note: python-telegram-bot uses a single-threaded async model,
# so concurrent access to this variable is safe within Telegram handlers.
# The SpotController itself wraps blocking SDK calls with asyncio.to_thread(),
# which is also safe as those calls don't share mutable state.
# Do NOT access this from external threads without proper synchronization.
spot_controller: Optional[SpotController] = None


# Define a few command handlers. These usually take the two arguments update and
# context.
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    if not update.message or not user:
        return
    await update.message.reply_html(
        rf"Hi {user.mention_html()}!",
        reply_markup=ForceReply(selective=True),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
     
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
        "Other:\n"
        "/start - Start the bot\n"
        "/help - Show this help message"
    )


async def connect_spot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Connect to SPOT robot."""
    global spot_controller
    
    if not update.message:
        return

    hostname = os.getenv("SPOT_HOSTNAME", DEFAULT_SPOT_HOSTNAME)
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
    if not update.message:
        return

    hostname = os.getenv("SPOT_HOSTNAME", DEFAULT_SPOT_HOSTNAME)
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

        await update.message.reply_text("\n".join(lines))

    except Exception as e:
        logger.exception(f"Error getting status: {e}")
        await update.message.reply_text(f"Error getting status: {e}")


async def goto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send inline keyboard with location options."""
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


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user message."""
    if not (update.message and update.message.text):
        return
    await update.message.reply_text(update.message.text)


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inform the user that the command was not found."""
    if not update.message:
        return
    await update.message.reply_text(
        "Sorry, I didn't understand that command.\n\n"
        "Available commands:\n"
        "/start - Start the bot\n"
        "/help - Get help\n"
        "/connect - Connect to SPOT robot\n"
        "/goto - Go to a location"
    )


async def post_init(application: Application) -> None:
    """Try to connect to SPOT once on startup."""
    global spot_controller

    hostname = os.getenv("SPOT_HOSTNAME", DEFAULT_SPOT_HOSTNAME)
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

    # Connection commands
    application.add_handler(CommandHandler("connect", connect_spot))
    application.add_handler(CommandHandler("forceconnect", forceconnect_spot))
    application.add_handler(CommandHandler("disconnect", disconnect_spot))
    application.add_handler(CommandHandler("status", status_spot))

    # Navigation commands
    application.add_handler(CommandHandler("goto", goto))
    application.add_handler(CallbackQueryHandler(goto_callback, pattern=f"^{CALLBACK_DATA_PREFIX}"))

    # General commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    # on non command i.e message - echo the message on Telegram
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # handle unknown commands
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    logger.info("Starting bot with graceful shutdown enabled...")

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()