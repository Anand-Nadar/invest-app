"""Logging setup: console + rotating file (logs/app.log)."""
import logging
import logging.config

from app.config import Settings


def configure_logging(settings: Settings) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {"format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s"}
            },
            "handlers": {
                "console": {"class": "logging.StreamHandler", "formatter": "standard"},
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "formatter": "standard",
                    "filename": str(settings.log_dir / "app.log"),
                    "maxBytes": 1_000_000,
                    "backupCount": 5,
                    "encoding": "utf-8",
                },
            },
            "root": {"level": settings.log_level.upper(), "handlers": ["console", "file"]},
        }
    )
    logging.getLogger(__name__).debug("Logging configured")
