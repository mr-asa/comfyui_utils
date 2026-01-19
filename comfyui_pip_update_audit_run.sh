#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="$SCRIPT_DIR/config.json"
PYTHON_EXE=""
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"

if [ -f "$CONFIG_PATH" ]; then
  PYTHON_EXE="$(python - "$CONFIG_PATH" <<'PY'
import json
import os
import sys

path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
except Exception:
    cfg = {}

conda = cfg.get("conda_env_folder") or ""
venv = cfg.get("venv_path") or ""

for c in (
    os.path.join(conda, "bin", "python") if conda else "",
    os.path.join(venv, "bin", "python") if venv else "",
):
    if c and os.path.exists(c):
        print(c)
        break
PY
)"
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
