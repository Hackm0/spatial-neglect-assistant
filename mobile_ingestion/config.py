from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


def _parse_bool(raw_value: str | bool | None, default: bool) -> bool:
  if raw_value is None:
    return default
  if isinstance(raw_value, bool):
    return raw_value
  normalized = raw_value.strip().lower()
  if normalized in {"1", "true", "yes", "on"}:
    return True
  if normalized in {"0", "false", "no", "off"}:
    return False
  return default


def _parse_ice_servers(raw_value: Any) -> tuple[str, ...]:
  if raw_value is None:
    return ("stun:stun.l.google.com:19302",)
  if isinstance(raw_value, str):
    items = [item.strip() for item in raw_value.split(",")]
    parsed = tuple(item for item in items if item)
    return parsed or ("stun:stun.l.google.com:19302",)
  if isinstance(raw_value, (list, tuple)):
    parsed = tuple(str(item).strip() for item in raw_value if str(item).strip())
    return parsed or ("stun:stun.l.google.com:19302",)
  return ("stun:stun.l.google.com:19302",)


def _parse_csv_values(raw_value: Any,
                      default: tuple[str, ...]) -> tuple[str, ...]:
  if raw_value is None:
    return default
  if isinstance(raw_value, str):
    parsed = tuple(item.strip() for item in raw_value.split(",") if item.strip())
    return parsed or default
  if isinstance(raw_value, (list, tuple)):
    parsed = tuple(str(item).strip() for item in raw_value if str(item).strip())
    return parsed or default
  return default


def _parse_optional_string(raw_value: Any, default: str | None) -> str | None:
  if raw_value is None:
    return default
  value = str(raw_value).strip()
  return value or None


def load_dotenv_file(dotenv_path: str | Path = ".env",
                     *, override: bool = False) -> None:
  env_file = Path(dotenv_path)
  if not env_file.exists() or not env_file.is_file():
    return

  for raw_line in env_file.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#"):
      continue
    if line.startswith("export "):
      line = line[7:].strip()
    if "=" not in line:
      continue

    key, value = line.split("=", 1)
    normalized_key = key.strip()
    if not normalized_key:
      continue

    normalized_value = value.strip()
    if (len(normalized_value) >= 2
        and normalized_value[0] == normalized_value[-1]
        and normalized_value[0] in {"'", '"'}):
      normalized_value = normalized_value[1:-1]

    if override or normalized_key not in os.environ:
      os.environ[normalized_key] = normalized_value


@dataclass(frozen=True, slots=True)
class AppConfig:
  host: str = "0.0.0.0"
  port: int = 5000
  debug: bool = False
  testing: bool = False
  secret_key: str = "spatial-neglect-assistant-dev"
  video_max_fps: float = 5.0
  ice_servers: tuple[str, ...] = ("stun:stun.l.google.com:19302",)
  ice_gathering_timeout_seconds: float = 10.0
  session_shutdown_timeout_seconds: float = 5.0
  voice_model: str = "gpt-4o-transcribe"
  voice_language: str | None = "fr"
  voice_prompt: str | None = None
  voice_realtime_url: str = "wss://api.openai.com/v1/realtime?intent=transcription"
  voice_transcript_buffer_size: int = 50
  voice_audio_buffer_seconds: float = 20.0
  voice_wake_phrases: tuple[str, ...] = ("okay jarvis", "ok jarvis")
  voice_wake_cooldown_seconds: float = 3.0

  @classmethod
  def from_mapping(cls,
                   overrides: Mapping[str, Any] | None = None) -> "AppConfig":
    defaults = cls()
    values: dict[str, Any] = {
        "host": os.getenv("MOBILE_INGEST_HOST", defaults.host),
        "port": int(os.getenv("MOBILE_INGEST_PORT", defaults.port)),
        "debug": _parse_bool(os.getenv("MOBILE_INGEST_DEBUG"), defaults.debug),
        "testing": _parse_bool(os.getenv("MOBILE_INGEST_TESTING"),
                               defaults.testing),
        "secret_key": os.getenv("MOBILE_INGEST_SECRET_KEY", defaults.secret_key),
        "video_max_fps": float(
            os.getenv("MOBILE_INGEST_VIDEO_MAX_FPS", defaults.video_max_fps)),
        "ice_servers": _parse_ice_servers(os.getenv("MOBILE_INGEST_ICE_SERVERS")),
        "ice_gathering_timeout_seconds": float(
            os.getenv("MOBILE_INGEST_ICE_TIMEOUT_SECONDS",
                      defaults.ice_gathering_timeout_seconds)),
        "session_shutdown_timeout_seconds": float(
            os.getenv("MOBILE_INGEST_SESSION_SHUTDOWN_TIMEOUT_SECONDS",
                      defaults.session_shutdown_timeout_seconds)),
        "voice_model": os.getenv("MOBILE_INGEST_VOICE_MODEL",
                                  defaults.voice_model),
        "voice_language": _parse_optional_string(
            os.getenv("MOBILE_INGEST_VOICE_LANGUAGE"),
            defaults.voice_language,
        ),
        "voice_prompt": _parse_optional_string(
            os.getenv("MOBILE_INGEST_VOICE_PROMPT"),
            defaults.voice_prompt,
        ),
        "voice_realtime_url": os.getenv("MOBILE_INGEST_VOICE_REALTIME_URL",
                                         defaults.voice_realtime_url),
        "voice_transcript_buffer_size": int(
            os.getenv("MOBILE_INGEST_VOICE_TRANSCRIPT_BUFFER_SIZE",
                      defaults.voice_transcript_buffer_size)),
        "voice_audio_buffer_seconds": float(
            os.getenv("MOBILE_INGEST_VOICE_AUDIO_BUFFER_SECONDS",
                      defaults.voice_audio_buffer_seconds)),
        "voice_wake_phrases": _parse_csv_values(
            os.getenv("MOBILE_INGEST_VOICE_WAKE_PHRASES"),
            defaults.voice_wake_phrases,
        ),
        "voice_wake_cooldown_seconds": float(
            os.getenv("MOBILE_INGEST_VOICE_WAKE_COOLDOWN_SECONDS",
                      defaults.voice_wake_cooldown_seconds)),
    }
    if overrides:
      for key, value in overrides.items():
        normalized_key = key.lower()
        if normalized_key == "ice_servers":
          values[normalized_key] = _parse_ice_servers(value)
        elif normalized_key == "voice_wake_phrases":
          values[normalized_key] = _parse_csv_values(
              value,
              values[normalized_key],
          )
        elif normalized_key in {"voice_language", "voice_prompt"}:
          values[normalized_key] = _parse_optional_string(
              value,
              values[normalized_key],
          )
        elif normalized_key in {"debug", "testing"}:
          values[normalized_key] = _parse_bool(value, values[normalized_key])
        elif normalized_key in {"port", "voice_transcript_buffer_size"}:
          values[normalized_key] = int(value)
        elif normalized_key in {
            "video_max_fps",
            "ice_gathering_timeout_seconds",
            "session_shutdown_timeout_seconds",
            "voice_audio_buffer_seconds",
            "voice_wake_cooldown_seconds",
        }:
          values[normalized_key] = float(value)
        elif normalized_key in values:
          values[normalized_key] = value
    return cls(**values)

  def to_flask_mapping(self) -> dict[str, Any]:
    return {
        "SECRET_KEY": self.secret_key,
        "DEBUG": self.debug,
        "TESTING": self.testing,
        "APP_CONFIG": self,
    }
