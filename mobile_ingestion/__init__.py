from __future__ import annotations

import atexit
from typing import Any, Mapping

from flask import Flask

from mobile_ingestion.blueprints.arduino import arduino_blueprint
from mobile_ingestion.blueprints.api import api_blueprint
from mobile_ingestion.blueprints.object_search import object_search_blueprint
from mobile_ingestion.blueprints.ui import ui_blueprint
from mobile_ingestion.blueprints.voice import voice_blueprint
from mobile_ingestion.config import AppConfig, load_dotenv_file
from mobile_ingestion.services import ServiceContainer, build_services


def create_app(config: Mapping[str, Any] | AppConfig | None = None,
               services: ServiceContainer | None = None) -> Flask:
  load_dotenv_file()
  settings = config if isinstance(config,
                                  AppConfig) else AppConfig.from_mapping(config)
  app = Flask(__name__, template_folder="templates", static_folder="static")
  app.config.update(settings.to_flask_mapping())

  service_container = services or build_services(settings)
  app.extensions["mobile_ingestion.services"] = service_container

  app.register_blueprint(ui_blueprint)
  app.register_blueprint(api_blueprint)
  app.register_blueprint(arduino_blueprint)
  app.register_blueprint(voice_blueprint)
  app.register_blueprint(object_search_blueprint)

  @app.get("/health")
  def health() -> tuple[dict[str, str], int]:
    return {
        "status": "ok",
    }, 200

  atexit.register(service_container.shutdown)
  return app
