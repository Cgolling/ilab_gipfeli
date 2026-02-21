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

from src.perception.recording_manager import RecordingState, RecordingStatus
from src.telegram.bot import (
    start,
    id_command,
    help_command,
    forceconnect_spot,
    goto,
    goto_callback,
    sound,
    sound_callback,
    volume,
    snapshot,
    record,
    env_var_is_true,
    CALLBACK_DATA_PREFIX,
    SOUND_CALLBACK_DATA_PREFIX,
)
from src.telegram.security import RbacConfig


@pytest.fixture(autouse=True)
def disable_rbac_by_default():
    """Keep legacy handler tests focused on command behavior unless overridden."""
    with patch("src.telegram.bot.rbac_enabled", False), patch("src.telegram.bot.rbac_config", None):
        yield


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
        assert "/id" in help_text
        assert "/help" in help_text
        assert "/connect" in help_text
        assert "/goto" in help_text
        assert "/sound" in help_text
        assert "/volume" in help_text
        assert "/snapshot" in help_text
        assert "/record" in help_text

    @pytest.mark.asyncio
    async def test_help_mentions_spot_robot(
        self, mock_telegram_update, mock_telegram_context
    ):
        """Help text mentions SPOT robot."""
        await help_command(mock_telegram_update, mock_telegram_context)

        help_text = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "SPOT" in help_text


class TestEnvHelpers:
    """Tests for environment parsing helper."""

    def test_env_var_is_true_default_true_when_missing(self, monkeypatch):
        """Missing env var should return default value."""
        monkeypatch.delenv("SPOT_AUTO_CONNECT", raising=False)
        assert env_var_is_true("SPOT_AUTO_CONNECT", default=True) is True

    def test_env_var_is_true_parses_false_values(self, monkeypatch):
        """False-like strings should parse to False."""
        monkeypatch.setenv("SPOT_AUTO_CONNECT", "false")
        assert env_var_is_true("SPOT_AUTO_CONNECT", default=True) is False


class TestIdCommand:
    """Tests for /id command."""

    @pytest.mark.asyncio
    async def test_id_command_shows_user_and_chat_id(
        self, mock_telegram_update, mock_telegram_context
    ):
        """ID command should return user and chat IDs."""
        mock_telegram_update.effective_user.id = 12345
        mock_telegram_update.effective_chat.id = 67890

        await id_command(mock_telegram_update, mock_telegram_context)

        mock_telegram_update.message.reply_text.assert_called_once()
        msg = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "12345" in msg
        assert "67890" in msg


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
        with patch("src.telegram.bot.spot_controller", None):
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

        with patch("src.telegram.bot.spot_controller", mock_controller):
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

        with patch("src.telegram.bot.spot_controller", None):
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

        with patch("src.telegram.bot.spot_controller", mock_controller):
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

        with patch("src.telegram.bot.spot_controller", mock_controller):
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

        with patch("src.telegram.bot.spot_controller", mock_controller):
            await goto_callback(update, mock_telegram_context)

        final_msg = mock_callback_query.edit_message_text.call_args[0][0]
        assert "Failed" in final_msg or "failed" in final_msg.lower()


class TestSoundCommand:
    """Tests for /sound command."""

    @pytest.mark.asyncio
    async def test_sound_no_files_shows_hint(
        self, mock_telegram_update, mock_telegram_context
    ):
        """Command shows guidance when no WAV files exist."""
        mock_telegram_context.args = []
        with patch("src.telegram.bot.get_available_sounds", return_value={}):
            await sound(mock_telegram_update, mock_telegram_context)

        reply = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "No WAV files found" in reply

    @pytest.mark.asyncio
    async def test_sound_with_arg_plays_selected_file(
        self, mock_telegram_update, mock_telegram_context
    ):
        """Known sound name triggers SpotController.play_wav_file."""
        mock_telegram_context.args = ["beep"]
        mock_controller = MagicMock()
        mock_controller.play_wav_file = AsyncMock(return_value=True)

        with patch(
            "src.telegram.bot.get_available_sounds",
            return_value={"beep": "sounds/beep.wav"}
        ), patch("src.telegram.bot.spot_controller", mock_controller):
            await sound(mock_telegram_update, mock_telegram_context)

        mock_controller.play_wav_file.assert_called_once()
        assert mock_controller.play_wav_file.call_args.kwargs["gain"] is None

    @pytest.mark.asyncio
    async def test_sound_with_gain_passes_gain_to_controller(
        self, mock_telegram_update, mock_telegram_context
    ):
        """Optional gain argument should be parsed and forwarded."""
        mock_telegram_context.args = ["beep", "0.75"]
        mock_controller = MagicMock()
        mock_controller.play_wav_file = AsyncMock(return_value=True)

        with patch(
            "src.telegram.bot.get_available_sounds",
            return_value={"beep": "sounds/beep.wav"}
        ), patch("src.telegram.bot.spot_controller", mock_controller):
            await sound(mock_telegram_update, mock_telegram_context)

        assert mock_controller.play_wav_file.call_args.kwargs["gain"] == pytest.approx(0.75)

    @pytest.mark.asyncio
    async def test_sound_invalid_gain_shows_error(
        self, mock_telegram_update, mock_telegram_context
    ):
        """Invalid gain should be rejected before controller call."""
        mock_telegram_context.args = ["beep", "abc"]
        mock_controller = MagicMock()
        mock_controller.play_wav_file = AsyncMock(return_value=True)

        with patch(
            "src.telegram.bot.get_available_sounds",
            return_value={"beep": "sounds/beep.wav"}
        ), patch("src.telegram.bot.spot_controller", mock_controller):
            await sound(mock_telegram_update, mock_telegram_context)

        mock_controller.play_wav_file.assert_not_called()
        reply = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "Gain must be a number" in reply

    @pytest.mark.asyncio
    async def test_sound_callback_not_connected_shows_error(
        self, mock_callback_query, mock_telegram_context
    ):
        """Sound callback should fail gracefully when SPOT is disconnected."""
        update = MagicMock()
        mock_callback_query.data = f"{SOUND_CALLBACK_DATA_PREFIX}beep"
        update.callback_query = mock_callback_query

        mock_controller = MagicMock()
        mock_controller.is_connected = False

        with patch(
            "src.telegram.bot.get_available_sounds",
            return_value={"beep": "sounds/beep.wav"}
        ), patch("src.telegram.bot.spot_controller", mock_controller):
            await sound_callback(update, mock_telegram_context)

        msg = mock_callback_query.edit_message_text.call_args[0][0]
        assert "not connected" in msg.lower()


class TestVolumeCommand:
    """Tests for /volume command."""

    @pytest.mark.asyncio
    async def test_volume_not_connected_shows_error(
        self, mock_telegram_update, mock_telegram_context
    ):
        """Volume command should require active connection."""
        mock_telegram_context.args = []
        with patch("src.telegram.bot.spot_controller", None):
            await volume(mock_telegram_update, mock_telegram_context)

        reply = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "not connected" in reply.lower()

    @pytest.mark.asyncio
    async def test_volume_get_current_value(
        self, mock_telegram_update, mock_telegram_context
    ):
        """Without args, command should return current volume."""
        mock_telegram_context.args = []
        mock_controller = MagicMock()
        mock_controller.is_connected = True
        mock_controller.get_audio_volume_percent = AsyncMock(return_value=42.5)

        with patch("src.telegram.bot.spot_controller", mock_controller):
            await volume(mock_telegram_update, mock_telegram_context)

        reply = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "42.5%" in reply

    @pytest.mark.asyncio
    async def test_volume_set_value(
        self, mock_telegram_update, mock_telegram_context
    ):
        """With one argument, command should set volume."""
        mock_telegram_context.args = ["65"]
        mock_controller = MagicMock()
        mock_controller.is_connected = True
        mock_controller.set_audio_volume_percent = AsyncMock(return_value=True)
        mock_controller.get_audio_volume_percent = AsyncMock(return_value=65.0)

        with patch("src.telegram.bot.spot_controller", mock_controller):
            await volume(mock_telegram_update, mock_telegram_context)

        mock_controller.set_audio_volume_percent.assert_called_once_with(65.0)
        reply = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "65.0%" in reply

    @pytest.mark.asyncio
    async def test_volume_invalid_input_rejected(
        self, mock_telegram_update, mock_telegram_context
    ):
        """Non-numeric values should be rejected."""
        mock_telegram_context.args = ["loud"]
        mock_controller = MagicMock()
        mock_controller.is_connected = True

        with patch("src.telegram.bot.spot_controller", mock_controller):
            await volume(mock_telegram_update, mock_telegram_context)

        reply = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "must be a number" in reply.lower()


class TestSnapshotCommand:
    """Tests for /snapshot command."""

    @pytest.mark.asyncio
    async def test_snapshot_without_args_lists_sources(
        self, mock_telegram_update, mock_telegram_context
    ):
        mock_telegram_context.args = []
        manager = MagicMock()
        manager.list_sources = AsyncMock(
            return_value=(True, "ok", ["frontleft_fisheye_image", "frontright_fisheye_image"])
        )

        with patch("src.telegram.bot.snapshot_manager", manager):
            await snapshot(mock_telegram_update, mock_telegram_context)

        reply = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "Available image sources" in reply
        assert "frontleft_fisheye_image" in reply

    @pytest.mark.asyncio
    async def test_snapshot_with_source_captures_file(
        self, mock_telegram_update, mock_telegram_context, tmp_path
    ):
        mock_telegram_context.args = ["frontleft_fisheye_image"]
        manager = MagicMock()
        image_path = tmp_path / "snapshot.jpg"
        image_path.write_bytes(b"jpeg-bytes")
        result = MagicMock()
        result.source = "frontleft_fisheye_image"
        result.saved_path = str(image_path)
        result.byte_size = 1234
        result.captured_at.strftime.return_value = "2026-02-21 10:00:00 UTC"
        manager.capture_snapshot = AsyncMock(return_value=(True, "saved", result))

        with patch("src.telegram.bot.snapshot_manager", manager):
            await snapshot(mock_telegram_update, mock_telegram_context)

        mock_telegram_update.message.reply_photo.assert_called_once()
        photo_kwargs = mock_telegram_update.message.reply_photo.call_args.kwargs
        assert "caption" not in photo_kwargs or photo_kwargs["caption"] is None
        calls = [call[0][0] for call in mock_telegram_update.message.reply_text.call_args_list]
        assert any("Capturing snapshot" in text for text in calls)
        assert not any("Source:" in text for text in calls)

    @pytest.mark.asyncio
    async def test_snapshot_failure_shows_usage(
        self, mock_telegram_update, mock_telegram_context
    ):
        mock_telegram_context.args = ["unknown_source"]
        manager = MagicMock()
        manager.capture_snapshot = AsyncMock(return_value=(False, "Unknown source", None))

        with patch("src.telegram.bot.snapshot_manager", manager):
            await snapshot(mock_telegram_update, mock_telegram_context)

        final_reply = mock_telegram_update.message.reply_text.call_args_list[-1][0][0]
        assert "Unknown source" in final_reply
        assert "Snapshot command usage" in final_reply

    @pytest.mark.asyncio
    async def test_snapshot_admin_gets_source_caption_and_details(
        self, mock_telegram_update, mock_telegram_context, tmp_path
    ):
        mock_telegram_context.args = ["frontleft_fisheye_image"]
        config = RbacConfig(
            private_only=True,
            admin_user_ids={12345},
            users={},
        )
        manager = MagicMock()
        image_path = tmp_path / "snapshot.jpg"
        image_path.write_bytes(b"jpeg-bytes")
        result = MagicMock()
        result.source = "frontleft_fisheye_image"
        result.saved_path = str(image_path)
        result.byte_size = 1234
        result.captured_at.strftime.return_value = "2026-02-21 10:00:00 UTC"
        manager.capture_snapshot = AsyncMock(return_value=(True, "saved", result))

        with patch("src.telegram.bot.rbac_enabled", True), patch(
            "src.telegram.bot.rbac_config", config
        ), patch("src.telegram.bot.snapshot_manager", manager):
            await snapshot(mock_telegram_update, mock_telegram_context)

        photo_kwargs = mock_telegram_update.message.reply_photo.call_args.kwargs
        assert "Snapshot from 'frontleft_fisheye_image'" == photo_kwargs["caption"]
        calls = [call[0][0] for call in mock_telegram_update.message.reply_text.call_args_list]
        assert any("Snapshot captured." in text for text in calls)
        assert any("Source: frontleft_fisheye_image" in text for text in calls)

    @pytest.mark.asyncio
    async def test_snapshot_operator_gets_image_without_source_details_when_rbac_enabled(
        self, mock_telegram_update, mock_telegram_context, tmp_path
    ):
        mock_telegram_context.args = ["frontleft_fisheye_image"]
        mock_telegram_update.effective_user.id = 222
        config = RbacConfig(
            private_only=True,
            admin_user_ids=set(),
            users={222: "operator"},
        )
        manager = MagicMock()
        image_path = tmp_path / "snapshot.jpg"
        image_path.write_bytes(b"jpeg-bytes")
        result = MagicMock()
        result.source = "frontleft_fisheye_image"
        result.saved_path = str(image_path)
        result.byte_size = 1234
        result.captured_at.strftime.return_value = "2026-02-21 10:00:00 UTC"
        manager.capture_snapshot = AsyncMock(return_value=(True, "saved", result))

        with patch("src.telegram.bot.rbac_enabled", True), patch(
            "src.telegram.bot.rbac_config", config
        ), patch("src.telegram.bot.snapshot_manager", manager):
            await snapshot(mock_telegram_update, mock_telegram_context)

        photo_kwargs = mock_telegram_update.message.reply_photo.call_args.kwargs
        assert "caption" not in photo_kwargs or photo_kwargs["caption"] is None
        calls = [call[0][0] for call in mock_telegram_update.message.reply_text.call_args_list]
        assert not any("Source:" in text for text in calls)


class TestRecordCommand:
    """Tests for /record command."""

    @pytest.mark.asyncio
    async def test_record_without_args_shows_usage(
        self, mock_telegram_update, mock_telegram_context
    ):
        mock_telegram_context.args = []

        await record(mock_telegram_update, mock_telegram_context)

        reply = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "Record command usage" in reply

    @pytest.mark.asyncio
    async def test_record_start_without_source_calls_manager_without_source(
        self, mock_telegram_update, mock_telegram_context
    ):
        mock_telegram_context.args = ["start"]
        manager = MagicMock()
        manager.start_recording = AsyncMock(return_value=(True, "WebRTC recording started"))

        with patch("src.telegram.bot.recording_manager", manager):
            await record(mock_telegram_update, mock_telegram_context)

        manager.start_recording.assert_called_once_with(None)
        reply = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "WebRTC recording started" in reply

    @pytest.mark.asyncio
    async def test_record_start_with_source_calls_manager(
        self, mock_telegram_update, mock_telegram_context
    ):
        mock_telegram_context.args = ["start", "frontleft_fisheye_image"]
        manager = MagicMock()
        manager.start_recording = AsyncMock(return_value=(True, "Recording started"))

        with patch("src.telegram.bot.recording_manager", manager):
            await record(mock_telegram_update, mock_telegram_context)

        manager.start_recording.assert_called_once_with("frontleft_fisheye_image")
        reply = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "Recording started" in reply

    @pytest.mark.asyncio
    async def test_record_stop_sends_video(
        self, mock_telegram_update, mock_telegram_context, tmp_path
    ):
        mock_telegram_context.args = ["stop"]
        video_path = tmp_path / "recording.mp4"
        video_path.write_bytes(b"video-bytes")
        result = MagicMock()
        result.source = "frontleft_fisheye_image"
        result.video_path = str(video_path)
        result.frame_count = 5
        result.duration_seconds = 3.2
        result.backend = "timelapse"
        result.has_audio = False

        manager = MagicMock()
        manager.stop_recording = AsyncMock(return_value=(True, "ok", result))

        with patch("src.telegram.bot.recording_manager", manager):
            await record(mock_telegram_update, mock_telegram_context)

        mock_telegram_update.message.reply_video.assert_called_once()
        texts = [call[0][0] for call in mock_telegram_update.message.reply_text.call_args_list]
        assert any("Finalizing recording" in text for text in texts)
        assert any("Recording finalized." in text for text in texts)

    @pytest.mark.asyncio
    async def test_record_abort_calls_manager(
        self, mock_telegram_update, mock_telegram_context
    ):
        mock_telegram_context.args = ["abort"]
        manager = MagicMock()
        manager.abort_recording = AsyncMock(return_value=(True, "Recording aborted and discarded."))

        with patch("src.telegram.bot.recording_manager", manager):
            await record(mock_telegram_update, mock_telegram_context)

        manager.abort_recording.assert_called_once()
        reply = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "aborted" in reply.lower()

    @pytest.mark.asyncio
    async def test_record_status_reports_state(
        self, mock_telegram_update, mock_telegram_context
    ):
        mock_telegram_context.args = ["status"]
        manager = MagicMock()
        manager.get_status = AsyncMock(
            return_value=RecordingStatus(
                state=RecordingState.RECORDING,
                source="frontleft_fisheye_image",
                frame_count=4,
                duration_seconds=2.1,
            )
        )

        with patch("src.telegram.bot.recording_manager", manager):
            await record(mock_telegram_update, mock_telegram_context)

        reply = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "Recording status" in reply
        assert "recording" in reply.lower()
        assert "backend" in reply.lower()


class TestRbacEnforcement:
    """RBAC enforcement checks on handlers."""

    @pytest.fixture
    def rbac_config(self):
        return RbacConfig(
            private_only=True,
            admin_user_ids={111},
            users={222: "operator", 333: "viewer"},
        )

    @pytest.mark.asyncio
    async def test_viewer_denied_for_operator_command(
        self, mock_telegram_update, mock_telegram_context, rbac_config
    ):
        """Viewer should not be able to run /goto."""
        mock_telegram_update.effective_user.id = 333
        with patch("src.telegram.bot.rbac_enabled", True), patch(
            "src.telegram.bot.rbac_config", rbac_config
        ):
            await goto(mock_telegram_update, mock_telegram_context)

        deny = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "required role: operator" in deny.lower()

    @pytest.mark.asyncio
    async def test_operator_allowed_for_goto(
        self, mock_telegram_update, mock_telegram_context, rbac_config
    ):
        """Operator should be able to use /goto."""
        mock_telegram_update.effective_user.id = 222
        with patch("src.telegram.bot.rbac_enabled", True), patch(
            "src.telegram.bot.rbac_config", rbac_config
        ):
            await goto(mock_telegram_update, mock_telegram_context)

        mock_telegram_update.message.reply_text.assert_called_once()
        msg = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "where" in msg.lower()

    @pytest.mark.asyncio
    async def test_operator_denied_for_admin_command(
        self, mock_telegram_update, mock_telegram_context, rbac_config
    ):
        """Operator should not be able to run /forceconnect."""
        mock_telegram_update.effective_user.id = 222
        with patch("src.telegram.bot.rbac_enabled", True), patch(
            "src.telegram.bot.rbac_config", rbac_config
        ), patch("src.telegram.bot.SpotController") as mock_controller_cls:
            await forceconnect_spot(mock_telegram_update, mock_telegram_context)

        deny = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "required role: admin" in deny.lower()
        mock_controller_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_private_only_policy_blocks_group_chat(
        self, mock_telegram_update, mock_telegram_context, rbac_config
    ):
        """Commands should be denied in group chats when private_only is enabled."""
        mock_telegram_update.effective_user.id = 111
        mock_telegram_update.effective_chat.type = "group"
        with patch("src.telegram.bot.rbac_enabled", True), patch(
            "src.telegram.bot.rbac_config", rbac_config
        ):
            await help_command(mock_telegram_update, mock_telegram_context)

        deny = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "private chat" in deny.lower()

    @pytest.mark.asyncio
    async def test_unknown_user_defaults_to_viewer_and_can_use_help(
        self, mock_telegram_update, mock_telegram_context, rbac_config
    ):
        """Unknown users should be treated as viewer for low-risk commands."""
        mock_telegram_update.effective_user.id = 999999
        with patch("src.telegram.bot.rbac_enabled", True), patch(
            "src.telegram.bot.rbac_config", rbac_config
        ):
            await help_command(mock_telegram_update, mock_telegram_context)

        help_text = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "SPOT Robot Control Bot" in help_text

    @pytest.mark.asyncio
    async def test_unknown_user_defaults_to_viewer_and_cannot_use_operator_command(
        self, mock_telegram_update, mock_telegram_context, rbac_config
    ):
        """Unknown users should still be blocked from operator/admin commands."""
        mock_telegram_update.effective_user.id = 999999
        with patch("src.telegram.bot.rbac_enabled", True), patch(
            "src.telegram.bot.rbac_config", rbac_config
        ):
            await goto(mock_telegram_update, mock_telegram_context)

        deny = mock_telegram_update.message.reply_text.call_args[0][0]
        assert "required role: operator" in deny.lower()
