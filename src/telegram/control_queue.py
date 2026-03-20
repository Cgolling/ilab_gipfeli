"""In-memory control queue for Telegram bot users."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class QueueEntry:
    """A Telegram user currently waiting for or holding control."""

    user_id: int
    chat_id: int
    display_name: str


@dataclass
class ControlQueue:
    """Simple FIFO queue for exclusive robot control."""

    entries: list[QueueEntry] = field(default_factory=list)

    def join(self, user_id: int, chat_id: int, display_name: str) -> tuple[int, bool]:
        """Add a user to the queue if missing and return position and whether it was added."""
        for index, entry in enumerate(self.entries):
            if entry.user_id == user_id:
                return index + 1, False

        self.entries.append(QueueEntry(user_id=user_id, chat_id=chat_id, display_name=display_name))
        return len(self.entries), True

    def leave(self, user_id: int) -> tuple[QueueEntry | None, bool]:
        """Remove a user from the queue and return whether they held control."""
        for index, entry in enumerate(self.entries):
            if entry.user_id == user_id:
                self.entries.pop(index)
                return entry, index == 0
        return None, False

    def has_control(self, user_id: int | None) -> bool:
        """Return whether the given user currently holds control."""
        return bool(self.entries) and user_id is not None and self.entries[0].user_id == user_id

    def position_of(self, user_id: int | None) -> int | None:
        """Return the 1-based queue position for a user."""
        if user_id is None:
            return None
        for index, entry in enumerate(self.entries):
            if entry.user_id == user_id:
                return index + 1
        return None

    def status_message_for(self, user_id: int | None) -> str | None:
        """Return the queue status message for a user or None if absent."""
        position = self.position_of(user_id)
        if position is None:
            return None
        if position == 1:
            return "You are in control."
        return f"You are {position}. in the queue."

    def user_statuses(self) -> dict[int, str]:
        """Return status messages for all users currently in the queue."""
        return {
            entry.user_id: self.status_message_for(entry.user_id) or ""
            for entry in self.entries
        }

    def chat_id_for(self, user_id: int) -> int | None:
        """Return the chat ID for a queued user."""
        for entry in self.entries:
            if entry.user_id == user_id:
                return entry.chat_id
        return None
