"""Logging helpers."""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from .settings import DOCKER_MODE, LOG_FILE

logger = logging.getLogger("traktor")


def setup_logging(verbose=False):
    """Setup logging with rotating file handler and optional console output."""
    logger.setLevel(logging.DEBUG)
    logger.handlers = []

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    logger.info("=" * 80)
    logger.info("Traktor logging initialized")
    logger.info(f"Log file: {LOG_FILE}")
    logger.info(f"Verbose mode: {verbose}")
    logger.info(f"Docker mode: {DOCKER_MODE}")
    logger.info(f"Python version: {sys.version}")
    logger.info(f"Working directory: {os.getcwd()}")
    logger.info("=" * 80)
