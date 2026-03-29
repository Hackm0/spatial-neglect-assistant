from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from mobile_ingestion.config import normalize_object_search_vision_model
from mobile_ingestion.object_search import (OPENAI_RESPONSES_URL,
                                            ObjectSearchFrame,
                                            _coerce_string,
                                            _extract_openai_http_error,
                                            _extract_openai_message_content)


@dataclass(frozen=True, slots=True)
class EatingDetectionResult:
  plate_visible: bool
  is_eating: bool
  one_side_food_remaining: bool
  remaining_side: str


class EatingDetectorPort(Protocol):

  @property
  def available(self) -> bool:
    raise NotImplementedError

  @property
  def error(self) -> str | None:
    raise NotImplementedError

  def detect(self, frame: ObjectSearchFrame) -> EatingDetectionResult:
    raise NotImplementedError


class UnavailableEatingDetector(EatingDetectorPort):

  def __init__(self, error: str) -> None:
    self._error = error

  @property
  def available(self) -> bool:
    return False

  @property
  def error(self) -> str | None:
    return self._error

  def detect(self, frame: ObjectSearchFrame) -> EatingDetectionResult:
    del frame
    raise RuntimeError(self._error)


class OpenAiEatingDetector(EatingDetectorPort):

  def __init__(
      self,
      *,
      api_key: str,
      model: str,
      request_timeout_seconds: float = 12.0,
      endpoint_url: str = OPENAI_RESPONSES_URL,
      urlopen: Callable[..., object] | None = None,
  ) -> None:
    self._api_key = api_key.strip()
    self._model = normalize_object_search_vision_model(model)
    self._request_timeout_seconds = max(1.0, request_timeout_seconds)
    self._endpoint_url = endpoint_url.strip()
    self._urlopen = urlopen or urllib.request.urlopen
    self._image_module: object | None = None
    self._error: str | None = None

    if not self._api_key:
      self._error = (
          "OPENAI_API_KEY est absent. La detection d'alimentation est indisponible."
      )
      return
    if not self._endpoint_url:
      self._error = "L'endpoint OpenAI est invalide pour la detection d'alimentation."
      return

    try:
      from PIL import Image
    except ImportError:
      self._error = (
          "Pillow n'est pas installe. Installe Pillow pour la detection d'alimentation."
      )
      return

    self._image_module = Image

  @property
  def available(self) -> bool:
    return self._error is None

  @property
  def error(self) -> str | None:
    return self._error

  def detect(self, frame: ObjectSearchFrame) -> EatingDetectionResult:
    if not self.available:
      raise RuntimeError(self._error or "Eating detector unavailable.")

    jpeg_bytes = self._encode_frame_as_jpeg(frame.image_rgb)
    payload = self._build_request_payload(jpeg_bytes=jpeg_bytes)
    response_payload = self._post_json(payload)
    return self._parse_detection(response_payload)

  def _encode_frame_as_jpeg(self, image_rgb: object) -> bytes:
    assert self._image_module is not None
    image = self._image_module.fromarray(image_rgb)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=80, optimize=True)
    return buffer.getvalue()

  def _build_request_payload(self, *, jpeg_bytes: bytes) -> dict[str, object]:
    image_url = (
        "data:image/jpeg;base64,"
        f"{base64.b64encode(jpeg_bytes).decode('ascii')}")
    return {
        "model": self._model,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Analyse cette image d'un repas. Reponds strictement au schema JSON. "
                            "plateVisible=true si une assiette est clairement visible. "
                            "isEating=true si la personne semble actuellement en train de manger. "
                            "oneSideFoodRemaining=true seulement si la nourriture semble rester "
                            "principalement d'un seul cote de l'assiette (gauche ou droite). "
                            "remainingSide doit etre one of: left, right, none, unknown."
                        ),
                    },
                    {
                        "type": "input_image",
                        "image_url": image_url,
                    },
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "eating_detection",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "plateVisible": {"type": "boolean"},
                        "isEating": {"type": "boolean"},
                        "oneSideFoodRemaining": {"type": "boolean"},
                        "remainingSide": {
                            "type": "string",
                            "enum": ["left", "right", "none", "unknown"],
                        },
                    },
                    "required": [
                        "plateVisible",
                        "isEating",
                        "oneSideFoodRemaining",
                        "remainingSide",
                    ],
                    "additionalProperties": False,
                },
            },
        },
    }

  def _post_json(self, payload: Mapping[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        self._endpoint_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
      with self._urlopen(request, timeout=self._request_timeout_seconds) as response:
        body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
      raise RuntimeError(_extract_openai_http_error(exc)) from exc
    except urllib.error.URLError as exc:
      raise RuntimeError("La requete OpenAI de detection d'alimentation a echoue.") from exc

    try:
      parsed = json.loads(body)
    except json.JSONDecodeError as exc:
      raise RuntimeError(
          "OpenAI a retourne une reponse invalide pour la detection d'alimentation.") from exc
    if not isinstance(parsed, dict):
      raise RuntimeError("OpenAI a retourne un payload inattendu.")
    return parsed

  def _parse_detection(self, payload: Mapping[str, object]) -> EatingDetectionResult:
    content = _extract_openai_message_content(
        payload,
        missing_content_error=(
            "OpenAI n'a retourne aucun contenu pour la detection d'alimentation."),
    )

    try:
      response_payload = json.loads(content)
    except json.JSONDecodeError as exc:
      raise RuntimeError(
          "OpenAI a retourne un JSON invalide pour la detection d'alimentation.") from exc
    if not isinstance(response_payload, dict):
      raise RuntimeError("Le JSON OpenAI n'est pas un objet.")

    plate_visible = response_payload.get("plateVisible")
    is_eating = response_payload.get("isEating")
    one_side_food_remaining = response_payload.get("oneSideFoodRemaining")
    remaining_side = _coerce_string(response_payload.get("remainingSide"))

    if not isinstance(plate_visible, bool):
      raise RuntimeError("OpenAI a retourne plateVisible invalide.")
    if not isinstance(is_eating, bool):
      raise RuntimeError("OpenAI a retourne isEating invalide.")
    if not isinstance(one_side_food_remaining, bool):
      raise RuntimeError("OpenAI a retourne oneSideFoodRemaining invalide.")
    if remaining_side not in {"left", "right", "none", "unknown"}:
      raise RuntimeError("OpenAI a retourne remainingSide invalide.")

    return EatingDetectionResult(
        plate_visible=plate_visible,
        is_eating=is_eating,
        one_side_food_remaining=one_side_food_remaining,
        remaining_side=remaining_side,
    )
