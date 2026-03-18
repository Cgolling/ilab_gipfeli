"""Snapshot capture manager for perception Phase A."""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ControllerProvider = Callable[[], object | None]


@dataclass(frozen=True, slots=True)
class SnapshotResult:
    """Result metadata for a saved snapshot."""

    source: str
    saved_path: str
    byte_size: int
    captured_at: datetime


class SnapshotManager:
    """Handles source listing and snapshot storage."""

    def __init__(self, controller_provider: ControllerProvider, output_dir: str) -> None:
        self._controller_provider = controller_provider
        self._output_dir = Path(output_dir)

    async def list_sources(self) -> tuple[bool, str, list[str]]:
        """List available image sources from SPOT."""
        controller = self._controller_provider()
        if controller is None or not getattr(controller, "is_connected", False):
            return False, "SPOT is not connected. Use /connect first.", []

        try:
            sources = await asyncio.to_thread(controller.list_image_sources)
        except Exception as exc:
            return False, f"Failed to list image sources: {exc}", []

        if not sources:
            return False, "No image sources available on this robot.", []

        return True, "Image sources loaded.", sources

    async def capture_snapshot(self, source: str) -> tuple[bool, str, SnapshotResult | None]:
        """Capture a JPEG snapshot from a source and store it on disk."""
        controller = self._controller_provider()
        if controller is None or not getattr(controller, "is_connected", False):
            return False, "SPOT is not connected. Use /connect first.", None

        source_name = source.strip()
        if not source_name:
            return False, "Missing image source. Usage: /snapshot <source>", None

        sources_ok, source_message, sources = await self.list_sources()
        if not sources_ok:
            return False, source_message, None

        if source_name not in sources:
            return (
                False,
                f"Unknown source '{source_name}'. Available: {', '.join(sources)}",
                None,
            )

        try:
            image_bytes = await controller.capture_image_jpeg(source_name)
        except Exception as exc:
            return False, f"Snapshot capture failed: {exc}", None

        if not image_bytes:
            return False, f"No image bytes returned for source '{source_name}'.", None

        captured_at = datetime.now(timezone.utc)
        saved_path = await asyncio.to_thread(
            self._save_snapshot,
            source_name=source_name,
            captured_at=captured_at,
            image_bytes=image_bytes,
        )

        result = SnapshotResult(
            source=source_name,
            saved_path=str(saved_path),
            byte_size=len(image_bytes),
            captured_at=captured_at,
        )
        return True, f"Snapshot saved: {saved_path}", result

    def _save_snapshot(self, *, source_name: str, captured_at: datetime, image_bytes: bytes) -> Path:
        """Write JPEG bytes to output directory and return saved path."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        safe_source = re.sub(r"[^a-zA-Z0-9._-]+", "_", source_name)
        timestamp = captured_at.strftime("%Y%m%d_%H%M%S")
        output_path = self._output_dir / f"{timestamp}_{safe_source}.jpg"
        output_path.write_bytes(image_bytes)
        return output_path
