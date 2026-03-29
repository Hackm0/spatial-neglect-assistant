from __future__ import annotations

import io
import json
import queue
import time
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from mobile_ingestion.object_feedback import ObjectFeedbackPort
from mobile_ingestion.object_search import (ObjectDetectionResult,
                                            ObjectDetectorStatus,
                                            ObjectSearchCoordinator,
                                            ObjectSearchFrame,
                                            ObjectTargetResolution,
                                            OpenAiVisionDetector)
from mobile_ingestion.voice import (TranscriptEntry, VoiceEvent,
                                    VoiceSubscription, WakeWordEvent)


def wait_until(predicate: object, *, timeout_seconds: float = 1.0) -> None:
  deadline = time.monotonic() + timeout_seconds
  while time.monotonic() < deadline:
    if predicate():
      return
    time.sleep(0.01)
  raise AssertionError("Timed out waiting for object-search background work.")


class FakeVoiceProcessor:

  def __init__(self) -> None:
    self._subscriptions: list["queue.Queue[VoiceEvent | None]"] = []
    self._next_subscription_id = 1

  def subscribe(self) -> VoiceSubscription:
    events: "queue.Queue[VoiceEvent | None]" = queue.Queue()
    subscription = VoiceSubscription(self._next_subscription_id, events)
    self._next_subscription_id += 1
    self._subscriptions.append(events)
    return subscription

  def unsubscribe(self, subscription: VoiceSubscription) -> None:
    del subscription

  def emit(self, event: VoiceEvent) -> None:
    for subscription_queue in self._subscriptions:
      subscription_queue.put(event)


class FakeObjectDetector:

  def __init__(self) -> None:
    self.available = True
    self.error = None
    self.selected_model = "gpt-5.4-mini"
    self.model_ready = False
    self.model_state = "pending"
    self.model_detail = "Préparation du modèle vision OpenAI..."
    self.prepare_calls = 0
    self.set_model_calls: list[str] = []
    self.results: "queue.Queue[ObjectDetectionResult]" = queue.Queue()
    self.calls: list[tuple[ObjectSearchFrame, tuple[str, ...]]] = []

  def prepare(self) -> None:
    self.prepare_calls += 1
    self.model_ready = True
    self.model_state = "ready"
    self.model_detail = f"Modèle vision OpenAI actif : {self.selected_model}."

  def runtime_status(self) -> ObjectDetectorStatus:
    return ObjectDetectorStatus(
        available=self.available,
        model_ready=self.model_ready,
        model_state=self.model_state,
        detail=self.model_detail,
        selected_model=self.selected_model,
    )

  def set_model(self, model: str) -> ObjectDetectorStatus:
    self.selected_model = model
    self.set_model_calls.append(model)
    self.model_ready = True
    self.model_state = "ready"
    self.model_detail = f"Modèle vision OpenAI actif : {self.selected_model}."
    return self.runtime_status()

  def detect(self, *, frame: ObjectSearchFrame,
             labels: tuple[str, ...]) -> ObjectDetectionResult:
    self.calls.append((frame, labels))
    try:
      return self.results.get_nowait()
    except queue.Empty:
      return ObjectDetectionResult(detected=False)


class FakeResolver:

  def __init__(self) -> None:
    self.available = True
    self.error = None
    self._responses: "queue.Queue[ObjectTargetResolution]" = queue.Queue()
    self.calls: list[str] = []

  def queue_response(self, response: ObjectTargetResolution) -> None:
    self._responses.put(response)

  def resolve(self, transcript_text: str) -> ObjectTargetResolution:
    self.calls.append(transcript_text)
    try:
      return self._responses.get_nowait()
    except queue.Empty:
      return ObjectTargetResolution(action="unknown")


class FakeFeedback(ObjectFeedbackPort):

  def __init__(self) -> None:
    self.started_sessions: list[str] = []
    self.stopped_sessions: list[str] = []
    self.detected_sessions: list[str] = []
    self.cleared_sessions: list[str] = []
    self.shutdown_calls = 0

  def start_session(self, session_id: str) -> None:
    self.started_sessions.append(session_id)

  def stop_session(self, session_id: str) -> None:
    self.stopped_sessions.append(session_id)

  def notify_target_detected(self, session_id: str) -> None:
    self.detected_sessions.append(session_id)

  def clear(self, session_id: str) -> None:
    self.cleared_sessions.append(session_id)

  def shutdown(self) -> None:
    self.shutdown_calls += 1


class FakeUrlOpen:

  def __init__(self, response_payload: dict[str, object]) -> None:
    self.response_payload = response_payload
    self.calls: list[tuple[object, float]] = []

  def __call__(self, request: object, timeout: float) -> object:
    self.calls.append((request, timeout))
    return _FakeHttpResponse(self.response_payload)


class _FakeHttpResponse:

  def __init__(self, response_payload: dict[str, object]) -> None:
    self._body = json.dumps(response_payload).encode("utf-8")

  def __enter__(self) -> "_FakeHttpResponse":
    return self

  def __exit__(self, exc_type, exc, traceback) -> bool:
    del exc_type, exc, traceback
    return False

  def read(self) -> bytes:
    return self._body


def _response_payload(content: dict[str, object]) -> dict[str, object]:
  return {
      "output": [
          {
              "type": "message",
              "content": [
                  {
                      "type": "output_text",
                      "text": json.dumps(content),
                  },
              ],
          },
      ],
  }


def _http_error(message: str) -> urllib.error.HTTPError:
  return urllib.error.HTTPError(
      url="https://api.openai.com/v1/responses",
      code=400,
      msg="Bad Request",
      hdrs=None,
      fp=io.BytesIO(
          json.dumps({"error": {"message": message}}).encode("utf-8")),
  )


def _frame(*, session_id: str = "session-one") -> ObjectSearchFrame:
  return ObjectSearchFrame(
      session_id=session_id,
      received_at=datetime.now(timezone.utc),
      image_rgb=object(),
      width=320,
      height=240,
  )


def _final_transcript(text: str, *, session_id: str = "session-one") -> TranscriptEntry:
  return TranscriptEntry(
      entry_id=f"entry-{time.monotonic_ns()}",
      session_id=session_id,
      text=text,
      is_final=True,
      received_at=datetime.now(timezone.utc),
  )


def _wake_event(*, session_id: str = "session-one") -> WakeWordEvent:
  return WakeWordEvent(
      session_id=session_id,
      phrase="jarvis",
      received_at=datetime.now(timezone.utc),
      entry_id=f"wake-{time.monotonic_ns()}",
  )


@dataclass
class CoordinatorFixture:
  coordinator: ObjectSearchCoordinator
  voice: FakeVoiceProcessor
  detector: FakeObjectDetector
  resolver: FakeResolver
  feedback: FakeFeedback


def make_fixture(*, command_timeout_seconds: float = 8.0) -> CoordinatorFixture:
  voice = FakeVoiceProcessor()
  detector = FakeObjectDetector()
  resolver = FakeResolver()
  feedback = FakeFeedback()
  coordinator = ObjectSearchCoordinator(
      voice_processor=voice,
      object_detector=detector,
      target_resolver=resolver,
      feedback=feedback,
      wake_phrases=("jarvis",),
      detection_interval_seconds=0.01,
      command_timeout_seconds=command_timeout_seconds,
  )
  return CoordinatorFixture(
      coordinator=coordinator,
      voice=voice,
      detector=detector,
      resolver=resolver,
      feedback=feedback,
  )


def test_object_search_uses_follow_up_after_wake_word() -> None:
  fixture = make_fixture()
  fixture.resolver.queue_response(
      ObjectTargetResolution(
          action="search",
          display_label_fr="clés",
          detector_labels_en=("keys", "keychain"),
      ))

  fixture.coordinator.start_session("session-one")
  wait_until(lambda: fixture.detector.prepare_calls == 1)
  fixture.voice.emit(VoiceEvent("wake-word", _wake_event()))
  wait_until(lambda: fixture.coordinator.snapshot().state == "awaiting_request")

  fixture.voice.emit(
      VoiceEvent("transcript", _final_transcript("aide moi a trouver mes cles")))
  wait_until(lambda: fixture.coordinator.snapshot().state == "searching")

  snapshot = fixture.coordinator.snapshot()
  fixture.coordinator.stop_session("session-one")

  assert snapshot.target_label == "clés"
  assert snapshot.detected is False
  assert snapshot.selected_vision_model == "gpt-5.4-mini"
  assert fixture.feedback.started_sessions == ["session-one"]
  assert fixture.feedback.stopped_sessions == ["session-one"]
  assert fixture.resolver.calls[-1] == "aide moi a trouver mes cles"


def test_object_search_preloads_detector_on_session_start() -> None:
  fixture = make_fixture()

  fixture.coordinator.start_session("session-one")
  wait_until(lambda: fixture.detector.prepare_calls == 1)
  wait_until(lambda: fixture.coordinator.snapshot().model_ready is True)

  snapshot = fixture.coordinator.snapshot()
  fixture.coordinator.stop_session("session-one")

  assert snapshot.active is True
  assert snapshot.model_ready is True
  assert snapshot.model_state == "ready"
  assert snapshot.selected_vision_model == "gpt-5.4-mini"


def test_switching_vision_model_preserves_target_and_resets_search() -> None:
  fixture = make_fixture()
  fixture.resolver.queue_response(
      ObjectTargetResolution(
          action="search",
          display_label_fr="bouteille d'eau",
          detector_labels_en=("water bottle", "bottle"),
      ))

  fixture.coordinator.start_session("session-one")
  wait_until(lambda: fixture.detector.prepare_calls == 1)
  fixture.voice.emit(
      VoiceEvent(
          "transcript",
          _final_transcript("jarvis trouve ma bouteille d'eau"),
      ))
  wait_until(lambda: fixture.coordinator.snapshot().state == "searching")

  fixture.detector.results.put(
      ObjectDetectionResult(detected=True, matched_label="water bottle"))
  fixture.coordinator.submit_frame(_frame())
  wait_until(lambda: fixture.coordinator.snapshot().state == "found")

  snapshot = fixture.coordinator.set_selected_vision_model("gpt-5.4")
  fixture.coordinator.stop_session("session-one")

  assert snapshot.state == "searching"
  assert snapshot.target_label == "bouteille d'eau"
  assert snapshot.detected is False
  assert snapshot.last_detected_at is None
  assert snapshot.selected_vision_model == "gpt-5.4"
  assert fixture.detector.set_model_calls == ["gpt-5.4"]
  assert fixture.feedback.cleared_sessions == ["session-one"]


def test_object_search_detection_triggers_feedback_and_cancel_clears_it() -> None:
  fixture = make_fixture()
  fixture.resolver.queue_response(
      ObjectTargetResolution(
          action="search",
          display_label_fr="téléphone",
          detector_labels_en=("phone",),
      ))
  fixture.resolver.queue_response(ObjectTargetResolution(action="cancel"))

  fixture.coordinator.start_session("session-one")
  fixture.voice.emit(
      VoiceEvent(
          "transcript",
          _final_transcript("jarvis trouve mon telephone"),
      ))
  wait_until(lambda: fixture.coordinator.snapshot().state == "searching")

  fixture.detector.results.put(
      ObjectDetectionResult(detected=True, matched_label="phone"))
  fixture.coordinator.submit_frame(_frame())
  wait_until(lambda: fixture.coordinator.snapshot().state == "found")

  fixture.voice.emit(
      VoiceEvent(
          "transcript",
          _final_transcript("jarvis arrete"),
      ))
  wait_until(lambda: fixture.coordinator.snapshot().state == "idle")
  fixture.coordinator.stop_session("session-one")

  assert fixture.feedback.detected_sessions == ["session-one"]
  assert fixture.feedback.cleared_sessions[-1] == "session-one"


def test_openai_vision_detector_requires_api_key() -> None:
  detector = OpenAiVisionDetector(api_key="", model="gpt-5.4-mini")

  assert detector.available is False
  assert detector.error is not None


def test_openai_vision_detector_builds_responses_payload_for_gpt54_models(
    monkeypatch) -> None:
  urlopen = FakeUrlOpen(_response_payload({
      "detected": True,
      "matchedLabel": "phone",
  }))
  detector = OpenAiVisionDetector(
      api_key="test-key",
      model="gpt-5.4-mini",
      urlopen=urlopen,
  )
  monkeypatch.setattr(detector, "_encode_frame_as_jpeg", lambda image_rgb: b"jpeg")

  result = detector.detect(frame=_frame(), labels=("phone",))

  request, timeout = urlopen.calls[0]
  payload = json.loads(request.data.decode("utf-8"))

  assert result.detected is True
  assert result.matched_label == "phone"
  assert timeout == 10.0
  assert payload["model"] == "gpt-5.4-mini"
  assert payload["reasoning"] == {"effort": "low"}
  assert payload["input"][0]["content"][1]["type"] == "input_image"
  assert payload["input"][0]["content"][1]["image_url"].startswith(
      "data:image/jpeg;base64,")
  enum_values = payload["text"]["format"]["schema"]["properties"][
      "matchedLabel"]["anyOf"][0]["enum"]
  assert "phone" in enum_values


def test_openai_vision_detector_omits_reasoning_for_gpt4o(
    monkeypatch) -> None:
  urlopen = FakeUrlOpen(_response_payload({
      "detected": False,
      "matchedLabel": None,
  }))
  detector = OpenAiVisionDetector(
      api_key="test-key",
      model="gpt-4o",
      urlopen=urlopen,
  )
  monkeypatch.setattr(detector, "_encode_frame_as_jpeg", lambda image_rgb: b"jpeg")

  result = detector.detect(frame=_frame(), labels=("remote",))
  request, _ = urlopen.calls[0]
  payload = json.loads(request.data.decode("utf-8"))

  assert result.detected is False
  assert "reasoning" not in payload


def test_openai_vision_detector_surfaces_http_errors(monkeypatch) -> None:
  def raising_urlopen(request: object, timeout: float) -> object:
    del request, timeout
    raise _http_error("vision request failed")

  detector = OpenAiVisionDetector(
      api_key="test-key",
      model="gpt-5.4-mini",
      urlopen=raising_urlopen,
  )
  monkeypatch.setattr(detector, "_encode_frame_as_jpeg", lambda image_rgb: b"jpeg")

  with pytest.raises(RuntimeError, match="vision request failed"):
    detector.detect(frame=_frame(), labels=("phone",))
