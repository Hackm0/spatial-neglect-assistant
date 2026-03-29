from __future__ import annotations

import base64
import json
import queue
import re
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol


PCM_SAMPLE_RATE = 24000
PCM_SAMPLE_WIDTH_BYTES = 2
PCM_CHANNEL_COUNT = 1
DEFAULT_AUDIO_BUFFER_SECONDS = 5.0
REALTIME_KEEPALIVE_INTERVAL_SECONDS = 10.0
MAX_RECOGNIZER_RECOVERY_ATTEMPTS = 3


def _utcnow() -> datetime:
  return datetime.now(timezone.utc)


def _coerce_string(value: object) -> str | None:
  if not isinstance(value, str):
    return None
  stripped = value.strip()
  return stripped or None


def _is_benign_socket_close_error(message: str) -> bool:
  normalized = message.strip().casefold()
  return normalized in {
      "socket is already closed.",
      "socket is already closed",
  }


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
  entry_id: str
  session_id: str
  text: str
  is_final: bool
  received_at: datetime


@dataclass(frozen=True, slots=True)
class WakeWordEvent:
  session_id: str
  phrase: str
  received_at: datetime
  entry_id: str


@dataclass(frozen=True, slots=True)
class VoiceStatus:
  available: bool
  active: bool
  session_id: str | None
  error: str | None
  dropped_chunks: int = 0
  mode_state: str = "idle"
  last_wake_word: WakeWordEvent | None = None
  entries: tuple[TranscriptEntry, ...] = tuple()


@dataclass(frozen=True, slots=True)
class RecognitionUpdate:
  entry_id: str
  text: str
  is_final: bool
  received_at: datetime
  previous_entry_id: str | None = None


@dataclass(frozen=True, slots=True)
class AudioChunk:
  session_id: str
  pcm_s16le: bytes
  received_at: datetime


@dataclass(frozen=True, slots=True)
class VoiceEvent:
  event_type: str
  payload: VoiceStatus | TranscriptEntry | WakeWordEvent


@dataclass(frozen=True, slots=True)
class VoiceSubscription:
  subscription_id: int
  events: "queue.Queue[VoiceEvent | None]"


class SpeechRecognitionSessionPort(Protocol):

  def accept_audio(self, chunk: AudioChunk) -> tuple[RecognitionUpdate, ...]:
    raise NotImplementedError

  def poll_updates(self) -> tuple[RecognitionUpdate, ...]:
    raise NotImplementedError

  def finalize(self, received_at: datetime) -> tuple[RecognitionUpdate, ...]:
    raise NotImplementedError


class SpeechRecognizerPort(Protocol):

  @property
  def available(self) -> bool:
    raise NotImplementedError

  @property
  def error(self) -> str | None:
    raise NotImplementedError

  def create_session(self) -> SpeechRecognitionSessionPort:
    raise NotImplementedError


class VoiceProcessingPort(Protocol):

  def start_session(self, session_id: str) -> None:
    raise NotImplementedError

  def submit_audio(self, chunk: AudioChunk) -> None:
    raise NotImplementedError

  def stop_session(self, session_id: str) -> None:
    raise NotImplementedError

  def snapshot(self) -> VoiceStatus:
    raise NotImplementedError

  def subscribe(self) -> VoiceSubscription:
    raise NotImplementedError

  def unsubscribe(self, subscription: VoiceSubscription) -> None:
    raise NotImplementedError

  def shutdown(self) -> None:
    raise NotImplementedError


class WakePhraseDetectorPort(Protocol):

  def reset(self) -> None:
    raise NotImplementedError

  def detect(self, text: str, occurred_at: datetime) -> str | None:
    raise NotImplementedError


class WakeWordActionPort(Protocol):

  def on_detected(self, event: WakeWordEvent) -> None:
    raise NotImplementedError


def normalize_transcript_text(text: str) -> str:
  normalized = re.sub(r"[^\w\s]+", " ", text.casefold(), flags=re.UNICODE)
  normalized = re.sub(r"\s+", " ", normalized).strip()
  return normalized


class NormalizedWakePhraseDetector(WakePhraseDetectorPort):

  def __init__(self, *, phrases: tuple[str, ...], cooldown_seconds: float) -> None:
    self._phrases = tuple((phrase, normalize_transcript_text(phrase))
                          for phrase in phrases if phrase.strip())
    self._cooldown = timedelta(seconds=max(0.0, cooldown_seconds))
    self._last_detected_at: datetime | None = None

  def reset(self) -> None:
    self._last_detected_at = None

  def detect(self, text: str, occurred_at: datetime) -> str | None:
    normalized_text = normalize_transcript_text(text)
    if not normalized_text:
      return None

    if (self._last_detected_at is not None
        and occurred_at - self._last_detected_at < self._cooldown):
      return None

    for original_phrase, normalized_phrase in self._phrases:
      if normalized_text == normalized_phrase:
        self._last_detected_at = occurred_at
        return original_phrase
    return None


class NoOpWakeWordAction(WakeWordActionPort):

  def on_detected(self, event: WakeWordEvent) -> None:
    del event


class _UnavailableRecognitionSession(SpeechRecognitionSessionPort):

  def accept_audio(self, chunk: AudioChunk) -> tuple[RecognitionUpdate, ...]:
    del chunk
    return tuple()

  def poll_updates(self) -> tuple[RecognitionUpdate, ...]:
    return tuple()

  def finalize(self, received_at: datetime) -> tuple[RecognitionUpdate, ...]:
    del received_at
    return tuple()


class UnavailableSpeechRecognizer(SpeechRecognizerPort):

  def __init__(self, error: str) -> None:
    self._error = error

  @property
  def available(self) -> bool:
    return False

  @property
  def error(self) -> str:
    return self._error

  def create_session(self) -> SpeechRecognitionSessionPort:
    return _UnavailableRecognitionSession()


@dataclass(frozen=True, slots=True)
class _OpenAiRealtimeSessionConfig:
  api_key: str
  connection_url: str
  model: str
  language: str | None
  prompt: str | None
  noise_reduction: str
  vad_threshold: float
  prefix_padding_ms: int
  silence_duration_ms: int


def _build_openai_realtime_session_update_payload(
    config: _OpenAiRealtimeSessionConfig) -> dict[str, object]:
  transcription: dict[str, object] = {"model": config.model}
  if config.language is not None:
    transcription["language"] = config.language
  if config.prompt is not None:
    transcription["prompt"] = config.prompt

  return {
      "type": "transcription_session.update",
      "session": {
          "input_audio_format": "pcm16",
          "input_audio_transcription": transcription,
          "turn_detection": {
              "type": "server_vad",
              "threshold": config.vad_threshold,
              "prefix_padding_ms": config.prefix_padding_ms,
              "silence_duration_ms": config.silence_duration_ms,
          },
          "input_audio_noise_reduction": {
              "type": config.noise_reduction,
          },
      },
  }


class OpenAiRealtimeRecognizer(SpeechRecognizerPort):

  def __init__(
      self,
      *,
      api_key: str,
      connection_url: str,
      model: str,
      language: str | None = None,
      prompt: str | None = None,
      noise_reduction: str = "near_field",
      vad_threshold: float = 0.5,
      prefix_padding_ms: int = 300,
      silence_duration_ms: int = 500,
  ) -> None:
    self._config = _OpenAiRealtimeSessionConfig(
        api_key=api_key.strip(),
        connection_url=connection_url.strip(),
        model=model.strip(),
        language=_coerce_string(language),
        prompt=_coerce_string(prompt),
        noise_reduction=noise_reduction.strip() or "near_field",
        vad_threshold=vad_threshold,
        prefix_padding_ms=prefix_padding_ms,
        silence_duration_ms=silence_duration_ms,
    )
    self._websocket_module: object | None = None
    self._error: str | None = None

    try:
      import websocket
    except ImportError:
      self._error = (
          "websocket-client n'est pas installe. Installe les dependances du "
          "projet pour activer la transcription vocale.")
      return

    if not self._config.api_key:
      self._error = (
          "OPENAI_API_KEY est absent. Definis la variable d'environnement pour "
          "activer la transcription vocale.")
      return

    if not self._config.connection_url:
      self._error = (
          "L'URL temps reel OpenAI n'est pas configuree. Definis "
          "MOBILE_INGEST_VOICE_REALTIME_URL.")
      return

    if not self._config.model:
      self._error = (
          "Le modele de transcription n'est pas configure. Definis "
          "MOBILE_INGEST_VOICE_MODEL.")
      return

    self._websocket_module = websocket

  @property
  def available(self) -> bool:
    return self._websocket_module is not None and self._error is None

  @property
  def error(self) -> str | None:
    return self._error

  def create_session(self) -> SpeechRecognitionSessionPort:
    if not self.available:
      return _UnavailableRecognitionSession()
    assert self._websocket_module is not None
    return _OpenAiRealtimeRecognitionSession(
        websocket_module=self._websocket_module,
        config=self._config,
    )


class _OpenAiRealtimeRecognitionSession(SpeechRecognitionSessionPort):

  def __init__(self, *, websocket_module: object,
               config: _OpenAiRealtimeSessionConfig) -> None:
    self._websocket_module = websocket_module
    self._config = config
    self._messages: "queue.Queue[RecognitionUpdate]" = queue.Queue()
    self._send_lock = threading.Lock()
    self._receiver_stop = threading.Event()
    self._receiver_error: str | None = None
    self._closed = False
    self._last_keepalive_at = time.monotonic()
    self._partial_text_by_item_id: dict[str, str] = {}
    self._previous_item_id_by_item_id: dict[str, str | None] = {}
    self._ws = websocket_module.create_connection(
        config.connection_url,
        header=[
            f"Authorization: Bearer {config.api_key}",
            "OpenAI-Beta: realtime=v1",
        ],
        enable_multithread=True,
        timeout=1.0,
    )
    self._ws.settimeout(0.25)
    self._send_json(self._session_update_payload())
    self._receiver_thread = threading.Thread(
        target=self._receiver_loop,
        name="openai-realtime-transcription",
        daemon=True,
    )
    self._receiver_thread.start()

  def accept_audio(self, chunk: AudioChunk) -> tuple[RecognitionUpdate, ...]:
    if not chunk.pcm_s16le:
      return self.poll_updates()
    self._send_json({
        "type": "input_audio_buffer.append",
        "audio": base64.b64encode(chunk.pcm_s16le).decode("ascii"),
    })
    return self.poll_updates()

  def poll_updates(self) -> tuple[RecognitionUpdate, ...]:
    updates: list[RecognitionUpdate] = []
    while True:
      try:
        updates.append(self._messages.get_nowait())
      except queue.Empty:
        break
    if updates:
      return tuple(updates)
    self._raise_receiver_error()
    return tuple()

  def finalize(self, received_at: datetime) -> tuple[RecognitionUpdate, ...]:
    del received_at
    updates = list(self.poll_updates())
    if not self._closed:
      try:
        self._send_json({"type": "input_audio_buffer.commit"})
      except RuntimeError:
        pass

      deadline = time.monotonic() + 1.0
      while time.monotonic() < deadline:
        batch = self.poll_updates()
        if batch:
          updates.extend(batch)
          continue
        time.sleep(0.05)

    self._close()
    updates.extend(self.poll_updates())
    self._raise_receiver_error()
    return tuple(updates)

  def _session_update_payload(self) -> dict[str, object]:
    return _build_openai_realtime_session_update_payload(self._config)

  def _receiver_loop(self) -> None:
    websocket_timeout = getattr(self._websocket_module, "WebSocketTimeoutException",
                                TimeoutError)
    websocket_closed = getattr(self._websocket_module,
                               "WebSocketConnectionClosedException",
                               RuntimeError)
    try:
      while not self._receiver_stop.is_set():
        try:
          raw_message = self._ws.recv()
        except websocket_timeout:
          self._maybe_send_keepalive_ping()
          continue
        except websocket_closed:
          break
        except Exception as exc:
          formatted_error = self._format_error(exc)
          if self._receiver_stop.is_set() and _is_benign_socket_close_error(
              formatted_error):
            break
          self._receiver_error = formatted_error
          break

        if not raw_message:
          continue

        try:
          payload = json.loads(raw_message)
        except json.JSONDecodeError:
          continue
        self._handle_event(payload)
    finally:
      self._receiver_stop.set()

  def _maybe_send_keepalive_ping(self) -> None:
    if self._receiver_stop.is_set() or self._closed:
      return
    now = time.monotonic()
    if now - self._last_keepalive_at < REALTIME_KEEPALIVE_INTERVAL_SECONDS:
      return
    try:
      self._ws.ping()
      self._last_keepalive_at = now
    except Exception as exc:
      formatted_error = self._format_error(exc)
      if not _is_benign_socket_close_error(formatted_error):
        self._receiver_error = formatted_error

  def _handle_event(self, payload: dict[str, object]) -> None:
    event_type = _coerce_string(payload.get("type"))
    if event_type is None:
      return

    if event_type == "input_audio_buffer.committed":
      item_id = _coerce_string(payload.get("item_id"))
      previous_item_id = _coerce_string(payload.get("previous_item_id"))
      if item_id is not None:
        self._previous_item_id_by_item_id[item_id] = previous_item_id
        partial_text = self._partial_text_by_item_id.get(item_id)
        if partial_text is not None:
          self._messages.put(RecognitionUpdate(
              entry_id=item_id,
              text=partial_text,
              is_final=False,
              received_at=_utcnow(),
              previous_entry_id=previous_item_id,
          ))
      return

    if event_type == "conversation.item.input_audio_transcription.delta":
      item_id = _coerce_string(payload.get("item_id"))
      delta = _coerce_string(payload.get("delta"))
      if item_id is None or delta is None:
        return
      partial_text = self._partial_text_by_item_id.get(item_id, "") + delta
      self._partial_text_by_item_id[item_id] = partial_text
      self._messages.put(RecognitionUpdate(
          entry_id=item_id,
          text=partial_text,
          is_final=False,
          received_at=_utcnow(),
          previous_entry_id=self._previous_item_id_by_item_id.get(item_id),
      ))
      return

    if event_type == "conversation.item.input_audio_transcription.completed":
      item_id = _coerce_string(payload.get("item_id"))
      transcript = _coerce_string(payload.get("transcript"))
      if item_id is None:
        return
      self._partial_text_by_item_id.pop(item_id, None)
      self._messages.put(RecognitionUpdate(
          entry_id=item_id,
          text=transcript or "",
          is_final=True,
          received_at=_utcnow(),
          previous_entry_id=self._previous_item_id_by_item_id.get(item_id),
      ))
      return

    if event_type.endswith(".failed") or event_type == "error":
      self._receiver_error = self._extract_error_message(payload)

  def _extract_error_message(self, payload: dict[str, object]) -> str:
    error_payload = payload.get("error")
    if isinstance(error_payload, dict):
      message = _coerce_string(error_payload.get("message"))
      if message is not None:
        return message
    message = _coerce_string(payload.get("message"))
    if message is not None:
      return message
    return "La session de transcription temps reel a echoue."

  def _format_error(self, exc: Exception) -> str:
    message = str(exc).strip()
    if message:
      return message
    return "La connexion OpenAI Realtime a ete interrompue."

  def _send_json(self, payload: dict[str, Any]) -> None:
    self._raise_receiver_error()
    if self._closed:
      raise RuntimeError("La session de transcription est deja fermee.")
    with self._send_lock:
      try:
        self._ws.send(json.dumps(payload))
      except Exception as exc:
        self._receiver_error = self._format_error(exc)
        raise RuntimeError(self._receiver_error) from exc

  def _raise_receiver_error(self) -> None:
    if self._receiver_error is not None:
      if self._closed and _is_benign_socket_close_error(self._receiver_error):
        return
      raise RuntimeError(self._receiver_error)

  def _close(self) -> None:
    if self._closed:
      return
    self._closed = True
    self._receiver_stop.set()
    try:
      self._ws.close()
    except Exception:
      pass
    self._receiver_thread.join(timeout=1.0)


_QUEUE_STOP = object()


class _BoundedAudioQueue:

  def __init__(self, *, max_buffered_bytes: int) -> None:
    self._max_buffered_bytes = max_buffered_bytes
    self._buffered_bytes = 0
    self._lock = threading.Lock()
    self._queue: "queue.Queue[AudioChunk | object]" = queue.Queue()

  def put(self, chunk: AudioChunk) -> bool:
    with self._lock:
      if self._buffered_bytes + len(chunk.pcm_s16le) > self._max_buffered_bytes:
        return False
      self._buffered_bytes += len(chunk.pcm_s16le)
    self._queue.put(chunk)
    return True

  def get(self, timeout: float) -> AudioChunk | object:
    item = self._queue.get(timeout=timeout)
    if isinstance(item, AudioChunk):
      with self._lock:
        self._buffered_bytes = max(0, self._buffered_bytes - len(item.pcm_s16le))
    return item

  def close(self) -> None:
    self._queue.put(_QUEUE_STOP)


class VoiceCoordinator(VoiceProcessingPort):

  def __init__(
      self,
      *,
      speech_recognizer: SpeechRecognizerPort,
      wake_phrase_detector: WakePhraseDetectorPort,
      wake_word_action: WakeWordActionPort,
      transcript_buffer_size: int,
      audio_buffer_seconds: float = DEFAULT_AUDIO_BUFFER_SECONDS,
  ) -> None:
    self._speech_recognizer = speech_recognizer
    self._wake_phrase_detector = wake_phrase_detector
    self._wake_word_action = wake_word_action
    self._transcript_buffer_size = max(1, transcript_buffer_size)
    self._max_buffered_bytes = int(audio_buffer_seconds * PCM_SAMPLE_RATE
                                   * PCM_SAMPLE_WIDTH_BYTES
                                   * PCM_CHANNEL_COUNT)
    self._lock = threading.Lock()
    self._entries: list[TranscriptEntry] = []
    self._previous_entry_id_by_entry_id: dict[str, str | None] = {}
    self._next_subscription_id = 1
    self._subscriptions: dict[int, "queue.Queue[VoiceEvent | None]"] = {}
    self._active_session_id: str | None = None
    self._audio_queue: _BoundedAudioQueue | None = None
    self._worker_thread: threading.Thread | None = None
    self._status = VoiceStatus(
        available=self._speech_recognizer.available,
        active=False,
        session_id=None,
        error=self._speech_recognizer.error,
    )

  def start_session(self, session_id: str) -> None:
    previous_session_id = None
    with self._lock:
      previous_session_id = self._active_session_id
    if previous_session_id is not None:
      self.stop_session(previous_session_id)

    recognition_session = self._speech_recognizer.create_session()
    audio_queue = (_BoundedAudioQueue(max_buffered_bytes=self._max_buffered_bytes)
                   if self._speech_recognizer.available else None)
    worker_thread = None
    if audio_queue is not None:
      worker_thread = threading.Thread(
          target=self._run_worker,
          args=(session_id, audio_queue, recognition_session),
          name=f"voice-worker-{session_id[:8]}",
          daemon=True,
      )

    with self._lock:
      self._entries = []
      self._previous_entry_id_by_entry_id = {}
      self._wake_phrase_detector.reset()
      self._active_session_id = session_id
      self._audio_queue = audio_queue
      self._worker_thread = worker_thread
      self._status = replace(
          self._status,
          available=self._speech_recognizer.available,
          active=worker_thread is not None,
          session_id=session_id,
          error=self._speech_recognizer.error,
          dropped_chunks=0,
          mode_state="idle",
          last_wake_word=None,
          entries=tuple(),
      )

    if worker_thread is not None:
      worker_thread.start()
    self._broadcast_status()

  def submit_audio(self, chunk: AudioChunk) -> None:
    status_changed = False
    with self._lock:
      if chunk.session_id != self._active_session_id or self._audio_queue is None:
        return
      if not self._audio_queue.put(chunk):
        self._status = replace(
            self._status,
            dropped_chunks=self._status.dropped_chunks + 1,
        )
        status_changed = True

    if status_changed:
      self._broadcast_status()

  def stop_session(self, session_id: str) -> None:
    worker_thread = None
    audio_queue = None
    with self._lock:
      if session_id != self._active_session_id:
        return
      worker_thread = self._worker_thread
      audio_queue = self._audio_queue

    if audio_queue is not None:
      audio_queue.close()
    if worker_thread is not None:
      worker_thread.join(timeout=2.0)

    with self._lock:
      if session_id != self._active_session_id:
        return
      self._active_session_id = None
      self._audio_queue = None
      self._worker_thread = None
      self._status = replace(
          self._status,
          active=False,
          session_id=None,
          mode_state="idle",
          error=self._speech_recognizer.error if not self._speech_recognizer.available
          else self._status.error,
      )
    self._broadcast_status()

  def snapshot(self) -> VoiceStatus:
    with self._lock:
      return self._status

  def subscribe(self) -> VoiceSubscription:
    event_queue: "queue.Queue[VoiceEvent | None]" = queue.Queue()
    with self._lock:
      subscription_id = self._next_subscription_id
      self._next_subscription_id += 1
      self._subscriptions[subscription_id] = event_queue
      snapshot = self._status

    event_queue.put(VoiceEvent("status", snapshot))
    return VoiceSubscription(subscription_id=subscription_id, events=event_queue)

  def unsubscribe(self, subscription: VoiceSubscription) -> None:
    with self._lock:
      self._subscriptions.pop(subscription.subscription_id, None)

  def shutdown(self) -> None:
    session_id = None
    with self._lock:
      session_id = self._active_session_id

    if session_id is not None:
      self.stop_session(session_id)

    with self._lock:
      subscriptions = tuple(self._subscriptions.values())
      self._subscriptions.clear()

    for subscription_queue in subscriptions:
      subscription_queue.put(None)

  def _run_worker(
      self,
      session_id: str,
      audio_queue: _BoundedAudioQueue,
      recognition_session: SpeechRecognitionSessionPort,
  ) -> None:
    worker_error: str | None = None
    recovery_attempts = 0
    try:
      while True:
        try:
          item = audio_queue.get(timeout=0.2)
        except queue.Empty:
          try:
            for update in recognition_session.poll_updates():
              self._apply_recognition_update(session_id, update)
          except Exception as exc:
            replacement = self._recover_recognition_session(
                session_id=session_id,
                failed_session=recognition_session,
                failure=exc,
                recovery_attempts=recovery_attempts,
            )
            if replacement is None:
              worker_error = str(exc)
              break
            recognition_session, recovery_attempts = replacement
          continue

        if item is _QUEUE_STOP:
          break
        assert isinstance(item, AudioChunk)

        try:
          updates = recognition_session.accept_audio(item)
        except Exception as exc:
          replacement = self._recover_recognition_session(
              session_id=session_id,
              failed_session=recognition_session,
              failure=exc,
              recovery_attempts=recovery_attempts,
          )
          if replacement is None:
            worker_error = str(exc)
            break
          recognition_session, recovery_attempts = replacement
          try:
            updates = recognition_session.accept_audio(item)
          except Exception as retry_exc:
            worker_error = str(retry_exc)
            break

        for update in updates:
          self._apply_recognition_update(session_id, update)
        recovery_attempts = 0
    except Exception as exc:
      worker_error = str(exc)
    finally:
      try:
        for update in recognition_session.finalize(_utcnow()):
          self._apply_recognition_update(session_id, update)
      except Exception as exc:
        if worker_error is None:
          worker_error = str(exc)

      if worker_error is not None and _is_benign_socket_close_error(worker_error):
        worker_error = None

      if worker_error is not None:
        self._record_worker_error(session_id, worker_error)

  def _recover_recognition_session(
      self,
      *,
      session_id: str,
      failed_session: SpeechRecognitionSessionPort,
      failure: Exception,
      recovery_attempts: int,
  ) -> tuple[SpeechRecognitionSessionPort, int] | None:
    del failure
    if recovery_attempts >= MAX_RECOGNIZER_RECOVERY_ATTEMPTS:
      return None
    if not self._speech_recognizer.available:
      return None

    with self._lock:
      if session_id != self._active_session_id:
        return None

    try:
      failed_session.finalize(_utcnow())
    except Exception:
      pass

    replacement = self._speech_recognizer.create_session()
    if isinstance(replacement, _UnavailableRecognitionSession):
      return None
    return replacement, recovery_attempts + 1

  def _apply_recognition_update(self, session_id: str,
                                update: RecognitionUpdate) -> None:
    updated_entry = None
    wake_word_event = None
    with self._lock:
      if session_id != self._active_session_id:
        return

      entry_count_before = len(self._entries)
      updated_entry = self._merge_update(session_id, update)

      if updated_entry is not None or len(self._entries) != entry_count_before:
        self._status = replace(self._status, entries=tuple(self._entries))

      if updated_entry is not None and updated_entry.is_final:
        detected_phrase = self._wake_phrase_detector.detect(
            updated_entry.text,
            updated_entry.received_at,
        )
        if detected_phrase is not None:
          wake_word_event = WakeWordEvent(
              session_id=session_id,
              phrase=detected_phrase,
              received_at=updated_entry.received_at,
              entry_id=updated_entry.entry_id,
          )
          self._status = replace(
              self._status,
              mode_state="wake_pending",
              last_wake_word=wake_word_event,
          )

    if updated_entry is not None:
      self._broadcast("transcript", updated_entry)
    if wake_word_event is not None:
      self._wake_word_action.on_detected(wake_word_event)
      self._broadcast("wake-word", wake_word_event)
      self._broadcast_status()

  def _merge_update(self, session_id: str,
                    update: RecognitionUpdate) -> TranscriptEntry | None:
    existing_index = self._find_entry_index(update.entry_id)
    if existing_index is not None:
      if update.is_final and not update.text:
        del self._entries[existing_index]
        return None

      updated_entry = TranscriptEntry(
          entry_id=update.entry_id,
          session_id=session_id,
          text=update.text,
          is_final=update.is_final,
          received_at=update.received_at,
      )
      self._entries[existing_index] = updated_entry
      self._previous_entry_id_by_entry_id[update.entry_id] = update.previous_entry_id
      self._reposition_entry(update.entry_id, update.previous_entry_id)
      return self._entry_by_id(update.entry_id)

    if not update.text:
      return None

    new_entry = TranscriptEntry(
        entry_id=update.entry_id,
        session_id=session_id,
        text=update.text,
        is_final=update.is_final,
        received_at=update.received_at,
    )
    self._previous_entry_id_by_entry_id[update.entry_id] = update.previous_entry_id
    insert_index = self._resolve_insert_index(update.entry_id,
                                              update.previous_entry_id)
    self._entries.insert(insert_index, new_entry)
    self._reposition_dependents(update.entry_id)
    self._trim_entries()
    return self._entry_by_id(update.entry_id)

  def _resolve_insert_index(self, entry_id: str,
                            previous_entry_id: str | None) -> int:
    if previous_entry_id is None:
      dependent_index = self._first_dependent_index(entry_id)
      if dependent_index is not None:
        return dependent_index
      return len(self._entries)
    previous_index = self._find_entry_index(previous_entry_id)
    if previous_index is None:
      return len(self._entries)
    return previous_index + 1

  def _reposition_entry(self, entry_id: str, previous_entry_id: str | None) -> None:
    current_index = self._find_entry_index(entry_id)
    if current_index is None:
      return
    entry = self._entries.pop(current_index)
    insert_index = self._resolve_insert_index(entry_id, previous_entry_id)
    if insert_index > len(self._entries):
      insert_index = len(self._entries)
    self._entries.insert(insert_index, entry)
    self._reposition_dependents(entry_id)

  def _reposition_dependents(self, entry_id: str) -> None:
    dependent_ids = [
        candidate_id for candidate_id, previous_entry_id
        in self._previous_entry_id_by_entry_id.items()
        if previous_entry_id == entry_id
    ]
    for dependent_id in dependent_ids:
      dependent_previous_entry_id = self._previous_entry_id_by_entry_id.get(
          dependent_id)
      self._reposition_entry(dependent_id, dependent_previous_entry_id)

  def _first_dependent_index(self, entry_id: str) -> int | None:
    for index, entry in enumerate(self._entries):
      if self._previous_entry_id_by_entry_id.get(entry.entry_id) == entry_id:
        return index
    return None

  def _trim_entries(self) -> None:
    overflow = len(self._entries) - self._transcript_buffer_size
    if overflow <= 0:
      return
    removed_entry_ids = [entry.entry_id for entry in self._entries[:overflow]]
    del self._entries[:overflow]
    for removed_entry_id in removed_entry_ids:
      self._previous_entry_id_by_entry_id.pop(removed_entry_id, None)

  def _find_entry_index(self, entry_id: str) -> int | None:
    for index, entry in enumerate(self._entries):
      if entry.entry_id == entry_id:
        return index
    return None

  def _entry_by_id(self, entry_id: str) -> TranscriptEntry | None:
    entry_index = self._find_entry_index(entry_id)
    if entry_index is None:
      return None
    return self._entries[entry_index]

  def _record_worker_error(self, session_id: str, message: str) -> None:
    with self._lock:
      if session_id != self._active_session_id:
        return
      self._audio_queue = None
      self._worker_thread = None
      self._status = replace(
          self._status,
          active=False,
          error=message,
      )
    self._broadcast_status()

  def _broadcast_status(self) -> None:
    self._broadcast("status", self.snapshot())

  def _broadcast(self, event_type: str,
                 payload: VoiceStatus | TranscriptEntry | WakeWordEvent) -> None:
    with self._lock:
      subscribers = tuple(self._subscriptions.values())

    for subscriber in subscribers:
      subscriber.put(VoiceEvent(event_type, payload))
