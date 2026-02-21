"""Hybrid recording manager (WebRTC first, timelapse fallback)."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from src.perception.webrtc_session import (
    SpotCamWebRTCSession,
    WebRTCConfig,
    webrtc_dependencies_available,
)

ControllerProvider = Callable[[], object | None]


class RecordingState(str, Enum):
    """Lifecycle states for a recording session."""

    IDLE = "idle"
    RECORDING = "recording"
    FINALIZING = "finalizing"
    READY = "ready"
    ABORTED = "aborted"
    FAILED = "failed"


class RecordingBackend(str, Enum):
    """Recording backend identifiers."""

    TIMELAPSE = "timelapse"
    WEBRTC = "webrtc"


@dataclass(frozen=True, slots=True)
class RecordingStatus:
    """Current/last status of recording service."""

    state: RecordingState = RecordingState.IDLE
    backend: Optional[str] = None
    source: Optional[str] = None
    started_at: Optional[datetime] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    frame_count: int = 0
    output_path: Optional[str] = None
    duration_seconds: float = 0.0
    has_audio: bool = False
    error: Optional[str] = None


@dataclass(frozen=True, slots=True)
class RecordingResult:
    """Result metadata for a finalized recording."""

    source: str
    video_path: str
    frame_count: int
    duration_seconds: float
    captured_at: datetime
    backend: str = RecordingBackend.TIMELAPSE.value
    has_audio: bool = False


class RecordingManager:
    """Coordinates start/stop/abort for WebRTC and timelapse recordings."""

    def __init__(
        self,
        controller_provider: ControllerProvider,
        output_dir: str,
        *,
        frame_interval_seconds: float = 1.0,
        ffmpeg_bin: str = "ffmpeg",
        backend_default: Optional[str] = None,
        webrtc_sdp_port: Optional[int] = None,
        webrtc_sdp_filename: Optional[str] = None,
        webrtc_verify_tls: Optional[bool] = None,
        webrtc_ca_cert_path: Optional[str] = None,
        webrtc_connect_timeout_seconds: Optional[float] = None,
        webrtc_ice_timeout_seconds: Optional[float] = None,
    ) -> None:
        self._controller_provider = controller_provider
        self._output_dir = Path(output_dir)
        self._frame_interval = max(0.2, frame_interval_seconds)
        self._ffmpeg_bin = ffmpeg_bin

        self._backend_default = self._resolve_backend_default(backend_default)
        self._webrtc_config = WebRTCConfig(
            sdp_port=webrtc_sdp_port or _env_int("RECORD_WEBRTC_SDP_PORT", 31102),
            sdp_filename=webrtc_sdp_filename or os.getenv("RECORD_WEBRTC_SDP_FILENAME", "h264.sdp"),
            verify_tls=(
                webrtc_verify_tls
                if webrtc_verify_tls is not None
                else _env_var_is_true("RECORD_WEBRTC_VERIFY_TLS", False)
            ),
            ca_cert_path=webrtc_ca_cert_path or os.getenv("RECORD_WEBRTC_CA_CERT_PATH") or None,
            connect_timeout_seconds=(
                webrtc_connect_timeout_seconds
                if webrtc_connect_timeout_seconds is not None
                else _env_float("RECORD_WEBRTC_CONNECT_TIMEOUT_SECONDS", 10.0)
            ),
            ice_timeout_seconds=(
                webrtc_ice_timeout_seconds
                if webrtc_ice_timeout_seconds is not None
                else _env_float("RECORD_WEBRTC_ICE_TIMEOUT_SECONDS", 15.0)
            ),
        )

        self._lock = asyncio.Lock()
        self._status = RecordingStatus(updated_at=datetime.now(timezone.utc))

        # Timelapse runtime
        self._capture_task: Optional[asyncio.Task[None]] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._session_frames_dir: Optional[Path] = None
        self._session_started_mono: Optional[float] = None
        self._abort_requested = False

        # WebRTC runtime
        self._webrtc_session: Optional[SpotCamWebRTCSession] = None

    async def list_sources(self) -> tuple[bool, str, list[str]]:
        """List available image sources for timelapse mode."""
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

    async def start_recording(self, source: Optional[str] = None) -> tuple[bool, str]:
        """
        Start recording.

        Behavior:
        - /record start <source>: always timelapse using image service.
        - /record start (without source): use default backend strategy.
        """
        source_name = (source or "").strip()

        async with self._lock:
            if self._has_active_recording_locked():
                return False, "A recording is already in progress. Use /record status."

        if source_name:
            return await self._start_timelapse(source_name)

        if self._backend_default == "timelapse":
            return False, "Missing source. Usage: /record start <source>"

        webrtc_ok, webrtc_message = await self._start_webrtc()
        if webrtc_ok:
            return True, webrtc_message

        if self._backend_default == "webrtc":
            return False, webrtc_message

        # Hybrid mode fallback guidance.
        sources_ok, _, sources = await self.list_sources()
        if sources_ok and sources:
            return (
                False,
                f"{webrtc_message}\n\nFallback to timelapse:\n"
                f"/record start <source>\nAvailable sources: {', '.join(sources)}",
            )
        return (
            False,
            f"{webrtc_message}\n\nFallback to timelapse:\n"
            "/record start <source>",
        )

    async def stop_recording(self) -> tuple[bool, str, Optional[RecordingResult]]:
        """Stop active recording and build output."""
        async with self._lock:
            backend = self._status.backend
            if backend == RecordingBackend.TIMELAPSE.value:
                return await self._stop_timelapse_locked()
            if backend == RecordingBackend.WEBRTC.value:
                return await self._stop_webrtc_locked()
            return False, "No active recording to stop.", None

    async def abort_recording(self) -> tuple[bool, str]:
        """Abort active recording and discard captured output."""
        async with self._lock:
            backend = self._status.backend
            if backend == RecordingBackend.TIMELAPSE.value:
                return await self._abort_timelapse_locked()
            if backend == RecordingBackend.WEBRTC.value:
                return await self._abort_webrtc_locked()
            return False, "No active recording to abort."

    async def get_status(self) -> RecordingStatus:
        """Return current recording status snapshot."""
        async with self._lock:
            return replace(self._status)

    def _resolve_backend_default(self, explicit_backend_default: Optional[str]) -> str:
        if explicit_backend_default:
            value = explicit_backend_default.strip().lower()
        else:
            value = os.getenv("RECORD_BACKEND_DEFAULT", "hybrid").strip().lower()

        if value not in {"hybrid", "webrtc", "timelapse"}:
            return "hybrid"
        return value

    def _has_active_recording_locked(self) -> bool:
        if self._status.state not in {RecordingState.RECORDING, RecordingState.FINALIZING}:
            return False
        if self._status.backend == RecordingBackend.TIMELAPSE.value:
            return self._capture_task is not None and not self._capture_task.done()
        if self._status.backend == RecordingBackend.WEBRTC.value:
            return self._webrtc_session is not None
        return False

    async def _start_timelapse(self, source_name: str) -> tuple[bool, str]:
        sources_ok, source_message, sources = await self.list_sources()
        if not sources_ok:
            return False, source_message
        if source_name not in sources:
            return False, f"Unknown source '{source_name}'. Available: {', '.join(sources)}"

        now = datetime.now(timezone.utc)
        safe_source = re.sub(r"[^a-zA-Z0-9._-]+", "_", source_name)
        session_id = now.strftime("%Y%m%d_%H%M%S")
        frames_dir = self._output_dir / f"session_{session_id}_{safe_source}"
        await asyncio.to_thread(frames_dir.mkdir, parents=True, exist_ok=True)

        async with self._lock:
            self._stop_event = asyncio.Event()
            self._session_frames_dir = frames_dir
            self._session_started_mono = time.monotonic()
            self._abort_requested = False
            self._status = RecordingStatus(
                state=RecordingState.RECORDING,
                backend=RecordingBackend.TIMELAPSE.value,
                source=source_name,
                started_at=now,
                updated_at=now,
                frame_count=0,
                output_path=None,
                duration_seconds=0.0,
                has_audio=False,
                error=None,
            )
            self._capture_task = asyncio.create_task(self._capture_loop(source_name))

        return True, f"Timelapse recording started from '{source_name}'. Use /record stop to finalize."

    async def _start_webrtc(self) -> tuple[bool, str]:
        deps_ok, deps_error = webrtc_dependencies_available()
        if not deps_ok:
            return False, deps_error or "WebRTC dependencies unavailable."

        controller = self._controller_provider()
        if controller is None or not getattr(controller, "is_connected", False):
            return False, "SPOT is not connected. Use /connect first."

        if not self._controller_webrtc_available(controller):
            return False, "Spot CAM WebRTC is not available on this robot."

        context = self._controller_webrtc_context(controller)
        if context is None:
            return False, "Could not resolve WebRTC context (hostname/token missing)."

        now = datetime.now(timezone.utc)
        session_id = now.strftime("%Y%m%d_%H%M%S")
        output_path = self._output_dir / f"webrtc_{session_id}.mp4"
        session = SpotCamWebRTCSession(
            hostname=context[0],
            token=context[1],
            output_path=str(output_path),
            config=self._webrtc_config,
        )
        ok, message = await session.start()
        if not ok:
            return False, message

        async with self._lock:
            self._webrtc_session = session
            self._session_started_mono = time.monotonic()
            self._status = RecordingStatus(
                state=RecordingState.RECORDING,
                backend=RecordingBackend.WEBRTC.value,
                source="spot_cam_webrtc",
                started_at=now,
                updated_at=now,
                frame_count=0,
                output_path=None,
                duration_seconds=0.0,
                has_audio=False,
                error=None,
            )

        return True, "WebRTC recording started. Use /record stop to finalize."

    async def _stop_timelapse_locked(self) -> tuple[bool, str, Optional[RecordingResult]]:
        capture_task = self._capture_task
        session_dir = self._session_frames_dir
        source = self._status.source

        if capture_task is None or capture_task.done():
            return False, "No active recording to stop.", None

        assert self._stop_event is not None
        self._stop_event.set()
        self._status = replace(
            self._status,
            state=RecordingState.FINALIZING,
            updated_at=datetime.now(timezone.utc),
        )

        # Release lock while waiting for background capture.
        self._lock.release()
        try:
            try:
                await capture_task
            except Exception as exc:
                await self._mark_failed(f"Capture loop crashed: {exc}")
                return False, f"Recording failed: {exc}", None
        finally:
            await self._lock.acquire()

        if self._abort_requested:
            return False, "Recording was aborted.", None

        assert session_dir is not None
        assert source is not None
        frame_count = self._status.frame_count
        started_at = self._status.started_at or datetime.now(timezone.utc)

        if frame_count == 0:
            await self._mark_failed("No frames captured.")
            await asyncio.to_thread(self._cleanup_session_dir, session_dir)
            return False, "Recording failed: No frames were captured.", None

        video_name = f"{session_dir.name}.mp4"
        output_path = self._output_dir / video_name
        fps = max(1.0, 1.0 / self._frame_interval)

        self._lock.release()
        try:
            ok, error = await asyncio.to_thread(self._build_video, session_dir, output_path, fps)
        finally:
            await self._lock.acquire()

        if not ok:
            await self._mark_failed(error or "ffmpeg failed.")
            await asyncio.to_thread(self._cleanup_session_dir, session_dir)
            return False, f"Recording finalization failed: {error}", None

        duration = max(0.0, time.monotonic() - (self._session_started_mono or time.monotonic()))
        result = RecordingResult(
            source=source,
            video_path=str(output_path),
            frame_count=frame_count,
            duration_seconds=duration,
            captured_at=started_at,
            backend=RecordingBackend.TIMELAPSE.value,
            has_audio=False,
        )
        self._status = replace(
            self._status,
            state=RecordingState.READY,
            updated_at=datetime.now(timezone.utc),
            output_path=str(output_path),
            duration_seconds=duration,
            has_audio=False,
            error=None,
        )
        self._reset_timelapse_runtime_locked()

        await asyncio.to_thread(self._cleanup_session_dir, session_dir)
        return True, f"Recording finalized: {output_path}", result

    async def _stop_webrtc_locked(self) -> tuple[bool, str, Optional[RecordingResult]]:
        session = self._webrtc_session
        if session is None:
            return False, "No active recording to stop.", None

        started_at = self._status.started_at or datetime.now(timezone.utc)
        source = self._status.source or "spot_cam_webrtc"
        self._status = replace(
            self._status,
            state=RecordingState.FINALIZING,
            updated_at=datetime.now(timezone.utc),
        )

        self._lock.release()
        try:
            ok, message, duration, has_audio = await session.stop(abort=False)
        finally:
            await self._lock.acquire()

        if not ok:
            await self._mark_failed(message)
            self._webrtc_session = None
            self._session_started_mono = None
            return False, message, None

        result = RecordingResult(
            source=source,
            video_path=session.output_path,
            frame_count=0,
            duration_seconds=duration,
            captured_at=started_at,
            backend=RecordingBackend.WEBRTC.value,
            has_audio=has_audio,
        )
        self._status = replace(
            self._status,
            state=RecordingState.READY,
            updated_at=datetime.now(timezone.utc),
            output_path=session.output_path,
            duration_seconds=duration,
            has_audio=has_audio,
            frame_count=0,
            error=None,
        )
        self._webrtc_session = None
        self._session_started_mono = None
        return True, message, result

    async def _abort_timelapse_locked(self) -> tuple[bool, str]:
        capture_task = self._capture_task
        session_dir = self._session_frames_dir
        if capture_task is None or capture_task.done():
            return False, "No active recording to abort."

        self._abort_requested = True
        assert self._stop_event is not None
        self._stop_event.set()

        self._lock.release()
        try:
            try:
                await capture_task
            except Exception:
                pass
            if session_dir is not None:
                await asyncio.to_thread(self._cleanup_session_dir, session_dir)
        finally:
            await self._lock.acquire()

        self._status = replace(
            self._status,
            state=RecordingState.ABORTED,
            updated_at=datetime.now(timezone.utc),
            output_path=None,
            duration_seconds=0.0,
            has_audio=False,
            error="Recording aborted and discarded.",
        )
        self._reset_timelapse_runtime_locked()
        return True, "Recording aborted and discarded."

    async def _abort_webrtc_locked(self) -> tuple[bool, str]:
        session = self._webrtc_session
        if session is None:
            return False, "No active recording to abort."

        self._lock.release()
        try:
            ok, message, _, _ = await session.stop(abort=True)
        finally:
            await self._lock.acquire()

        self._webrtc_session = None
        self._session_started_mono = None
        self._status = replace(
            self._status,
            state=RecordingState.ABORTED,
            updated_at=datetime.now(timezone.utc),
            output_path=None,
            duration_seconds=0.0,
            has_audio=False,
            error="Recording aborted and discarded.",
        )
        if ok:
            return True, message
        return False, message

    async def _capture_loop(self, source: str) -> None:
        """Background capture loop storing frame JPEG files."""
        try:
            controller = self._controller_provider()
            if controller is None:
                await self._mark_failed("SPOT disconnected during recording.")
                return

            frame_idx = 0
            assert self._stop_event is not None
            assert self._session_frames_dir is not None
            session_dir = self._session_frames_dir

            while not self._stop_event.is_set():
                image_bytes = await controller.capture_image_jpeg(source)
                if image_bytes:
                    frame_idx += 1
                    frame_path = session_dir / f"frame_{frame_idx:06d}.jpg"
                    await asyncio.to_thread(frame_path.write_bytes, image_bytes)
                    async with self._lock:
                        if self._status.state == RecordingState.RECORDING:
                            self._status = replace(
                                self._status,
                                frame_count=frame_idx,
                                updated_at=datetime.now(timezone.utc),
                            )

                await asyncio.sleep(self._frame_interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._mark_failed(f"Capture loop failed: {exc}")

    def _build_video(self, frames_dir: Path, output_path: Path, fps: float) -> tuple[bool, Optional[str]]:
        """Build MP4 video using ffmpeg from frame sequence."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        input_pattern = str(frames_dir / "frame_%06d.jpg")
        cmd = [
            self._ffmpeg_bin,
            "-y",
            "-framerate",
            f"{fps:.2f}",
            "-i",
            input_pattern,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return False, "ffmpeg not found on PATH."
        except Exception as exc:
            return False, f"ffmpeg invocation failed: {exc}"

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip().splitlines()
            last_line = stderr[-1] if stderr else "unknown ffmpeg error"
            return False, last_line

        if not output_path.exists():
            return False, "ffmpeg reported success, but no output file was created."
        return True, None

    async def _mark_failed(self, error_message: str) -> None:
        self._status = replace(
            self._status,
            state=RecordingState.FAILED,
            updated_at=datetime.now(timezone.utc),
            error=error_message,
        )
        self._reset_timelapse_runtime_locked()
        self._webrtc_session = None
        self._session_started_mono = None

    def _reset_timelapse_runtime_locked(self) -> None:
        self._capture_task = None
        self._stop_event = None
        self._session_frames_dir = None
        self._session_started_mono = None
        self._abort_requested = False

    def _cleanup_session_dir(self, session_dir: Path) -> None:
        """Remove temporary frame directory."""
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)

    def _controller_webrtc_available(self, controller: object) -> bool:
        has_method = getattr(controller, "is_webrtc_available", None)
        if callable(has_method):
            try:
                return bool(has_method())
            except Exception:
                return False
        # Fallback for mocked / older controllers.
        return bool(getattr(controller, "is_connected", False) and getattr(controller, "audio_client", None))

    def _controller_webrtc_context(self, controller: object) -> Optional[tuple[str, str]]:
        context_getter = getattr(controller, "get_webrtc_context", None)
        if callable(context_getter):
            try:
                context = context_getter()
                if context is None:
                    return None
                hostname = getattr(context, "hostname", None)
                token = getattr(context, "token", None)
                if hostname and token:
                    return str(hostname), str(token)
                return None
            except Exception:
                return None

        hostname = getattr(controller, "hostname", None)
        robot = getattr(controller, "robot", None)
        token = getattr(robot, "user_token", None) if robot is not None else None
        if hostname and token:
            return str(hostname), str(token)
        return None


def _env_var_is_true(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value.strip())
    except Exception:
        return default
