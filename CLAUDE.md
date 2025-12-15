# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

iLab Projekt Gipfeli is a Gipfeli (croissant) delivery service for iLab Kanti Glarus. It integrates a Telegram Bot for ordering snacks with a Boston Dynamics SPOT robot for autonomous delivery using GraphNav-based navigation.

## Development Commands

```bash
# Install UV package manager (if not installed)
pip install uv

# Create virtual environment and install dependencies
uv venv .venv
source .venv/bin/activate
uv sync

# Install dev dependencies (for testing)
uv sync --extra dev

# Run the Telegram bot
uv run python -m src.telegram.echobot
```

## Testing

```bash
# Run all tests
uv run pytest

# Run with verbose output
uv run pytest -v

# Run specific test file
uv run pytest tests/test_spot/test_pure_functions.py

# Run tests matching a pattern
uv run pytest -k "test_connect"

# Run with coverage report (HTML)
uv run pytest --cov-report=html

# View coverage report
open coverage_html/index.html
```

## Logging

Logs are written to `logs/` directory with automatic rotation (5 MB max, 5 backups):
- `logs/spot.log` - SPOT robot operations (connection, lease, navigation, power)
- `logs/telegram.log` - Telegram bot events (commands, callbacks)

Console shows INFO level; files capture DEBUG level for detailed debugging.

## Environment Variables

Required in `.env` file (see `.env.example`):
- `BOSDYN_CLIENT_USERNAME` - Robot username
- `BOSDYN_CLIENT_PASSWORD` - Robot password
- `TELEGRAM_BOT_TOKEN` - Bot token from BotFather
- `SPOT_HOSTNAME` - Robot IP address (default: 192.168.80.3)

## Architecture

### Three Main Components

1. **Telegram Bot** (`src/telegram/echobot.py`) - User interface handling commands (`/start`, `/help`, `/connect`, `/disconnect`, `/forceconnect`, `/status`, `/goto`) and inline button callbacks for location selection

2. **SPOT Controller** (`src/spot/spot_controller.py`) - Robot control managing authentication, lease acquisition, map upload, fiducial-based localization, and GraphNav navigation

3. **Navigation Maps** (`maps/`) - Pre-recorded GraphNav maps with waypoint and edge snapshots for autonomous navigation

### Key Patterns

- **Async-first design**: Uses asyncio throughout; blocking SPOT SDK calls wrapped with `asyncio.to_thread()`
- **Status callbacks**: Long-running operations (connection, navigation) accept async callbacks for real-time Telegram status updates
- **Heartbeat pattern**: Navigation sends periodic 3-second updates with elapsed time
- **Global state**: Single `SpotController` instance shared across Telegram handlers
- **Waypoint mapping**: Locations use 2-letter short codes (from `WAYPOINTS` dict) mapped to full GraphNav waypoint IDs
- **Graceful shutdown**: `post_shutdown` hook automatically releases lease when bot stops (Ctrl+C/SIGTERM)
- **Force acquire**: `/forceconnect` can take lease from crashed bots or tablet without manual intervention

### Waypoint Locations

Defined in `src/spot/spot_controller.py`:
- `Au` - Aula (assembly hall)
- `Tr` - Triangle
- `Ha` - Hauswart (caretaker's room)
- `Tu` - Turnhalle (gymnasium)

## Dependencies

- Python >= 3.13
- `python-telegram-bot` - Telegram bot framework
- `bosdyn-client`, `bosdyn-mission` - Boston Dynamics SPOT SDK
- `python-dotenv` - Environment variable management
- `spot-sdk/` - Git submodule pointing to official Boston Dynamics SDK repository
