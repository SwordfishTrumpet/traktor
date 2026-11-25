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


# ---------------------------------------------------------------------------
# Issue #86: the container entrypoint installed the dev group at start
# ---------------------------------------------------------------------------


def test_container_entrypoint_skips_the_dev_group():
    """`uv run` without --no-dev re-syncs the tools the build left out (issue #86)."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = next(
        line for line in dockerfile.splitlines() if line.strip().upper().startswith("ENTRYPOINT")
    )

    assert "--no-dev" in entrypoint


# ---------------------------------------------------------------------------
# Issue #88: .env.example blanked the documented redirect URI default
# ---------------------------------------------------------------------------


def test_env_example_keeps_the_documented_redirect_uri():
    """A blank value overrides the default, so the login sent no redirect_uri."""
    from dotenv import dotenv_values

    values = dotenv_values(ROOT / ".env.example")

    assert values.get("TRAKT_REDIRECT_URI") == "urn:ietf:wg:oauth:2.0:oob"


# ---------------------------------------------------------------------------
# Issue #90: the rollback steps of the release runbook could not run
# ---------------------------------------------------------------------------


def test_release_workflow_can_be_dispatched_by_hand():
    """RUNBOOK recovers a broken release with `gh workflow run` (issue #90)."""
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    triggers = workflow.split("on:", 1)[1].split("\npermissions:", 1)[0]

    assert "workflow_dispatch" in triggers


# ---------------------------------------------------------------------------
# Issue #101: SECURITY.md named a token file the product no longer writes
# ---------------------------------------------------------------------------


def test_security_policy_names_the_token_store_the_code_writes():
    """The policy must describe the .env store, not the retired JSON file."""
    policy = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    settings = (ROOT / "src" / "traktor" / "settings.py").read_text(encoding="utf-8")

    assert "ENV_FILE" in settings
    assert "Trakt OAuth tokens are stored in the `.env` file" in policy


# ---------------------------------------------------------------------------
# Issue #87: every path DOCKER_MODE persists must be under a Compose mount
# ---------------------------------------------------------------------------


def _compose_mount_targets() -> list:
    """Return the container paths docker-compose.yml mounts."""
    import re

    targets = []
    for line in (ROOT / "docker-compose.yml").read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*-\s*[^:\n]+:\s*(\S+)\s*$", line)
        if match and match.group(1).startswith("/"):
            targets.append(match.group(1))
    return targets


def test_compose_mounts_every_path_docker_mode_writes(monkeypatch):
    """`docker compose run --rm` discarded the delta file, snapshots and backups (#87)."""
    import importlib.util
    from pathlib import Path as _Path

    monkeypatch.setenv("DOCKER_MODE", "true")
    spec = importlib.util.spec_from_file_location(
        "packaging_probe_settings", ROOT / "src" / "traktor" / "settings.py"
    )
    settings = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(settings)

    targets = _compose_mount_targets()

    def covered(path):
        text = str(path)
        return any(text == t or text.startswith(t.rstrip("/") + "/") for t in targets)

    # /data holds config, cache and logs; the delta file and the undo snapshots
    # sit at the data root; snapshots default to the container home.
    docker_paths = [
        settings.CONFIG_FILE,
        settings.CACHE_DIR,
        settings.LOG_FILE,
        settings.DATA_DIR / ".traktor_watch_sync.json",
        settings.DATA_DIR / ".traktor",
        _Path("/home/traktor/.traktor_backups"),
    ]

    uncovered = [str(path) for path in docker_paths if not covered(path)]
    assert uncovered == []

    # The mount for the container home must exist by name, or a backup written
    # there still disappears with the container.
    assert "/home/traktor/.traktor_backups" in targets


def test_the_compose_healthcheck_uses_the_check_that_can_fail():
    """A healthcheck that cannot report a bad config is not a healthcheck (#116)."""
    import pathlib

    compose = pathlib.Path(__file__).parent.parent / "docker-compose.yml"
    text = compose.read_text(encoding="utf-8")

    healthcheck = [line for line in text.splitlines() if "--health-check" in line]
    assert healthcheck, "the compose healthcheck does not run --health-check"
    # Check the command itself, not the surrounding comment: the comment
    # explains the change and names the old command (issue #116).
    commands = [line for line in text.splitlines() if "test:" in line or "CMD" in line]
    assert commands, "the compose healthcheck has no command"
    assert not any("--diagnose" in line for line in commands), commands


def test_ci_runs_the_suite_the_way_the_readme_says():
    """README promises warnings-as-errors in CI; the step has to carry it (#117)."""
    import pathlib

    root = pathlib.Path(__file__).parent.parent
    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    pytest_steps = [line for line in ci.splitlines() if "pytest" in line and "run:" in line]

    assert pytest_steps, "ci.yml has no pytest step"
    assert all("-W error" in line for line in pytest_steps), pytest_steps

    makefile = (root / "Makefile").read_text(encoding="utf-8")
    test_target = makefile.split("test:", 1)[1].split("\n\n", 1)[0]
    assert "-W error" in test_target, test_target


def test_the_runbook_downloads_the_wheel_where_it_installs_it():
    """gh release download needs --dir, or the install path is empty (#118)."""
    import pathlib

    root = pathlib.Path(__file__).parent.parent
    runbook = (root / "RUNBOOK.md").read_text(encoding="utf-8")
    case2 = runbook.split("### Case 2", 1)[1].split("### Case 3", 1)[0]

    downloads = [line for line in case2.splitlines() if "gh release download" in line]
    assert downloads, "Case 2 has no download step"
    assert all("--dir" in line for line in downloads), downloads
    assert "dist/traktor-$VERSION-py3-none-any.whl" in case2 or "dist/" in case2


# ---------------------------------------------------------------------------
# Issue #119: setup.sh reported success when an install step failed
# ---------------------------------------------------------------------------


def _run_setup(tmp_path, path_prefix):
    import shutil
    import subprocess

    root = Path(__file__).resolve().parents[1]
    script = tmp_path / "setup.sh"
    shutil.copy(root / "setup.sh", script)
    env = {"PATH": f"{path_prefix}:/usr/bin:/bin", "HOME": str(tmp_path / "home")}
    (tmp_path / "home").mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )


def test_setup_fails_when_dependency_sync_fails(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "uv").write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    (stub / "uv").chmod(0o755)

    result = _run_setup(tmp_path, stub)

    assert result.returncode != 0
    assert "Setup complete" not in result.stdout


def test_setup_fails_when_uv_cannot_be_installed(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "curl").write_text("#!/bin/sh\nexit 6\n", encoding="utf-8")
    (stub / "curl").chmod(0o755)

    result = _run_setup(tmp_path, stub)

    assert result.returncode != 0
    assert "Setup complete" not in result.stdout


# ---------------------------------------------------------------------------
# Issue #121: setup.sh told the user to edit a .env it never created
# ---------------------------------------------------------------------------


def test_setup_creates_the_env_it_tells_the_user_to_edit(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "uv").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (stub / "uv").chmod(0o755)
    (tmp_path / ".env.example").write_text("PLEX_URL=\\nPLEX_TOKEN=\\n", encoding="utf-8")

    result = _run_setup(tmp_path, stub)

    assert result.returncode == 0, result.stderr
    created = tmp_path / ".env"
    assert created.exists()
    assert created.read_text(encoding="utf-8") == "PLEX_URL=\\nPLEX_TOKEN=\\n"
    assert created.stat().st_mode & 0o777 == 0o600


def test_setup_does_not_overwrite_an_existing_env(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "uv").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (stub / "uv").chmod(0o755)
    (tmp_path / ".env.example").write_text("PLEX_URL=\\n", encoding="utf-8")
    (tmp_path / ".env").write_text("PLEX_URL=http://mine:32400\\n", encoding="utf-8")

    assert _run_setup(tmp_path, stub).returncode == 0
    assert "mine" in (tmp_path / ".env").read_text(encoding="utf-8")
