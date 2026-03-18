"""WebRTC recording session for Spot CAM."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Optional

try:
    import requests
except Exception:  # pragma: no cover - optional dependency in tests
    requests = None  # type: ignore[assignment]

try:
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
    from aiortc.contrib.media import MediaRecorder
except Exception:  # pragma: no cover - optional dependency in tests
    RTCConfiguration = None  # type: ignore[assignment]
    RTCPeerConnection = None  # type: ignore[assignment]
    RTCSessionDescription = None  # type: ignore[assignment]
    MediaRecorder = None  # type: ignore[assignment]


DEFAULT_WEB_REQUEST_TIMEOUT = 10.0


@dataclass(frozen=True, slots=True)
class WebRTCConfig:
    """Configuration for Spot CAM WebRTC session."""

    sdp_port: int = 31102
    sdp_filename: str = "h264.sdp"
    verify_tls: bool = False
    ca_cert_path: Optional[str] = None
    connect_timeout_seconds: float = 10.0
    ice_timeout_seconds: float = 15.0


def webrtc_dependencies_available() -> tuple[bool, Optional[str]]:
    """Check whether runtime dependencies for WebRTC are installed."""
    if requests is None:
        return False, "Python package 'requests' is not installed."
    if RTCPeerConnection is None or RTCSessionDescription is None or MediaRecorder is None:
        return False, "Python package 'aiortc' is not installed."
    return True, None


class SpotCamWebRTCSession:
    """Controls one WebRTC recording lifecycle."""

    def __init__(
        self,
        *,
        hostname: str,
        token: str,
        output_path: str,
        config: WebRTCConfig,
    ) -> None:
        self._hostname = hostname
        self._token = token
        self._output_path = Path(output_path)
        self._config = config

        self._pc = None
        self._recorder = None
        self._offer_id: Optional[str] = None
        self._started_mono: Optional[float] = None
        self._has_video = False
        self._has_audio = False
        self._is_started = False

        self._ice_connected = asyncio.Event()
        self._ice_failed = asyncio.Event()
        self._ice_gathering_complete = asyncio.Event()
        self._last_error: Optional[str] = None

    @property
    def output_path(self) -> str:
        return str(self._output_path)

    @property
    def has_audio(self) -> bool:
        return self._has_audio

    async def start(self) -> tuple[bool, str]:
        """Start WebRTC negotiation and begin recording."""
        deps_ok, deps_error = webrtc_dependencies_available()
        if not deps_ok:
            return False, deps_error or "WebRTC dependencies unavailable."

        if self._is_started:
            return False, "WebRTC session already started."

        self._output_path.parent.mkdir(parents=True, exist_ok=True)

        verify_setting: bool | str
        if self._config.verify_tls:
            verify_setting = self._config.ca_cert_path or True
        else:
            verify_setting = False

        try:
            assert RTCConfiguration is not None
            assert RTCPeerConnection is not None
            assert RTCSessionDescription is not None
            assert MediaRecorder is not None

            self._pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[]))
            self._recorder = MediaRecorder(str(self._output_path))
            self._bind_peer_events()

            offer_id, sdp_offer = await asyncio.to_thread(
                self._fetch_sdp_offer,
                verify_setting,
            )
            self._offer_id = offer_id

            await self._pc.setRemoteDescription(RTCSessionDescription(sdp_offer, "offer"))
            local_answer = await self._pc.createAnswer()
            await self._pc.setLocalDescription(local_answer)

            await self._wait_for_ice_gathering()
            local_description = self._pc.localDescription
            if local_description is None:
                raise RuntimeError("WebRTC local description was not created.")

            await asyncio.to_thread(
                self._send_sdp_answer,
                verify_setting,
                offer_id,
                local_description.sdp,
            )

            await self._wait_for_ice_connection()
            await self._recorder.start()
            self._started_mono = time.monotonic()
            self._is_started = True
            return True, "WebRTC recording started."
        except Exception as exc:
            self._last_error = str(exc)
            await self._cleanup_runtime()
            return False, f"WebRTC start failed: {exc}"

    async def stop(self, *, abort: bool = False) -> tuple[bool, str, float, bool]:
        """
        Stop recording and close peer connection.

        Returns:
            (ok, message, duration_seconds, has_audio)
        """
        duration = 0.0
        if self._started_mono is not None:
            duration = max(0.0, time.monotonic() - self._started_mono)

        try:
            if self._recorder is not None:
                await self._recorder.stop()
            if self._pc is not None:
                await self._pc.close()
        except Exception as exc:
            self._last_error = str(exc)
            await self._cleanup_runtime()
            return False, f"WebRTC stop failed: {exc}", duration, self._has_audio

        await self._cleanup_runtime()

        if abort:
            try:
                if self._output_path.exists():
                    self._output_path.unlink()
            except Exception as exc:
                return False, f"Recording aborted, but cleanup failed: {exc}", duration, self._has_audio
            return True, "Recording aborted and discarded.", duration, self._has_audio

        if not self._output_path.exists():
            return False, "Recording failed: output file was not created.", duration, self._has_audio

        return True, f"Recording finalized: {self._output_path}", duration, self._has_audio

    def _bind_peer_events(self) -> None:
        assert self._pc is not None

        @self._pc.on("icegatheringstatechange")
        def _on_ice_gathering_state_change() -> None:
            if self._pc is not None and self._pc.iceGatheringState == "complete":
                self._ice_gathering_complete.set()

        @self._pc.on("iceconnectionstatechange")
        async def _on_ice_connection_state_change() -> None:
            if self._pc is None:
                return
            state = self._pc.iceConnectionState
            if state in {"connected", "completed"}:
                self._ice_connected.set()
                return
            if state in {"failed", "disconnected", "closed"}:
                self._last_error = f"ICE connection state: {state}"
                self._ice_failed.set()

        @self._pc.on("track")
        def _on_track(track) -> None:
            if self._recorder is None:
                return

            if track.kind == "audio":
                self._has_audio = True
            if track.kind == "video":
                self._has_video = True
            self._recorder.addTrack(track)

    async def _wait_for_ice_gathering(self) -> None:
        if self._ice_gathering_complete.is_set():
            return
        await asyncio.wait_for(
            self._ice_gathering_complete.wait(),
            timeout=self._config.connect_timeout_seconds,
        )

    async def _wait_for_ice_connection(self) -> None:
        if self._ice_connected.is_set():
            return

        wait_connected = asyncio.create_task(self._ice_connected.wait())
        wait_failed = asyncio.create_task(self._ice_failed.wait())
        done, pending = await asyncio.wait(
            {wait_connected, wait_failed},
            timeout=self._config.ice_timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()

        if not done:
            raise TimeoutError("Timed out while waiting for WebRTC ICE connection.")
        if wait_failed in done and self._ice_failed.is_set():
            raise RuntimeError(self._last_error or "WebRTC ICE connection failed.")
        if not self._ice_connected.is_set():
            raise RuntimeError("WebRTC ICE connection was not established.")

    def _fetch_sdp_offer(self, verify_setting: bool | str) -> tuple[str, str]:
        assert requests is not None

        headers = {"Authorization": f"Bearer {self._token}"}
        server_url = f"https://{self._hostname}:{self._config.sdp_port}/{self._config.sdp_filename}"
        response = requests.get(
            server_url,
            verify=verify_setting,
            headers=headers,
            timeout=max(DEFAULT_WEB_REQUEST_TIMEOUT, self._config.connect_timeout_seconds),
        )
        response.raise_for_status()
        result = response.json()
        offer_id = str(result["id"])
        sdp_offer = base64.b64decode(result["sdp"]).decode()
        return offer_id, sdp_offer

    def _send_sdp_answer(self, verify_setting: bool | str, offer_id: str, sdp_answer: str) -> None:
        assert requests is not None

        headers = {"Authorization": f"Bearer {self._token}"}
        server_url = f"https://{self._hostname}:{self._config.sdp_port}/{self._config.sdp_filename}"
        payload = {"id": offer_id, "sdp": base64.b64encode(sdp_answer.encode()).decode("utf8")}
        response = requests.post(
            server_url,
            verify=verify_setting,
            json=payload,
            headers=headers,
            timeout=max(DEFAULT_WEB_REQUEST_TIMEOUT, self._config.connect_timeout_seconds),
        )
        response.raise_for_status()

    async def _cleanup_runtime(self) -> None:
        self._pc = None
        self._recorder = None
        self._offer_id = None
        self._is_started = False
