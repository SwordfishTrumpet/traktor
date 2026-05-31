"""Tests for log module."""

import json
import logging
import sys
import threading
import uuid
from logging.handlers import RotatingFileHandler
from unittest.mock import patch

from traktor import log as log_module
from traktor.log import (
    JSONFormatter,
    get_correlation_id,
    set_correlation_id,
    setup_logging,
)


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


class TestJSONFormatter:
    """Tests for JSONFormatter."""

    def test_json_formatter_basic(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="traktor",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        data = json.loads(result)
        assert data["level"] == "INFO"
        assert data["message"] == "Test message"
        assert data["logger"] == "traktor"
        assert "timestamp" in data
        assert "source" in data

    def test_json_formatter_with_correlation_id(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="traktor",
            level=logging.DEBUG,
            pathname="test.py",
            lineno=2,
            msg="Debug msg",
            args=(),
            exc_info=None,
        )
        record.correlation_id = "test-cid-123"
        result = formatter.format(record)
        data = json.loads(result)
        assert data["correlation_id"] == "test-cid-123"

    def test_json_formatter_with_extra(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="traktor",
            level=logging.WARNING,
            pathname="test.py",
            lineno=3,
            msg="Warning msg",
            args=(),
            exc_info=None,
        )
        record.extra = {"endpoint": "/test", "duration": 0.5}
        result = formatter.format(record)
        data = json.loads(result)
        assert data["extra"]["endpoint"] == "/test"
        assert data["extra"]["duration"] == 0.5

    def test_json_formatter_with_exception(self):
        formatter = JSONFormatter()
        try:
            raise ValueError("test error")
        except Exception:
            exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="traktor",
            level=logging.ERROR,
            pathname="test.py",
            lineno=4,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )
        result = formatter.format(record)
        data = json.loads(result)
        assert "exception" in data
        assert "ValueError" in data["exception"]


class TestCorrelationId:
    """Tests for correlation ID helpers."""

    def test_set_correlation_id_returns_uuid(self):
        cid = set_correlation_id()
        assert cid is not None
        assert len(cid) > 0
        uuid.UUID(cid)

    def test_set_correlation_id_custom(self):
        cid = set_correlation_id("custom-id")
        assert cid == "custom-id"
        assert get_correlation_id() == "custom-id"

    def test_get_correlation_id_default(self):
        set_correlation_id(None)
        result = get_correlation_id()
        assert result is None

    def test_correlation_id_thread_local(self):
        set_correlation_id("main-thread")
        results = {}

        def worker():
            set_correlation_id("worker-thread")
            results["worker"] = get_correlation_id()

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        assert get_correlation_id() == "main-thread"
        assert results["worker"] == "worker-thread"

    def test_correlation_id_none(self):
        set_correlation_id(None)
        assert get_correlation_id() is None


class TestSetupLoggingStructured:
    """Tests for structured logging setup."""

    def test_setup_logging_structured_uses_json_formatter(self, tmp_path):
        log_file = tmp_path / "test.log"
        with patch("traktor.log.LOG_FILE", log_file):
            with patch("traktor.log.DOCKER_MODE", False):
                setup_logging(verbose=False, structured=True)

        logger = log_module.logger
        file_handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 1
        assert isinstance(file_handlers[0].formatter, JSONFormatter)

    def test_setup_logging_structured_console_plain_text(self, tmp_path):
        log_file = tmp_path / "test.log"
        with patch("traktor.log.LOG_FILE", log_file):
            with patch("traktor.log.DOCKER_MODE", False):
                setup_logging(verbose=False, structured=True)

        logger = log_module.logger
        console_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
        ]
        assert len(console_handlers) == 1
        # Console should remain plain text
        assert not isinstance(console_handlers[0].formatter, JSONFormatter)
        assert isinstance(console_handlers[0].formatter, logging.Formatter)

    def test_setup_logging_non_structured_default(self, tmp_path):
        log_file = tmp_path / "test.log"
        with patch("traktor.log.LOG_FILE", log_file):
            with patch("traktor.log.DOCKER_MODE", False):
                setup_logging(verbose=False, structured=False)

        logger = log_module.logger
        file_handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
        assert not isinstance(file_handlers[0].formatter, JSONFormatter)
        assert isinstance(file_handlers[0].formatter, logging.Formatter)

    def test_setup_logging_structured_logs_json(self, tmp_path):
        log_file = tmp_path / "test.log"
        with patch("traktor.log.LOG_FILE", log_file):
            with patch("traktor.log.DOCKER_MODE", False):
                setup_logging(verbose=False, structured=True)
                log_module.logger.info("Test JSON log")

        with open(log_file, "r") as f:
            lines = f.readlines()

        # Find the JSON log line
        json_lines = [line for line in lines if "Test JSON log" in line]
        assert len(json_lines) == 1
        data = json.loads(json_lines[0])
        assert data["message"] == "Test JSON log"
        assert data["level"] == "INFO"

    def test_json_formatter_correlation_id_from_getter(self):
        formatter = JSONFormatter()
        set_correlation_id("auto-cid")
        record = logging.LogRecord(
            name="traktor",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        data = json.loads(result)
        assert data["correlation_id"] == "auto-cid"
        set_correlation_id(None)
