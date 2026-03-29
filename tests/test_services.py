from mobile_ingestion.config import AppConfig
from mobile_ingestion.services import _build_voice_prompt


def test_build_voice_prompt_uses_setting_override() -> None:
  config = AppConfig(voice_prompt="custom prompt")

  assert _build_voice_prompt(config) == "custom prompt"


def test_build_voice_prompt_default_discourages_hallucinations() -> None:
  config = AppConfig()
  prompt = _build_voice_prompt(config)

  assert prompt is not None
  assert "N'invente jamais" in prompt
  assert "transcription vide" in prompt
  assert "'jarvis'" in prompt
