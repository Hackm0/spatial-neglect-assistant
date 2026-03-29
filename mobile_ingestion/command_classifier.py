from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


_COMMAND_KEYS = (
    "command",
    "cmd",
    "action",
    "type",
    "event",
    "intent",
    "operation",
)

_PAYLOAD_KEYS = (
    "payload",
    "data",
    "params",
    "args",
    "arguments",
    "body",
)

_COMMAND_ALIASES: dict[str, str] = {
    "offer": "offer",
    "webrtc_offer": "offer",
    "connect": "offer",
    "start": "offer",
    "negotiate": "offer",
    "open_session": "offer",
    "status": "status",
    "get_status": "status",
    "state": "status",
    "health": "status",
    "close": "close_session",
    "disconnect": "close_session",
    "stop": "close_session",
    "terminate": "close_session",
    "end": "close_session",
    "close_session": "close_session",
    "delete_session": "close_session",
    "ping": "ping",
    "heartbeat": "ping",
    "transcript": "transcript_get",
    "get_transcript": "transcript_get",
    "transcript_status": "transcript_get",
    "transcribe": "transcript_add",
    "add_transcript": "transcript_add",
    "transcript_add": "transcript_add",
    "caption": "transcript_add",
    "clear_transcript": "transcript_clear",
    "reset_transcript": "transcript_clear",
    "transcript_clear": "transcript_clear",
    "spatial_detection": "spatial_detection",
    "spatial_detect": "spatial_detection",
    "detect_object": "spatial_detection",
    "find_object": "spatial_detection",
}


@dataclass(frozen=True, slots=True)
class ClassifiedCommand:
  command: str
  raw_command: str | None
  arguments: dict[str, Any]


class CommandClassifier:

  def classify(self, payload: Mapping[str, Any]) -> ClassifiedCommand:
    raw_command = self._extract_raw_command(payload)
    canonical = self._canonicalize(raw_command)

    if canonical == "unknown" and self._looks_like_offer_payload(payload):
      canonical = "offer"

    arguments = self._extract_arguments(payload, raw_command)
    return ClassifiedCommand(
        command=canonical,
        raw_command=raw_command,
        arguments=arguments,
    )

  def _extract_raw_command(self, payload: Mapping[str, Any]) -> str | None:
    for key in _COMMAND_KEYS:
      value = payload.get(key)
      if isinstance(value, str) and value.strip():
        return value

    for key, value in payload.items():
      normalized_key = self._normalize_token(key)
      if normalized_key in _COMMAND_ALIASES:
        return key
      if isinstance(value, Mapping):
        nested_command = self._extract_raw_command(value)
        if nested_command:
          return nested_command

    return None

  def _extract_arguments(self, payload: Mapping[str, Any],
                         raw_command: str | None) -> dict[str, Any]:
    arguments: dict[str, Any] = {}

    for key in _PAYLOAD_KEYS:
      value = payload.get(key)
      if isinstance(value, Mapping):
        arguments.update(value)

    if raw_command:
      command_key = next(
          (key for key in payload if self._normalize_token(key) == self._normalize_token(raw_command)),
          None,
      )
      if command_key:
        command_value = payload.get(command_key)
        if isinstance(command_value, Mapping):
          arguments.update(command_value)

    for key, value in payload.items():
      normalized = self._normalize_token(key)
      if normalized in _COMMAND_KEYS or normalized in _PAYLOAD_KEYS:
        continue
      arguments.setdefault(key, value)

    return arguments

  def _canonicalize(self, raw_command: str | None) -> str:
    if raw_command is None:
      return "unknown"
    normalized = self._normalize_token(raw_command)
    direct_match = _COMMAND_ALIASES.get(normalized)
    if direct_match:
      return direct_match

    inferred = self._infer_from_phrase(normalized)
    if inferred:
      return inferred

    return "unknown"

  def _normalize_token(self, raw: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", raw.strip().lower())
    return normalized.strip("_")

  def _looks_like_offer_payload(self, payload: Mapping[str, Any]) -> bool:
    sdp = payload.get("sdp")
    kind = payload.get("type")
    if isinstance(sdp, str) and sdp.strip() and isinstance(kind, str) and kind == "offer":
      return True

    for key in _PAYLOAD_KEYS:
      nested = payload.get(key)
      if isinstance(nested, Mapping):
        nested_sdp = nested.get("sdp")
        nested_type = nested.get("type")
        if isinstance(nested_sdp, str) and nested_sdp.strip() and isinstance(nested_type, str) and nested_type == "offer":
          return True

    return False

  def _infer_from_phrase(self, normalized: str) -> str | None:
    tokens = {token for token in normalized.split("_") if token}

    if not tokens:
      return None

    transcript_tokens = {
        "transcript",
        "caption",
        "captions",
        "subtitle",
        "subtitles",
        "text",
        "texte",
        "transcribe",
    }
    if tokens.intersection(transcript_tokens):
      if tokens.intersection({"clear", "reset", "delete", "erase", "wipe", "clean"}):
        return "transcript_clear"
      if tokens.intersection({"get", "show", "read", "status", "list", "display"}):
        return "transcript_get"
      if tokens.intersection({"add", "note", "save", "write", "append", "record", "transcribe"}):
        return "transcript_add"
      return "transcript_get"

    if tokens.intersection({"ping", "heartbeat", "alive"}):
      return "ping"

    if tokens.intersection({"status", "state", "health", "ready"}):
      return "status"

    close_tokens = {"close", "disconnect", "stop", "terminate", "end", "shutdown", "quit"}
    if tokens.intersection(close_tokens):
      return "close_session"

    offer_tokens = {"connect", "start", "open", "resume", "reconnect", "negotiate"}
    if tokens.intersection(offer_tokens) and tokens.intersection({"session", "stream", "webrtc", "connection"}):
      return "offer"

    detection_tokens = {
        "find",
        "locate",
        "spot",
        "where",
        "detect",
        "localise",
        "localiser",
        "trouve",
        "trouver",
        "repere",
        "reperer",
    }
    if tokens.intersection(detection_tokens) and not tokens.intersection({"status", "state", "health", "ping"}):
      return "spatial_detection"

    return None
