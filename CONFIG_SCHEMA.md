# config.json schema v2

## Goals
- One place for all settings of one `venv`.
- Minimal data duplication.
- Safe automatic migration from legacy flat config.
- Backward-compatible reads for existing scripts.

## Top-level structure
```json
{
  "schema_version": 2,
  "paths": {
    "comfyui_root": "",
    "custom_nodes_path": "",
    "custom_nodes_repo_path": "",
    "custom_nodes_paths": []
  },
  "runtime": {
    "env_type": "venv",
    "selected": { "kind": "venv", "name": "" }
  },
  "defaults": {
    "env_vars": {}
  },
  "environments": {
    "venvs": {
      "<venv_name>": {
        "path": "",
        "cuda_path": "",
        "env_vars": {},
        "pip": {
          "hold_packages": [],
          "pin_packages": {}
        }
      }
    },
    "conda": {
      "path": "",
      "env": "",
      "env_folder": "",
      "cuda_path": "",
      "env_vars": {},
      "pip": {
        "hold_packages": [],
        "pin_packages": {}
      }
    }
  },
  "extras": {}
}
```

## Fill rules
1. Put global paths only into `paths`.
2. Put shared env vars into `defaults.env_vars`.
3. Put per-venv settings only into `environments.venvs.<name>`.
4. Keep current selection only in `runtime.selected`.
5. Put non-standard legacy/custom keys into `extras`.
6. Do not duplicate `venv` data in multiple places.

## Migration
- Use `python config_cli.py ensure`.
- Migration runs automatically on first access in updated scripts.
- Legacy keys (`venv_path`, `venv_paths`, `holds`, `env_by_venv`, etc.) are converted into v2.
