#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="$SCRIPT_DIR/config.json"
CONFIG_CLI="$SCRIPT_DIR/config_cli.py"
PYTHON_EXE=""
CANDIDATE_PY=""

if [ -f "$CONFIG_PATH" ]; then
  CANDIDATE_PY="$(python "$CONFIG_CLI" --config "$CONFIG_PATH" get --key python_for_active_env || true)"
fi

if [ -n "$CANDIDATE_PY" ] && [ -x "$CANDIDATE_PY" ]; then
  PYTHON_EXE="$CANDIDATE_PY"
else
  echo "config.json missing python path or file not found. Using python from PATH..."
  PYTHON_EXE="python"
fi

"$PYTHON_EXE" "$SCRIPT_DIR/update_comfy_repos.py"
