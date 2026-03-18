"""Role-based access control for Telegram command handling."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import yaml
from telegram import Update

Role = Literal["viewer", "operator", "admin"]

ROLE_LEVELS: dict[Role, int] = {
    "viewer": 1,
    "operator": 2,
    "admin": 3,
}

COMMAND_REQUIRED_ROLE: dict[str, Role] = {
    "start": "viewer",
    "id": "viewer",
    "help": "viewer",
    "status": "viewer",
    "map": "viewer",
    "task": "viewer",
    "connect": "operator",
    "disconnect": "operator",
    "goto": "operator",
    "sound": "operator",
    "volume": "operator",
    "snapshot": "operator",
    "record": "operator",
    "forceconnect": "admin",
}

CRITICAL_COMMANDS: set[str] = {
    "connect",
    "disconnect",
    "goto",
    "sound",
    "volume",
    "snapshot",
    "record",
    "forceconnect",
}


@dataclass(frozen=True)
class RbacConfig:
    """Resolved RBAC configuration."""

    private_only: bool
    admin_user_ids: set[int]
    users: dict[int, Role]


def _parse_role(value: str) -> Role:
    normalized = value.strip().lower()
    if normalized not in ROLE_LEVELS:
        raise ValueError(f"Unknown role '{value}'. Allowed roles: viewer, operator, admin.")
    return normalized  # type: ignore[return-value]


def load_rbac_config(path: str) -> RbacConfig:
    """
    Load and validate RBAC config from YAML.

    Expected schema:
    chat_policy:
      private_only: true
    admin_user_ids: [123]
    users:
      "123": admin
      "456": operator
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"RBAC config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if not isinstance(raw, dict):
        raise ValueError("RBAC config root must be a mapping/object.")

    chat_policy = raw.get("chat_policy", {})
    if chat_policy is None:
        chat_policy = {}
    if not isinstance(chat_policy, dict):
        raise ValueError("chat_policy must be a mapping/object.")
    private_only = bool(chat_policy.get("private_only", True))

    admin_user_ids_raw = raw.get("admin_user_ids", [])
    if admin_user_ids_raw is None:
        admin_user_ids_raw = []
    if not isinstance(admin_user_ids_raw, list):
        raise ValueError("admin_user_ids must be a list.")
    admin_user_ids = {int(item) for item in admin_user_ids_raw}

    users_raw = raw.get("users", {})
    if users_raw is None:
        users_raw = {}
    if not isinstance(users_raw, dict):
        raise ValueError("users must be a mapping/object.")

    users: dict[int, Role] = {}
    for user_id_raw, role_raw in users_raw.items():
        if not isinstance(role_raw, str):
            raise ValueError(f"Role for user '{user_id_raw}' must be a string.")
        role = _parse_role(role_raw)
        users[int(user_id_raw)] = role

    return RbacConfig(
        private_only=private_only,
        admin_user_ids=admin_user_ids,
        users=users,
    )


def resolve_user_role(user_id: int, config: RbacConfig) -> Role:
    """Resolve effective role for user id, defaulting to viewer."""
    if user_id in config.admin_user_ids:
        return "admin"
    return config.users.get(user_id, "viewer")


def required_role_for_command(command_name: str) -> Optional[Role]:
    """Get required role for a known command."""
    return COMMAND_REQUIRED_ROLE.get(command_name)


def is_allowed(user_role: Role, required_role: Role) -> bool:
    """Check if a user role satisfies required role."""
    return ROLE_LEVELS[user_role] >= ROLE_LEVELS[required_role]


def is_private_chat(update: Update) -> bool:
    """Return True when update comes from a private chat."""
    chat = update.effective_chat
    return bool(chat and chat.type == "private")


def format_deny_message(required_role: Role) -> str:
    """User-facing deny message."""
    return f"No access. Required role: {required_role}."
