"""Mission/task definitions."""

import asyncio
from collections.abc import Awaitable, Callable

from src.tasks.task_types import TaskStep

StatusCallback = Callable[[str], Awaitable[None]]
StepCallback = Callable[[TaskStep, int], Awaitable[None]]


class TaskExecutionError(RuntimeError):
    """Raised when a task step fails."""


class TaskCancelledError(RuntimeError):
    """Raised when a task is cancelled."""


async def run_gipfeli_task(
    *,
    controller,
    pickup_waypoint: str,
    destination_waypoint: str,
    status_callback: StatusCallback,
    step_callback: StepCallback,
    cancel_event: asyncio.Event,
) -> None:
    """
    Execute the gipfeli delivery task.

    Flow:
    1) Navigate to pickup waypoint.
    2) Report pickup done.
    3) Navigate to destination waypoint.
    4) Report delivery done.
    """
    await step_callback(TaskStep.PRECHECK, 1)
    if cancel_event.is_set():
        raise TaskCancelledError("Task was cancelled before navigation started.")

    await status_callback(
        f"Starting task 'gipfeli': heading to pickup waypoint '{pickup_waypoint}'."
    )
    await step_callback(TaskStep.NAVIGATE_PICKUP, 2)
    reached_pickup = await controller.navigate_to(pickup_waypoint, status_callback)
    if not reached_pickup:
        raise TaskExecutionError(
            f"Could not reach pickup waypoint '{pickup_waypoint}'."
        )
    if cancel_event.is_set():
        raise TaskCancelledError("Task cancelled after pickup navigation.")

    await step_callback(TaskStep.PICKUP_DONE, 3)
    await status_callback(
        f"Gipfeli picked up. Heading to destination '{destination_waypoint}'."
    )
    if cancel_event.is_set():
        raise TaskCancelledError("Task cancelled after pickup confirmation.")

    await step_callback(TaskStep.NAVIGATE_DROPOFF, 4)
    reached_destination = await controller.navigate_to(destination_waypoint, status_callback)
    if not reached_destination:
        raise TaskExecutionError(
            f"Could not reach destination waypoint '{destination_waypoint}'."
        )
    if cancel_event.is_set():
        raise TaskCancelledError("Task cancelled after destination navigation.")

    await step_callback(TaskStep.DELIVERED, 5)
    await status_callback("Gipfeli delivered successfully.")
