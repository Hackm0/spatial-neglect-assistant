from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

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
