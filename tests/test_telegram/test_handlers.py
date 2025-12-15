"""
Tests for Telegram bot command handlers.

These tests verify that handlers respond correctly to user input.
We mock the Telegram Update and Context objects to simulate user interaction.

Educational notes:
- Telegram handlers receive Update and Context objects
- We mock these objects to control their behavior
- Test that handlers send appropriate responses
- Use patch to isolate handlers from global state
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.telegram.echobot import (
    start,
    help_command,
    goto,
    goto_callback,
    CALLBACK_DATA_PREFIX,
)


class TestStartCommand:
    """Tests for /start command."""

    @pytest.mark.asyncio
    async def test_start_greets_user(
        self, mock_telegram_update, mock_telegram_context
    ):
        """Start command sends personalized greeting."""
        await start(mock_telegram_update, mock_telegram_context)

        mock_telegram_update.message.reply_html.assert_called_once()
        call_args = mock_telegram_update.message.reply_html.call_args[0][0]
        assert "TestUser" in call_args

    @pytest.mark.asyncio
    async def test_start_uses_reply_html(
        self, mock_telegram_update, mock_telegram_context
    ):
        """Start uses reply_html for formatted output."""
        await start(mock_telegram_update, mock_telegram_context)

        # Should use reply_html, not reply_text
        mock_telegram_update.message.reply_html.assert_called_once()


class TestHelpCommand:
    """Tests for /help command."""

    @pytest.mark.asyncio
    async def test_help_lists_all_commands(
        self, mock_telegram_update, mock_telegram_context
    ):
        """Help command lists all available commands."""
        await help_command(mock_telegram_update, mock_telegram_context)

        mock_telegram_update.message.reply_text.assert_called_once()
        help_text = mock_telegram_update.message.reply_text.call_args[0][0]

        # Check all commands are mentioned
        assert "/start" in help_text
        assert "/help" in help_text
        assert "/connect" in help_text
        assert "/goto" in help_text

    @pytest.mark.asyncio
    async def test_help_mentions_spot_robot(
        self, mock_telegram_update, mock_telegram_context
    ):
        """Help text mentions SPOT robot."""
        await help_command(mock_telegram_update, mock_telegram_context)

        help_text = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "SPOT" in help_text


class TestGotoCommand:
    """Tests for /goto command."""

    @pytest.mark.asyncio
    async def test_goto_shows_location_buttons(
        self, mock_telegram_update, mock_telegram_context
    ):
        """Goto command presents inline keyboard with locations."""
        await goto(mock_telegram_update, mock_telegram_context)

        mock_telegram_update.message.reply_text.assert_called_once()

        # Get the reply_markup from call kwargs
        call_kwargs = mock_telegram_update.message.reply_text.call_args[1]
        reply_markup = call_kwargs["reply_markup"]

        # Flatten buttons and get their texts
        button_texts = []
        for row in reply_markup.inline_keyboard:
            for button in row:
                button_texts.append(button.text)

        # Check all locations are present
        assert "Aula" in button_texts
        assert "Triangle" in button_texts
        assert "Hauswart" in button_texts
        assert "Turnhalle" in button_texts

    @pytest.mark.asyncio
    async def test_goto_buttons_have_correct_callback_data(
        self, mock_telegram_update, mock_telegram_context
    ):
        """Buttons have callback data with correct prefix."""
        await goto(mock_telegram_update, mock_telegram_context)

        call_kwargs = mock_telegram_update.message.reply_text.call_args[1]
        reply_markup = call_kwargs["reply_markup"]

        # Check callback data format
        for row in reply_markup.inline_keyboard:
            for button in row:
                assert button.callback_data.startswith(CALLBACK_DATA_PREFIX)

    @pytest.mark.asyncio
    async def test_goto_asks_where_to_go(
        self, mock_telegram_update, mock_telegram_context
    ):
        """Goto command asks user for destination."""
        await goto(mock_telegram_update, mock_telegram_context)

        message_text = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "where" in message_text.lower() or "go" in message_text.lower()


class TestGotoCallback:
    """Tests for goto button callback."""

    @pytest.mark.asyncio
    async def test_callback_not_connected_shows_error(
        self, mock_callback_query, mock_telegram_context
    ):
        """Callback shows error when SPOT not connected."""
        update = MagicMock()
        update.callback_query = mock_callback_query

        # Ensure global controller is None
        with patch("src.telegram.echobot.spot_controller", None):
            await goto_callback(update, mock_telegram_context)

        mock_callback_query.edit_message_text.assert_called()
        msg = mock_callback_query.edit_message_text.call_args[0][0]
        assert "not connected" in msg.lower()

    @pytest.mark.asyncio
    async def test_callback_disconnected_controller_shows_error(
        self, mock_callback_query, mock_telegram_context
    ):
        """Callback shows error when controller exists but not connected."""
        update = MagicMock()
        update.callback_query = mock_callback_query

        # Create mock controller that's not connected
        mock_controller = MagicMock()
        mock_controller.is_connected = False

        with patch("src.telegram.echobot.spot_controller", mock_controller):
            await goto_callback(update, mock_telegram_context)

        msg = mock_callback_query.edit_message_text.call_args[0][0]
        assert "not connected" in msg.lower()

    @pytest.mark.asyncio
    async def test_callback_answers_query(
        self, mock_callback_query, mock_telegram_context
    ):
        """Callback always answers the query to dismiss loading state."""
        update = MagicMock()
        update.callback_query = mock_callback_query

        with patch("src.telegram.echobot.spot_controller", None):
            await goto_callback(update, mock_telegram_context)

        mock_callback_query.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_callback_extracts_location_from_data(
        self, mock_callback_query, mock_telegram_context
    ):
        """Callback correctly extracts location from callback data."""
        update = MagicMock()
        mock_callback_query.data = f"{CALLBACK_DATA_PREFIX}triangle"
        update.callback_query = mock_callback_query

        # Create connected controller
        mock_controller = MagicMock()
        mock_controller.is_connected = True
        mock_controller.navigate_to = AsyncMock(return_value=True)

        with patch("src.telegram.echobot.spot_controller", mock_controller):
            await goto_callback(update, mock_telegram_context)

        # Verify navigate_to was called with correct location
        mock_controller.navigate_to.assert_called_once()
        call_args = mock_controller.navigate_to.call_args[0]
        assert call_args[0] == "triangle"

    @pytest.mark.asyncio
    async def test_callback_success_shows_arrival_message(
        self, mock_callback_query, mock_telegram_context
    ):
        """Successful navigation shows arrival message."""
        update = MagicMock()
        mock_callback_query.data = f"{CALLBACK_DATA_PREFIX}aula"
        update.callback_query = mock_callback_query

        mock_controller = MagicMock()
        mock_controller.is_connected = True
        mock_controller.navigate_to = AsyncMock(return_value=True)

        with patch("src.telegram.echobot.spot_controller", mock_controller):
            await goto_callback(update, mock_telegram_context)

        # Check final message mentions arrival
        final_msg = mock_callback_query.edit_message_text.call_args[0][0]
        assert "Arrived" in final_msg or "aula" in final_msg.lower()

    @pytest.mark.asyncio
    async def test_callback_failure_shows_error_message(
        self, mock_callback_query, mock_telegram_context
    ):
        """Failed navigation shows error message."""
        update = MagicMock()
        mock_callback_query.data = f"{CALLBACK_DATA_PREFIX}aula"
        update.callback_query = mock_callback_query

        mock_controller = MagicMock()
        mock_controller.is_connected = True
        mock_controller.navigate_to = AsyncMock(return_value=False)

        with patch("src.telegram.echobot.spot_controller", mock_controller):
            await goto_callback(update, mock_telegram_context)

        final_msg = mock_callback_query.edit_message_text.call_args[0][0]
        assert "Failed" in final_msg or "failed" in final_msg.lower()
