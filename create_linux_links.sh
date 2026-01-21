#!/usr/bin/env bash
# =========================================================
# ComfyUI Utils - Linux Desktop Files Generator
# =========================================================

set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="${BASE_DIR}/run_linux"
ICO_DIR="${BASE_DIR}/ico"

mkdir -p "${RUN_DIR}"

# =========================================================
# EDIT HERE - shortcuts list
# =========================================================

SHORTCUTS=(
  "ComfyUI Run|run_comfyui.sh"
  "Clone Workflow Repos|clone_workflow_repos_run.sh"
  "Pip Update Audit|comfyui_pip_update_audit_run.sh"
  "Junk Links Manager|custom_nodes_link_manager.py"
  "Partial Repo Sync|partial_repo_sync.py"
  "PNG to JSON|png_to_json_run.sh"
  "Update Comfy Repos|update_comfy_repos_run.sh"
  "Update Workflow Repos|update_workflow_repos_run.sh"
)

# =========================================================
# DO NOT EDIT BELOW
# =========================================================

for entry in "${SHORTCUTS[@]}"; do
  name="${entry%%|*}"
  target="${entry#*|}"

  target_path="${BASE_DIR}/${target}"
  if [[ ! -f "${target_path}" ]]; then
    echo "SKIP (target not found): ${target}"
    continue
  fi

  icon_name="${target%.*}.ico"
  icon_path="${ICO_DIR}/${icon_name}"
  if [[ ! -f "${icon_path}" ]]; then
    icon_path=""
  fi

  case "${target}" in
    *.sh) exec_cmd=(bash "${target_path}") ;;
    *.py) exec_cmd=(python3 "${target_path}") ;;
    *) exec_cmd=(bash "${target_path}") ;;
  esac

  desktop_path="${RUN_DIR}/${name}.desktop"
  {
    echo "[Desktop Entry]"
    echo "Type=Application"
    echo "Name=${name}"
    printf "Exec="
    printf "%q " "${exec_cmd[@]}"
    echo
    echo "Path=${BASE_DIR}"
    echo "Terminal=true"
    if [[ -n "${icon_path}" ]]; then
      echo "Icon=${icon_path}"
    fi
    echo "Categories=Utility;"
  } > "${desktop_path}"

  chmod +x "${desktop_path}"
  echo "Created: ${desktop_path}"
done

echo
echo "Done. Desktop files created in: ${RUN_DIR}"
