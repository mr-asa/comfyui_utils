#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="$SCRIPT_DIR/config.json"
PYTHON_EXE=""
CANDIDATE_PY=""

if [ -f "$CONFIG_PATH" ]; then
  CANDIDATE_PY="$(python - "$CONFIG_PATH" <<'PY'
import json
import os
import sys

path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
except Exception:
    cfg = {}

candidates = []
conda = cfg.get("conda_env_folder") or ""
venv = cfg.get("venv_path") or ""
if conda:
    candidates.append(os.path.join(conda, "bin", "python"))
if venv:
    candidates.append(os.path.join(venv, "bin", "python"))

for c in candidates:
    if c and os.path.exists(c):
        print(c)
        break
PY
)"
fi

if [ -n "$CANDIDATE_PY" ] && [ -x "$CANDIDATE_PY" ]; then
  PYTHON_EXE="$CANDIDATE_PY"
else
  echo "config.json missing python path or file not found. Using python from PATH..."
  PYTHON_EXE="python"
fi

"$PYTHON_EXE" "$SCRIPT_DIR/update_comfy_repos.py"
