from __future__ import annotations

from flask import Blueprint, current_app, render_template


ui_blueprint = Blueprint("ui", __name__)


@ui_blueprint.get("/")
def index() -> str:
  settings = current_app.config["APP_CONFIG"]
  return render_template(
      "index.html",
      ice_servers=list(settings.ice_servers),
      video_max_fps=settings.video_max_fps,
  )
