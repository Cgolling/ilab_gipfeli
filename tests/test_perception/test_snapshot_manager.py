"""Tests for SnapshotManager."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.perception.snapshot_manager import SnapshotManager


@pytest.mark.asyncio
async def test_list_sources_requires_connection(tmp_path: Path):
    manager = SnapshotManager(lambda: None, output_dir=str(tmp_path))

    ok, message, sources = await manager.list_sources()

    assert ok is False
    assert "not connected" in message.lower()
    assert sources == []


@pytest.mark.asyncio
async def test_capture_snapshot_rejects_unknown_source(tmp_path: Path):
    controller = MagicMock()
    controller.is_connected = True
    controller.list_image_sources.return_value = ["frontleft_fisheye_image"]
    controller.capture_image_jpeg = AsyncMock(return_value=b"jpeg-bytes")
    manager = SnapshotManager(lambda: controller, output_dir=str(tmp_path))

    ok, message, result = await manager.capture_snapshot("unknown_source")

    assert ok is False
    assert "unknown source" in message.lower()
    assert result is None
    controller.capture_image_jpeg.assert_not_called()


@pytest.mark.asyncio
async def test_capture_snapshot_saves_jpeg(tmp_path: Path):
    controller = MagicMock()
    controller.is_connected = True
    controller.list_image_sources.return_value = ["frontleft_fisheye_image"]
    controller.capture_image_jpeg = AsyncMock(return_value=b"jpeg-bytes")
    manager = SnapshotManager(lambda: controller, output_dir=str(tmp_path))

    ok, message, result = await manager.capture_snapshot("frontleft_fisheye_image")

    assert ok is True
    assert "snapshot saved" in message.lower()
    assert result is not None
    saved_path = Path(result.saved_path)
    assert saved_path.exists()
    assert saved_path.suffix == ".jpg"
    assert saved_path.read_bytes() == b"jpeg-bytes"
