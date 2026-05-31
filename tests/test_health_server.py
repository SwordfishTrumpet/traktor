"""Tests for health server endpoints."""

import json
import time
from http.client import HTTPConnection
from unittest.mock import patch

import pytest

from traktor.resilience import health_checker


class TestHealthEndpoints:
    """Tests for HTTP health endpoint server."""

    @pytest.fixture(autouse=True)
    def reset_health_checker(self):
        """Reset health checker before each test."""
        health_checker._checks.clear()
        yield
        health_checker._checks.clear()

    def _get_free_port(self):
        """Get a free port for testing."""
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def test_health_endpoint_healthy(self):
        """Test /health returns 200 when all components are healthy."""
        from traktor.health_server import HealthServer

        health_checker.register("cache", lambda: True)
        port = self._get_free_port()
        server = HealthServer(port=port)
        server.start()
        time.sleep(0.1)

        try:
            conn = HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/health")
            response = conn.getresponse()
            data = json.loads(response.read().decode())

            assert response.status == 200
            assert data["status"] == "healthy"
            assert "components" in data
            assert data["components"]["cache"] == "healthy"
            conn.close()
        finally:
            server.stop()

    def test_health_endpoint_degraded(self):
        """Test /health returns 503 when a component is degraded."""
        from traktor.health_server import HealthServer

        # Need 2 consecutive failures to trigger degraded status
        call_count = 0

        def fail_twice():
            nonlocal call_count
            call_count += 1
            return False

        health_checker.register("cache", fail_twice)
        port = self._get_free_port()
        server = HealthServer(port=port)
        server.start()
        time.sleep(0.1)

        try:
            # Trigger 2 failures to reach degraded status
            conn = HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/health")
            conn.getresponse().read()
            conn.close()

            conn = HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/health")
            response = conn.getresponse()
            data = json.loads(response.read().decode())

            assert response.status == 503
            assert data["status"] == "degraded"
            assert data["components"]["cache"] == "degraded"
            conn.close()
        finally:
            server.stop()

    def test_metrics_endpoint(self):
        """Test /metrics returns valid Prometheus format."""
        from traktor.health_server import HealthServer

        health_checker.register("cache", lambda: True)
        health_checker.register("config", lambda: False)
        port = self._get_free_port()
        server = HealthServer(port=port)
        server.start()
        time.sleep(0.1)

        try:
            conn = HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/metrics")
            response = conn.getresponse()
            body = response.read().decode()

            assert response.status == 200
            assert "traktor_health_status" in body
            assert 'component="cache"' in body
            assert 'component="config"' in body
            conn.close()
        finally:
            server.stop()

    def test_status_endpoint(self):
        """Test /status returns JSON with sync status."""
        from traktor.health_server import HealthServer

        port = self._get_free_port()
        server = HealthServer(port=port)
        server.start()
        time.sleep(0.1)

        try:
            conn = HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/status")
            response = conn.getresponse()
            data = json.loads(response.read().decode())

            assert response.status == 200
            assert "sync_status" in data
            assert data["sync_status"] == "idle"
            conn.close()
        finally:
            server.stop()

    def test_unknown_path_returns_404(self):
        """Test unknown paths return 404."""
        from traktor.health_server import HealthServer

        port = self._get_free_port()
        server = HealthServer(port=port)
        server.start()
        time.sleep(0.1)

        try:
            conn = HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/unknown")
            response = conn.getresponse()

            assert response.status == 404
            conn.close()
        finally:
            server.stop()

    def test_server_start_stop(self):
        """Test server can be started and stopped cleanly."""
        from traktor.health_server import HealthServer

        port = self._get_free_port()
        server = HealthServer(port=port)

        assert server.server is None
        assert server.thread is None

        server.start()
        assert server.server is not None
        assert server.thread is not None
        assert server.thread.is_alive()

        server.stop()
        time.sleep(0.1)
        assert not server.thread.is_alive()

    def test_server_uses_correct_port(self):
        """Test server listens on the configured port."""
        from traktor.health_server import HealthServer

        port = self._get_free_port()
        server = HealthServer(port=port)
        server.start()
        time.sleep(0.1)

        try:
            conn = HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/health")
            response = conn.getresponse()
            assert response.status == 200
            conn.close()
        finally:
            server.stop()

    def test_health_handler_log_message(self):
        """Test HealthHandler log_message uses logger."""
        from traktor.health_server import HealthHandler
        from traktor.log import logger

        with patch.object(logger, "debug") as mock_debug:
            # Create a mock handler to test log_message
            class MockHandler(HealthHandler):
                def __init__(self):
                    pass

            handler = MockHandler()
            handler.log_message("test %s", "message")
            mock_debug.assert_called_once()
            assert "Health server" in mock_debug.call_args[0][0]
