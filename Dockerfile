# syntax=docker/dockerfile:1

# Use the official Python image with uv pre-installed
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set SSL certificates environment
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV CERTIFI_CERTIFICATE=/etc/ssl/certs/ca-certificates.crt

# Copy project files
COPY pyproject.toml ./
COPY README.md ./
COPY src/ ./src/

# Create a non-root user and necessary directories
RUN useradd -m -u 1000 traktor && \
    mkdir -p /data/logs /data/config && \
    chown -R traktor:traktor /app /data

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV UV_SYSTEM_PYTHON=1
ENV DOCKER_MODE=true

# Install Python dependencies using uv as traktor user
USER traktor
RUN uv sync

# Ensure certifi can find the CA bundle (run as root)
USER root
RUN uv pip install --system certifi && \
    mkdir -p /app/.venv/lib/python3.12/site-packages/certifi && \
    ln -sf /etc/ssl/certs/ca-certificates.crt /app/.venv/lib/python3.12/site-packages/certifi/cacert.pem && \
    chown -R traktor:traktor /app/.venv
USER traktor

# Set the entrypoint
ENTRYPOINT ["uv", "run", "--python", "/usr/local/bin/python", "traktor"]

# Default command (can be overridden)
CMD []
