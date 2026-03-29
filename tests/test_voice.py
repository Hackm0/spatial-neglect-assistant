from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from mobile_ingestion.voice import (AudioChunk, NoOpWakeWordAction,
                                    NormalizedWakePhraseDetector,
                                    OpenAiRealtimeRecognizer,
                                    RecognitionUpdate, TranscriptEntry,
                                    UnavailableSpeechRecognizer,
                                    VoiceCoordinator, WakeWordEvent,
                                    _build_openai_realtime_session_update_payload,
                                    _is_benign_socket_close_error,
                                    normalize_transcript_text)


@dataclass
class FakeRecognitionSession:
  updates_by_payload: dict[bytes, tuple[RecognitionUpdate, ...]] = field(
      default_factory=dict)
  failures_by_payload: dict[bytes, Exception] = field(default_factory=dict)

  def accept_audio(self, chunk: AudioChunk) -> tuple[RecognitionUpdate, ...]:
    failure = self.failures_by_payload.get(chunk.pcm_s16le)
    if failure is not None:
      raise failure
    return self.updates_by_payload.get(chunk.pcm_s16le, tuple())

  def poll_updates(self) -> tuple[RecognitionUpdate, ...]:
    return tuple()

  def finalize(self, received_at: datetime) -> tuple[RecognitionUpdate, ...]:
    del received_at
    return tuple()


class FakeSpeechRecognizer:

  def __init__(self, *, updates_by_payload: dict[bytes,
                                                 tuple[RecognitionUpdate, ...]]
               | None = None, available: bool = True,
               error: str | None = None) -> None:
    self._updates_by_payload = updates_by_payload or {}
    self._available = available
    self._error = error

  @property
  def available(self) -> bool:
    return self._available

  @property
  def error(self) -> str | None:
    return self._error

  def create_session(self) -> FakeRecognitionSession:
    return FakeRecognitionSession(updates_by_payload=self._updates_by_payload)


class SequencedSpeechRecognizer:

  def __init__(self, sessions: list[FakeRecognitionSession]) -> None:
    self._sessions = list(sessions)

  @property
  def available(self) -> bool:
    return True

  @property
  def error(self) -> str | None:
    return None

  def create_session(self) -> FakeRecognitionSession:
    if not self._sessions:
      raise AssertionError("No more fake recognition sessions available.")
    return self._sessions.pop(0)


class RecordingWakeWordAction(NoOpWakeWordAction):

  def __init__(self) -> None:
    self.events: list[WakeWordEvent] = []

  def on_detected(self, event: WakeWordEvent) -> None:
    self.events.append(event)


def wait_until(predicate: object, *, timeout_seconds: float = 1.0) -> None:
  deadline = time.monotonic() + timeout_seconds
  while time.monotonic() < deadline:
    if predicate():
      return
    time.sleep(0.01)
  raise AssertionError("Timed out waiting for background voice processing.")


def _update(entry_id: str, text: str, *, is_final: bool,
            previous_entry_id: str | None = None) -> RecognitionUpdate:
  return RecognitionUpdate(
      entry_id=entry_id,
      text=text,
      is_final=is_final,
      received_at=datetime.now(timezone.utc),
      previous_entry_id=previous_entry_id,
  )


def test_normalize_transcript_text_compacts_case_and_punctuation() -> None:
  assert normalize_transcript_text("  Okay,   JARVIS!!!  ") == "okay jarvis"


def test_wake_phrase_detector_matches_alias_and_applies_cooldown() -> None:
  detector = NormalizedWakePhraseDetector(
      phrases=("okay jarvis", "ok jarvis"),
      cooldown_seconds=3.0,
  )
  now = datetime.now(timezone.utc)

  assert detector.detect("ok jarvis", now) == "ok jarvis"
  assert detector.detect("okay jarvis", now + timedelta(seconds=1)) is None
  assert detector.detect("okay jarvis", now + timedelta(seconds=4)) == "okay jarvis"


def test_wake_phrase_detector_requires_standalone_phrase() -> None:
  detector = NormalizedWakePhraseDetector(
      phrases=("okay jarvis", "ok jarvis"),
      cooldown_seconds=0.0,
  )

  assert detector.detect("bonjour okay jarvis", datetime.now(timezone.utc)) is None
  assert detector.detect("okay jarvis merci", datetime.now(timezone.utc)) is None


def test_voice_coordinator_promotes_partial_to_final_and_detects_wake_word() -> None:
  wake_word_action = RecordingWakeWordAction()
  coordinator = VoiceCoordinator(
      speech_recognizer=FakeSpeechRecognizer(updates_by_payload={
          b"partial": (_update("turn-one", "okay", is_final=False),),
          b"final": (_update("turn-one", "okay jarvis", is_final=True),),
      }),
      wake_phrase_detector=NormalizedWakePhraseDetector(
          phrases=("okay jarvis", "ok jarvis"),
          cooldown_seconds=3.0,
      ),
      wake_word_action=wake_word_action,
      transcript_buffer_size=5,
  )

  coordinator.start_session("session-one")
  coordinator.submit_audio(
      AudioChunk(
          session_id="session-one",
          pcm_s16le=b"partial",
          received_at=datetime.now(timezone.utc),
      ))
  coordinator.submit_audio(
      AudioChunk(
          session_id="session-one",
          pcm_s16le=b"final",
          received_at=datetime.now(timezone.utc),
      ))

  wait_until(lambda: len(coordinator.snapshot().entries) == 1
             and coordinator.snapshot().entries[0].is_final)
  snapshot = coordinator.snapshot()
  coordinator.stop_session("session-one")

  assert len(snapshot.entries) == 1
  entry = snapshot.entries[0]
  assert isinstance(entry, TranscriptEntry)
  assert entry.entry_id == "turn-one"
  assert entry.text == "okay jarvis"
  assert entry.is_final is True
  assert snapshot.mode_state == "wake_pending"
  assert snapshot.last_wake_word is not None
  assert snapshot.last_wake_word.entry_id == entry.entry_id
  assert len(wake_word_action.events) == 1
  assert wake_word_action.events[0].phrase == "okay jarvis"


def test_voice_coordinator_exposes_unavailable_recognizer_without_activation() -> None:
  coordinator = VoiceCoordinator(
      speech_recognizer=UnavailableSpeechRecognizer("missing model"),
      wake_phrase_detector=NormalizedWakePhraseDetector(
          phrases=("okay jarvis",),
          cooldown_seconds=3.0,
      ),
      wake_word_action=NoOpWakeWordAction(),
      transcript_buffer_size=5,
  )

  coordinator.start_session("session-one")
  snapshot = coordinator.snapshot()

  assert snapshot.available is False
  assert snapshot.active is False
  assert snapshot.session_id == "session-one"
  assert snapshot.error == "missing model"

  coordinator.stop_session("session-one")
  assert coordinator.snapshot().session_id is None


def test_voice_coordinator_resets_transcript_when_new_session_starts() -> None:
  coordinator = VoiceCoordinator(
      speech_recognizer=FakeSpeechRecognizer(updates_by_payload={
          b"final": (_update("turn-one", "bonjour", is_final=True),),
      }),
      wake_phrase_detector=NormalizedWakePhraseDetector(
          phrases=("okay jarvis",),
          cooldown_seconds=3.0,
      ),
      wake_word_action=NoOpWakeWordAction(),
      transcript_buffer_size=5,
  )

  coordinator.start_session("session-one")
  coordinator.submit_audio(
      AudioChunk(
          session_id="session-one",
          pcm_s16le=b"final",
          received_at=datetime.now(timezone.utc),
      ))
  wait_until(lambda: len(coordinator.snapshot().entries) == 1)
  coordinator.stop_session("session-one")

  coordinator.start_session("session-two")
  snapshot = coordinator.snapshot()
  coordinator.stop_session("session-two")

  assert snapshot.session_id == "session-two"
  assert snapshot.entries == tuple()
  assert snapshot.last_wake_word is None
  assert snapshot.mode_state == "idle"
  assert snapshot.dropped_chunks == 0


def test_voice_coordinator_counts_dropped_chunks_when_queue_is_full() -> None:
  coordinator = VoiceCoordinator(
      speech_recognizer=FakeSpeechRecognizer(),
      wake_phrase_detector=NormalizedWakePhraseDetector(
          phrases=("okay jarvis",),
          cooldown_seconds=3.0,
      ),
      wake_word_action=NoOpWakeWordAction(),
      transcript_buffer_size=5,
      audio_buffer_seconds=0.0001,
  )

  coordinator.start_session("session-one")
  coordinator.submit_audio(
      AudioChunk(
          session_id="session-one",
          pcm_s16le=b"x" * 128,
          received_at=datetime.now(timezone.utc),
      ))
  coordinator.stop_session("session-one")

  assert coordinator.snapshot().dropped_chunks == 1


def test_voice_coordinator_orders_turns_using_previous_entry_id() -> None:
  coordinator = VoiceCoordinator(
      speech_recognizer=FakeSpeechRecognizer(updates_by_payload={
          b"second": (_update("turn-two",
                              "deuxieme phrase",
                              is_final=True,
                              previous_entry_id="turn-one"),),
          b"first": (_update("turn-one", "premiere phrase", is_final=True),),
      }),
      wake_phrase_detector=NormalizedWakePhraseDetector(
          phrases=("okay jarvis",),
          cooldown_seconds=3.0,
      ),
      wake_word_action=NoOpWakeWordAction(),
      transcript_buffer_size=5,
  )

  coordinator.start_session("session-one")
  coordinator.submit_audio(
      AudioChunk(
          session_id="session-one",
          pcm_s16le=b"second",
          received_at=datetime.now(timezone.utc),
      ))
  coordinator.submit_audio(
      AudioChunk(
          session_id="session-one",
          pcm_s16le=b"first",
          received_at=datetime.now(timezone.utc),
      ))
  wait_until(lambda: len(coordinator.snapshot().entries) == 2)
  snapshot = coordinator.snapshot()
  coordinator.stop_session("session-one")

  assert [entry.entry_id for entry in snapshot.entries] == ["turn-one", "turn-two"]


def test_voice_coordinator_reorders_live_entry_when_previous_item_arrives() -> None:
  coordinator = VoiceCoordinator(
      speech_recognizer=FakeSpeechRecognizer(updates_by_payload={
          b"partial-second": (_update("turn-two", "deuxieme", is_final=False),),
          b"first": (_update("turn-one", "premiere phrase", is_final=True),),
          b"reorder-second": (_update("turn-two",
                                      "deuxieme",
                                      is_final=False,
                                      previous_entry_id="turn-one"),),
      }),
      wake_phrase_detector=NormalizedWakePhraseDetector(
          phrases=("okay jarvis",),
          cooldown_seconds=3.0,
      ),
      wake_word_action=NoOpWakeWordAction(),
      transcript_buffer_size=5,
  )

  coordinator.start_session("session-one")
  coordinator.submit_audio(
      AudioChunk(
          session_id="session-one",
          pcm_s16le=b"partial-second",
          received_at=datetime.now(timezone.utc),
      ))
  coordinator.submit_audio(
      AudioChunk(
          session_id="session-one",
          pcm_s16le=b"first",
          received_at=datetime.now(timezone.utc),
      ))
  coordinator.submit_audio(
      AudioChunk(
          session_id="session-one",
          pcm_s16le=b"reorder-second",
          received_at=datetime.now(timezone.utc),
      ))
  wait_until(lambda: len(coordinator.snapshot().entries) == 2)
  snapshot = coordinator.snapshot()
  coordinator.stop_session("session-one")

  assert [entry.entry_id for entry in snapshot.entries] == ["turn-one", "turn-two"]


def test_voice_coordinator_recovers_when_recognition_session_closes() -> None:
  first_session = FakeRecognitionSession(
      failures_by_payload={
          b"first": RuntimeError("socket is already closed."),
      })
  second_session = FakeRecognitionSession(
      updates_by_payload={
          b"first": (_update("turn-one", "bonjour", is_final=True),),
      })
  coordinator = VoiceCoordinator(
      speech_recognizer=SequencedSpeechRecognizer([first_session, second_session]),
      wake_phrase_detector=NormalizedWakePhraseDetector(
          phrases=("okay jarvis",),
          cooldown_seconds=3.0,
      ),
      wake_word_action=NoOpWakeWordAction(),
      transcript_buffer_size=5,
  )

  coordinator.start_session("session-one")
  coordinator.submit_audio(
      AudioChunk(
          session_id="session-one",
          pcm_s16le=b"first",
          received_at=datetime.now(timezone.utc),
      ))
  wait_until(lambda: len(coordinator.snapshot().entries) == 1)
  snapshot = coordinator.snapshot()
  coordinator.stop_session("session-one")

  assert snapshot.entries[0].text == "bonjour"
  assert snapshot.error is None


def test_openai_realtime_recognizer_requires_api_key() -> None:
  recognizer = OpenAiRealtimeRecognizer(
      api_key="",
      connection_url="wss://api.openai.com/v1/realtime?intent=transcription",
      model="gpt-4o-transcribe",
  )

  assert recognizer.available is False
  assert recognizer.error is not None


def test_openai_realtime_session_update_payload_omits_session_type() -> None:
  recognizer = OpenAiRealtimeRecognizer(
      api_key="test-key",
      connection_url="wss://api.openai.com/v1/realtime?intent=transcription",
      model="gpt-4o-transcribe",
      language="fr",
      prompt="okay jarvis",
  )

  payload = _build_openai_realtime_session_update_payload(recognizer._config)

  assert payload["type"] == "transcription_session.update"
  assert payload["session"]["input_audio_format"] == "pcm16"
  assert payload["session"]["input_audio_transcription"] == {
      "model": "gpt-4o-transcribe",
      "language": "fr",
      "prompt": "okay jarvis",
  }
  assert payload["session"]["turn_detection"] == {
      "type": "server_vad",
      "threshold": 0.5,
      "prefix_padding_ms": 300,
      "silence_duration_ms": 500,
  }


def test_benign_socket_close_error_is_recognized() -> None:
  assert _is_benign_socket_close_error("socket is already closed.")
  assert _is_benign_socket_close_error("socket is already closed")
  assert _is_benign_socket_close_error(" Socket is already closed. ")
  assert not _is_benign_socket_close_error("connection reset by peer")
