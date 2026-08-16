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
