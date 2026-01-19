from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


CONFIG_ROOT_KEY = "Comfyui_root"
_ALT_KEYS = ("comfyui_root", "COMFYUI_ROOT")


def _load_config(path: str) -> Dict[str, object]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config(path: str, cfg: Dict[str, object]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)


def _get_config_root(cfg: Dict[str, object]) -> Optional[str]:
    raw = cfg.get(CONFIG_ROOT_KEY)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    for k in _ALT_KEYS:
        raw = cfg.get(k)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _set_config_root(config_path: str, root: Path) -> None:
    cfg = _load_config(config_path)
    cfg[CONFIG_ROOT_KEY] = str(root)
    _save_config(config_path, cfg)


def validate_root(path: Path) -> Tuple[bool, List[str]]:
    """
    Consider the root valid if all required markers exist:
    - custom_nodes directory
    - models directory
    - main.py file
    - extra_model_paths.yaml.example file
    """
    reasons: List[str] = []
    if not (path / "custom_nodes").is_dir():
        reasons.append("missing custom_nodes")
    if not (path / "models").is_dir():
        reasons.append("missing models")
    if not (path / "main.py").is_file():
        reasons.append("missing main.py")
    if not (path / "extra_model_paths.yaml.example").is_file():
        reasons.append("missing extra_model_paths.yaml.example")

    return not reasons, reasons


def find_root_upwards(start: Path, max_levels: int = 6) -> Optional[Path]:
    candidates = [start] + list(start.parents)
    for idx, p in enumerate(candidates):
        if idx > max_levels:
            break
        ok, _ = validate_root(p)
        if ok:
            return p
    return None


def resolve_comfyui_root(
    config_path: str,
    cli_root: Optional[str] = None,
    start_path: Optional[Path] = None,
) -> Path:
    if cli_root:
        root = Path(cli_root).expanduser().resolve()
        ok, reasons = validate_root(root)
        if ok:
            _set_config_root(config_path, root)
            return root
        raise SystemExit(f"Invalid ComfyUI root: {root} ({', '.join(reasons)})")

    cfg = _load_config(config_path)
    cfg_root = _get_config_root(cfg)
    if cfg_root:
        root = Path(cfg_root).expanduser().resolve()
        ok, _ = validate_root(root)
        if ok:
            return root

    start = start_path or Path.cwd()
    found = find_root_upwards(start)
    if found:
        _set_config_root(config_path, found)
        return found

    if sys.stdin.isatty():
        while True:
            raw = input("Enter ComfyUI root path (or 'NO' to exit): ").strip()
            if raw.upper() == "NO":
                raise SystemExit("ComfyUI root not provided.")
            if not raw:
                continue
            root = Path(raw).expanduser().resolve()
            ok, reasons = validate_root(root)
            if ok:
                _set_config_root(config_path, root)
                return root
            print(f"Invalid ComfyUI root: {root} ({', '.join(reasons)})")

    raise SystemExit(
        "ComfyUI root not found. Set Comfyui_root in config.json or pass --comfyui-root."
    )


def default_custom_nodes(root: Path) -> Path:
    return root / "custom_nodes"


def default_workflows_dir(root: Path) -> Path:
    return root / "user" / "default" / "workflows" / "github"
