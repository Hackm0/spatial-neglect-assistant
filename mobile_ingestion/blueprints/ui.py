from __future__ import annotations

from flask import Blueprint, current_app, render_template

from mobile_ingestion.config import OBJECT_SEARCH_VISION_MODELS


ui_blueprint = Blueprint("ui", __name__)


@ui_blueprint.get("/")
def index() -> str:
  settings = current_app.config["APP_CONFIG"]
  return render_template(
      "index.html",
      ice_servers=list(settings.ice_servers),
      video_max_fps=settings.video_max_fps,
      object_search_vision_models=list(OBJECT_SEARCH_VISION_MODELS),
      object_search_vision_model=settings.object_search_vision_model,
  )
