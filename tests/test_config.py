from __future__ import annotations

import os

import pytest

from mobile_ingestion.config import (OBJECT_SEARCH_VISION_MODELS, AppConfig,
                                     load_dotenv_file,
                                     normalize_object_search_vision_model)


def test_load_dotenv_file_reads_quoted_values(tmp_path, monkeypatch) -> None:
  dotenv_path = tmp_path / ".env"
  dotenv_path.write_text(
      "OPENAI_API_KEY='secret-value'\n"
      "MOBILE_INGEST_VOICE_MODEL=gpt-4o-transcribe\n",
      encoding="utf-8",
  )
  monkeypatch.delenv("OPENAI_API_KEY", raising=False)
  monkeypatch.delenv("MOBILE_INGEST_VOICE_MODEL", raising=False)

  load_dotenv_file(dotenv_path)

  assert os.environ["OPENAI_API_KEY"] == "secret-value"
  assert os.environ["MOBILE_INGEST_VOICE_MODEL"] == "gpt-4o-transcribe"


def test_app_config_defaults_voice_language_to_french() -> None:
  config = AppConfig()

  assert config.voice_language == "fr"


def test_app_config_defaults_video_to_five_fps() -> None:
  config = AppConfig()

  assert config.video_max_fps == 5.0


def test_app_config_defaults_voice_buffer_to_twenty_seconds() -> None:
  config = AppConfig()

  assert config.voice_audio_buffer_seconds == 20.0


def test_app_config_defaults_object_search_command_timeout() -> None:
  config = AppConfig()

  assert config.object_search_command_timeout_seconds == 8.0


def test_app_config_defaults_object_search_vision_model() -> None:
  config = AppConfig()

  assert config.object_search_vision_model == "gpt-5.4-mini"


def test_normalize_object_search_vision_model_accepts_allowlisted_values() -> None:
  assert normalize_object_search_vision_model("gpt-5.4") == "gpt-5.4"
  assert normalize_object_search_vision_model("gpt-5.4-mini") == "gpt-5.4-mini"
  assert normalize_object_search_vision_model("gpt-4o") == "gpt-4o"
  assert OBJECT_SEARCH_VISION_MODELS == (
      "gpt-5.4",
      "gpt-5.4-mini",
      "gpt-4o",
  )


def test_normalize_object_search_vision_model_rejects_unknown_values() -> None:
  with pytest.raises(ValueError):
    normalize_object_search_vision_model("google/owlv2-large-patch14-ensemble")


def test_app_config_supports_object_search_overrides() -> None:
  config = AppConfig.from_mapping({
      "object_search_vision_model": "gpt-5.4",
      "object_search_detection_interval_seconds": 2.5,
      "object_search_command_timeout_seconds": 6.0,
      "object_search_resolver_model": "custom-resolver",
  })

  assert config.object_search_vision_model == "gpt-5.4"
  assert config.object_search_detection_interval_seconds == 2.5
  assert config.object_search_command_timeout_seconds == 6.0
  assert config.object_search_resolver_model == "custom-resolver"


def test_app_config_rejects_invalid_object_search_vision_model() -> None:
  with pytest.raises(ValueError):
    AppConfig.from_mapping({
        "object_search_vision_model": "not-a-real-model",
    })
