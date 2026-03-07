#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="$SCRIPT_DIR/config.json"
CONFIG_CLI="$SCRIPT_DIR/config_cli.py"
PYTHON_EXE=""
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"

if [ -f "$CONFIG_PATH" ]; then
  PYTHON_EXE="$(python "$CONFIG_CLI" --config "$CONFIG_PATH" get --key python_for_active_env || true)"
fi

if [ -z "$PYTHON_EXE" ]; then
  if [ -x "$VENV_PY" ]; then
    PYTHON_EXE="$VENV_PY"
  else
    echo "config.json not found or missing conda_env_folder. Using python from PATH to bootstrap config..."
    PYTHON_EXE="python"
  fi
fi

"$PYTHON_EXE" "$SCRIPT_DIR/comfyui_pip_update_audit.py" "$@"
