from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


SECRET_MARKERS = ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE")


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for marker in SECRET_MARKERS:
            message = message.replace(marker, "[REDACTED_ENV_NAME]")
        for attr in ("api_key", "api_secret", "passphrase"):
            if hasattr(record, attr):
                setattr(record, attr, "[REDACTED]")
        record.msg = message
        record.args = ()
        return True


def setup_logging(level: str, directory: str) -> logging.Logger:
    log_dir = Path(directory)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("okx_auto_trader")
    logger.setLevel(level.upper())
    logger.handlers.clear()
    logger.addFilter(SecretRedactionFilter())

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(SecretRedactionFilter())
    logger.addHandler(console)

    file_handler = TimedRotatingFileHandler(
        log_dir / "trading.log", when="midnight", backupCount=30, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(SecretRedactionFilter())
    logger.addHandler(file_handler)
    return logger

