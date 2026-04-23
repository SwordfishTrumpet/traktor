#!/bin/bash

# Traktor Setup Script

echo "Traktor Setup"
echo "================"
echo ""

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "uv is not installed. Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi

# Check Python version
python_version=$(uv run python --version 2>/dev/null || python3 --version 2>/dev/null)
echo "Python version: $python_version"

# Sync dependencies
echo ""
echo "Installing dependencies..."
uv sync

echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit .env file with your Plex credentials"
echo "2. Run: uv run traktor"
echo ""
