from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone

from aiortc import (RTCConfiguration, RTCIceServer, RTCPeerConnection,
                    RTCSessionDescription)
from aiortc.mediastreams import MediaStreamError

from mobile_ingestion.analyzer import (AudioFrameEnvelope, SessionMetadata,
                                       VideoFrameEnvelope)
from mobile_ingestion.dto import SessionDescriptionDto
from mobile_ingestion.session_manager import (PeerSessionPort, SessionCallbacks,
                                              SessionContext)


class WebRtcPeerSession(PeerSessionPort):

  def __init__(self, context: SessionContext, callbacks: SessionCallbacks) -> None:
    self._context = context
    self._callbacks = callbacks
    self._metadata = SessionMetadata(
        session_id=context.session_id,
        started_at=context.started_at,
    )
    self._configuration = RTCConfiguration(iceServers=[
        RTCIceServer(urls=url) for url in context.settings.ice_servers
    ])
    self._peer_connection = RTCPeerConnection(configuration=self._configuration)
    self._consumer_tasks: set[asyncio.Task[None]] = set()
    self._closed = False
    self._session_started = False
    self._session_stopped = False
    self._register_callbacks()

  async def accept_offer(
      self, offer: SessionDescriptionDto) -> SessionDescriptionDto:
    remote_description = RTCSessionDescription(sdp=offer.sdp, type=offer.type)
    await self._peer_connection.setRemoteDescription(remote_description)
    answer = await self._peer_connection.createAnswer()
    await self._peer_connection.setLocalDescription(answer)
    await self._wait_for_ice_completion()
    if not self._session_started:
      self._context.analyzer.on_session_started(self._metadata)
      self._session_started = True
    local_description = self._peer_connection.localDescription
    assert local_description is not None
    return SessionDescriptionDto(
        sdp=local_description.sdp,
        type=local_description.type,
    )

  async def close(self) -> None:
    if self._closed:
      return
    self._closed = True
    for task in list(self._consumer_tasks):
      task.cancel()
    if self._consumer_tasks:
      await asyncio.gather(*self._consumer_tasks, return_exceptions=True)
    await self._peer_connection.close()
    self._notify_session_stopped()
    self._callbacks.on_closed()

  def _register_callbacks(self) -> None:
    @self._peer_connection.on("connectionstatechange")
    async def _on_connection_state_change() -> None:
      state = self._peer_connection.connectionState
      self._callbacks.on_connection_state_changed(state)
      if state in {"failed", "disconnected", "closed"}:
        await self.close()

    @self._peer_connection.on("track")
    def _on_track(track: object) -> None:
      kind = getattr(track, "kind", None)
      if kind == "video":
        self._callbacks.on_video_track_detected()
        self._track_task(asyncio.create_task(self._consume_video(track)))
      elif kind == "audio":
        self._callbacks.on_audio_track_detected()
        self._track_task(asyncio.create_task(self._consume_audio(track)))

  def _track_task(self, task: asyncio.Task[None]) -> None:
    self._consumer_tasks.add(task)

    def _remove_task(completed_task: asyncio.Task[None]) -> None:
      self._consumer_tasks.discard(completed_task)
      if completed_task.cancelled():
        return
      exception = completed_task.exception()
      if exception is not None:
        self._callbacks.on_error(str(exception))

    task.add_done_callback(_remove_task)

  async def _consume_video(self, track: object) -> None:
    try:
      while True:
        frame = await track.recv()
        envelope = VideoFrameEnvelope(
            session_id=self._context.session_id,
            received_at=datetime.now(timezone.utc),
            width=int(frame.width),
            height=int(frame.height),
            pts=frame.pts,
        )
        self._context.analyzer.on_video_frame(envelope)
    except (asyncio.CancelledError, MediaStreamError):
      return

  async def _consume_audio(self, track: object) -> None:
    try:
      while True:
        frame = await track.recv()
        envelope = AudioFrameEnvelope(
            session_id=self._context.session_id,
            received_at=datetime.now(timezone.utc),
            sample_rate=int(frame.sample_rate),
            samples=int(frame.samples),
            pts=frame.pts,
        )
        self._context.analyzer.on_audio_frame(envelope)
    except (asyncio.CancelledError, MediaStreamError):
      return

  async def _wait_for_ice_completion(self) -> None:
    if self._peer_connection.iceGatheringState == "complete":
      return

    gathering_complete = asyncio.Event()

    @self._peer_connection.on("icegatheringstatechange")
    async def _on_ice_gathering_state_change() -> None:
      if self._peer_connection.iceGatheringState == "complete":
        gathering_complete.set()

    await asyncio.wait_for(
        gathering_complete.wait(),
        timeout=self._context.settings.ice_gathering_timeout_seconds,
    )

  def _notify_session_stopped(self) -> None:
    if self._session_started and not self._session_stopped:
      with suppress(Exception):
        self._context.analyzer.on_session_stopped(self._metadata)
      self._session_stopped = True
