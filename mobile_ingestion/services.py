from __future__ import annotations

import os
from dataclasses import dataclass

from mobile_ingestion.analyzer import AnalyzerPort, NoOpAnalyzer
from mobile_ingestion.arduino import ArduinoControllerPort, PySerialArduinoController
from mobile_ingestion.config import AppConfig
from mobile_ingestion.eating_detection import (OpenAiEatingDetector,
                                               UnavailableEatingDetector)
from mobile_ingestion.mode_manager import RuntimeModeManager, RuntimeModePort
from mobile_ingestion.object_feedback import ArduinoBurstFeedbackController
from mobile_ingestion.object_search import (ObjectSearchCoordinator,
                                            ObjectSearchPort,
                                            OpenAiObjectTargetResolver,
                                            OpenAiVisionDetector,
                                            SwitchableObjectDetector)
from mobile_ingestion.runtime import AsyncioRunner
from mobile_ingestion.session_manager import SessionManager
from mobile_ingestion.voice import (NoOpWakeWordAction,
                                    NormalizedWakePhraseDetector,
                                    OpenAiRealtimeRecognizer, VoiceCoordinator,
                                    VoiceProcessingPort)


@dataclass(slots=True)
class ServiceContainer:
    settings: AppConfig
    runtime: AsyncioRunner
    analyzer: AnalyzerPort
    session_manager: SessionManager
    arduino_controller: ArduinoControllerPort
    voice_processor: VoiceProcessingPort
    object_search: ObjectSearchPort
    runtime_mode: RuntimeModePort

    def shutdown(self) -> None:
        self.runtime_mode.shutdown()
        self.arduino_controller.shutdown()
        self.session_manager.shutdown()
        self.object_search.shutdown()
        self.voice_processor.shutdown()


def _build_voice_prompt(settings: AppConfig) -> str | None:
  if settings.voice_prompt:
    return settings.voice_prompt
  return (
            "Transcris mot a mot uniquement ce qui est entendu (francais quebecois). "
            "N'invente jamais de mots, n'ajoute aucun contexte et ne reformule pas. "
            "Si l'audio est incomprehensible, retourne une transcription vide. "
            "Ne traduis jamais vers une autre langue. "
            "Si quelqu'un dit clairement 'jarvis', conserve-le verbatim.")


def build_services(settings: AppConfig) -> ServiceContainer:
  try:
    from mobile_ingestion.webrtc_session import (SenderVideoRelayHub,
                                                 WebRtcPeerSession)
  except ImportError as exc:
    raise RuntimeError(
        "Missing WebRTC dependencies. Install the project requirements before "
        "starting the Flask server.") from exc

  runtime = AsyncioRunner()
  analyzer = NoOpAnalyzer()
  arduino_controller = PySerialArduinoController()
  voice_processor = VoiceCoordinator(
      speech_recognizer=OpenAiRealtimeRecognizer(
          api_key=os.getenv("OPENAI_API_KEY", ""),
          connection_url=settings.voice_realtime_url,
          model=settings.voice_model,
          language=settings.voice_language,
          prompt=_build_voice_prompt(settings),
      ),
      wake_phrase_detector=NormalizedWakePhraseDetector(
          phrases=settings.voice_wake_phrases,
          cooldown_seconds=settings.voice_wake_cooldown_seconds,
      ),
      wake_word_action=NoOpWakeWordAction(),
      transcript_buffer_size=settings.voice_transcript_buffer_size,
      audio_buffer_seconds=settings.voice_audio_buffer_seconds,
  )
  object_search = ObjectSearchCoordinator(
      voice_processor=voice_processor,
      arduino_controller=arduino_controller,
      object_detector=SwitchableObjectDetector(
          model=settings.object_search_vision_model,
          detector_factory=lambda model: OpenAiVisionDetector(
              api_key=os.getenv("OPENAI_API_KEY", ""),
              model=model,
          ),
      ),
      target_resolver=OpenAiObjectTargetResolver(
          api_key=os.getenv("OPENAI_API_KEY", ""),
          model=settings.object_search_resolver_model,
      ),
      feedback=ArduinoBurstFeedbackController(
          controller=arduino_controller,
          burst_duration_seconds=(
              settings.mode_object_search_completion_seconds),
      ),
      wake_phrases=settings.voice_wake_phrases,
      detection_interval_seconds=settings.object_search_detection_interval_seconds,
      command_timeout_seconds=settings.object_search_command_timeout_seconds,
  )
  eating_detector = OpenAiEatingDetector(
      api_key=os.getenv("OPENAI_API_KEY", ""),
      model=settings.mode_eating_vision_model,
  )
  if not eating_detector.available:
    eating_detector = UnavailableEatingDetector(
        eating_detector.error or "Eating detector unavailable.")

  runtime_mode = RuntimeModeManager(
      voice_processor=voice_processor,
      object_search=object_search,
      arduino_controller=arduino_controller,
      eating_detector=eating_detector,
      idle_check_interval_seconds=settings.mode_idle_check_interval_seconds,
      eating_check_interval_seconds=settings.mode_eating_check_interval_seconds,
      eating_streak_required=settings.mode_eating_streak_required,
      eating_vibration_seconds=settings.mode_eating_vibration_seconds,
      eating_vibration_cooldown_seconds=(
          settings.mode_eating_vibration_cooldown_seconds),
      object_search_completion_seconds=(
          settings.mode_object_search_completion_seconds),
  )
  sender_video_relay = SenderVideoRelayHub()
  session_manager = SessionManager(
      runtime=runtime,
      analyzer=analyzer,
      voice_processor=voice_processor,
      object_search=object_search,
      runtime_mode=runtime_mode,
      settings=settings,
      session_factory=lambda context, callbacks: WebRtcPeerSession(
          context,
          callbacks,
          sender_video_relay=sender_video_relay,
      ),
  )
  return ServiceContainer(
      settings=settings,
      runtime=runtime,
      analyzer=analyzer,
      session_manager=session_manager,
      arduino_controller=arduino_controller,
      voice_processor=voice_processor,
      object_search=object_search,
      runtime_mode=runtime_mode,
  )
