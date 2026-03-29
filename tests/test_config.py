from __future__ import annotations

import os

from mobile_ingestion.config import AppConfig, load_dotenv_file


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
