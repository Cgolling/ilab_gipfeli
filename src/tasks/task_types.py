"""Task state and progress types."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class TaskState(str, Enum):
    """High-level task lifecycle state."""

    IDLE = "idle"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStep(str, Enum):
    """Task progress steps for the gipfeli delivery flow."""

    PRECHECK = "precheck"
    NAVIGATE_PICKUP = "navigate_pickup"
    PICKUP_DONE = "pickup_done"
    NAVIGATE_DROPOFF = "navigate_dropoff"
    DELIVERED = "delivered"


@dataclass(slots=True)
class TaskStatus:
    """Current or last-known task status."""

    task_name: Optional[str] = None
    state: TaskState = TaskState.IDLE
    step: Optional[TaskStep] = None
    progress_current: int = 0
    progress_total: int = 0
    started_at: Optional[datetime] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: Optional[str] = None
