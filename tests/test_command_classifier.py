from __future__ import annotations

from mobile_ingestion.command_classifier import CommandClassifier


classifier = CommandClassifier()


def test_classify_explicit_command_key() -> None:
  result = classifier.classify({"command": "status"})

  assert result.command == "status"
  assert result.raw_command == "status"


def test_classify_nested_alias_arguments() -> None:
  result = classifier.classify({
      "action": "connect",
      "payload": {
          "sdp": "abc",
          "type": "offer",
      },
      "metadata": {
          "client": "mobile",
      },
  })

  assert result.command == "offer"
  assert result.arguments["sdp"] == "abc"
  assert result.arguments["type"] == "offer"
  assert result.arguments["metadata"] == {"client": "mobile"}


def test_classify_offer_without_explicit_command() -> None:
  result = classifier.classify({
      "payload": {
          "sdp": "abc",
          "type": "offer",
      }
  })

  assert result.command == "offer"
  assert result.raw_command == "offer"


def test_classify_unknown_command() -> None:
  result = classifier.classify({"intent": "custom_mode"})

  assert result.command == "unknown"
  assert result.raw_command == "custom_mode"


def test_classify_root_key_command_shape() -> None:
  result = classifier.classify({
      "disconnect": {
          "reason": "manual",
      }
  })

  assert result.command == "close_session"
  assert result.raw_command == "disconnect"
  assert result.arguments["reason"] == "manual"


def test_classify_transcript_add_alias() -> None:
  result = classifier.classify({
      "event": "caption",
      "payload": {
          "text": "hello world",
      },
  })

  assert result.command == "transcript_add"
  assert result.arguments["text"] == "hello world"


def test_classify_natural_language_status_phrase() -> None:
  result = classifier.classify({"command": "what is the current status"})

  assert result.command == "status"
  assert result.raw_command == "what is the current status"


def test_classify_natural_language_transcript_clear_phrase() -> None:
  result = classifier.classify({"action": "please clear transcript now"})

  assert result.command == "transcript_clear"


def test_classify_explicit_spatial_detection_alias() -> None:
  result = classifier.classify({"command": "spatial_detect"})

  assert result.command == "spatial_detection"


def test_classify_natural_language_object_locator_phrase() -> None:
  result = classifier.classify({"command": "where are my glasses"})

  assert result.command == "spatial_detection"
