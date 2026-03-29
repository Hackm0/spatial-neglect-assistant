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
  jpeg_base64: str | None = None


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


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
  index: int
  text: str
  final: bool
  source: str
  created_at: str

  def to_dict(self) -> dict[str, object]:
    return {
        "index": self.index,
        "text": self.text,
        "final": self.final,
        "source": self.source,
        "createdAt": self.created_at,
    }


@dataclass(frozen=True, slots=True)
class TranscriptSnapshot:
  entries: tuple[TranscriptEntry, ...]

  def to_dict(self) -> dict[str, object]:
    return {
        "entries": [entry.to_dict() for entry in self.entries],
        "count": len(self.entries),
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

  @abstractmethod
  def on_transcript(self, text: str, *, final: bool, source: str) -> None:
    raise NotImplementedError

  @abstractmethod
  def transcript_snapshot(self) -> TranscriptSnapshot:
    raise NotImplementedError

  @abstractmethod
  def clear_transcript(self) -> None:
    raise NotImplementedError


class NoOpAnalyzer(AnalyzerPort):

  def __init__(self) -> None:
    self._metrics = AnalyzerMetrics()
    self._transcript_entries: tuple[TranscriptEntry, ...] = ()
    self._latest_video_frame: VideoFrameEnvelope | None = None
    self._lock = Lock()

  def on_session_started(self, metadata: SessionMetadata) -> None:
    del metadata
    with self._lock:
      self._metrics = replace(self._metrics,
                              sessions_started=self._metrics.sessions_started + 1)

  def on_video_frame(self, frame: VideoFrameEnvelope) -> None:
    with self._lock:
      self._metrics = replace(self._metrics,
                              video_frames=self._metrics.video_frames + 1)
      self._latest_video_frame = frame

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

  def on_transcript(self, text: str, *, final: bool, source: str) -> None:
    cleaned_text = text.strip()
    if not cleaned_text:
      return
    cleaned_source = source.strip() or "server"
    with self._lock:
      next_index = len(self._transcript_entries)
      entry = TranscriptEntry(
          index=next_index,
          text=cleaned_text,
          final=final,
          source=cleaned_source,
          created_at=datetime.utcnow().isoformat() + "Z",
      )
      self._transcript_entries = self._transcript_entries + (entry,)

  def transcript_snapshot(self) -> TranscriptSnapshot:
    with self._lock:
      return TranscriptSnapshot(entries=self._transcript_entries)

  def clear_transcript(self) -> None:
    with self._lock:
      self._transcript_entries = ()

  def get_latest_video_frame(self) -> VideoFrameEnvelope | None:
    with self._lock:
      return self._latest_video_frame
