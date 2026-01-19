#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_EXE="python"

if ! "$PYTHON_EXE" -c "import PIL" >/dev/null 2>&1; then
  echo "Pillow not found. Installing..."
  "$PYTHON_EXE" -m pip install pillow
fi

"$PYTHON_EXE" "$SCRIPT_DIR/png_to_json.py"
