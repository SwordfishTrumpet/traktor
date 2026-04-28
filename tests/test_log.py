"""Tests for log module."""

import logging
from logging.handlers import RotatingFileHandler
from unittest.mock import patch

from traktor import log as log_module
from traktor.log import setup_logging


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_logger_name_is_traktor(self):
        """Test that the module logger is named 'traktor'."""
        assert log_module.logger.name == "traktor"

    def test_setup_logging_creates_rotating_file_handler(self, tmp_path):
        """Test that setup_logging creates a RotatingFileHandler."""
        log_file = tmp_path / "test.log"
        with patch("traktor.log.LOG_FILE", log_file):
            with patch("traktor.log.DOCKER_MODE", False):
                setup_logging(verbose=False)

        logger = log_module.logger
        assert logger.level == logging.DEBUG

        file_handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 1

        fh = file_handlers[0]
        assert fh.maxBytes == 5 * 1024 * 1024
        assert fh.backupCount == 5
        assert fh.encoding == "utf-8"

    def test_setup_logging_verbose_mode_enables_debug_console(self, tmp_path):
        """Test verbose mode sets console handler to DEBUG level."""
        log_file = tmp_path / "test.log"
        with patch("traktor.log.LOG_FILE", log_file):
            with patch("traktor.log.DOCKER_MODE", False):
                setup_logging(verbose=True)

        logger = log_module.logger
        console_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
        ]
        assert len(console_handlers) == 1
        assert console_handlers[0].level == logging.DEBUG

    def test_setup_logging_non_verbose_console_is_info(self, tmp_path):
        """Test non-verbose mode sets console handler to INFO level."""
        log_file = tmp_path / "test.log"
        with patch("traktor.log.LOG_FILE", log_file):
            with patch("traktor.log.DOCKER_MODE", False):
                setup_logging(verbose=False)

        logger = log_module.logger
        console_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
        ]
        assert len(console_handlers) == 1
        assert console_handlers[0].level == logging.INFO

    def test_setup_logging_docker_mode_log_path(self, tmp_path):
        """Test that Docker mode is respected in log path setup."""
        log_file = tmp_path / "traktor.log"
        with patch("traktor.log.LOG_FILE", log_file):
            with patch("traktor.log.DOCKER_MODE", True):
                setup_logging(verbose=False)

        logger = log_module.logger
        file_handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 1
        assert file_handlers[0].baseFilename == str(log_file)

    def test_setup_logging_local_mode_log_path(self, tmp_path):
        """Test that local mode uses correct log path."""
        log_file = tmp_path / "traktor.log"
        with patch("traktor.log.LOG_FILE", log_file):
            with patch("traktor.log.DOCKER_MODE", False):
                setup_logging(verbose=False)

        logger = log_module.logger
        file_handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 1
        assert file_handlers[0].baseFilename == str(log_file)

    def test_setup_logging_formatter_format(self, tmp_path):
        """Test that the formatter includes expected fields."""
        log_file = tmp_path / "test.log"
        with patch("traktor.log.LOG_FILE", log_file):
            with patch("traktor.log.DOCKER_MODE", False):
                setup_logging(verbose=False)

        logger = log_module.logger
        file_handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
        fmt_str = file_handlers[0].formatter._fmt

        assert "asctime" in fmt_str
        assert "name" in fmt_str
        assert "levelname" in fmt_str
        assert "funcName" in fmt_str
        assert "lineno" in fmt_str
        assert "message" in fmt_str

    def test_setup_logging_clears_existing_handlers(self, tmp_path):
        """Test that setup_logging clears existing handlers."""
        log_file = tmp_path / "test.log"
        logger = log_module.logger

        dummy = logging.StreamHandler()
        logger.addHandler(dummy)

        with patch("traktor.log.LOG_FILE", log_file):
            with patch("traktor.log.DOCKER_MODE", False):
                setup_logging(verbose=False)

        assert dummy not in logger.handlers
        assert len(logger.handlers) >= 2

    def test_setup_logging_file_handler_is_debug_level(self, tmp_path):
        """Test that the file handler is always set to DEBUG level."""
        log_file = tmp_path / "test.log"
        with patch("traktor.log.LOG_FILE", log_file):
            with patch("traktor.log.DOCKER_MODE", False):
                setup_logging(verbose=False)

        logger = log_module.logger
        file_handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 1
        assert file_handlers[0].level == logging.DEBUG

    def test_setup_logging_handles_nested_log_path(self, tmp_path):
        """Test that setup_logging works with nested log paths."""
        log_file = tmp_path / "subdir" / "traktor.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)

        with patch("traktor.log.LOG_FILE", log_file):
            with patch("traktor.log.DOCKER_MODE", False):
                setup_logging(verbose=False)

        logger = log_module.logger
        file_handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 1
