from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from mobile_ingestion.analyzer import AnalyzerMetrics


@dataclass(frozen=True, slots=True)
class SessionDescriptionDto:
  sdp: str
  type: str

  @classmethod
  def from_mapping(cls,
                   payload: Mapping[str, Any]) -> "SessionDescriptionDto":
    sdp = payload.get("sdp")
    kind = payload.get("type")
    if not isinstance(sdp, str) or not sdp.strip():
      raise ValueError("Field 'sdp' must be a non-empty string.")
    if not isinstance(kind, str) or kind not in {"offer", "answer"}:
      raise ValueError("Field 'type' must be either 'offer' or 'answer'.")
    return cls(sdp=sdp, type=kind)

  def to_dict(self) -> dict[str, str]:
    return {
        "sdp": self.sdp,
        "type": self.type,
    }


@dataclass(frozen=True, slots=True)
class SessionStatusDto:
  state: str
  active: bool
  session_id: str | None
  connection_state: str
  has_video_track: bool
  has_audio_track: bool
  started_at: str | None
  error: str | None
  analyzer_metrics: AnalyzerMetrics

  @classmethod
  def from_values(
      cls,
      *,
      state: str,
      active: bool,
      session_id: str | None,
      connection_state: str,
      has_video_track: bool,
      has_audio_track: bool,
      started_at: datetime | None,
      error: str | None,
      analyzer_metrics: AnalyzerMetrics,
  ) -> "SessionStatusDto":
    return cls(
        state=state,
        active=active,
        session_id=session_id,
        connection_state=connection_state,
        has_video_track=has_video_track,
        has_audio_track=has_audio_track,
        started_at=started_at.isoformat() if started_at else None,
        error=error,
        analyzer_metrics=analyzer_metrics,
    )

  def to_dict(self) -> dict[str, Any]:
    return {
        "state": self.state,
        "active": self.active,
        "sessionId": self.session_id,
        "connectionState": self.connection_state,
        "hasVideoTrack": self.has_video_track,
        "hasAudioTrack": self.has_audio_track,
        "startedAt": self.started_at,
        "error": self.error,
        "analyzerMetrics": self.analyzer_metrics.to_dict(),
    }
