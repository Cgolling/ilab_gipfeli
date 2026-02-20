"""Tests for telegram RBAC security helpers."""

from pathlib import Path

import pytest

from src.telegram.security import (
    format_deny_message,
    is_allowed,
    is_private_chat,
    load_rbac_config,
    required_role_for_command,
    resolve_user_role,
)


class TestLoadRbacConfig:
    """YAML loading and validation tests."""

    def test_load_valid_config(self, tmp_path: Path):
        config_file = tmp_path / "rbac.yml"
        config_file.write_text(
            "chat_policy:\n"
            "  private_only: true\n"
            "admin_user_ids:\n"
            "  - 1\n"
            "users:\n"
            "  \"2\": operator\n"
            "  \"3\": viewer\n",
            encoding="utf-8",
        )

        config = load_rbac_config(str(config_file))
        assert config.private_only is True
        assert 1 in config.admin_user_ids
        assert config.users[2] == "operator"
        assert config.users[3] == "viewer"

    def test_load_invalid_role_raises(self, tmp_path: Path):
        config_file = tmp_path / "rbac.yml"
        config_file.write_text(
            "users:\n"
            "  \"2\": superadmin\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError):
            load_rbac_config(str(config_file))


class TestRoleResolution:
    """Role lookup and hierarchy tests."""

    def test_admin_user_id_overrides_users_mapping(self, tmp_path: Path):
        config_file = tmp_path / "rbac.yml"
        config_file.write_text(
            "admin_user_ids:\n"
            "  - 42\n"
            "users:\n"
            "  \"42\": viewer\n",
            encoding="utf-8",
        )
        config = load_rbac_config(str(config_file))

        assert resolve_user_role(42, config) == "admin"

    def test_unknown_user_defaults_to_viewer(self, tmp_path: Path):
        config_file = tmp_path / "rbac.yml"
        config_file.write_text(
            "admin_user_ids: []\n"
            "users:\n"
            "  \"42\": operator\n",
            encoding="utf-8",
        )
        config = load_rbac_config(str(config_file))

        assert resolve_user_role(999, config) == "viewer"

    def test_is_allowed_hierarchy(self):
        assert is_allowed("admin", "viewer") is True
        assert is_allowed("operator", "viewer") is True
        assert is_allowed("viewer", "operator") is False


class TestCommandMapping:
    """Required role mapping tests."""

    def test_required_roles(self):
        assert required_role_for_command("status") == "viewer"
        assert required_role_for_command("id") == "viewer"
        assert required_role_for_command("goto") == "operator"
        assert required_role_for_command("forceconnect") == "admin"

    def test_unknown_command_returns_none(self):
        assert required_role_for_command("unknown_command") is None


class TestUtilityHelpers:
    """Misc helper tests."""

    def test_is_private_chat(self, mock_telegram_update):
        mock_telegram_update.effective_chat.type = "private"
        assert is_private_chat(mock_telegram_update) is True
        mock_telegram_update.effective_chat.type = "group"
        assert is_private_chat(mock_telegram_update) is False

    def test_format_deny_message(self):
        assert "operator" in format_deny_message("operator")
