from __future__ import annotations

from flask import Blueprint, current_app, render_template


ui_blueprint = Blueprint("ui", __name__)


@ui_blueprint.get("/")
def index() -> str:
  settings = current_app.config["APP_CONFIG"]
  return render_template(
      "index.html",
      ice_servers=list(settings.ice_servers),
      voice_wake_phrases=list(settings.voice_wake_phrases),
      voice_idle_timeout_seconds=settings.voice_idle_timeout_seconds,
  )
