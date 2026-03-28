from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass

import pytest
from werkzeug.serving import make_server

from mobile_ingestion import create_app
from mobile_ingestion.analyzer import AnalyzerMetrics, AnalyzerPort, SessionMetadata, TranscriptSnapshot
from mobile_ingestion.config import AppConfig
from mobile_ingestion.dto import SessionDescriptionDto
from mobile_ingestion.services import ServiceContainer

playwright = pytest.importorskip("playwright.sync_api")


class RecordingAnalyzer(AnalyzerPort):

  def on_session_started(self, metadata: SessionMetadata) -> None:
    del metadata

  def on_video_frame(self, frame: object) -> None:
    del frame

  def on_audio_frame(self, frame: object) -> None:
    del frame

  def on_session_stopped(self, metadata: SessionMetadata) -> None:
    del metadata

  def snapshot(self) -> AnalyzerMetrics:
    return AnalyzerMetrics()

  def on_transcript(self, text: str, *, final: bool, source: str) -> None:
    del text, final, source

  def transcript_snapshot(self) -> TranscriptSnapshot:
    return TranscriptSnapshot(entries=())

  def clear_transcript(self) -> None:
    return


@dataclass
class FakeStatus:
  active: bool = False
  state: str = "idle"
  connection_state: str = "closed"
  has_video_track: bool = False
  has_audio_track: bool = False
  error: str | None = None

  def to_dict(self) -> dict[str, object]:
    return {
        "state": self.state,
        "active": self.active,
        "sessionId": "fake-session" if self.active else None,
        "connectionState": self.connection_state,
        "hasVideoTrack": self.has_video_track,
        "hasAudioTrack": self.has_audio_track,
        "startedAt": None,
        "error": self.error,
        "analyzerMetrics": AnalyzerMetrics().to_dict(),
    }


class FakeSessionManager:

  def __init__(self) -> None:
    self.status = FakeStatus()

  def accept_offer(self, offer: SessionDescriptionDto) -> SessionDescriptionDto:
    del offer
    self.status = FakeStatus(
        active=True,
        state="streaming",
        connection_state="connected",
        has_video_track=True,
        has_audio_track=True,
    )
    return SessionDescriptionDto(sdp="answer-sdp", type="answer")

  def get_status(self) -> FakeStatus:
    return self.status

  def close_active_session(self) -> None:
    self.status = FakeStatus()

  def shutdown(self) -> None:
    self.close_active_session()


@dataclass(slots=True)
class FakeRuntime:

  def stop(self, timeout: float = 0.0) -> None:
    del timeout


class _ServerThread(threading.Thread):

  def __init__(self, app, host: str = "127.0.0.1", port: int = 0) -> None:
    super().__init__(daemon=True)
    self._server = make_server(host, port, app)
    self.host = host
    self.port = self._server.socket.getsockname()[1]

  def run(self) -> None:
    self._server.serve_forever()

  def shutdown(self) -> None:
    self._server.shutdown()


@pytest.fixture
def frontend_url():
  settings = AppConfig(
      testing=True,
      voice_wake_phrases=("ok jarvis", "hey atlas"),
      voice_idle_timeout_seconds=12,
  )
  services = ServiceContainer(
      settings=settings,
      runtime=FakeRuntime(),
      analyzer=RecordingAnalyzer(),
      session_manager=FakeSessionManager(),
  )
  app = create_app(settings, services=services)
  server = _ServerThread(app)
  server.start()
  url = f"http://{server.host}:{server.port}"
  try:
    yield url
  finally:
    server.shutdown()


@contextlib.contextmanager
def _playwright_page():
  with playwright.sync_playwright() as p:
    try:
      browser = p.chromium.launch(headless=True)
    except Exception as exc:  # pragma: no cover - environment dependent
      pytest.skip(f"Playwright Chromium unavailable: {exc}")
    context = browser.new_context()
    page = context.new_page()
    try:
      yield page
    finally:
      context.close()
      browser.close()


def _install_fake_speech_api(page) -> None:
  page.add_init_script("""
    (() => {
      window.__speechInstances = [];
      class FakeSpeechRecognition {
        constructor() {
          this.continuous = true;
          this.interimResults = false;
          this.lang = 'fr-FR';
          this._listeners = {};
          window.__speechInstances.push(this);
        }
        addEventListener(type, handler) {
          if (!this._listeners[type]) {
            this._listeners[type] = [];
          }
          this._listeners[type].push(handler);
        }
        start() {
          this._started = true;
        }
        stop() {
          this._started = false;
        }
      }
      window.__emitSpeech = (text) => {
        const instance = window.__speechInstances[0];
        if (!instance) {
          return false;
        }
        const event = {
          resultIndex: 0,
          results: [{
            isFinal: true,
            0: { transcript: text },
          }],
        };
        const handlers = instance._listeners.result || [];
        for (const handler of handlers) {
          handler(event);
        }
        return true;
      };
      window.SpeechRecognition = FakeSpeechRecognition;
      window.webkitSpeechRecognition = FakeSpeechRecognition;
    })();
  """)


def test_voice_settings_persist_in_browser(frontend_url: str) -> None:
  with _playwright_page() as page:
    _install_fake_speech_api(page)
    page.goto(frontend_url, wait_until="networkidle")

    page.fill("#wake-phrase-input", "hello ops, hey atlas")
    page.fill("#voice-idle-timeout-input", "9")
    page.click("#voice-save-settings-button")

    detail = page.inner_text("#voice-status-detail")
    assert "hello ops" in detail
    assert "Auto-off: 9s" in detail

    page.reload(wait_until="networkidle")
    assert "hello ops" in page.input_value("#wake-phrase-input")
    assert page.input_value("#voice-idle-timeout-input") == "9"


def test_wake_word_executes_voice_command(frontend_url: str) -> None:
  with _playwright_page() as page:
    _install_fake_speech_api(page)
    page.goto(frontend_url, wait_until="networkidle")

    page.click("#voice-toggle-button")
    assert page.inner_text("#voice-status-badge") == "Ecoute active"

    emitted = page.evaluate("window.__emitSpeech('ok jarvis what is the current status')")
    assert emitted is True

    page.wait_for_function("""
      () => {
        const content = document.querySelector('#voice-last-command')?.textContent || '';
        return content.includes('what is the current status');
      }
    """)

    assert "what is the current status" in page.inner_text("#voice-last-command")


def test_voice_auto_off_on_inactivity(frontend_url: str) -> None:
  with _playwright_page() as page:
    _install_fake_speech_api(page)
    page.goto(frontend_url, wait_until="networkidle")

    page.fill("#voice-idle-timeout-input", "5")
    page.click("#voice-save-settings-button")
    page.click("#voice-toggle-button")

    page.wait_for_timeout(5500)
    assert page.inner_text("#voice-status-badge") == "Desactivee"
    assert "Auto-off" in page.inner_text("#voice-status-detail")
