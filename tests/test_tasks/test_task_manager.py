"""Tests for task orchestration manager."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tasks.task_manager import TaskManager
from src.tasks.task_types import TaskState, TaskStep


async def wait_until_not_running(manager: TaskManager, timeout: float = 2.0) -> None:
    """Wait until task state leaves running state."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        status = await manager.get_status()
        if status.state != TaskState.RUNNING:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Timed out waiting for task completion.")


def make_connected_controller(battery_percent: float = 80.0) -> MagicMock:
    controller = MagicMock()
    controller.is_connected = True
    controller.get_status.return_value = {"battery_percent": battery_percent}
    controller.navigate_to = AsyncMock(return_value=True)
    return controller


@pytest.mark.asyncio
async def test_start_fails_when_robot_not_connected():
    manager = TaskManager(lambda: None)
    callback = AsyncMock()

    ok, message = await manager.start_gipfeli_task("aula", callback)

    assert ok is False
    assert "not connected" in message.lower()


@pytest.mark.asyncio
async def test_start_fails_when_battery_too_low():
    controller = make_connected_controller(battery_percent=12.0)
    manager = TaskManager(lambda: controller, min_battery_percent=20.0)
    callback = AsyncMock()

    ok, message = await manager.start_gipfeli_task("aula", callback)

    assert ok is False
    assert "battery too low" in message.lower()


@pytest.mark.asyncio
async def test_gipfeli_task_reaches_succeeded_state():
    controller = make_connected_controller()
    manager = TaskManager(lambda: controller, pickup_waypoint="pickup")
    callback = AsyncMock()

    ok, _ = await manager.start_gipfeli_task("room1", callback)
    assert ok is True

    await wait_until_not_running(manager)
    status = await manager.get_status()

    assert status.state == TaskState.SUCCEEDED
    assert status.step == TaskStep.DELIVERED
    assert status.progress_current == 5
    assert status.progress_total == 5
    assert controller.navigate_to.await_count == 2
    first_call = controller.navigate_to.await_args_list[0].args[0]
    second_call = controller.navigate_to.await_args_list[1].args[0]
    assert first_call == "pickup"
    assert second_call == "room1"


@pytest.mark.asyncio
async def test_second_task_start_is_blocked_while_running():
    controller = make_connected_controller()

    async def slow_nav(*args, **kwargs):
        await asyncio.sleep(0.5)
        return True

    controller.navigate_to = AsyncMock(side_effect=slow_nav)
    manager = TaskManager(lambda: controller, pickup_waypoint="pickup")
    callback = AsyncMock()

    ok1, _ = await manager.start_gipfeli_task("room1", callback)
    ok2, message2 = await manager.start_gipfeli_task("room2", callback)

    assert ok1 is True
    assert ok2 is False
    assert "already running" in message2.lower()

    await manager.cancel_active_task()
    await wait_until_not_running(manager)


@pytest.mark.asyncio
async def test_cancel_changes_state_to_cancelled():
    controller = make_connected_controller()

    async def slow_nav(*args, **kwargs):
        await asyncio.sleep(1.0)
        return True

    controller.navigate_to = AsyncMock(side_effect=slow_nav)
    manager = TaskManager(lambda: controller, pickup_waypoint="pickup")
    callback = AsyncMock()

    ok, _ = await manager.start_gipfeli_task("room1", callback)
    assert ok is True

    cancelled, _ = await manager.cancel_active_task()
    assert cancelled is True

    await wait_until_not_running(manager)
    status = await manager.get_status()
    assert status.state == TaskState.CANCELLED


@pytest.mark.asyncio
async def test_navigation_failure_sets_failed_status():
    controller = make_connected_controller()
    controller.navigate_to = AsyncMock(return_value=False)
    manager = TaskManager(lambda: controller, pickup_waypoint="pickup")
    callback = AsyncMock()

    ok, _ = await manager.start_gipfeli_task("room1", callback)
    assert ok is True

    await wait_until_not_running(manager)
    status = await manager.get_status()

    assert status.state == TaskState.FAILED
    assert status.error is not None
    assert "pickup" in status.error.lower()
