from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


def _load_env_file_fallback() -> None:
  candidates = [
      Path.cwd() / ".env",
      Path(__file__).resolve().parent.parent / ".env",
  ]
  for env_path in candidates:
    if not env_path.exists():
      continue
    try:
      for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
          continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
          os.environ.setdefault(key, value)
      return
    except Exception:
      continue


try:
  from dotenv import load_dotenv
  load_dotenv()
except ImportError:
  _load_env_file_fallback()

_load_env_file_fallback()



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


def _parse_wake_phrases(raw_value: Any) -> tuple[str, ...]:
  if raw_value is None:
    return ("ok jarvis", "okay jarvis")
  if isinstance(raw_value, str):
    items = [item.strip().lower() for item in raw_value.split(",")]
    parsed = tuple(item for item in items if item)
    return parsed or ("ok jarvis", "okay jarvis")
  if isinstance(raw_value, (list, tuple)):
    parsed = tuple(str(item).strip().lower() for item in raw_value if str(item).strip())
    return parsed or ("ok jarvis", "okay jarvis")
  return ("ok jarvis", "okay jarvis")


def _parse_secret(raw_value: Any) -> str | None:
  if raw_value is None:
    return None
  if not isinstance(raw_value, str):
    raw_value = str(raw_value)
  cleaned = raw_value.strip().strip('"').strip("'")
  return cleaned or None


_JARVIS_SYSTEM_PROMPT = """You are an empathetic, patient, and highly trained Occupational Therapy Assistant specializing in Spatial Neglect (Hemispatial Neglect). Your primary goal is to help the user—who has suffered a brain injury and tends to ignore one side of their space (usually the left)—to actively engage with their neglected side.

## Core Directives
1. **Gentle Reorientation:** Frequently remind the user to "scan" or look toward their neglected side. Use phrases like, "Let's turn your head a bit to the left."
2. **Pacing and Tone:** Speak slowly, clearly, and with an encouraging, warm tone.
3. **Grounding and Awareness:** Help the user become aware of their surroundings.
4. **Task Assistance:** If guiding the user through a task, remind them to check the entire area.
5. **Feedback & Encouragement:** Celebrate successes, no matter how small.
6. **Safety First:** Continually observe context clues from the camera/audio to ensure they are safe and seated.

## Interaction Style
- Keep sentences short, simple, and easy to understand.
- Ask one question at a time.
- Avoid overwhelming the user with too much information at once.
- Always be encouraging, judgment-free, and avoid overly complex medical jargon."""

@dataclass(frozen=True, slots=True)
class AppConfig:
  host: str = "0.0.0.0"
  port: int = 5000
  debug: bool = False
  testing: bool = False
  secret_key: str = "spatial-neglect-assistant-dev"
  ice_servers: tuple[str, ...] = ("stun:stun.l.google.com:19302",)
  ice_gathering_timeout_seconds: float = 10.0
  session_shutdown_timeout_seconds: float = 5.0
  voice_wake_phrases: tuple[str, ...] = ("ok jarvis", "okay jarvis")
  voice_idle_timeout_seconds: int = 180
  elevenlabs_api_key: str | None = None
  elevenlabs_agent_id: str | None = None
  openrouter_api_key: str | None = None
  llm_system_prompt: str = _JARVIS_SYSTEM_PROMPT

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
        "ice_servers": _parse_ice_servers(os.getenv("MOBILE_INGEST_ICE_SERVERS")),
        "ice_gathering_timeout_seconds": float(
            os.getenv("MOBILE_INGEST_ICE_TIMEOUT_SECONDS",
                      defaults.ice_gathering_timeout_seconds)),
        "session_shutdown_timeout_seconds": float(
            os.getenv("MOBILE_INGEST_SESSION_SHUTDOWN_TIMEOUT_SECONDS",
                      defaults.session_shutdown_timeout_seconds)),
        "voice_wake_phrases": _parse_wake_phrases(
          os.getenv("MOBILE_INGEST_VOICE_WAKE_PHRASES")),
        "voice_idle_timeout_seconds": int(
          os.getenv("MOBILE_INGEST_VOICE_IDLE_TIMEOUT_SECONDS",
                defaults.voice_idle_timeout_seconds)),
        "elevenlabs_api_key": _parse_secret(
          os.getenv("ELEVENLABS_API_KEY", defaults.elevenlabs_api_key)),
        "elevenlabs_agent_id": _parse_secret(
          os.getenv("ELEVENLABS_AGENT_ID", defaults.elevenlabs_agent_id)),
        "openrouter_api_key": _parse_secret(
          os.getenv("OPENROUTER_API_KEY", defaults.openrouter_api_key)),
        "llm_system_prompt": os.getenv("LLM_SYSTEM_PROMPT", defaults.llm_system_prompt),
    }
    if overrides:
      for key, value in overrides.items():
        normalized_key = key.lower()
        if normalized_key == "ice_servers":
          values[normalized_key] = _parse_ice_servers(value)
        elif normalized_key in {"debug", "testing"}:
          values[normalized_key] = _parse_bool(value, values[normalized_key])
        elif normalized_key in {"port"}:
          values[normalized_key] = int(value)
        elif normalized_key in {
            "ice_gathering_timeout_seconds",
            "session_shutdown_timeout_seconds",
        }:
          values[normalized_key] = float(value)
        elif normalized_key in {"voice_idle_timeout_seconds"}:
          values[normalized_key] = int(value)
        elif normalized_key in {"voice_wake_phrases"}:
          values[normalized_key] = _parse_wake_phrases(value)
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
