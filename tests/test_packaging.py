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


def test_documented_dev_install_matches_the_declared_dependency_group():
    """Every documented install command names the group pyproject.toml declares (#60).

    `uv sync --extra dev` exits 2 ("Extra `dev` is not defined") because the dev
    toolchain lives in [dependency-groups], so `make dev` and the three documents
    that copied it left a contributor unable to install what they need.
    """
    documented = {
        "Makefile": (ROOT / "Makefile").read_text(encoding="utf-8"),
        "CONTRIBUTING.md": (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8"),
        "QUICKSTART.md": (ROOT / "QUICKSTART.md").read_text(encoding="utf-8"),
        "diagnose.py": (ROOT / "src" / "traktor" / "diagnose.py").read_text(encoding="utf-8"),
    }

    for name, text in documented.items():
        assert "uv sync --extra dev" not in text, f"{name} still documents a missing extra"
        assert "uv sync --group dev" in text, f"{name} does not document the dev group"


def test_make_lint_runs_the_formatting_check_ci_runs():
    """`make lint` must cover black, or a batch passes locally and lands red (#71).

    CI fails a formatting difference. While `lint` ran ruff only, the local gate
    could be green on a tree CI rejects.
    """
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    targets = {}
    for block in makefile.split("\n\n"):
        lines = [line for line in block.splitlines() if line and not line.startswith("#")]
        if not lines or ":" not in lines[0] or lines[0].startswith("\t"):
            continue
        name, _, prerequisites = lines[0].partition(":")
        targets[name.strip()] = (prerequisites.split(), lines[1:])

    assert "lint" in targets
    prerequisites, recipe = targets["lint"]
    assert "format-check" in prerequisites
    assert any("ruff check" in line for line in recipe), "lint still runs ruff"

    _, format_recipe = targets["format-check"]
    assert any(
        "black --check src/ tests/" in line for line in format_recipe
    ), "format-check must run the command CI runs: black --check src/ tests/"


def test_runbook_documents_how_to_roll_back_a_release():
    """A shipped version that turns out wrong needs a written remedy (#72).

    The release path was forward-only: the tag workflow publishes and creates a
    Release, and nothing said what to do about a bad one - which is the moment
    nobody wants to invent a procedure.
    """
    runbook = (ROOT / "RUNBOOK.md").read_text(encoding="utf-8")

    assert "## Rolling back a release" in runbook
    section = runbook.split("## Rolling back a release", 1)[1].split("\n## ", 1)[0]

    for case in ("the release notes are wrong", "the artifact is broken", "the code is wrong"):
        assert case in section, f"the rollback procedure does not cover: {case}"

    # The rules that keep the tag auditable.
    assert "Never" in section and "move or re-point the tag" in section
    assert "force" in section
    # And how each case is checked, not just what to type.
    assert section.count("Verify:") >= 2


def test_runbook_rollback_does_not_promise_a_pypi_yank():
    """Nothing is published to PyPI, so a yank command would be a lie (#72)."""
    runbook = (ROOT / "RUNBOOK.md").read_text(encoding="utf-8")
    section = runbook.split("## Rolling back a release", 1)[1].split("\n## ", 1)[0]

    assert "uv publish --yank" not in section
    assert "nothing is published to PyPI" in section
