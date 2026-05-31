"""Logging helpers."""

import json
import logging
import os
import sys
import threading
import uuid
from logging.handlers import RotatingFileHandler

from .settings import DOCKER_MODE, LOG_FILE

logger = logging.getLogger("traktor")

_correlation_id = threading.local()


_UNSET = object()


def set_correlation_id(cid=_UNSET):
    """Set the correlation ID for the current thread.

    Args:
        cid: Custom correlation ID. If not provided, auto-generates a UUID.
            Pass None to explicitly clear the correlation ID.

    Returns:
        The correlation ID string or None if cleared
    """
    if cid is _UNSET:
        cid = str(uuid.uuid4())
    _correlation_id.value = cid
    return _correlation_id.value


def get_correlation_id():
    """Get the correlation ID for the current thread.

    Returns:
        The correlation ID string or None if not set
    """
    return getattr(_correlation_id, "value", None)


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging.

    Outputs log records as JSON objects with timestamp, level, logger name,
    message, source location, correlation ID, and extra fields.
    """

    def format(self, record):
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "source": f"{record.funcName}:{record.lineno}",
            "correlation_id": getattr(record, "correlation_id", None)
            or get_correlation_id(),
            "extra": getattr(record, "extra", {}),
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data, default=str)


def setup_logging(verbose=False, structured=False):
    """Setup logging with rotating file handler and optional console output.

    Args:
        verbose: If True, set console output to DEBUG level
        structured: If True, use JSONFormatter for the file handler
    """
    logger.setLevel(logging.DEBUG)
    logger.handlers = []

    # File handler uses JSON if structured, otherwise plain text
    if structured:
        file_formatter = JSONFormatter()
    else:
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # Console handler always uses plain text for readability
    console_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    logger.info("=" * 80)
    logger.info("Traktor logging initialized")
    logger.info(f"Log file: {LOG_FILE}")
    logger.info(f"Verbose mode: {verbose}")
    logger.info(f"Structured logging: {structured}")
    logger.info(f"Docker mode: {DOCKER_MODE}")
    logger.info(f"Python version: {sys.version}")
    logger.info(f"Working directory: {os.getcwd()}")
    logger.info("=" * 80)
