"""
Centralized logging configuration with rotating file handlers.

This module provides production-ready logging for debugging connection
issues, lease problems, and navigation events with the SPOT robot.

Usage:
    from src.logging_config import setup_logging
    setup_logging()  # Call once at application startup
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Log directory (relative to project root)
LOG_DIR = Path(__file__).parent.parent / "logs"

# Log format with function name and line number for debugging
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Rotation settings: 5 MB per file, keep 5 backups (25 MB total max)
MAX_LOG_SIZE_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5


def setup_logging(
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
) -> None:
    """
    Configure logging with console and rotating file handlers.

    Sets up two logging outputs:
    - Console: Shows INFO and above (user-facing messages)
    - File: Shows DEBUG and above (detailed debugging info)

    Log files are stored in the logs/ directory with automatic rotation.

    Args:
        console_level: Minimum log level for console output (default: INFO)
        file_level: Minimum log level for file output (default: DEBUG)

    Example:
        # At application startup
        from src.logging_config import setup_logging
        setup_logging()

        # For verbose console output during debugging
        setup_logging(console_level=logging.DEBUG)
    """
    # Create logs directory if it doesn't exist
    LOG_DIR.mkdir(exist_ok=True)

    # Create formatter
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture all, let handlers filter

    # Clear any existing handlers (prevents duplicate logs on re-init)
    root_logger.handlers.clear()

    # Console handler - INFO level for user-facing output
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Rotating file handler for SPOT-related logs
    spot_file_handler = RotatingFileHandler(
        LOG_DIR / "spot.log",
        maxBytes=MAX_LOG_SIZE_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    spot_file_handler.setLevel(file_level)
    spot_file_handler.setFormatter(formatter)

    # Rotating file handler for Telegram bot logs
    telegram_file_handler = RotatingFileHandler(
        LOG_DIR / "telegram.log",
        maxBytes=MAX_LOG_SIZE_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    telegram_file_handler.setLevel(file_level)
    telegram_file_handler.setFormatter(formatter)

    # Attach file handlers to specific loggers
    spot_logger = logging.getLogger("src.spot")
    spot_logger.addHandler(spot_file_handler)

    telegram_logger = logging.getLogger("src.telegram")
    telegram_logger.addHandler(telegram_file_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("bosdyn").setLevel(logging.WARNING)

    # Log startup message
    root_logger.info("Logging initialized - console: %s, file: %s",
                     logging.getLevelName(console_level),
                     logging.getLevelName(file_level))
