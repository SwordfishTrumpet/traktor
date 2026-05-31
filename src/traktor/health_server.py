"""HTTP server for health check endpoints.

Provides HTTP endpoints for container health checks, Prometheus metrics,
and sync status monitoring.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from .log import logger
from .resilience import health_checker

DEFAULT_HEALTH_PORT = 8080
HEALTH_OK = 200
HEALTH_DEGRADED = 503


class HealthHandler(BaseHTTPRequestHandler):
    """HTTP request handler for health endpoints."""

    def do_GET(self) -> None:
        """Handle GET requests for health endpoints."""
        if self.path == "/health":
            self._send_health()
        elif self.path == "/metrics":
            self._send_metrics()
        elif self.path == "/status":
            self._send_status()
        else:
            self._send_404()

    def _send_health(self) -> None:
        """Send health check response."""
        results = health_checker.check_all()
        status = HEALTH_OK if results["status"] == "healthy" else HEALTH_DEGRADED
        self._send_json({"status": results["status"], "components": results["components"]}, status)

    def _send_metrics(self) -> None:
        """Send Prometheus-compatible metrics."""
        metrics = []
        results = health_checker.check_all()

        for component, status in results.get("components", {}).items():
            value = 1 if status == "healthy" else 0
            metrics.append(f'traktor_health_status{{component="{component}"}} {value}')

        self._send_text("\n".join(metrics))

    def _send_status(self) -> None:
        """Send sync status."""
        self._send_json({"sync_status": "idle", "last_sync": None})

    def _send_404(self) -> None:
        """Send 404 response."""
        self.send_response(404)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Not Found")

    def _send_json(self, data: dict[str, Any], status: int = HEALTH_OK) -> None:
        """Send JSON response."""
        self.send_response(status)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_text(self, text: str, status: int = HEALTH_OK) -> None:
        """Send plain text response."""
        self.send_response(status)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(text.encode())

    def log_message(self, format: str, *args: Any) -> None:
        """Log message using project logger instead of stderr."""
        logger.debug(f"Health server: {format % args}")


class HealthServer:
    """HTTP server for health endpoints."""

    def __init__(self, port: int = DEFAULT_HEALTH_PORT) -> None:
        """Initialize health server.

        Args:
            port: Port to listen on (default: 8080)
        """
        self.port = port
        self.server: HTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the health server in a background thread."""
        self.server = HTTPServer(("0.0.0.0", self.port), HealthHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logger.info(f"Health server started on port {self.port}")

    def stop(self) -> None:
        """Stop the health server."""
        if self.server:
            self.server.shutdown()
            logger.info("Health server stopped")
