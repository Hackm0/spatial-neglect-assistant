from __future__ import annotations

from dataclasses import dataclass

from mobile_ingestion.analyzer import AnalyzerPort, NoOpAnalyzer
from mobile_ingestion.config import AppConfig
from mobile_ingestion.runtime import AsyncioRunner
from mobile_ingestion.session_manager import SessionManager


@dataclass(slots=True)
class ServiceContainer:
  settings: AppConfig
  runtime: AsyncioRunner
  analyzer: AnalyzerPort
  session_manager: SessionManager

  def shutdown(self) -> None:
    self.session_manager.shutdown()


def build_services(settings: AppConfig) -> ServiceContainer:
  try:
    from mobile_ingestion.webrtc_session import WebRtcPeerSession
  except ImportError as exc:
    raise RuntimeError(
        "Missing WebRTC dependencies. Install the project requirements before "
        "starting the Flask server.") from exc

  runtime = AsyncioRunner()
  analyzer = NoOpAnalyzer()
  session_manager = SessionManager(
      runtime=runtime,
      analyzer=analyzer,
      settings=settings,
      session_factory=lambda context, callbacks: WebRtcPeerSession(
          context, callbacks),
  )
  return ServiceContainer(
      settings=settings,
      runtime=runtime,
      analyzer=analyzer,
      session_manager=session_manager,
  )
