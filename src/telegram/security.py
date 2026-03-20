"""Simple Telegram role-based access control for the bot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


Role = str
DEFAULT_ROLE: Role = "user"
ADMIN_ROLE: Role = "admin"
ADMIN_ONLY_COMMANDS = {"connect", "disconnect", "forceconnect"}


@dataclass(frozen=True)
class SecurityConfig:
    """Runtime configuration for Telegram command access."""

    admin_user_ids: frozenset[int]


def load_security_config(config_path: Path) -> SecurityConfig:
    """Load admin Telegram IDs from TOML configuration."""
    if not config_path.exists():
        return SecurityConfig(admin_user_ids=frozenset())

    with config_path.open("rb") as file:
        raw_data = tomllib.load(file)

    raw_ids = raw_data.get("admin_user_ids", [])
    admin_ids = {int(user_id) for user_id in raw_ids}
    return SecurityConfig(admin_user_ids=frozenset(admin_ids))


def get_user_role(user_id: int | None, config: SecurityConfig) -> Role:
    """Return the effective role for a Telegram user ID."""
    if user_id is not None and user_id in config.admin_user_ids:
        return ADMIN_ROLE
    return DEFAULT_ROLE


def is_admin_command(command_name: str) -> bool:
    """Return whether a command is restricted to admins."""
    return command_name in ADMIN_ONLY_COMMANDS


def can_execute_command(command_name: str, user_id: int | None, config: SecurityConfig) -> bool:
    """Check whether the user is allowed to execute the command."""
    if not is_admin_command(command_name):
        return True
    return get_user_role(user_id, config) == ADMIN_ROLE


def describe_access_denied(command_name: str) -> str:
    """Return a user-facing denial message."""
    if is_admin_command(command_name):
        return f"`/{command_name}` is admin-only."
    return "You are not allowed to run this command."
