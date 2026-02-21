"""Tests for RecordingManager."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.perception.recording_manager import (
    RecordingBackend,
    RecordingManager,
    RecordingState,
    RecordingStatus,
)


def make_connected_controller() -> MagicMock:
    """Create a connected controller mock for recording tests."""
    controller = MagicMock()
    controller.is_connected = True
    controller.list_image_sources.return_value = ["frontleft_fisheye_image"]
    controller.capture_image_jpeg = AsyncMock(return_value=b"jpeg-bytes")
    controller.is_webrtc_available.return_value = True
    controller.get_webrtc_context.return_value = SimpleNamespace(
        hostname="192.168.80.3",
        token="fake-token",
    )
    return controller


@pytest.mark.asyncio
async def test_start_recording_requires_connection(tmp_path: Path):
    manager = RecordingManager(lambda: None, output_dir=str(tmp_path))

    ok, message = await manager.start_recording("frontleft_fisheye_image")

    assert ok is False
    assert "not connected" in message.lower()


@pytest.mark.asyncio
async def test_start_recording_rejects_unknown_source(tmp_path: Path):
    controller = make_connected_controller()
    manager = RecordingManager(lambda: controller, output_dir=str(tmp_path))

    ok, message = await manager.start_recording("unknown_source")

    assert ok is False
    assert "unknown source" in message.lower()


@pytest.mark.asyncio
async def test_start_recording_without_source_uses_webrtc_first(tmp_path: Path):
    controller = make_connected_controller()
    manager = RecordingManager(lambda: controller, output_dir=str(tmp_path), backend_default="hybrid")
    manager._start_webrtc = AsyncMock(return_value=(True, "WebRTC recording started"))  # type: ignore[method-assign]

    ok, message = await manager.start_recording(None)

    assert ok is True
    assert "webrtc" in message.lower()
    manager._start_webrtc.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_start_recording_without_source_hybrid_shows_fallback_sources(tmp_path: Path):
    controller = make_connected_controller()
    manager = RecordingManager(lambda: controller, output_dir=str(tmp_path), backend_default="hybrid")
    manager._start_webrtc = AsyncMock(return_value=(False, "WebRTC unavailable"))  # type: ignore[method-assign]
    manager.list_sources = AsyncMock(return_value=(True, "ok", ["frontleft_fisheye_image"]))  # type: ignore[method-assign]

    ok, message = await manager.start_recording(None)

    assert ok is False
    assert "fallback" in message.lower()
    assert "frontleft_fisheye_image" in message


@pytest.mark.asyncio
async def test_recording_start_stop_timelapse_produces_result(tmp_path: Path):
    controller = make_connected_controller()
    manager = RecordingManager(
        lambda: controller,
        output_dir=str(tmp_path),
        frame_interval_seconds=0.2,
    )

    ok, _ = await manager.start_recording("frontleft_fisheye_image")
    assert ok is True
    await asyncio.sleep(0.25)

    def fake_build_video(frames_dir: Path, output_path: Path, fps: float):
        output_path.write_bytes(b"fake-video")
        return True, None

    manager._build_video = fake_build_video  # type: ignore[method-assign]

    stop_ok, _, result = await manager.stop_recording()
    assert stop_ok is True
    assert result is not None
    assert Path(result.video_path).exists()
    assert result.frame_count >= 1
    assert result.backend == RecordingBackend.TIMELAPSE.value

    status = await manager.get_status()
    assert status.state == RecordingState.READY
    assert status.backend == RecordingBackend.TIMELAPSE.value
    assert status.output_path is not None


@pytest.mark.asyncio
async def test_abort_discards_timelapse_recording(tmp_path: Path):
    controller = make_connected_controller()
    manager = RecordingManager(
        lambda: controller,
        output_dir=str(tmp_path),
        frame_interval_seconds=0.2,
    )

    ok, _ = await manager.start_recording("frontleft_fisheye_image")
    assert ok is True

    abort_ok, message = await manager.abort_recording()
    assert abort_ok is True
    assert "discarded" in message.lower()

    status = await manager.get_status()
    assert status.state == RecordingState.ABORTED
    assert status.output_path is None


@pytest.mark.asyncio
async def test_stop_webrtc_updates_backend_and_audio_flag(tmp_path: Path):
    manager = RecordingManager(lambda: None, output_dir=str(tmp_path))

    fake_session = MagicMock()
    fake_session.stop = AsyncMock(return_value=(True, "Recording finalized", 2.5, True))
    fake_session.output_path = str(tmp_path / "webrtc.mp4")

    started_at = datetime.now(timezone.utc)
    manager._webrtc_session = fake_session
    manager._status = RecordingStatus(
        state=RecordingState.RECORDING,
        backend=RecordingBackend.WEBRTC.value,
        source="spot_cam_webrtc",
        started_at=started_at,
        updated_at=started_at,
    )

    ok, _, result = await manager.stop_recording()
    assert ok is True
    assert result is not None
    assert result.backend == RecordingBackend.WEBRTC.value
    assert result.has_audio is True

    status = await manager.get_status()
    assert status.state == RecordingState.READY
    assert status.backend == RecordingBackend.WEBRTC.value
    assert status.has_audio is True
