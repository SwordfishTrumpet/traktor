#!/bin/bash
# Traktor Setup Script
#
# Every step is checked: a failed install or dependency sync must not print the
# completion banner, because the user then runs traktor against a broken
# environment (issue #119).

set -u

fail() {
    echo ""
    echo "Setup failed: $*" >&2
    echo "Nothing was completed. Fix the error above and run ./setup.sh again." >&2
    exit 1
}

echo "Traktor Setup"
echo "================"
echo ""

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "uv is not installed. Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh || fail "could not install uv"
    export PATH="$HOME/.cargo/bin:$PATH"
    command -v uv &> /dev/null || fail "uv is still not on PATH after installing it"
fi

# Check Python version
python_version=$(uv run python --version 2>/dev/null || python3 --version 2>/dev/null)
echo "Python version: $python_version"

# Sync dependencies
echo ""
echo "Installing dependencies..."
uv sync || fail "dependency sync failed"

echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit .env file with your Plex credentials"
echo "2. Run: uv run traktor"
echo ""
