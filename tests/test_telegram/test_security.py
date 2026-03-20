"""Tests for simplified Telegram RBAC."""

from pathlib import Path

from src.telegram.security import (
    ADMIN_ROLE,
    DEFAULT_ROLE,
    can_execute_command,
    describe_access_denied,
    get_user_role,
    load_security_config,
)


def test_unknown_user_defaults_to_user_role():
    config = load_security_config(Path("does-not-exist.toml"))

    assert get_user_role(12345, config) == DEFAULT_ROLE


def test_admin_id_gets_admin_role(tmp_path):
    config_path = tmp_path / "telegram_rbac.toml"
    config_path.write_text("admin_user_ids = [12345]\n", encoding="utf-8")

    config = load_security_config(config_path)

    assert get_user_role(12345, config) == ADMIN_ROLE


def test_users_cannot_run_admin_only_commands():
    config = load_security_config(Path("does-not-exist.toml"))

    assert can_execute_command("connect", 12345, config) is False
    assert can_execute_command("goto", 12345, config) is True


def test_access_denied_message_mentions_admin_only_command():
    assert describe_access_denied("disconnect") == "`/disconnect` is admin-only."
