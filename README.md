[![English](https://img.shields.io/badge/lang-English-blue)](README.md) [![Russian](https://img.shields.io/badge/lang-Russian-red)](README_RU.md)

# ComfyUI Utils

Utilities for maintaining a ComfyUI install and related repos. The scripts can live anywhere,
but placing this repo near your ComfyUI folder keeps paths tidy.

## What is included

- `update_comfy_repos.py` updates the main ComfyUI repo and every repo in `custom_nodes` (skips
  disabled folders), and writes a detailed change log with commit messages and file diffs.
- `update_workflow_repos.py` updates all Git repos under `user/default/workflows/github` and
  reports skipped non-repo folders.
- `comfyui_pip_update_audit.py` scans `requirements.txt` in the ComfyUI root and top-level
  custom nodes, compares installed vs latest versions, and prints update commands.
- `requirements_checker/` provides a richer requirements audit with config-driven environment
  selection (venv/conda), custom paths, and per-package status reporting.
- `clone-workflow_repos.py` clones workflow repos from `clone-workflow_repos.txt` into the
  workflows `github` folder (prompts for target path).
- `make_tmp_custom_nodes.py` generates `tmp_custom_nodes.json` with loaded/disabled nodes and
  their repo URLs, useful for collecting all plugins into a single list/repository.
- `png_to_json.py` scans a folder of `.png/.jpeg` images, reads ComfyUI `workflow` metadata,
  and writes a matching `.json` file next to each image that contains a workflow.
- `comfyui_root.py` resolves the ComfyUI root using config, validation, and upward search.

## Path handling

- Scripts no longer assume a fixed folder layout.
- `comfyui_root.py` validates a root by checking `custom_nodes`, `models`, `main.py`,
  and `extra_model_paths.yaml.example`.
- If `Comfyui_root` is missing or invalid, the scripts search upward and save it to `config.json`.

## Launchers

- Windows: `*.bat` files.
- Linux: `*.sh` equivalents (same names, run with `bash` or `./file.sh`).

## comfyui_pip_update_audit.py highlights

- Scans `requirements.txt` only (root and top-level custom nodes).
- Merges duplicate constraints and reports max allowed versions.
- Filters out pre-release and dev releases from suggested upgrades.
- Classifies updates as safe/risky/unknown and prints reasons for risky items.
- Checks reverse dependencies of already installed packages to flag conflicts before install.
- Treats inline comments in `requirements.txt` correctly (e.g. `pkg  # comment`).

## Hold / pin usage (comfyui_pip_update_audit.py)

- Hold: exclude packages from updates for the current env.
- Pin: lock packages to a specific version for the current env.
- Risky: dependency conflicts detected by reverse-check or `pip --dry-run`.
- Unknown: network/timeout/other non-dependency errors during dry-run.

Commands:

```bash
python comfyui_pip_update_audit.py --hold pkg1 pkg2
python comfyui_pip_update_audit.py --unhold pkg1 pkg2
python comfyui_pip_update_audit.py --pin pkg1==1.2.3 pkg2
python comfyui_pip_update_audit.py --unpin pkg1 pkg2
```

---

> [!WARNING]
> Tested on Windows and venv environment.
