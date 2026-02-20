"""Task orchestration for Telegram-triggered missions."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

from src.tasks.config import GIPFELI_PICKUP_WAYPOINT, MIN_BATTERY_PERCENT
from src.tasks.missions import TaskCancelledError, TaskExecutionError, run_gipfeli_task
from src.tasks.task_types import TaskState, TaskStatus, TaskStep

StatusCallback = Callable[[str], Awaitable[None]]
ControllerProvider = Callable[[], object | None]


class TaskManager:
    """Coordinates creation, status tracking, and cancellation of bot tasks."""

    def __init__(
        self,
        controller_provider: ControllerProvider,
        *,
        pickup_waypoint: str = GIPFELI_PICKUP_WAYPOINT,
        min_battery_percent: float = MIN_BATTERY_PERCENT,
    ) -> None:
        self._controller_provider = controller_provider
        self._pickup_waypoint = pickup_waypoint
        self._min_battery_percent = min_battery_percent

        self._lock = asyncio.Lock()
        self._status = TaskStatus(updated_at=datetime.now(timezone.utc))
        self._active_task: Optional[asyncio.Task[None]] = None
        self._cancel_event: Optional[asyncio.Event] = None

    async def start_gipfeli_task(
        self,
        destination_waypoint: str,
        status_callback: StatusCallback,
    ) -> tuple[bool, str]:
        """Start the gipfeli delivery task if no task is currently running."""
        destination = destination_waypoint.strip()
        if not destination:
            return False, "Missing destination waypoint. Usage: /task gipfeli <destination>"

        async with self._lock:
            if self._active_task and not self._active_task.done():
                return False, "Another task is already running. Use /task status or /task cancel."

            controller = self._controller_provider()
            if controller is None or not getattr(controller, "is_connected", False):
                return False, "SPOT is not connected. Use /connect first."

            try:
                robot_status = controller.get_status()
            except Exception as exc:
                return False, f"Could not read SPOT status: {exc}"
            battery = robot_status.get("battery_percent")
            if battery is None:
                return (
                    False,
                    "Battery status unavailable. Cannot start task safely.",
                )
            if float(battery) < self._min_battery_percent:
                return (
                    False,
                    "Battery too low "
                    f"({float(battery):.1f}% < {self._min_battery_percent:.1f}%).",
                )

            now = datetime.now(timezone.utc)
            self._status = TaskStatus(
                task_name="gipfeli",
                state=TaskState.RUNNING,
                step=TaskStep.PRECHECK,
                progress_current=0,
                progress_total=5,
                started_at=now,
                updated_at=now,
                error=None,
            )
            self._cancel_event = asyncio.Event()
            self._active_task = asyncio.create_task(
                self._run_gipfeli_task(
                    controller=controller,
                    destination_waypoint=destination,
                    status_callback=status_callback,
                )
            )
            return True, f"Task started: gipfeli -> {destination}"

    async def get_status(self) -> TaskStatus:
        """Return a snapshot of current/last task status."""
        async with self._lock:
            return replace(self._status)

    async def cancel_active_task(self) -> tuple[bool, str]:
        """Cancel the currently running task if there is one."""
        active_task: Optional[asyncio.Task[None]]
        async with self._lock:
            active_task = self._active_task
            if active_task is None or active_task.done():
                return False, "No active task to cancel."

            if self._cancel_event:
                self._cancel_event.set()
            self._status.state = TaskState.CANCELLED
            self._status.error = "Task cancelled."
            self._status.updated_at = datetime.now(timezone.utc)
            active_task.cancel()

        return True, "Cancellation requested."

    async def _run_gipfeli_task(
        self,
        *,
        controller,
        destination_waypoint: str,
        status_callback: StatusCallback,
    ) -> None:
        """Background runner for the gipfeli mission."""

        async def step_callback(step: TaskStep, progress: int) -> None:
            async with self._lock:
                if self._status.state != TaskState.RUNNING:
                    return
                self._status.step = step
                self._status.progress_current = progress
                self._status.updated_at = datetime.now(timezone.utc)

        try:
            assert self._cancel_event is not None
            await run_gipfeli_task(
                controller=controller,
                pickup_waypoint=self._pickup_waypoint,
                destination_waypoint=destination_waypoint,
                status_callback=status_callback,
                step_callback=step_callback,
                cancel_event=self._cancel_event,
            )
            async with self._lock:
                self._status.state = TaskState.SUCCEEDED
                self._status.updated_at = datetime.now(timezone.utc)
                self._status.error = None
        except (TaskCancelledError, asyncio.CancelledError):
            async with self._lock:
                self._status.state = TaskState.CANCELLED
                self._status.updated_at = datetime.now(timezone.utc)
                self._status.error = "Task cancelled."
            await status_callback("Task cancelled.")
        except TaskExecutionError as exc:
            await self._set_failed(str(exc))
            await status_callback(f"Task failed: {exc}")
        except Exception as exc:
            await self._set_failed(f"Unexpected error: {exc}")
            await status_callback(f"Task failed: {exc}")
        finally:
            async with self._lock:
                self._active_task = None
                self._cancel_event = None

    async def _set_failed(self, error_message: str) -> None:
        async with self._lock:
            self._status.state = TaskState.FAILED
            self._status.error = error_message
            self._status.updated_at = datetime.now(timezone.utc)
