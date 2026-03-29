from __future__ import annotations

import base64
import io
import json
import queue
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol

from mobile_ingestion.config import normalize_object_search_vision_model

from mobile_ingestion.voice import (TranscriptEntry, VoiceEvent,
                                    VoiceProcessingPort, VoiceSubscription,
                                    WakeWordEvent, normalize_transcript_text)

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
_FRAME_QUEUE_STOP = object()
_LEADING_ARTICLES = ("a ", "an ", "the ")
_PAIR_OBJECT_LABELS = frozenset({
    "binoculars",
    "glasses",
    "pliers",
    "scissors",
    "shears",
    "sunglasses",
    "tongs",
})
_VISUAL_LABEL_ALIASES: dict[str, tuple[str, ...]] = {
    "bottle": ("water bottle", "drink bottle"),
    "cell phone": ("phone", "smartphone", "mobile phone"),
    "cup": ("mug", "drinking cup"),
    "glasses": ("eyeglasses", "spectacles"),
    "key": ("keys", "keychain"),
    "keys": ("key", "keychain", "house keys"),
    "mobile phone": ("phone", "smartphone", "cell phone"),
    "mug": ("cup", "coffee mug"),
    "needle nose pliers": ("pliers", "needle-nose pliers", "hand tool"),
    "needle-nose pliers": ("pliers", "needle nose pliers", "hand tool"),
    "pen": ("ballpoint pen", "ink pen", "writing instrument"),
    "pencil": ("colored pencil", "wooden pencil", "writing instrument"),
    "phone": ("smartphone", "cell phone", "mobile phone"),
    "pliers": ("needle-nose pliers", "needle nose pliers", "hand tool"),
    "colored pencil": ("pencil", "wooden pencil", "writing instrument"),
    "remote": ("remote control", "tv remote"),
    "remote control": ("remote", "tv remote"),
    "smartphone": ("phone", "cell phone", "mobile phone"),
    "water bottle": ("bottle", "drink bottle"),
    "wooden pencil": ("pencil", "colored pencil", "writing instrument"),
    "writing instrument": ("pencil", "pen"),
}


def _utcnow() -> datetime:
  return datetime.now(timezone.utc)


def _coerce_string(value: object) -> str | None:
  if not isinstance(value, str):
    return None
  stripped = value.strip()
  return stripped or None


def _normalize_phrase_tokens(phrase: str) -> tuple[str, ...]:
  normalized = normalize_transcript_text(phrase)
  if not normalized:
    return tuple()
  return tuple(part for part in normalized.split(" ") if part)


def _normalize_detector_label(label: str) -> str:
  normalized = label.strip().lower()
  if not normalized:
    return ""
  if normalized.startswith(_LEADING_ARTICLES):
    return normalized
  if " " in normalized and normalized.endswith("s"):
    return normalized
  if normalized.endswith("s"):
    return normalized
  vowels = {"a", "e", "i", "o", "u"}
  article = "an" if normalized[:1] in vowels else "a"
  return f"{article} {normalized}"


def _strip_leading_article(label: str) -> str:
  normalized = label.strip().lower()
  for prefix in _LEADING_ARTICLES:
    if normalized.startswith(prefix):
      return normalized[len(prefix):].strip()
  return normalized


def _singularize_label(label: str) -> str | None:
  if not label or label.endswith("ss"):
    return None
  if label.endswith("ies") and len(label) > 3:
    return label[:-3] + "y"
  if label.endswith("s") and len(label) > 3:
    return label[:-1]
  return None


def _pluralize_label(label: str) -> str | None:
  if not label or label.endswith("s"):
    return None
  vowels = {"a", "e", "i", "o", "u"}
  if label.endswith("y") and len(label) > 1 and label[-2] not in vowels:
    return label[:-1] + "ies"
  if label.endswith(("ch", "sh", "x", "z")):
    return label + "es"
  return label + "s"


def _expand_detector_labels(labels: tuple[str, ...]) -> tuple[str, ...]:
  expanded: dict[str, None] = {}

  def add(label: str) -> None:
    original = label.strip().lower()
    if original.startswith(_LEADING_ARTICLES):
      expanded.setdefault(original, None)
    stripped = _strip_leading_article(label)
    if not stripped:
      return
    for candidate in (stripped, _normalize_detector_label(stripped)):
      if candidate:
        expanded.setdefault(candidate, None)

  for label in labels:
    stripped = _strip_leading_article(label)
    if not stripped:
      continue

    add(stripped)

    if "-" in stripped:
      add(stripped.replace("-", " "))
    elif " " in stripped:
      add(stripped.replace(" ", "-"))

    singular = _singularize_label(stripped)
    if singular is not None:
      add(singular)

    plural = _pluralize_label(stripped)
    if plural is not None:
      add(plural)

    if stripped in _PAIR_OBJECT_LABELS:
      add(f"a pair of {stripped}")

    if " " in stripped:
      head_noun = stripped.split(" ")[-1]
      add(head_noun)

    for alias in _VISUAL_LABEL_ALIASES.get(stripped, tuple()):
      add(alias)

  return tuple(expanded.keys())[:12]


def _extract_openai_http_error(exc: urllib.error.HTTPError) -> str:
  body = exc.read().decode("utf-8", errors="ignore")
  if body:
    try:
      payload = json.loads(body)
    except json.JSONDecodeError:
      payload = None
    if isinstance(payload, dict):
      error_payload = payload.get("error")
      if isinstance(error_payload, dict):
        message = _coerce_string(error_payload.get("message"))
        if message is not None:
          return message
  return f"OpenAI a retourne l'erreur HTTP {exc.code}."


def _extract_openai_message_content(
    payload: Mapping[str, object],
    *,
    missing_content_error: str,
) -> str:
  output_text = _coerce_string(payload.get("output_text"))
  if output_text is not None:
    return output_text

  output = payload.get("output")
  if not isinstance(output, list):
    raise RuntimeError(missing_content_error)

  text_parts: list[str] = []
  for item in output:
    if not isinstance(item, dict):
      continue
    content = item.get("content")
    if not isinstance(content, list):
      continue
    for part in content:
      if not isinstance(part, dict):
        continue
      if part.get("type") == "refusal":
        refusal = _coerce_string(part.get("refusal"))
        if refusal is not None:
          raise RuntimeError(refusal)
      part_text = _coerce_string(part.get("text"))
      if part_text is not None:
        text_parts.append(part_text)

  if text_parts:
    return "".join(text_parts)
  raise RuntimeError(missing_content_error)


@dataclass(frozen=True, slots=True)
class ObjectSearchFrame:
  session_id: str
  received_at: datetime
  image_rgb: object
  width: int
  height: int


@dataclass(frozen=True, slots=True)
class ObjectDetectionResult:
  detected: bool
  matched_label: str | None = None
  score: float | None = None


@dataclass(frozen=True, slots=True)
class ObjectDetectorStatus:
  available: bool
  model_ready: bool
  model_state: str
  detail: str | None = None
  selected_model: str | None = None


@dataclass(frozen=True, slots=True)
class ObjectTargetResolution:
  action: str
  display_label_fr: str | None = None
  detector_labels_en: tuple[str, ...] = tuple()


@dataclass(frozen=True, slots=True)
class ObjectSearchStatus:
  available: bool
  active: bool
  session_id: str | None
  state: str
  target_label: str | None
  detected: bool
  last_detected_at: datetime | None
  error: str | None
  model_ready: bool = False
  model_state: str = "pending"
  model_detail: str | None = None
  selected_vision_model: str | None = None


@dataclass(frozen=True, slots=True)
class ObjectSearchEvent:
  event_type: str
  payload: ObjectSearchStatus


@dataclass(frozen=True, slots=True)
class ObjectSearchSubscription:
  subscription_id: int
  events: "queue.Queue[ObjectSearchEvent | None]"


class ObjectDetectorPort(Protocol):

  @property
  def available(self) -> bool:
    raise NotImplementedError

  @property
  def error(self) -> str | None:
    raise NotImplementedError

  def prepare(self) -> None:
    raise NotImplementedError

  def runtime_status(self) -> ObjectDetectorStatus:
    raise NotImplementedError

  def set_model(self, model: str) -> ObjectDetectorStatus:
    raise NotImplementedError

  def detect(self, *, frame: ObjectSearchFrame,
             labels: tuple[str, ...]) -> ObjectDetectionResult:
    raise NotImplementedError


class ObjectTargetResolverPort(Protocol):

  @property
  def available(self) -> bool:
    raise NotImplementedError

  @property
  def error(self) -> str | None:
    raise NotImplementedError

  def resolve(self, transcript_text: str) -> ObjectTargetResolution:
    raise NotImplementedError


class ObjectSearchPort(Protocol):

  def start_session(self, session_id: str) -> None:
    raise NotImplementedError

  def submit_frame(self, frame: ObjectSearchFrame) -> None:
    raise NotImplementedError

  def stop_session(self, session_id: str) -> None:
    raise NotImplementedError

  def snapshot(self) -> ObjectSearchStatus:
    raise NotImplementedError

  def set_selected_vision_model(self, model: str) -> ObjectSearchStatus:
    raise NotImplementedError

  def subscribe(self) -> ObjectSearchSubscription:
    raise NotImplementedError

  def unsubscribe(self, subscription: ObjectSearchSubscription) -> None:
    raise NotImplementedError

  def shutdown(self) -> None:
    raise NotImplementedError


class _LatestFrameQueue:

  def __init__(self) -> None:
    self._condition = threading.Condition()
    self._item: ObjectSearchFrame | object | None = None

  def put(self, frame: ObjectSearchFrame) -> None:
    with self._condition:
      self._item = frame
      self._condition.notify()

  def get(self, timeout: float) -> ObjectSearchFrame | object:
    deadline = time.monotonic() + timeout
    with self._condition:
      while self._item is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
          raise queue.Empty
        self._condition.wait(timeout=remaining)
      item = self._item
      self._item = None
      assert item is not None
      return item

  def close(self) -> None:
    with self._condition:
      self._item = _FRAME_QUEUE_STOP
      self._condition.notify_all()


class OpenAiObjectTargetResolver(ObjectTargetResolverPort):

  def __init__(
      self,
      *,
      api_key: str,
      model: str,
      request_timeout_seconds: float = 10.0,
      endpoint_url: str = OPENAI_CHAT_COMPLETIONS_URL,
  ) -> None:
    self._api_key = api_key.strip()
    self._model = model.strip()
    self._request_timeout_seconds = max(1.0, request_timeout_seconds)
    self._endpoint_url = endpoint_url.strip()
    self._error: str | None = None

    if not self._api_key:
      self._error = (
          "OPENAI_API_KEY est absent. Definis la variable d'environnement "
          "pour activer la resolution de requetes d'objet.")
    elif not self._model:
      self._error = (
          "Le modele OpenAI de resolution d'objet n'est pas configure. "
          "Definis MOBILE_INGEST_OBJECT_SEARCH_RESOLVER_MODEL.")
    elif not self._endpoint_url:
      self._error = "L'endpoint OpenAI de resolution d'objet est invalide."

  @property
  def available(self) -> bool:
    return self._error is None

  @property
  def error(self) -> str | None:
    return self._error

  def resolve(self, transcript_text: str) -> ObjectTargetResolution:
    if not self.available:
      raise RuntimeError(self._error or "Resolver unavailable.")

    cleaned_text = _coerce_string(transcript_text)
    if cleaned_text is None:
      return ObjectTargetResolution(action="unknown")

    payload = {
        "model": self._model,
        "temperature": 0,
        "store": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Tu convertis une commande vocale en JSON pour une "
                    "recherche d'objet. "
                    "Reponds uniquement via le schema. "
                    "Si la personne demande d'arreter, annuler ou stopper la "
                    "recherche, retourne action='cancel'. "
                "Si aucun objet clair n'est demande, retourne "
                    "action='unknown'. "
                    "Si un objet est demande, retourne action='search', un "
                    "displayLabelFr court en francais, et 1 a 3 detectorLabelsEn "
                    "courts en anglais utiles pour une detection open-vocabulary. "
                    "Les detectorLabelsEn doivent etre des groupes nominaux "
                    "naturels, par exemple 'a phone', 'a water bottle', "
                    "'keys', 'a remote control'. "
                    "Quand c'est pertinent, retourne 2 ou 3 formulations: "
                    "l'objet exact, un synonyme proche, puis une categorie "
                    "visuelle un peu plus large mais encore specifique."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Convertis cette phrase en JSON structure pour la recherche "
                    "d'objet. Exemples attendus: "
                    "\"aide-moi a trouver mes cles\" -> "
                    "{\"action\":\"search\",\"displayLabelFr\":\"cles\","
                    "\"detectorLabelsEn\":[\"keys\",\"keychain\",\"house keys\"]}; "
                    "\"ou est ma bouteille d'eau\" -> "
                    "{\"action\":\"search\",\"displayLabelFr\":\"bouteille d'eau\","
                    "\"detectorLabelsEn\":[\"water bottle\",\"bottle\",\"drink bottle\"]}; "
                    "\"trouve mon crayon\" -> "
                    "{\"action\":\"search\",\"displayLabelFr\":\"crayon\","
                    "\"detectorLabelsEn\":[\"pencil\",\"colored pencil\",\"wooden pencil\"]}; "
                    "\"aide-moi a trouver mes pinces\" -> "
                    "{\"action\":\"search\",\"displayLabelFr\":\"pinces\","
                    "\"detectorLabelsEn\":[\"pliers\",\"needle-nose pliers\",\"hand tool\"]}; "
                    "\"annule la recherche\" -> "
                    "{\"action\":\"cancel\",\"displayLabelFr\":null,"
                    "\"detectorLabelsEn\":[]}. "
                    f"Phrase a convertir: {cleaned_text}"
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "object_search_resolution",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["search", "cancel", "unknown"],
                        },
                        "displayLabelFr": {
                            "anyOf": [
                                {
                                    "type": "string",
                                    "minLength": 1,
                                },
                                {
                                    "type": "null",
                                },
                            ],
                        },
                        "detectorLabelsEn": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "minLength": 1,
                            },
                            "maxItems": 3,
                        },
                    },
                    "required": ["action", "displayLabelFr", "detectorLabelsEn"],
                    "additionalProperties": False,
                },
            },
        },
    }
    response_payload = self._post_json(payload)
    return self._parse_resolution(response_payload)

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
      with urllib.request.urlopen(
          request, timeout=self._request_timeout_seconds) as response:
        body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
      raise RuntimeError(self._extract_http_error(exc)) from exc
    except urllib.error.URLError as exc:
      raise RuntimeError("La requete OpenAI de resolution d'objet a echoue."
                         ) from exc

    try:
      parsed = json.loads(body)
    except json.JSONDecodeError as exc:
      raise RuntimeError(
          "OpenAI a retourne une reponse invalide pour la resolution d'objet."
      ) from exc
    if not isinstance(parsed, dict):
      raise RuntimeError("OpenAI a retourne un payload inattendu.")
    return parsed

  def _extract_http_error(self, exc: urllib.error.HTTPError) -> str:
    return _extract_openai_http_error(exc)

  def _parse_resolution(
      self, payload: Mapping[str, object]) -> ObjectTargetResolution:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
      raise RuntimeError("OpenAI n'a retourne aucun choix.")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
      raise RuntimeError("Le choix OpenAI est invalide.")
    message = first_choice.get("message")
    if not isinstance(message, dict):
      raise RuntimeError("Le message OpenAI est invalide.")

    refusal = _coerce_string(message.get("refusal"))
    if refusal is not None:
      raise RuntimeError(refusal)

    raw_content = message.get("content")
    content = None
    if isinstance(raw_content, str):
      content = _coerce_string(raw_content)
    elif isinstance(raw_content, list):
      content_parts = []
      for item in raw_content:
        if not isinstance(item, dict):
          continue
        item_text = _coerce_string(item.get("text"))
        if item_text is not None:
          content_parts.append(item_text)
      if content_parts:
        content = "".join(content_parts)
    if content is None:
      raise RuntimeError(
          "OpenAI n'a retourne aucun contenu pour la resolution d'objet.")

    try:
      response_payload = json.loads(content)
    except json.JSONDecodeError as exc:
      raise RuntimeError(
          "OpenAI a retourne un JSON invalide pour la resolution d'objet."
      ) from exc
    if not isinstance(response_payload, dict):
      raise RuntimeError("Le JSON OpenAI n'est pas un objet.")

    action = _coerce_string(response_payload.get("action"))
    if action not in {"search", "cancel", "unknown"}:
      raise RuntimeError("OpenAI a retourne une action de resolution invalide.")

    raw_display_label = response_payload.get("displayLabelFr")
    if raw_display_label is None:
      display_label = None
    else:
      display_label = _coerce_string(raw_display_label)

    raw_detector_labels = response_payload.get("detectorLabelsEn")
    detector_labels: tuple[str, ...] = tuple()
    if isinstance(raw_detector_labels, list):
      detector_labels = tuple(
          label.strip() for label in raw_detector_labels
          if isinstance(label, str) and label.strip())[:3]

    if action == "search":
      if display_label is None or not detector_labels:
        raise RuntimeError(
            "OpenAI a retourne une cible d'objet incomplete.")
      return ObjectTargetResolution(
          action=action,
          display_label_fr=display_label,
          detector_labels_en=detector_labels,
      )

    return ObjectTargetResolution(action=action)


class OpenAiVisionDetector(ObjectDetectorPort):

  def __init__(
      self,
      *,
      api_key: str,
      model: str,
      request_timeout_seconds: float = 10.0,
      endpoint_url: str = OPENAI_RESPONSES_URL,
      urlopen: Callable[..., object] | None = None,
  ) -> None:
    self._api_key = api_key.strip()
    self._model = normalize_object_search_vision_model(model)
    self._request_timeout_seconds = max(1.0, request_timeout_seconds)
    self._endpoint_url = endpoint_url.strip()
    self._urlopen = urlopen or urllib.request.urlopen
    self._error: str | None = None
    self._image_module: object | None = None
    self._model_state = "pending"
    self._model_detail = "Préparation du modèle vision OpenAI..."

    if not self._api_key:
      self._error = (
          "OPENAI_API_KEY est absent. Definis la variable d'environnement "
          "pour activer la detection visuelle.")
      self._model_state = "unavailable"
      self._model_detail = self._error
      return
    if not self._endpoint_url:
      self._error = "L'endpoint OpenAI de detection visuelle est invalide."
      self._model_state = "unavailable"
      self._model_detail = self._error
      return

    try:
      from PIL import Image
    except ImportError:
      self._error = (
          "Pillow n'est pas installe. Installe Pillow pour activer la "
          "detection visuelle OpenAI.")
      self._model_state = "unavailable"
      self._model_detail = self._error
      return

    self._image_module = Image
    self._model_state = "ready"
    self._model_detail = self._build_ready_detail()

  @property
  def available(self) -> bool:
    return self._error is None

  @property
  def error(self) -> str | None:
    return self._error

  def prepare(self) -> None:
    if not self.available:
      raise RuntimeError(self._error or "Detector unavailable.")

  def runtime_status(self) -> ObjectDetectorStatus:
    return ObjectDetectorStatus(
        available=self.available,
        model_ready=self.available,
        model_state="ready" if self.available else self._model_state,
        detail=(self._build_ready_detail()
                if self.available else (self._model_detail or self._error)),
        selected_model=self._model,
    )

  def set_model(self, model: str) -> ObjectDetectorStatus:
    self._model = normalize_object_search_vision_model(model)
    self._model_state = "ready" if self.available else "unavailable"
    self._model_detail = (self._build_ready_detail()
                          if self.available else (self._error
                                                  or self._model_detail))
    return self.runtime_status()

  def detect(self, *, frame: ObjectSearchFrame,
             labels: tuple[str, ...]) -> ObjectDetectionResult:
    if not self.available:
      raise RuntimeError(self._error or "Detector unavailable.")
    if not labels:
      return ObjectDetectionResult(detected=False)

    candidate_labels = _expand_detector_labels(labels)
    if not candidate_labels:
      return ObjectDetectionResult(detected=False)

    jpeg_bytes = self._encode_frame_as_jpeg(frame.image_rgb)
    payload = self._build_request_payload(
        jpeg_bytes=jpeg_bytes,
        candidate_labels=candidate_labels,
    )
    response_payload = self._post_json(payload)
    return self._parse_detection(response_payload, candidate_labels)

  def _build_ready_detail(self) -> str:
    return f"Modèle vision OpenAI actif : {self._model}."

  def _encode_frame_as_jpeg(self, image_rgb: object) -> bytes:
    assert self._image_module is not None
    image = self._image_module.fromarray(image_rgb)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85, optimize=True)
    return buffer.getvalue()

  def _build_request_payload(
      self,
      *,
      jpeg_bytes: bytes,
      candidate_labels: tuple[str, ...],
  ) -> dict[str, object]:
    image_url = (
        "data:image/jpeg;base64,"
        f"{base64.b64encode(jpeg_bytes).decode('ascii')}")
    labels_text = ", ".join(candidate_labels)
    payload: dict[str, object] = {
        "model": self._model,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Analyse l'image et determine si un objet correspondant "
                            "a l'une des etiquettes candidates est clairement visible. "
                            "Retourne detected=true uniquement si l'objet est present "
                            "de facon assez claire pour etre pointe avec confiance. "
                            "Si rien n'est clairement visible, retourne detected=false "
                            "et matchedLabel=null. "
                            "matchedLabel doit etre exactement une des etiquettes "
                            f"candidates suivantes: {labels_text}."
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
                "name": "object_search_detection",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "detected": {
                            "type": "boolean",
                        },
                        "matchedLabel": {
                            "anyOf": [
                                {
                                    "type": "string",
                                    "enum": list(candidate_labels),
                                },
                                {
                                    "type": "null",
                                },
                            ],
                        },
                    },
                    "required": ["detected", "matchedLabel"],
                    "additionalProperties": False,
                },
            },
        },
    }
    if self._model.startswith("gpt-5.4"):
      payload["reasoning"] = {"effort": "low"}
    return payload

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
      raise RuntimeError("La requete OpenAI de detection visuelle a echoue."
                         ) from exc

    try:
      parsed = json.loads(body)
    except json.JSONDecodeError as exc:
      raise RuntimeError(
          "OpenAI a retourne une reponse invalide pour la detection visuelle."
      ) from exc
    if not isinstance(parsed, dict):
      raise RuntimeError("OpenAI a retourne un payload inattendu.")
    return parsed

  def _parse_detection(
      self,
      payload: Mapping[str, object],
      candidate_labels: tuple[str, ...],
  ) -> ObjectDetectionResult:
    content = _extract_openai_message_content(
        payload,
        missing_content_error=(
            "OpenAI n'a retourne aucun contenu pour la detection visuelle."),
    )

    try:
      response_payload = json.loads(content)
    except json.JSONDecodeError as exc:
      raise RuntimeError(
          "OpenAI a retourne un JSON invalide pour la detection visuelle."
      ) from exc
    if not isinstance(response_payload, dict):
      raise RuntimeError("Le JSON OpenAI n'est pas un objet.")

    detected = response_payload.get("detected")
    if not isinstance(detected, bool):
      raise RuntimeError(
          "OpenAI a retourne un resultat de detection visuelle invalide.")

    raw_matched_label = response_payload.get("matchedLabel")
    matched_label = (_coerce_string(raw_matched_label)
                     if raw_matched_label is not None else None)
    if detected:
      if matched_label not in candidate_labels:
        raise RuntimeError(
            "OpenAI a retourne une etiquette invalide pour la detection visuelle."
        )
      return ObjectDetectionResult(
          detected=True,
          matched_label=matched_label,
      )
    return ObjectDetectionResult(detected=False)


class SwitchableObjectDetector(ObjectDetectorPort):

  def __init__(
      self,
      *,
      model: str,
      detector_factory: Callable[[str], ObjectDetectorPort],
  ) -> None:
    self._lock = threading.Lock()
    self._detector_factory = detector_factory
    normalized_model = normalize_object_search_vision_model(model)
    self._detector = detector_factory(normalized_model)

  @property
  def available(self) -> bool:
    return self._current_detector().available

  @property
  def error(self) -> str | None:
    return self._current_detector().error

  def prepare(self) -> None:
    self._current_detector().prepare()

  def runtime_status(self) -> ObjectDetectorStatus:
    return self._current_detector().runtime_status()

  def set_model(self, model: str) -> ObjectDetectorStatus:
    normalized_model = normalize_object_search_vision_model(model)
    with self._lock:
      current_status = self._detector.runtime_status()
      if current_status.selected_model == normalized_model:
        return current_status
      self._detector = self._detector_factory(normalized_model)
      return self._detector.runtime_status()

  def detect(self, *, frame: ObjectSearchFrame,
             labels: tuple[str, ...]) -> ObjectDetectionResult:
    return self._current_detector().detect(frame=frame, labels=labels)

  def _current_detector(self) -> ObjectDetectorPort:
    with self._lock:
      return self._detector


class ObjectSearchCoordinator(ObjectSearchPort):

  def __init__(
      self,
      *,
      voice_processor: VoiceProcessingPort,
      object_detector: ObjectDetectorPort,
      target_resolver: ObjectTargetResolverPort,
      wake_phrases: tuple[str, ...],
      detection_interval_seconds: float,
      command_timeout_seconds: float,
  ) -> None:
    self._voice_processor = voice_processor
    self._object_detector = object_detector
    self._target_resolver = target_resolver
    self._wake_phrase_tokens = tuple(
        phrase_tokens
        for phrase_tokens in (
            _normalize_phrase_tokens(phrase) for phrase in wake_phrases)
        if phrase_tokens)
    self._detection_interval_seconds = max(0.1, detection_interval_seconds)
    self._command_timeout = timedelta(seconds=max(1.0, command_timeout_seconds))
    self._lock = threading.Lock()
    self._next_subscription_id = 1
    self._subscriptions: dict[int, "queue.Queue[ObjectSearchEvent | None]"] = {}
    self._active_session_id: str | None = None
    self._frame_queue: _LatestFrameQueue | None = None
    self._voice_subscription: VoiceSubscription | None = None
    self._session_stop_event: threading.Event | None = None
    self._voice_thread: threading.Thread | None = None
    self._detection_thread: threading.Thread | None = None
    self._prepare_thread: threading.Thread | None = None
    self._awaiting_request_until: datetime | None = None
    self._target_detector_labels: tuple[str, ...] = tuple()
    self._status = ObjectSearchStatus(
        available=self._is_feature_available(),
        active=False,
        session_id=None,
        state="idle" if self._is_feature_available() else "unavailable",
        target_label=None,
        detected=False,
        last_detected_at=None,
        error=self._combined_unavailable_error(),
    )

  def start_session(self, session_id: str) -> None:
    previous_session_id = None
    with self._lock:
      previous_session_id = self._active_session_id
    if previous_session_id is not None:
      self.stop_session(previous_session_id)

    feature_available = self._is_feature_available()
    if not feature_available:
      with self._lock:
        self._reset_session_state_locked(session_id=session_id)
        self._active_session_id = session_id
        self._status = ObjectSearchStatus(
            available=False,
            active=False,
            session_id=session_id,
            state="unavailable",
            target_label=None,
            detected=False,
            last_detected_at=None,
            error=self._combined_unavailable_error(),
        )
      self._broadcast_status()
      return

    frame_queue = _LatestFrameQueue()
    stop_event = threading.Event()
    voice_subscription = self._voice_processor.subscribe()
    voice_thread = threading.Thread(
        target=self._run_voice_worker,
        args=(session_id, voice_subscription, stop_event),
        name=f"object-search-voice-{session_id[:8]}",
        daemon=True,
    )
    detection_thread = threading.Thread(
        target=self._run_detection_worker,
        args=(session_id, frame_queue, stop_event),
        name=f"object-search-detect-{session_id[:8]}",
        daemon=True,
    )

    with self._lock:
      self._reset_session_state_locked(session_id=session_id)
      self._active_session_id = session_id
      self._frame_queue = frame_queue
      self._voice_subscription = voice_subscription
      self._session_stop_event = stop_event
      self._voice_thread = voice_thread
      self._detection_thread = detection_thread
      self._status = ObjectSearchStatus(
          available=True,
          active=True,
          session_id=session_id,
          state="idle",
          target_label=None,
          detected=False,
          last_detected_at=None,
          error=None,
      )

    voice_thread.start()
    detection_thread.start()
    self._ensure_detector_prepare_started(session_id)
    self._broadcast_status()

  def submit_frame(self, frame: ObjectSearchFrame) -> None:
    frame_queue = None
    with self._lock:
      if frame.session_id != self._active_session_id:
        return
      frame_queue = self._frame_queue
    if frame_queue is not None:
      frame_queue.put(frame)

  def stop_session(self, session_id: str) -> None:
    voice_subscription = None
    stop_event = None
    frame_queue = None
    voice_thread = None
    detection_thread = None
    with self._lock:
      if session_id != self._active_session_id:
        return
      voice_subscription = self._voice_subscription
      stop_event = self._session_stop_event
      frame_queue = self._frame_queue
      voice_thread = self._voice_thread
      detection_thread = self._detection_thread
      self._active_session_id = None
      self._voice_subscription = None
      self._session_stop_event = None
      self._frame_queue = None
      self._voice_thread = None
      self._detection_thread = None
      self._prepare_thread = None
      self._reset_session_state_locked(session_id=None)
      self._status = ObjectSearchStatus(
          available=self._is_feature_available(),
          active=False,
          session_id=None,
          state="idle" if self._is_feature_available() else "unavailable",
          target_label=None,
          detected=False,
          last_detected_at=None,
          error=self._combined_unavailable_error(),
      )

    if stop_event is not None:
      stop_event.set()
    if frame_queue is not None:
      frame_queue.close()
    if voice_thread is not None:
      voice_thread.join(timeout=2.0)
    if detection_thread is not None:
      detection_thread.join(timeout=2.0)
    if voice_subscription is not None:
      self._voice_processor.unsubscribe(voice_subscription)

    self._broadcast_status()

  def snapshot(self) -> ObjectSearchStatus:
    with self._lock:
      status = self._status
    return self._status_with_detector_runtime(status)

  def set_selected_vision_model(self, model: str) -> ObjectSearchStatus:
    runtime_status = self._object_detector.set_model(model)
    active_session_id = None
    with self._lock:
      active_session_id = self._active_session_id
      feature_available = self._is_feature_available()
      combined_error = self._combined_unavailable_error()
      if self._status.active and self._status.target_label:
        self._replace_status_locked(
            available=feature_available,
            active=True,
            session_id=self._status.session_id,
            state="searching",
            target_label=self._status.target_label,
            detected=False,
            last_detected_at=None,
            error=None if feature_available else combined_error,
        )
      elif self._status.active and self._status.state == "error":
        self._replace_status_locked(
            available=feature_available,
            active=True,
            session_id=self._status.session_id,
            state="idle" if feature_available else "unavailable",
            target_label=None,
            detected=False,
            last_detected_at=None,
            error=None if feature_available else combined_error,
        )
      else:
        self._replace_status_locked(
            available=feature_available,
            error=(self._status.error if feature_available else combined_error),
        )
    if active_session_id is not None and not runtime_status.model_ready:
      self._ensure_detector_prepare_started(active_session_id)
    self._broadcast_status()
    return self.snapshot()

  def subscribe(self) -> ObjectSearchSubscription:
    event_queue: "queue.Queue[ObjectSearchEvent | None]" = queue.Queue()
    with self._lock:
      subscription_id = self._next_subscription_id
      self._next_subscription_id += 1
      self._subscriptions[subscription_id] = event_queue
      snapshot = self._status_with_detector_runtime(self._status)
    event_queue.put(ObjectSearchEvent("status", snapshot))
    return ObjectSearchSubscription(subscription_id=subscription_id,
                                    events=event_queue)

  def unsubscribe(self, subscription: ObjectSearchSubscription) -> None:
    with self._lock:
      self._subscriptions.pop(subscription.subscription_id, None)

  def shutdown(self) -> None:
    session_id = None
    with self._lock:
      session_id = self._active_session_id

    if session_id is not None:
      self.stop_session(session_id)

    with self._lock:
      subscriptions = tuple(self._subscriptions.values())
      self._subscriptions.clear()

    for subscription_queue in subscriptions:
      subscription_queue.put(None)

  def _ensure_detector_prepare_started(self, session_id: str) -> None:
    runtime_status = self._object_detector.runtime_status()
    if runtime_status.model_ready or runtime_status.model_state == "loading":
      return
    with self._lock:
      existing_thread = self._prepare_thread
      if existing_thread is not None and existing_thread.is_alive():
        return
      prepare_thread = threading.Thread(
          target=self._run_prepare_worker,
          args=(session_id,),
          name=f"object-search-prepare-{session_id[:8]}",
          daemon=True,
      )
      self._prepare_thread = prepare_thread
    prepare_thread.start()

  def _run_prepare_worker(self, session_id: str) -> None:
    self._broadcast_status()
    try:
      self._object_detector.prepare()
    except Exception as exc:
      self._record_runtime_error(session_id, str(exc))
    finally:
      with self._lock:
        if self._prepare_thread is threading.current_thread():
          self._prepare_thread = None
      self._broadcast_status()

  def _run_voice_worker(
      self,
      session_id: str,
      voice_subscription: VoiceSubscription,
      stop_event: threading.Event,
  ) -> None:
    while not stop_event.is_set():
      try:
        event = voice_subscription.events.get(timeout=0.25)
      except queue.Empty:
        self._expire_request_window(session_id)
        continue

      if event is None:
        break

      self._expire_request_window(session_id)
      if event.payload is None:
        continue
      self._handle_voice_event(session_id, event)

  def _run_detection_worker(
      self,
      session_id: str,
      frame_queue: _LatestFrameQueue,
      stop_event: threading.Event,
  ) -> None:
    last_detection_started_at = 0.0

    while not stop_event.is_set():
      try:
        item = frame_queue.get(timeout=0.25)
      except queue.Empty:
        continue

      if item is _FRAME_QUEUE_STOP:
        break
      assert isinstance(item, ObjectSearchFrame)

      target_labels = self._current_target_labels(session_id)
      if not target_labels:
        continue

      now = time.monotonic()
      if now - last_detection_started_at < self._detection_interval_seconds:
        continue
      last_detection_started_at = now

      try:
        detection = self._object_detector.detect(
            frame=item,
            labels=target_labels,
        )
      except Exception as exc:
        self._record_runtime_error(session_id, str(exc))
        continue

      self._apply_detection_result(session_id, detection, item.received_at)

  def _handle_voice_event(self, session_id: str, event: VoiceEvent) -> None:
    payload = event.payload
    if isinstance(payload, WakeWordEvent):
      self._handle_wake_word(session_id, payload)
      return
    if isinstance(payload, TranscriptEntry) and payload.is_final:
      self._handle_final_transcript(session_id, payload)

  def _handle_wake_word(self, session_id: str, event: WakeWordEvent) -> None:
    if event.session_id != session_id:
      return
    changed = False
    with self._lock:
      if session_id != self._active_session_id:
        return
      currently_detected = self._status.detected
      changed = self._replace_status_locked(
          available=True,
          active=True,
          session_id=session_id,
          state="awaiting_request",
          target_label=self._status.target_label,
          detected=currently_detected,
          last_detected_at=self._status.last_detected_at,
          error=None,
      )
      self._awaiting_request_until = event.received_at + self._command_timeout
    if changed:
      self._broadcast_status()

  def _handle_final_transcript(self, session_id: str,
                               entry: TranscriptEntry) -> None:
    if entry.session_id != session_id:
      return

    normalized_text = normalize_transcript_text(entry.text)
    if not normalized_text:
      return

    direct_command = self._extract_direct_command(normalized_text)
    if direct_command is not None:
      self._set_awaiting_request_until(entry.received_at + self._command_timeout)
      self._resolve_transcript_command(session_id, direct_command)
      return

    if self._should_consume_as_follow_up(session_id, entry.received_at):
      self._resolve_transcript_command(session_id, normalized_text)

  def _resolve_transcript_command(self, session_id: str,
                                  transcript_text: str) -> None:
    changed = False
    with self._lock:
      if session_id != self._active_session_id:
        return
      currently_detected = self._status.detected
      changed = self._replace_status_locked(
          available=True,
          active=True,
          session_id=session_id,
          state="resolving_target",
          target_label=self._status.target_label,
          detected=currently_detected,
          last_detected_at=self._status.last_detected_at,
          error=None,
      )
    if changed:
      self._broadcast_status()

    try:
      resolution = self._target_resolver.resolve(transcript_text)
    except Exception as exc:
      self._record_runtime_error(session_id, str(exc))
      return

    self._apply_resolution(session_id, resolution)

  def _apply_resolution(self, session_id: str,
                        resolution: ObjectTargetResolution) -> None:
    changed = False
    with self._lock:
      if session_id != self._active_session_id:
        return

      if resolution.action == "cancel":
        self._awaiting_request_until = None
        self._target_detector_labels = tuple()
        changed = self._replace_status_locked(
            available=True,
            active=True,
            session_id=session_id,
            state="idle",
            target_label=None,
            detected=False,
            last_detected_at=None,
            error=None,
        )
      elif resolution.action == "search":
        self._awaiting_request_until = None
        self._target_detector_labels = resolution.detector_labels_en
        changed = self._replace_status_locked(
            available=True,
            active=True,
            session_id=session_id,
            state="searching",
            target_label=resolution.display_label_fr,
            detected=False,
            last_detected_at=None,
            error=None,
        )
      else:
        deadline = self._awaiting_request_until
        next_state = "awaiting_request"
        if deadline is None or deadline <= _utcnow():
          self._awaiting_request_until = None
          next_state = "idle"
        currently_detected = self._status.detected
        changed = self._replace_status_locked(
            available=True,
            active=True,
            session_id=session_id,
            state=next_state,
            target_label=self._status.target_label,
            detected=currently_detected,
            last_detected_at=self._status.last_detected_at,
            error=None,
        )
    if changed:
      self._broadcast_status()

  def _apply_detection_result(self, session_id: str,
                              detection: ObjectDetectionResult,
                              detected_at: datetime) -> None:
    changed = False
    with self._lock:
      if session_id != self._active_session_id:
        return
      if not self._target_detector_labels:
        return
      if detection.detected:
        changed = self._replace_status_locked(
            available=True,
            active=True,
            session_id=session_id,
            state="found",
            target_label=self._status.target_label,
            detected=True,
            last_detected_at=detected_at,
            error=None,
        )
      else:
        changed = self._replace_status_locked(
            available=True,
            active=True,
            session_id=session_id,
            state="searching",
            target_label=self._status.target_label,
            detected=False,
            last_detected_at=self._status.last_detected_at,
            error=None,
        )
    if changed:
      self._broadcast_status()

  def _expire_request_window(self, session_id: str) -> None:
    changed = False
    with self._lock:
      if session_id != self._active_session_id:
        return
      deadline = self._awaiting_request_until
      if deadline is None or deadline > _utcnow():
        return
      self._awaiting_request_until = None
      if self._status.state != "awaiting_request":
        return
      currently_detected = self._status.detected
      changed = self._replace_status_locked(
          available=True,
          active=True,
          session_id=session_id,
          state="idle",
          target_label=self._status.target_label,
          detected=currently_detected,
          last_detected_at=self._status.last_detected_at,
          error=None,
      )
    if changed:
      self._broadcast_status()

  def _record_runtime_error(self, session_id: str, message: str) -> None:
    changed = False
    with self._lock:
      if session_id != self._active_session_id:
        return
      currently_detected = self._status.detected
      changed = self._replace_status_locked(
          available=True,
          active=True,
          session_id=session_id,
          state="error",
          target_label=self._status.target_label,
          detected=currently_detected,
          last_detected_at=self._status.last_detected_at,
          error=message,
      )
    if changed:
      self._broadcast_status()

  def _replace_status_locked(self, **changes: object) -> bool:
    updated_status = replace(self._status, **changes)
    if updated_status == self._status:
      return False
    self._status = updated_status
    return True

  def _reset_session_state_locked(self, *, session_id: str | None) -> None:
    del session_id
    self._awaiting_request_until = None
    self._target_detector_labels = tuple()

  def _set_awaiting_request_until(self, deadline: datetime) -> None:
    with self._lock:
      self._awaiting_request_until = deadline

  def _should_consume_as_follow_up(self, session_id: str,
                                   received_at: datetime) -> bool:
    with self._lock:
      if session_id != self._active_session_id:
        return False
      if self._status.state != "awaiting_request":
        return False
      deadline = self._awaiting_request_until
      return deadline is not None and received_at <= deadline

  def _current_target_labels(self, session_id: str) -> tuple[str, ...]:
    with self._lock:
      if session_id != self._active_session_id:
        return tuple()
      if self._status.detected:
        return tuple()
      return self._target_detector_labels

  def _extract_direct_command(self, normalized_text: str) -> str | None:
    words = tuple(part for part in normalized_text.split(" ") if part)
    if not words:
      return None
    for wake_phrase in self._wake_phrase_tokens:
      if len(words) < len(wake_phrase):
        continue
      for index in range(0, len(words) - len(wake_phrase) + 1):
        if words[index:index + len(wake_phrase)] != wake_phrase:
          continue
        trailing_words = words[index + len(wake_phrase):]
        if trailing_words:
          return " ".join(trailing_words)
        return None
    return None

  def _broadcast_status(self) -> None:
    self._broadcast("status", self.snapshot())

  def _broadcast(self, event_type: str, payload: ObjectSearchStatus) -> None:
    with self._lock:
      subscribers = tuple(self._subscriptions.values())
    for subscriber in subscribers:
      subscriber.put(ObjectSearchEvent(event_type, payload))

  def _status_with_detector_runtime(
      self, status: ObjectSearchStatus) -> ObjectSearchStatus:
    runtime_status = self._object_detector.runtime_status()
    return replace(
        status,
        model_ready=runtime_status.model_ready,
        model_state=runtime_status.model_state,
        model_detail=runtime_status.detail,
        selected_vision_model=runtime_status.selected_model,
    )

  def _is_feature_available(self) -> bool:
    return (self._object_detector.available
            and self._target_resolver.available)

  def _combined_unavailable_error(self) -> str | None:
    messages = [
        message for message in (
            self._object_detector.error,
            self._target_resolver.error,
        ) if message
    ]
    if not messages:
      return None
    return " ".join(messages)
