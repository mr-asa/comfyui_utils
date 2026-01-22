[![English](https://img.shields.io/badge/lang-English-blue)](README.md) [![Russian](https://img.shields.io/badge/lang-Russian-red)](README_RU.md)

# ComfyUI Utils

Utilities for maintaining a ComfyUI install and related repos. The scripts can live anywhere,
but placing this repo near your ComfyUI folder keeps paths tidy.

## What is included

- <img src="ico/update_comfy_repos_run.ico" width="16" height="16" alt=""> `update_comfy_repos.py` updates the main ComfyUI repo and every repo in `custom_nodes` (skips
  disabled folders), and writes a detailed change log with commit messages and file diffs.
- <img src="ico/update_workflow_repos_run.ico" width="16" height="16" alt=""> `update_workflow_repos.py` updates all Git repos under `user/default/workflows/github` and
  reports skipped non-repo folders.
- <img src="ico/comfyui_pip_update_audit_run.ico" width="16" height="16" alt=""> `comfyui_pip_update_audit.py` scans `requirements.txt` in the ComfyUI root and top-level
  custom nodes, compares installed vs latest versions, and prints update commands.
- <img src="ico/run_comfyui.ico" width="16" height="16" alt=""> `run_comfyui.bat` launches ComfyUI with venv selection and custom-nodes presets via junctions.
- <img src="ico/custom_nodes_link_manager_run.ico" width="16" height="16" alt=""> `custom_nodes_link_manager.py` manages custom nodes junction links (compare repo vs custom_nodes, add/remove).
- <img src="ico/partial_repo_sync_run.ico" width="16" height="16" alt=""> `partial_repo_sync.py` syncs selected files/folders from a git repo into a target folder.
- `requirements_checker/` provides a richer requirements audit with config-driven environment
  selection (venv/conda), custom paths, and per-package status reporting.
- <img src="ico/clone_workflow_repos_run.ico" width="16" height="16" alt=""> `clone-workflow_repos.py` clones workflow repos from `clone-workflow_repos.txt` into the
  workflows `github` folder (prompts for target path).
- `make_tmp_custom_nodes.py` generates `tmp_custom_nodes.json` with loaded/disabled nodes and
  their repo URLs, useful for collecting all plugins into a single list/repository.
- <img src="ico/png_to_json_run.ico" width="16" height="16" alt=""> `png_to_json.py` scans a folder of `.png/.jpeg` images, reads ComfyUI `workflow` metadata,
  and writes a matching `.json` file next to each image that contains a workflow.
- `comfyui_root.py` resolves the ComfyUI root using config, validation, and upward search.

## Path handling

- Scripts not assume a fixed folder layout.
- `comfyui_root.py` validates a root by checking `custom_nodes`, `models`, `main.py`,
  and `extra_model_paths.yaml.example`.
- If `Comfyui_root` is missing or invalid, the scripts search upward and save it to `config.json`.

## config.json template

Example with comments (JSONC). Remove comments in a real `config.json`.

```jsonc
{
  // ComfyUI root (auto-detected, but can be pinned)
  "Comfyui_root": "C:/ComfyUI/ComfyUI",
  // Alternative keys for the same value
  "comfyui_root": "C:/ComfyUI/ComfyUI",
  "COMFYUI_ROOT": "C:/ComfyUI/ComfyUI",

  // Single custom_nodes path (legacy, still supported)
  "custom_nodes_path": "C:/ComfyUI/ComfyUI/custom_nodes",
  // Multiple custom_nodes paths (new). Duplicates/junctions are de-duped.
  "custom_nodes_paths": [
    "D:/ComfyUI/custom_nodes",
    "E:/ComfyUI_nodes"
  ],

  // Environment type: "venv" or "conda"
  "env_type": "venv",

  // Venv: active path + known paths
  "venv_path": "C:/ComfyUI/venv",
  "venv_paths": [
    "C:/ComfyUI/venv",
    "D:/ComfyUI_envs/venv"
  ],

  // Conda: conda.exe path and environment name/folder
  "conda_path": "C:/Users/USER/miniconda3/Scripts/conda.exe",
  "conda_env": "comfyui",
  "conda_env_folder": "C:/Users/USER/miniconda3/envs/comfyui",

  // Optional project path (used by requirements_checker)
  "project_path": "C:/ComfyUI",

  // Path to custom nodes repository folder for run_comfyui.bat
  "custom_nodes_repo_path": "D:/ComfyUI/custom_nodes_repo",

  // Hold/Pin per environment (env_key = venv_path | conda_env_folder | conda_env | "default")
  "holds": {
    "C:/ComfyUI/venv": {
      "hold_packages": ["torch", "torchvision"],
      "pin_packages": {
        "numpy": "1.26.4"
      }
    },
    "conda:comfyui": {
      "hold_packages": ["xformers"],
      "pin_packages": {}
    }
  },

  // Legacy: no per-env scoping (still supported, but prefer "holds")
  "hold_packages": ["pkg1", "pkg2"],
  "pin_packages": {
    "pkg3": "1.2.3"
  }
}
```

## Launchers

- Windows: `*.bat` files.
- Linux: `*.sh` equivalents (same names, run with `bash` or `./file.sh`).

## Shortcuts

- Windows: `powershell -ExecutionPolicy Bypass -File create_windows_links.ps1` (creates `.lnk` in `run_windows`).
- Linux: `bash create_linux_links.sh` (creates `.desktop` in `run_linux`).

## Custom nodes manager (custom_nodes_link_manager.py)

### Concept: moving nodes

Core idea: keep all real nodes in a single `custom_nodes_repo` folder, while
`custom_nodes` contains only junction links. This gives one source of truth,
easier maintenance, and fast enable/disable of node sets without moving files.

### Utility

- Shows a compact two-column list of repo nodes; linked items are marked with `=>`.
- Numbering runs top-to-bottom per column (first column, then second).
- Commands: `a` add (all), `r` remove (all), `i` invert (all), `s` sync, `q` quit.
- Use `a <n>`, `r <n>`, `i <n>` to target specific indices.
- Index selection supports single numbers, ranges, and lists (`3`, `2-6`, `1,4,9`, `1 3-5`).
- Sync adds missing links and removes extra junctions.
- `custom_nodes_repo_path` comes from `config.json` or is requested.
- `custom_nodes_path`/`custom_nodes_paths` defines the target `custom_nodes`.

## Partial repo sync (partial_repo_sync.py)

- Syncs only selected files/directories from a git repo (not the full repo).
- Uses a local cache and git sparse-checkout, then copies selected paths into `target`.
- Jobs: `partial_repo_sync_config.json` (repo, branch, target, paths).
- `paths` can use regex patterns: `re:^styles/.*\\.json$` or `regex:^styles/` (git paths use `/`).
- Launcher: `partial_repo_sync_run.bat`.

## ComfyUI launcher (run_comfyui.bat)

- Reads ComfyUI root from `config.json` (`Comfyui_root`/`comfyui_root`/`COMFYUI_ROOT`) or searches upward.
- Lists `.venv*` folders and launches the selected `python.exe`.
- Applies custom nodes presets by creating junctions into `custom_nodes` from `custom_nodes_repo`.
- Cleans only junction folders; real directories are left intact.
- Presets are defined in `run_comfyui_presets_config.json` (`whitelist`/`blacklist` + `nodes` list).
- The `current` preset is a no-op.
- Creates a default `run_comfyui_presets_config.json` on first run if missing.
- Launch flags are defined in `run_comfyui_flags_config.json` as a list of objects (`name`, `keys`, `comment`).
- `current` is a list of active preset names. Press Enter to use it or enter numbers separated by spaces.
- Use `@no_update` in a preset to skip frontend package updates for that run.
- Updates ComfyUI frontend packages before launch.

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
> I currently work on Windows and venv environments. This combo is what gets tested.
> PS. I write these utilities for myself, try to update without breaking functionality, and add features as ideas appear.
