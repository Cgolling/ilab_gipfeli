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

# Run the Telegram bot
uv run python -m src.telegram.echobot
```

## Environment Variables

Required in `.env` file (see `.env.example`):
- `BOSDYN_CLIENT_USERNAME` - Robot username
- `BOSDYN_CLIENT_PASSWORD` - Robot password
- `TELEGRAM_BOT_TOKEN` - Bot token from BotFather
- `SPOT_HOSTNAME` - Robot IP address (default: 192.168.80.3)

## Architecture

### Three Main Components

1. **Telegram Bot** (`src/telegram/echobot.py`) - User interface handling commands (`/start`, `/help`, `/connect`, `/goto`) and inline button callbacks for location selection

2. **SPOT Controller** (`src/spot/spot_controller.py`) - Robot control managing authentication, lease acquisition, map upload, fiducial-based localization, and GraphNav navigation

3. **Navigation Maps** (`maps/`) - Pre-recorded GraphNav maps with waypoint and edge snapshots for autonomous navigation

### Key Patterns

- **Async-first design**: Uses asyncio throughout; blocking SPOT SDK calls wrapped with `asyncio.to_thread()`
- **Status callbacks**: Long-running operations (connection, navigation) accept async callbacks for real-time Telegram status updates
- **Heartbeat pattern**: Navigation sends periodic 3-second updates with elapsed time
- **Global state**: Single `SpotController` instance shared across Telegram handlers
- **Waypoint mapping**: Locations use 2-letter short codes (from `WAYPOINTS` dict) mapped to full GraphNav waypoint IDs

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
