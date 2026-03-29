from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from mobile_ingestion.webrtc_session import WebRtcPeerSession


@dataclass
class RecordingVoiceProcessor:
  submitted: list[object]

  def submit_audio(self, chunk: object) -> None:
    self.submitted.append(chunk)


@dataclass
class FakeContext:
  session_id: str
  voice_processor: RecordingVoiceProcessor


@dataclass
class FakeRelay:
  track: object | None

  def subscribe(self) -> object | None:
    return self.track


@dataclass
class RecordingPeerConnection:
  added_tracks: list[object]

  def addTrack(self, track: object) -> None:
    self.added_tracks.append(track)


def test_webrtc_session_batches_voice_pcm_before_submit() -> None:
  processor = RecordingVoiceProcessor(submitted=[])
  session = object.__new__(WebRtcPeerSession)
  session._context = FakeContext(  # type: ignore[attr-defined]
      session_id="session-one",
      voice_processor=processor,
  )
  session._voice_chunk_flush_bytes = 4  # type: ignore[attr-defined]
  session._pending_voice_pcm = bytearray()  # type: ignore[attr-defined]
  session._pending_voice_received_at = None  # type: ignore[attr-defined]

  first_timestamp = datetime.now(timezone.utc)
  second_timestamp = datetime.now(timezone.utc)
  session._queue_voice_pcm(b"ab", first_timestamp)
  assert processor.submitted == []

  session._queue_voice_pcm(b"cd", second_timestamp)

  assert len(processor.submitted) == 1
  submitted_chunk = processor.submitted[0]
  assert submitted_chunk.session_id == "session-one"
  assert submitted_chunk.pcm_s16le == b"abcd"
  assert submitted_chunk.received_at == first_timestamp


def test_spectator_session_attaches_sender_video_track() -> None:
  session = object.__new__(WebRtcPeerSession)
  session._sender_video_relay = FakeRelay(track="relayed-track")  # type: ignore[attr-defined]
  session._peer_connection = RecordingPeerConnection(added_tracks=[])  # type: ignore[attr-defined]
  session._spectator_video_track_attached = False  # type: ignore[attr-defined]
  session._callbacks = type(  # type: ignore[attr-defined]
      "Callbacks",
      (),
      {"on_video_track_detected": lambda self: None},
  )()

  session._attach_spectator_video_track()

  assert session._peer_connection.added_tracks == ["relayed-track"]  # type: ignore[attr-defined]


def test_spectator_session_requires_sender_video_track() -> None:
  session = object.__new__(WebRtcPeerSession)
  session._sender_video_relay = FakeRelay(track=None)  # type: ignore[attr-defined]
  session._peer_connection = RecordingPeerConnection(added_tracks=[])  # type: ignore[attr-defined]
  session._spectator_video_track_attached = False  # type: ignore[attr-defined]
  session._callbacks = type(  # type: ignore[attr-defined]
      "Callbacks",
      (),
      {"on_video_track_detected": lambda self: None},
  )()

  with pytest.raises(RuntimeError, match="sender video unavailable"):
    session._attach_spectator_video_track()
