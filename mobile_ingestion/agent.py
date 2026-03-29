from __future__ import annotations

import base64
import json
import logging
import os
import re
import requests

logger = logging.getLogger(__name__)
_DEFAULT_ELEVENLABS_VOICE_ID = "CwhRBWXzGAHq8TQ4Fs17"
_VALID_CONFIDENCE = {"high", "medium", "low"}
_VALID_HORIZONTAL = {"left", "center", "right", "unknown"}
_VALID_VERTICAL = {"top", "middle", "bottom", "unknown"}
_VALID_DEPTH = {"near", "mid", "far", "unknown"}


def synthesize_speech(elevenlabs_api_key: str,
            text: str) -> tuple[str | None, str | None]:
    if not elevenlabs_api_key:
        return "Configuration ElevenLabs manquante.", None
    if not text or not text.strip():
        return "Texte de synthese vide.", None
    try:
        voice_id = os.getenv("ELEVENLABS_VOICE_ID", _DEFAULT_ELEVENLABS_VOICE_ID)
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": elevenlabs_api_key
        }
        data = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75
            }
        }
        res_tts = requests.post(url, json=data, headers=headers, timeout=45)
        res_tts.raise_for_status()
        if not res_tts.content:
            return "Erreur ElevenLabs: audio vide.", None
        b64_audio = base64.b64encode(res_tts.content).decode("ascii")
        return None, b64_audio
    except requests.RequestException as exc:
        logger.exception("ElevenLabs request failed.")
        return _format_elevenlabs_error(exc, "Erreur ElevenLabs: synthese vocale impossible."), None
    except Exception:
        logger.exception("Unexpected ElevenLabs stage failure.")
        return "Erreur ElevenLabs inattendue.", None


def locate_object_from_image(openrouter_api_key: str,
               user_text: str,
               object_name: str,
               jpeg_base64: str) -> str | None:
    text_response, _ = detect_spatial_detection_from_image(
        openrouter_api_key=openrouter_api_key,
        user_text=user_text,
        object_name=object_name,
        jpeg_base64=jpeg_base64,
    )
    return text_response


def detect_spatial_detection_from_image(openrouter_api_key: str,
               user_text: str,
               object_name: str,
               jpeg_base64: str) -> tuple[str, dict[str, object] | None]:
    if not openrouter_api_key:
        return "Configuration OpenRouter manquante.", None
    if not jpeg_base64:
        return "Image camera indisponible.", None

    try:
        or_url = "https://openrouter.ai/api/v1/chat/completions"
        or_headers = {
            "Authorization": f"Bearer {openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:5000",
            "X-Title": "Spatial Neglect Assistant"
        }
        system_prompt = (
            "You analyze one smartphone image for accessibility guidance. "
            "Return ONLY valid JSON with keys: "
            "visible (boolean), confidence (high|medium|low), "
            "horizontal (left|center|right|unknown), "
            "vertical (top|middle|bottom|unknown), "
            "depth (near|mid|far|unknown), "
            "guidance (short French action, <=20 words), "
            "summary (French sentence, <=45 words). "
            "If target object is not visible, set visible=false and use unknown for position/depth. "
            "Never fabricate certainty."
        )
        user_payload = [
            {
                "type": "text",
                "text": (
                    f"User asked: {user_text}. "
                    f"Target object: {object_name}."
                ),
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{jpeg_base64}",
                },
            },
        ]
        or_data = {
            "model": "google/gemini-2.5-flash",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_payload},
            ],
            "temperature": 0.2,
        }
        response = requests.post(or_url,
                                 json=or_data,
                                 headers=or_headers,
                                 timeout=50)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if isinstance(content, str) and content.strip():
            parsed = _parse_spatial_detection_payload(content)
            if parsed is not None:
                summary = str(parsed.get("summary") or "").strip()
                if summary:
                    return summary, parsed
            fallback_text = content.strip()
            return fallback_text, None
        return "Je ne vois pas clairement l'objet dans cette image.", None
    except requests.RequestException:
        logger.exception("OpenRouter vision request failed.")
        return "Erreur OpenRouter vision: impossible d'analyser la camera.", None
    except Exception:
        logger.exception("Unexpected OpenRouter vision failure.")
        return "Erreur vision inattendue.", None


def _parse_spatial_detection_payload(content: str) -> dict[str, object] | None:
    candidate = content.strip()
    if not candidate:
        return None

    # Models sometimes prepend commentary around the JSON blob.
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?", "", candidate, flags=re.IGNORECASE).strip()
        candidate = re.sub(r"```$", "", candidate).strip()

    parsed_obj: dict[str, object] | None = None
    for payload_text in _iter_json_candidates(candidate):
        try:
            raw_obj = json.loads(payload_text)
        except json.JSONDecodeError:
            continue
        if isinstance(raw_obj, dict):
            parsed_obj = raw_obj
            break

    if parsed_obj is None:
        return None

    visible = bool(parsed_obj.get("visible", False))
    confidence = str(parsed_obj.get("confidence", "low")).strip().lower()
    if confidence not in _VALID_CONFIDENCE:
        confidence = "low"

    horizontal = str(parsed_obj.get("horizontal", "unknown")).strip().lower()
    if horizontal not in _VALID_HORIZONTAL:
        horizontal = "unknown"

    vertical = str(parsed_obj.get("vertical", "unknown")).strip().lower()
    if vertical not in _VALID_VERTICAL:
        vertical = "unknown"

    depth = str(parsed_obj.get("depth", "unknown")).strip().lower()
    if depth not in _VALID_DEPTH:
        depth = "unknown"

    guidance = str(parsed_obj.get("guidance", "")).strip()
    summary = str(parsed_obj.get("summary", "")).strip()
    if not summary:
        if visible:
            summary = "Objet repere. Ajustez doucement la camera selon l'indication."
        else:
            summary = "Objet non visible pour l'instant. Balayez lentement la scene gauche-droite."

    return {
        "visible": visible,
        "confidence": confidence,
        "horizontal": horizontal,
        "vertical": vertical,
        "depth": depth,
        "guidance": guidance,
        "summary": summary,
    }


def _iter_json_candidates(content: str) -> list[str]:
    candidates = [content]
    first_brace = content.find("{")
    last_brace = content.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        candidates.append(content[first_brace:last_brace + 1])
    return candidates

def generate_voice_response(
    openrouter_api_key: str,
    elevenlabs_api_key: str,
    system_prompt: str,
    user_text: str
) -> tuple[str | None, str | None]:
    """Generates a conversational response using OpenRouter, then synthesizes audio with ElevenLabs.
    Returns: (text_response, base64_audio_data)
    """
    if not openrouter_api_key or not elevenlabs_api_key:
        logger.error("Missing API keys for OpenRouter or ElevenLabs.")
        return "Erreur: configuration API manquante.", None

    try:
        # 1. Ask the OpenRouter LLM (Gemini 2.5 Flash via OpenRouter)
        or_url = "https://openrouter.ai/api/v1/chat/completions"
        or_headers = {
            "Authorization": f"Bearer {openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:5000",
            "X-Title": "Spatial Neglect Assistant"
        }
        or_data = {
            "model": "google/gemini-2.5-flash",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text}
            ],
            "temperature": 0.5
        }
        res_llm = requests.post(or_url, json=or_data, headers=or_headers, timeout=45)
        res_llm.raise_for_status()
        text_response = res_llm.json()["choices"][0]["message"]["content"]
        if not text_response:
            return "OpenRouter a renvoye une reponse vide.", None
    except requests.RequestException:
        logger.exception("OpenRouter request failed.")
        return "Erreur OpenRouter: generation texte impossible.", None
    except Exception:
        logger.exception("Unexpected OpenRouter stage failure.")
        return "Erreur OpenRouter inattendue.", None

    error_text, b64_audio = synthesize_speech(elevenlabs_api_key, text_response)
    if error_text:
        logger.warning("Falling back to text-only response because TTS failed: %s", error_text)
        return text_response, None
    return text_response, b64_audio


def _format_elevenlabs_error(exc: requests.RequestException, fallback_message: str) -> str:
    """Extract actionable ElevenLabs API error details when available."""
    if not isinstance(exc, requests.RequestException):
        return fallback_message
    response = getattr(exc, "response", None)
    if response is None:
        return fallback_message
    try:
        payload = response.json()
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, dict):
            message = detail.get("message")
            if isinstance(message, str) and message.strip():
                return f"ElevenLabs: {message.strip()}"
    except Exception:
        pass
    return fallback_message
