"""Tests for the `python -m traktor` module runner."""

import runpy
import sys
from unittest.mock import patch


def test_module_runner_calls_main(monkeypatch):
    """Test that running `python -m traktor` invokes cli.main()."""
    with patch("traktor.cli.main") as mock_main:
        monkeypatch.setitem(sys.modules, "__main__", None)
        runpy.run_module("traktor", run_name="__main__")

    mock_main.assert_called_once()


def test_exported_version_matches_the_package_metadata():
    """One release number, not a second copy that drifts (issue #52)."""
    from pathlib import Path

    import traktor

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = None
    for line in pyproject.read_text(encoding="utf-8").splitlines():
        if line.startswith("version"):
            declared = line.split("\"")[1]
            break

    assert declared is not None
    assert traktor.__version__ == declared
