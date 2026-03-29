from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import datetime
from threading import Lock


@dataclass(frozen=True, slots=True)
class SessionMetadata:
  session_id: str
  started_at: datetime


@dataclass(frozen=True, slots=True)
class VideoFrameEnvelope:
  session_id: str
  received_at: datetime
  width: int
  height: int
  pts: int | None


@dataclass(frozen=True, slots=True)
class AudioFrameEnvelope:
  session_id: str
  received_at: datetime
  sample_rate: int
  samples: int
  pts: int | None


@dataclass(frozen=True, slots=True)
class AnalyzerMetrics:
  sessions_started: int = 0
  sessions_stopped: int = 0
  video_frames: int = 0
  audio_frames: int = 0

  def to_dict(self) -> dict[str, int]:
    return {
        "sessionsStarted": self.sessions_started,
        "sessionsStopped": self.sessions_stopped,
        "videoFrames": self.video_frames,
        "audioFrames": self.audio_frames,
    }


class AnalyzerPort(ABC):

  @abstractmethod
  def on_session_started(self, metadata: SessionMetadata) -> None:
    raise NotImplementedError

  @abstractmethod
  def on_video_frame(self, frame: VideoFrameEnvelope) -> None:
    raise NotImplementedError

  @abstractmethod
  def on_audio_frame(self, frame: AudioFrameEnvelope) -> None:
    raise NotImplementedError

  @abstractmethod
  def on_session_stopped(self, metadata: SessionMetadata) -> None:
    raise NotImplementedError

  @abstractmethod
  def snapshot(self) -> AnalyzerMetrics:
    raise NotImplementedError


class NoOpAnalyzer(AnalyzerPort):

  def __init__(self) -> None:
    self._metrics = AnalyzerMetrics()
    self._lock = Lock()

  def on_session_started(self, metadata: SessionMetadata) -> None:
    del metadata
    with self._lock:
      self._metrics = replace(self._metrics,
                              sessions_started=self._metrics.sessions_started + 1)

  def on_video_frame(self, frame: VideoFrameEnvelope) -> None:
    del frame
    with self._lock:
      self._metrics = replace(self._metrics,
                              video_frames=self._metrics.video_frames + 1)

  def on_audio_frame(self, frame: AudioFrameEnvelope) -> None:
    del frame
    with self._lock:
      self._metrics = replace(self._metrics,
                              audio_frames=self._metrics.audio_frames + 1)

  def on_session_stopped(self, metadata: SessionMetadata) -> None:
    del metadata
    with self._lock:
      self._metrics = replace(self._metrics,
                              sessions_stopped=self._metrics.sessions_stopped + 1)

  def snapshot(self) -> AnalyzerMetrics:
    with self._lock:
      return self._metrics
