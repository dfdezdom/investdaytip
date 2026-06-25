#!/bin/bash
set -euo pipefail

# Detect Python interpreter (macOS often lacks `python`, only `python3`)
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON=python3
    elif command -v python >/dev/null 2>&1; then
        PYTHON=python
    else
        echo "Error: no Python interpreter found. Install python3 and try again." >&2
        exit 1
    fi
fi

echo "Using Python: $PYTHON ($($PYTHON --version))"

# Create virtual environment if missing
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment in .venv..."
    "$PYTHON" -m venv .venv
fi

# Use the venv's Python directly so we don't depend on `source` leaving the
# parent shell activated (a child script can never activate the caller's shell).
VENV_PYTHON="./.venv/bin/python"
VENV_PIP="./.venv/bin/pip"

"$VENV_PYTHON" --version

echo "Installing / upgrading investdaytip in editable mode..."
"$VENV_PIP" install --upgrade -e ".[dev]"

# Verify installed version
INSTALLED_VERSION=$("$VENV_PYTHON" -c "import investdaytip; print(investdaytip.__version__)")
echo "Installed investdaytip version: $INSTALLED_VERSION"

# Warn if a different version is also available globally and would shadow the
# venv binary when the venv is not active.
GLOBAL_BIN=""
if command -v investdaytip >/dev/null 2>&1; then
    GLOBAL_BIN=$(command -v investdaytip)
    GLOBAL_VERSION=$(investdaytip --version 2>/dev/null | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' || echo "unknown")
    if [ "$GLOBAL_VERSION" != "v$INSTALLED_VERSION" ]; then
        echo ""
        echo "⚠️  Warning: another investdaytip binary exists outside this virtual environment."
        echo "   Global binary: $GLOBAL_BIN ($GLOBAL_VERSION)"
        echo "   This usually happens when you previously installed it system-wide."
        echo "   Run the tool from the virtual environment (see below) to use v$INSTALLED_VERSION."
    fi
fi

echo ""
echo "Installation complete!"
echo ""
echo "To run investdaytip with this installation, activate the virtual environment first:"
echo "    source .venv/bin/activate"
echo ""
echo "Then run:"
echo "    investdaytip --version"
echo ""
