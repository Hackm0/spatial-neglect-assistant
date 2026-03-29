from __future__ import annotations

from mobile_ingestion import create_app


def main() -> None:
  app = create_app()
  settings = app.config["APP_CONFIG"]
  app.run(host=settings.host, port=settings.port, debug=settings.debug)


if __name__ == "__main__":
  main()
