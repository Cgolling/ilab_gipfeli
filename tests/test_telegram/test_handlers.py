"""Tests for Telegram bot command handlers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.telegram.bot import (
    CALLBACK_DATA_PREFIX,
    connect_spot,
    goto,
    goto_callback,
    help_command,
    id_command,
    start,
    stop_command,
)
from src.telegram.control_queue import ControlQueue
from src.telegram.security import SecurityConfig


class TestStartCommand:
    """Tests for /start queue behavior."""

    @pytest.mark.asyncio
    async def test_start_adds_first_user_and_grants_control(
        self, mock_telegram_update, mock_telegram_context
    ):
        await start(mock_telegram_update, mock_telegram_context)

        mock_telegram_context.bot.send_message.assert_awaited_once_with(
            chat_id=12345,
            text="You are in control.",
        )

    @pytest.mark.asyncio
    async def test_start_twice_returns_current_status(
        self, mock_telegram_update, mock_telegram_context
    ):
        await start(mock_telegram_update, mock_telegram_context)
        mock_telegram_context.bot.send_message.reset_mock()

        await start(mock_telegram_update, mock_telegram_context)

        mock_telegram_update.message.reply_text.assert_awaited_with("You are in control.")
        mock_telegram_context.bot.send_message.assert_not_awaited()


class TestHelpCommand:
    """Tests for /help."""

    @pytest.mark.asyncio
    async def test_help_mentions_queue_commands(
        self, mock_telegram_update, mock_telegram_context
    ):
        await help_command(mock_telegram_update, mock_telegram_context)

        help_text = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "/start - Join the control queue" in help_text
        assert "/stop - Release control or leave the queue" in help_text


class TestIdCommand:
    """Tests for /id."""

    @pytest.mark.asyncio
    async def test_id_command_shows_user_id_and_role(
        self, mock_telegram_update, mock_telegram_context
    ):
        await id_command(mock_telegram_update, mock_telegram_context)

        message = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "12345" in message
        assert "Role: user" in message


class TestConnectAuthorization:
    """Tests for admin-only connect commands."""

    @pytest.mark.asyncio
    async def test_connect_denied_for_regular_user(
        self, mock_telegram_update, mock_telegram_context
    ):
        await connect_spot(mock_telegram_update, mock_telegram_context)

        message = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "admin-only" in message

    @pytest.mark.asyncio
    async def test_connect_allowed_for_admin(
        self, mock_telegram_update, mock_telegram_context, monkeypatch
    ):
        mock_telegram_context.bot_data["security_config"] = SecurityConfig(
            admin_user_ids=frozenset({12345})
        )

        called = {"value": False}

        async def fake_handle_connection(update, context, force):
            called["value"] = True

        monkeypatch.setattr("src.telegram.bot._handle_connection", fake_handle_connection)

        await connect_spot(mock_telegram_update, mock_telegram_context)

        assert called["value"] is True


class TestGotoCommand:
    """Tests for /goto access control."""

    @pytest.mark.asyncio
    async def test_goto_requires_queue_membership(
        self, mock_telegram_update, mock_telegram_context
    ):
        await goto(mock_telegram_update, mock_telegram_context)

        message = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "requires control" in message
        assert "Use /start to join the queue." in message

    @pytest.mark.asyncio
    async def test_goto_requires_connected_controller_after_control(
        self, mock_telegram_update, mock_telegram_context
    ):
        await start(mock_telegram_update, mock_telegram_context)
        mock_telegram_update.message.reply_text.reset_mock()

        mock_telegram_context.bot_data["spot_controller"] = None
        await goto(mock_telegram_update, mock_telegram_context)

        message = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "not connected" in message.lower()

    @pytest.mark.asyncio
    async def test_goto_shows_location_buttons_for_controller(
        self, mock_telegram_update, mock_telegram_context
    ):
        await start(mock_telegram_update, mock_telegram_context)
        mock_telegram_update.message.reply_text.reset_mock()

        controller = MagicMock()
        controller.is_connected = True
        mock_telegram_context.bot_data["spot_controller"] = controller

        await goto(mock_telegram_update, mock_telegram_context)

        call_kwargs = mock_telegram_update.message.reply_text.call_args[1]
        reply_markup = call_kwargs["reply_markup"]
        button_texts = [
            button.text
            for row in reply_markup.inline_keyboard
            for button in row
        ]
        assert button_texts == ["Aula", "Turnhalle", "Zimmer 9"]


class TestGotoCallback:
    """Tests for goto callback handling."""

    @pytest.mark.asyncio
    async def test_callback_requires_control(
        self, mock_callback_query, mock_telegram_context
    ):
        update = MagicMock()
        update.callback_query = mock_callback_query
        update.effective_user.id = 12345

        await goto_callback(update, mock_telegram_context)

        message = mock_callback_query.edit_message_text.call_args[0][0]
        assert "requires control" in message

    @pytest.mark.asyncio
    async def test_callback_navigates_to_selected_waypoint_for_controller(
        self, mock_callback_query, mock_telegram_context
    ):
        queue = mock_telegram_context.bot_data["control_queue"]
        queue.join(user_id=12345, chat_id=12345, display_name="Test User")

        update = MagicMock()
        update.callback_query = mock_callback_query
        update.effective_user.id = 12345

        controller = MagicMock()
        controller.is_connected = True
        controller.navigate_to = AsyncMock(return_value=True)
        mock_telegram_context.bot_data["spot_controller"] = controller

        mock_callback_query.data = f"{CALLBACK_DATA_PREFIX}aula"

        await goto_callback(update, mock_telegram_context)

        controller.navigate_to.assert_called_once()
        assert controller.navigate_to.call_args[0][0] == "aula"
        final_message = mock_callback_query.edit_message_text.call_args[0][0]
        assert "Arrived" in final_message


class TestStopCommand:
    """Tests for /stop queue behavior."""

    @pytest.mark.asyncio
    async def test_stop_reports_missing_queue_entry(
        self, mock_telegram_update, mock_telegram_context
    ):
        await stop_command(mock_telegram_update, mock_telegram_context)

        mock_telegram_update.message.reply_text.assert_awaited_with("You are not in the queue.")

    @pytest.mark.asyncio
    async def test_stop_releases_control_and_promotes_next_user(self, mock_telegram_context):
        queue = mock_telegram_context.bot_data["control_queue"]
        queue.join(user_id=111, chat_id=111, display_name="User One")
        queue.join(user_id=222, chat_id=222, display_name="User Two")

        update = MagicMock()
        update.effective_user.id = 111
        update.message.reply_text = AsyncMock()

        await stop_command(update, mock_telegram_context)

        update.message.reply_text.assert_awaited_with("You released control.")
        mock_telegram_context.bot.send_message.assert_awaited_once_with(
            chat_id=222,
            text="You are in control.",
        )

    @pytest.mark.asyncio
    async def test_stop_removes_waiting_user(self, mock_telegram_context):
        queue = mock_telegram_context.bot_data["control_queue"]
        queue.join(user_id=111, chat_id=111, display_name="User One")
        queue.join(user_id=222, chat_id=222, display_name="User Two")
        queue.join(user_id=333, chat_id=333, display_name="User Three")

        update = MagicMock()
        update.effective_user.id = 222
        update.message.reply_text = AsyncMock()

        await stop_command(update, mock_telegram_context)

        update.message.reply_text.assert_awaited_with("You left the queue.")
        mock_telegram_context.bot.send_message.assert_awaited_once_with(
            chat_id=333,
            text="You are 2. in the queue.",
        )
