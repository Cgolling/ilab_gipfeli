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
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

from src.spot import SpotController

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Global SPOT controller instance
spot_controller: Optional[SpotController] = None


# Define a few command handlers. These usually take the two arguments update and
# context.
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_html(
        rf"Hi {user.mention_html()}!",
        reply_markup=ForceReply(selective=True),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    await update.message.reply_text(
        "SPOT Robot Control Bot\n\n"
        "Commands:\n"
        "/start - Start the bot\n"
        "/help - Show this help message\n"
        "/connect - Connect to SPOT robot\n"
        "/goto - Navigate to a location"
    )


async def connect_spot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Connect to SPOT robot."""
    global spot_controller

    hostname = os.getenv("SPOT_HOSTNAME", "192.168.80.3")
    map_path = "maps/map_catacombs_01"

    await update.message.reply_text("Starting SPOT connection procedure...")

    spot_controller = SpotController(hostname, map_path)

    async def send_status(msg: str):
        await update.message.reply_text(msg)

    success = await spot_controller.connect(send_status)

    if success:
        await update.message.reply_text("SPOT is ready! Use /goto to navigate.")
    else:
        await update.message.reply_text("SPOT connection failed. Use /connect to retry.")


async def goto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send inline keyboard with location options."""
    keyboard = [
        [
            InlineKeyboardButton("Aula", callback_data="goto_aula"),
            InlineKeyboardButton("Triangle", callback_data="goto_triangle"),
        ],
        [
            InlineKeyboardButton("Hauswart", callback_data="goto_hauswart"),
            InlineKeyboardButton("Turnhalle", callback_data="goto_turnhalle"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Where do you want to go?", reply_markup=reply_markup)


async def goto_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle goto button presses and navigate SPOT to the selected location."""
    query = update.callback_query
    await query.answer()

    location = query.data.replace("goto_", "")

    # Check if SPOT is connected
    if spot_controller is None or not spot_controller.is_connected:
        await query.edit_message_text("SPOT not connected. Use /connect first.")
        return

    # Navigate with heartbeat updates
    async def send_status(msg: str):
        try:
            await query.edit_message_text(msg)
        except Exception:
            pass  # Message might have been deleted or edited already

    success = await spot_controller.navigate_to(location, send_status)

    if success:
        await query.edit_message_text(f"Arrived at {location.title()}!")
    else:
        await query.edit_message_text(f"Failed to navigate to {location.title()}")


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user message."""
    await update.message.reply_text(update.message.text)


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inform the user that the command was not found."""
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

    hostname = os.getenv("SPOT_HOSTNAME", "192.168.80.3")
    map_path = "maps/map_catacombs_01"

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


def main() -> None:
    """Start the bot."""
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not found in environment variables")

    # Create the Application with post_init for auto-connect
    application = Application.builder().token(token).post_init(post_init).build()

    # on different commands - answer in Telegram
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("connect", connect_spot))
    application.add_handler(CommandHandler("goto", goto))
    application.add_handler(CallbackQueryHandler(goto_callback, pattern="^goto_"))

    # on non command i.e message - echo the message on Telegram
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # handle unknown commands
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()