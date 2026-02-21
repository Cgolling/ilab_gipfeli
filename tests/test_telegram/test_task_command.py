"""Tests for /task command handler."""

from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tasks.task_types import TaskState, TaskStatus, TaskStep
from src.telegram.bot import task
from src.telegram.security import RbacConfig


@pytest.fixture(autouse=True)
def disable_rbac_by_default():
    with patch("src.telegram.bot.rbac_enabled", False), patch("src.telegram.bot.rbac_config", None):
        yield


def make_status(**kwargs) -> TaskStatus:
    base = TaskStatus(
        task_name="gipfeli",
        state=TaskState.RUNNING,
        step=TaskStep.NAVIGATE_PICKUP,
        progress_current=2,
        progress_total=5,
        started_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        error=None,
    )
    return replace(base, **kwargs)


@pytest.mark.asyncio
async def test_task_without_args_shows_usage(mock_telegram_update, mock_telegram_context):
    mock_telegram_context.args = []

    await task(mock_telegram_update, mock_telegram_context)

    reply = mock_telegram_update.message.reply_text.call_args[0][0]
    assert "/task gipfeli" in reply
    assert "/task gipfeli <destination>" in reply
    assert "/task gipfeli status" in reply
    assert "/task status" in reply
    assert "/task cancel" in reply


@pytest.mark.asyncio
async def test_task_status_returns_formatted_status(mock_telegram_update, mock_telegram_context):
    mock_telegram_context.args = ["status"]
    manager = MagicMock()
    manager.get_status = AsyncMock(return_value=make_status())

    with patch("src.telegram.bot.task_manager", manager):
        await task(mock_telegram_update, mock_telegram_context)

    reply = mock_telegram_update.message.reply_text.call_args[0][0]
    assert "Global task status:" in reply
    assert "Task: gipfeli" in reply
    assert "State: running" in reply
    assert "Progress: 2/5" in reply


@pytest.mark.asyncio
async def test_task_gipfeli_without_destination_shows_possible_targets(
    mock_telegram_update, mock_telegram_context
):
    mock_telegram_context.args = ["gipfeli"]

    await task(mock_telegram_update, mock_telegram_context)

    reply = mock_telegram_update.message.reply_text.call_args[0][0]
    assert "Available destinations:" in reply
    assert "aula" in reply
    assert "triangle" in reply
    assert "hauswart" in reply
    assert "turnhalle" in reply


@pytest.mark.asyncio
async def test_task_gipfeli_starts_mission(mock_telegram_update, mock_telegram_context):
    mock_telegram_context.args = ["gipfeli", "aula"]
    manager = MagicMock()
    manager.start_gipfeli_task = AsyncMock(return_value=(True, "Task started: gipfeli -> aula"))

    with patch("src.telegram.bot.task_manager", manager):
        await task(mock_telegram_update, mock_telegram_context)

    manager.start_gipfeli_task.assert_called_once()
    call_args = manager.start_gipfeli_task.call_args
    assert call_args.args[0] == "aula"
    assert callable(call_args.args[1])


@pytest.mark.asyncio
async def test_task_gipfeli_invalid_destination_is_rejected(
    mock_telegram_update, mock_telegram_context
):
    mock_telegram_context.args = ["gipfeli", "zimmer1"]
    manager = MagicMock()
    manager.start_gipfeli_task = AsyncMock(return_value=(True, "Task started"))

    with patch("src.telegram.bot.task_manager", manager):
        await task(mock_telegram_update, mock_telegram_context)

    manager.start_gipfeli_task.assert_not_called()
    reply = mock_telegram_update.message.reply_text.call_args[0][0]
    assert "Unknown gipfeli destination" in reply
    assert "aula" in reply


@pytest.mark.asyncio
async def test_task_cancel_calls_manager(mock_telegram_update, mock_telegram_context):
    mock_telegram_context.args = ["cancel"]
    manager = MagicMock()
    manager.cancel_active_task = AsyncMock(return_value=(True, "Cancellation requested."))

    with patch("src.telegram.bot.task_manager", manager):
        await task(mock_telegram_update, mock_telegram_context)

    manager.cancel_active_task.assert_called_once()
    reply = mock_telegram_update.message.reply_text.call_args[0][0]
    assert "Cancellation requested." in reply


@pytest.mark.asyncio
async def test_viewer_denied_for_task_start_with_rbac(
    mock_telegram_update, mock_telegram_context
):
    mock_telegram_context.args = ["gipfeli", "aula"]
    config = RbacConfig(
        private_only=True,
        admin_user_ids=set(),
        users={12345: "viewer"},
    )
    manager = MagicMock()
    manager.start_gipfeli_task = AsyncMock(return_value=(True, "Task started"))

    with patch("src.telegram.bot.rbac_enabled", True), patch(
        "src.telegram.bot.rbac_config", config
    ), patch("src.telegram.bot.task_manager", manager):
        await task(mock_telegram_update, mock_telegram_context)

    manager.start_gipfeli_task.assert_not_called()
    reply = mock_telegram_update.message.reply_text.call_args[0][0]
    assert "required role: operator" in reply.lower()


@pytest.mark.asyncio
async def test_gipfeli_status_is_task_specific(
    mock_telegram_update, mock_telegram_context
):
    mock_telegram_context.args = ["gipfeli", "status"]
    manager = MagicMock()
    manager.get_status = AsyncMock(
        return_value=make_status(task_name="other_task", state=TaskState.RUNNING)
    )

    with patch("src.telegram.bot.task_manager", manager):
        await task(mock_telegram_update, mock_telegram_context)

    reply = mock_telegram_update.message.reply_text.call_args[0][0]
    assert "No gipfeli task has been started yet." in reply
    assert "/task status" in reply


@pytest.mark.asyncio
async def test_viewer_allowed_for_task_status_with_rbac(
    mock_telegram_update, mock_telegram_context
):
    mock_telegram_context.args = ["status"]
    config = RbacConfig(
        private_only=True,
        admin_user_ids=set(),
        users={12345: "viewer"},
    )
    manager = MagicMock()
    manager.get_status = AsyncMock(
        return_value=make_status(state=TaskState.IDLE, task_name=None, step=None, progress_current=0, progress_total=0)
    )

    with patch("src.telegram.bot.rbac_enabled", True), patch(
        "src.telegram.bot.rbac_config", config
    ), patch("src.telegram.bot.task_manager", manager):
        await task(mock_telegram_update, mock_telegram_context)

    manager.get_status.assert_called_once()
    reply = mock_telegram_update.message.reply_text.call_args[0][0]
    assert "No task has been started yet." in reply
