from __future__ import annotations

import logging

from mobile_ingestion import create_app


def main() -> None:
  app = create_app()
  settings = app.config["APP_CONFIG"]
  logging.basicConfig(
      level=logging.DEBUG if settings.debug else logging.INFO,
      format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
  )
  app.logger.setLevel(logging.DEBUG if settings.debug else logging.INFO)
  app.logger.info(
      "Starting mobile_ingestion host=%s port=%s debug=%s",
      settings.host,
      settings.port,
      settings.debug,
  )
  app.run(host=settings.host, port=settings.port, debug=settings.debug)


if __name__ == "__main__":
  main()
