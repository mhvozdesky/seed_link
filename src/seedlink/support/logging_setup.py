"""Local rotating diagnostics with defense-in-depth contact-data redaction."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re

from seedlink.support.paths import diagnostic_log_path


LOGGER_NAME = "seedlink"
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PHONE_CANDIDATE = re.compile(r"(?<![\w/.-])(?:\+|0)[\d\s().-]{8,}\d(?![\w/])")


def _redact_phone(match: re.Match[str]) -> str:
    digit_count = sum(character.isdigit() for character in match.group(0))
    return "[PHONE REDACTED]" if 10 <= digit_count <= 15 else match.group(0)


def redact_sensitive(value: str) -> str:
    value = _EMAIL.sub("[EMAIL REDACTED]", value)
    return _PHONE_CANDIDATE.sub(_redact_phone, value)


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_sensitive(record.getMessage())
        record.args = ()
        return True


def configure_logging(
    path: Path | None = None, *, level: int = logging.INFO
) -> logging.Logger:
    target = path or diagnostic_log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    handler = RotatingFileHandler(
        target,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.addFilter(SensitiveDataFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
    return logger
