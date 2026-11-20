"""Tests for the shipped container and environment files.

These files are the product for a Docker user the same way the CLI is for a
local one, so they get the same regression coverage: each test here failed
before its issue was fixed.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _dockerfile_copies() -> list:
    """Return the sources of the Dockerfile's COPY instructions."""
    sources = []
    for raw in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.upper().startswith("COPY "):
            continue
        parts = [part for part in line.split()[1:] if not part.startswith("--")]
        sources.extend(part.rstrip("/") for part in parts[:-1])
    return sources


def test_dockerfile_copies_the_lock_file():
    """`uv sync --frozen` aborts without uv.lock in the build context (issue #56)."""
    assert "uv.lock" in _dockerfile_copies()

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    copy_index = dockerfile.find("COPY uv.lock")
    sync_index = dockerfile.find("uv sync --no-dev --frozen")

    assert copy_index != -1
    assert sync_index != -1
    assert copy_index < sync_index, "uv.lock must be copied before the frozen sync runs"


def test_compose_service_builds_the_image_it_names():
    """A named image with no build context makes `docker compose build` a no-op (#57)."""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "build: ." in compose
    assert "image: traktor:test" in compose


def test_docker_run_clean_removes_the_image_compose_builds():
    """The clean command must name the tag compose produces (issue #57)."""
    script = (ROOT / "docker-run.sh").read_text(encoding="utf-8")

    assert "docker rmi traktor:test" in script


def test_env_example_leaves_the_official_source_usable():
    """The documented `cp .env.example .env` must not select zero sources (#58)."""
    values = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value

    assert values["TRAKTOR_LIST_SOURCE"] == "official"
    assert values["TRAKTOR_OFFICIAL_LISTS_ENABLED"].lower() == "true"


def test_env_example_documents_the_runtime_keys():
    """The example omits keys the manual documents (issue #58)."""
    example = (ROOT / ".env.example").read_text(encoding="utf-8")

    for key in (
        "TRAKTOR_HEALTH_PORT",
        "TRAKTOR_HEALTH_HOST",
        "TRAKTOR_HISTORY_RETENTION_DAYS",
        "TRAKTOR_MAX_MEMORY_MB",
        "TRAKTOR_BACKUP_BEFORE_SYNC",
    ):
        assert key in example, f"{key} is documented in README.md but missing from .env.example"
