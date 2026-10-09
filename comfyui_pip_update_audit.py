#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ComfyUI environment updater and audit (pip) — streamlined

- Scans only requirements.txt files:
  * In the ComfyUI root (top level only)
  * In each top-level plugin directory (ignores *.disable; no subfolder recursion)
- Detects comfy-env plugin configs:
  * comfy-env-root.toml at plugin root (host/runtime helper, no isolation by itself)
  * comfy-env.toml in plugin subdirectories (isolated pixi env managed by install.py)
- Merges duplicate constraints
- Installed color codes:
    RED     — not installed
    GREEN   — correct version
    CYAN    — upgrade available
    YELLOW  — downgrade suggested
- "Update" appears only when actions are actually needed
- Added stages and progress bars
"""

from __future__ import annotations
import argparse
import json
import os
import re
import sys
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from config_schema import load_legacy_compat, save_legacy_compat

try:
    from importlib import metadata as importlib_metadata
except ImportError:  # python<3.8
    import importlib_metadata  # type: ignore

def _canonicalize_simple(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _norm_path_simple(path: str) -> str:
    return os.path.normpath(path.strip())


def _load_venv_paths_simple(cfg: dict) -> list[str]:
    paths: list[str] = []
    venv_paths = cfg.get("venv_paths")
    if isinstance(venv_paths, list):
        for p in venv_paths:
            if isinstance(p, str) and p.strip():
                paths.append(p.strip())
    venv_path = cfg.get("venv_path")
    if isinstance(venv_path, str) and venv_path.strip():
        paths.append(venv_path.strip())
    # de-dup
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        norm = os.path.normcase(os.path.normpath(p))
        if norm in seen:
            continue
        seen.add(norm)
        out.append(os.path.normpath(p))
    return out


def _venv_name_from_path(path: str) -> str:
    p = os.path.normpath(path.strip())
    return os.path.basename(p.rstrip("\\/")) or p


def _load_venv_comments(cfg: dict) -> dict[str, str]:
    raw = cfg.get("venv_comments")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        key = k.strip()
        if not key:
            continue
        out[key] = str(v).strip() if v is not None else ""
    return out


def _comment_for_venv_path(cfg: dict, path: str) -> str:
    comments = _load_venv_comments(cfg)
    return comments.get(_venv_name_from_path(path), "")


def _set_venv_comment(cfg: dict, path: str, comment: str) -> None:
    comments = _load_venv_comments(cfg)
    comments[_venv_name_from_path(path)] = comment.strip()
    cfg["venv_comments"] = comments


def _remove_venv_comment(cfg: dict, path: str) -> None:
    comments = _load_venv_comments(cfg)
    comments.pop(_venv_name_from_path(path), None)
    cfg["venv_comments"] = comments


def _sync_venv_name_by_path(cfg: dict, paths: list[str]) -> dict[str, str]:
    raw = cfg.get("venv_name_by_path")
    name_by_path: dict[str, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
                name_by_path[_norm_path_simple(k)] = v.strip()
    normalized_paths = [_norm_path_simple(p) for p in paths]
    existing_keys = set(normalized_paths)
    # prune removed paths
    for k in list(name_by_path.keys()):
        if _norm_path_simple(k) not in existing_keys:
            name_by_path.pop(k, None)
    for p in normalized_paths:
        if p not in name_by_path:
            name_by_path[p] = _venv_name_from_path(p)
    cfg["venv_name_by_path"] = name_by_path
    return name_by_path


def _current_venv_path(cfg: dict, paths: list[str]) -> str:
    selected_name = cfg.get("venv_selected_name")
    if isinstance(selected_name, str) and selected_name.strip():
        target = selected_name.strip()
        name_map = cfg.get("venv_name_by_path")
        if isinstance(name_map, dict):
            for raw_path, raw_name in name_map.items():
                if isinstance(raw_path, str) and isinstance(raw_name, str) and raw_name.strip() == target:
                    for p in paths:
                        if os.path.normcase(os.path.normpath(p)) == os.path.normcase(os.path.normpath(raw_path)):
                            return p
    raw_current = cfg.get("venv_path")
    return raw_current.strip() if isinstance(raw_current, str) else ""


def _print_venv_table(cfg: dict, paths: list[str], current_norm: str) -> None:
    if not paths:
        return
    idx_w = max(1, len(str(len(paths))))
    path_w = max(len(p) for p in paths)
    for idx, p in enumerate(paths, 1):
        mark = "*" if current_norm and os.path.normcase(os.path.normpath(p)) == current_norm else " "
        comment = _comment_for_venv_path(cfg, p)
        cmt = comment if comment else "-"
        print(f" {idx:>{idx_w}}){mark} {p:<{path_w}} | {cmt}")


def _confirm_venv_path_simple(path: str) -> bool:
    if not os.path.isdir(path):
        print("Path does not exist.")
        return False
    return True


def _select_venv_simple(cfg: dict) -> dict:
    paths = _load_venv_paths_simple(cfg)
    _sync_venv_name_by_path(cfg, paths)
    current = _current_venv_path(cfg, paths)
    current_norm = os.path.normcase(os.path.normpath(current)) if current else ""
    if not paths and not sys.stdin.isatty():
        raise SystemExit("No venv paths found in config.json and input is non-interactive.")

    while True:
        missing = [p for p in paths if not os.path.isdir(p)]
        if missing:
            print("Missing venv paths in config:")
            for p in missing:
                print(f" - {p}")
            ans = input("Remove missing entries? [Y/n]: ").strip().lower()
            if ans in ("", "y", "yes"):
                for mp in missing:
                    paths = [x for x in paths if os.path.normcase(os.path.normpath(x)) != os.path.normcase(os.path.normpath(mp))]
                    _remove_venv_comment(cfg, mp)
                cfg["venv_paths"] = paths
                if current and os.path.normcase(os.path.normpath(current)) in {
                    os.path.normcase(os.path.normpath(m)) for m in missing
                }:
                    current = ""
                    current_norm = ""

        print("--> Select venv for updates <--")
        _print_venv_table(cfg, paths, current_norm)
        print("\nA=add new, D=delete, C=comment")
        if paths:
            default_idx = 1
            if current_norm:
                for i, p in enumerate(paths, 1):
                    if os.path.normcase(os.path.normpath(p)) == current_norm:
                        default_idx = i
                        break
            choice = input(f"Choice [{default_idx}]: ").strip()
        else:
            choice = input("Choice [A]: ").strip()

        if not choice:
            if paths:
                selected = paths[default_idx - 1]
                if _confirm_venv_path_simple(selected):
                    break
                continue
            choice = "A"

        if choice.lower() in ("a", "add", "n", "new"):
            new_path = input("New venv path: ").strip()
            if not new_path:
                print("No path provided.")
                continue
            new_path = _norm_path_simple(new_path)
            if not _confirm_venv_path_simple(new_path):
                continue
            paths = _load_venv_paths_simple({"venv_paths": paths + [new_path]})
            new_comment = input("Comment (optional): ").strip()
            if new_comment:
                _set_venv_comment(cfg, new_path, new_comment)
            selected = new_path
            break

        if choice.lower() in ("d", "del", "delete"):
            if not paths:
                print("No entries to delete.")
                continue
            raw = input("Index to delete: ").strip()
            if not raw.isdigit():
                print("Invalid index.")
                continue
            idx = int(raw)
            if idx < 1 or idx > len(paths):
                print("Invalid index.")
                continue
            removed = paths[idx - 1]
            paths = [p for i, p in enumerate(paths, 1) if i != idx]
            _remove_venv_comment(cfg, removed)
            if current_norm and os.path.normcase(os.path.normpath(removed)) == current_norm:
                current = ""
                current_norm = ""
            continue

        if choice.lower() in ("c", "comment"):
            if not paths:
                print("No entries to edit.")
                continue
            raw = input("Index for comment: ").strip()
            if not raw.isdigit():
                print("Invalid index.")
                continue
            idx = int(raw)
            if idx < 1 or idx > len(paths):
                print("Invalid index.")
                continue
            target = paths[idx - 1]
            new_comment = input("New comment (empty to clear): ").strip()
            _set_venv_comment(cfg, target, new_comment)
            continue

        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(paths):
                selected = paths[idx - 1]
                if not _confirm_venv_path_simple(selected):
                    continue
                break

        print("Invalid choice, try again.")

    cfg["venv_path"] = selected
    cfg["venv_paths"] = paths
    name_map = _sync_venv_name_by_path(cfg, paths)
    cfg["venv_selected_name"] = name_map.get(_norm_path_simple(selected), _venv_name_from_path(selected))
    return cfg


def _norm_env_key_simple(path: str) -> str:
    return os.path.normcase(os.path.normpath(path.strip()))


def _get_env_key_simple(cfg: dict) -> str:
    env_type = (cfg.get("env_type") or "").lower()
    if env_type == "venv":
        venv = cfg.get("venv_path") or ""
        if isinstance(venv, str) and venv.strip():
            return _norm_env_key_simple(venv)
    conda_folder = cfg.get("conda_env_folder") or ""
    if isinstance(conda_folder, str) and conda_folder.strip():
        return _norm_env_key_simple(conda_folder)
    conda_env = cfg.get("conda_env") or ""
    if isinstance(conda_env, str) and conda_env.strip():
        return f"conda:{conda_env.strip()}"
    return "default"


def _confirm_default_env_simple(env_key: str) -> None:
    if env_key != "default":
        return
    if not sys.stdin.isatty():
        raise SystemExit("Environment not selected (default). Run interactively or set env in config.json.")
    print("No environment selected (using default).")
    ans = input("Continue and write to default hold/pin? [y/N]: ").strip().lower()
    if ans not in ("y", "yes"):
        raise SystemExit("Cancelled by user.")


# For early --pin without version, resolve via selected env if possible
def _get_version_simple(cfg: dict, pkg: str) -> str:
    env_type = (cfg.get("env_type") or "").lower()
    if env_type == "venv":
        venv = cfg.get("venv_path") or ""
        if isinstance(venv, str) and venv.strip():
            py = os.path.join(venv, "Scripts", "python.exe")
            if os.path.exists(py):
                try:
                    p = subprocess.run([py, "-c", f"import importlib.metadata as m; print(m.version('''{pkg}'''))"],
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=20)
                    if p.returncode == 0 and p.stdout:
                        return p.stdout.strip()
                except Exception:
                    pass
    return importlib_metadata.version(pkg)


def _split_items(raw_items: list[str]) -> list[str]:
    items: list[str] = []
    for raw in raw_items:
        for item in [p.strip() for p in raw.split(",") if p.strip()]:
            items.append(item)
    return items


def _load_json(path: str) -> dict:
    data, _ = load_legacy_compat(path, auto_migrate=True)
    return data if isinstance(data, dict) else {}


def _save_json(path: str, data: dict) -> None:
    save_legacy_compat(path, data)


def _ensure_env_entry(cfg: dict, env_key: str) -> dict:
    holds = cfg.setdefault("holds", {})
    if not isinstance(holds, dict):
        holds = {}
        cfg["holds"] = holds
    entry = holds.setdefault(env_key, {})
    if not isinstance(entry, dict):
        entry = {}
        holds[env_key] = entry
    return entry


def _early_hold_pin() -> bool:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--hold", dest="hold_pkg", nargs="+", default=None)
    parser.add_argument("--pin", dest="pin_pkg", nargs="+", default=None)
    parser.add_argument("--unhold", dest="unhold_pkg", nargs="+", default=None)
    parser.add_argument("--unpin", dest="unpin_pkg", nargs="+", default=None)
    args, _ = parser.parse_known_args()
    if not args.hold_pkg and not args.pin_pkg and not args.unhold_pkg and not args.unpin_pkg:
        return False

    script_dir = Path(__file__).resolve().parent
    cfg_path = str(script_dir / "config.json")
    cfg = _load_json(cfg_path)
    if (cfg.get("env_type") or "").lower() == "venv":
        cfg = _select_venv_simple(cfg)
        _save_json(cfg_path, cfg)
    env_key = _get_env_key_simple(cfg)
    _confirm_default_env_simple(env_key)

    entry = _ensure_env_entry(cfg, env_key)
    if args.hold_pkg:
        hold_list = entry.setdefault("hold_packages", [])
        if not isinstance(hold_list, list):
            hold_list = []
            entry["hold_packages"] = hold_list
        added: list[str] = []
        for item in _split_items(args.hold_pkg):
            pkg = _canonicalize_simple(item)
            if pkg not in [_canonicalize_simple(p) for p in hold_list if isinstance(p, str)]:
                hold_list.append(pkg)
            added.append(item)
        _save_json(cfg_path, cfg)
        if added:
            print(f"Hold added for env '{env_key}': " + ", ".join(added))

    if args.pin_pkg:
        pins = entry.setdefault("pin_packages", {})
        if not isinstance(pins, dict):
            pins = {}
            entry["pin_packages"] = pins
        pinned: list[str] = []
        for raw in _split_items(args.pin_pkg):
            if "==" in raw:
                pkg, ver = raw.split("==", 1)
                pkg = pkg.strip()
                ver = ver.strip()
            else:
                pkg = raw
                try:
                    ver = _get_version_simple(cfg, pkg)
                except Exception:
                    raise SystemExit(f"Package not installed, cannot pin without version: {pkg}")
            pins[_canonicalize_simple(pkg)] = ver
            pinned.append(f"{pkg}=={ver}")
        _save_json(cfg_path, cfg)
        if pinned:
            print(f"Pin set for env '{env_key}': " + ", ".join(pinned))

    if args.unhold_pkg:
        hold_list = entry.get("hold_packages", [])
        if not isinstance(hold_list, list):
            hold_list = []
            entry["hold_packages"] = hold_list
        to_remove = {_canonicalize_simple(p) for p in _split_items(args.unhold_pkg)}
        if to_remove:
            hold_list[:] = [p for p in hold_list if _canonicalize_simple(str(p)) not in to_remove]
            _save_json(cfg_path, cfg)
            print(f"Hold removed for env '{env_key}': " + ", ".join(sorted(to_remove)))

    if args.unpin_pkg:
        pins = entry.get("pin_packages", {})
        if not isinstance(pins, dict):
            pins = {}
            entry["pin_packages"] = pins
        to_remove = {_canonicalize_simple(p) for p in _split_items(args.unpin_pkg)}
        if to_remove:
            for k in list(pins.keys()):
                if _canonicalize_simple(str(k)) in to_remove:
                    pins.pop(k, None)
            _save_json(cfg_path, cfg)
            print(f"Pin removed for env '{env_key}': " + ", ".join(sorted(to_remove)))

    return True


if _early_hold_pin():
    sys.exit(0)

from packaging.version import Version
from packaging.utils import canonicalize_name
from colorama import init as colorama_init, Fore, Style

from comfyui_root import default_custom_nodes, resolve_comfyui_root
from envtools import (
    custom_nodes_paths,
    dedupe_dirs,
    install_prefix,
    python_cmd,
    quote_cmd_part,
    unique_paths,
)
from pip_audit_core import (
    dedupe_pkg_versions,
    format_comfy_env_workspace_install_cmd,
    inst_ver_from_map,
    load_installed_dists,
    need_vcs_install,
    package_action,
    package_unchanged,
    run_audit,
    wheel_local_suffix_for_env,
)
colorama_init(autoreset=True)


# --------- utils ---------
def load_or_init_config(path: str) -> dict:
    """
    Ensure config.json exists and contains the fields this script needs.
    Uses requirements_checker's interactive prompts when available.
    """
    try:
        # Import locally to avoid hard dependency if the package is moved
        from requirements_checker.config_manager import ConfigManager  # type: ignore
    except Exception:
        if not os.path.exists(path):
            save_legacy_compat(path, {})
            print(f"Config file created at {path}. Please fill required fields manually.")
        cfg, _ = load_legacy_compat(path, auto_migrate=True)
        return cfg

    cm = ConfigManager(path)
    try:
        cfg = cm.read_config()
    except json.JSONDecodeError:
        cfg = {}
        cm.write_config(cfg)

    # Prompt for missing essentials so scripts can run on a fresh machine
    env_type = cm.get_value("env_type")
    if env_type == "conda":
        for key in ("conda_path", "conda_env", "conda_env_folder"):
            cm.get_value(key)
    elif env_type == "venv":
        cfg_now = cm.read_config()
        if not cfg_now.get("venv_path") and not cfg_now.get("venv_paths"):
            cm.get_value("venv_path")

    cfg_now = cm.read_config()
    if not cfg_now.get("custom_nodes_path") and not cfg_now.get("custom_nodes_paths"):
        cm.get_value("custom_nodes_path")
    return cm.read_config()


def guess_paths(cfg: dict, comfy_root: Optional[str] = None) -> Tuple[str, List[str]]:
    root = str(comfy_root) if comfy_root else ""
    paths = custom_nodes_paths(cfg, root)
    if paths:
        return root or os.path.dirname(paths[0].rstrip("\\/")), paths
    sys.exit("custom_nodes_path missing in config.json")


def _norm_path(path: str) -> str:
    return os.path.normpath(path.strip())


def _get_custom_nodes_repo_path(cfg: dict) -> str:
    raw = cfg.get("custom_nodes_repo_path")
    if not isinstance(raw, str):
        return ""
    path = raw.strip()
    if not path:
        return ""
    norm = os.path.normpath(path)
    if not os.path.isdir(norm):
        return ""
    return norm


def _ask_include_custom_nodes_repo_path(path: str, show_commented: bool = False) -> Tuple[bool, bool]:
    if not path:
        return False, show_commented
    if not sys.stdin.isatty():
        return False, show_commented
    print(Fore.MAGENTA + Style.BRIGHT + "--> Optional requirements source <--" + Style.RESET_ALL)
    print(f"custom_nodes_repo_path found: {path}")
    print("Commented packages log: " + (Fore.GREEN + "ON" if show_commented else Fore.YELLOW + "OFF") + Style.RESET_ALL)
    while True:
        choice = input("Input: '+' add custom path, 'c' add comments from requirements, Enter skip: ").strip().lower()
        if not choice:
            return False, show_commented
        compact = "".join(choice.split())
        add_path = "+" in compact
        enable_comments = ("c" in compact) or show_commented
        if add_path or ("c" in compact):
            return add_path, enable_comments
        print("Use '+', 'c', '+c' or press Enter.")


def _load_venv_paths(cfg: dict) -> List[str]:
    paths: List[str] = []
    venv_paths = cfg.get("venv_paths")
    if isinstance(venv_paths, list):
        for p in venv_paths:
            if isinstance(p, str) and p.strip():
                paths.append(p.strip())
    venv_path = cfg.get("venv_path")
    if isinstance(venv_path, str) and venv_path.strip():
        paths.append(venv_path.strip())
    return unique_paths(paths)


def _save_config(path: str, cfg: dict) -> None:
    save_legacy_compat(path, cfg)


def _venv_has_pip(path: str) -> bool:
    pip_exe = os.path.join(path, "Scripts", "pip.exe")
    pip_bin = os.path.join(path, "bin", "pip")
    return os.path.exists(pip_exe) or os.path.exists(pip_bin)


def _confirm_venv_path(path: str) -> bool:
    if not os.path.isdir(path):
        print("Path does not exist.")
        return False
    if not _venv_has_pip(path):
        ans = input("Pip not found in this venv. Use anyway? [y/N]: ").strip().lower()
        if ans not in ("y", "yes"):
            return False
    return True


def _norm_env_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path.strip()))


def _get_env_key(cfg: dict) -> str:
    env_type = (cfg.get("env_type") or "").lower()
    if env_type == "venv":
        venv = cfg.get("venv_path") or ""
        if isinstance(venv, str) and venv.strip():
            return _norm_env_key(venv)
    conda_folder = cfg.get("conda_env_folder") or ""
    if isinstance(conda_folder, str) and conda_folder.strip():
        return _norm_env_key(conda_folder)
    conda_env = cfg.get("conda_env") or ""
    if isinstance(conda_env, str) and conda_env.strip():
        return f"conda:{conda_env.strip()}"
    return "default"


def _load_hold_config(cfg: dict) -> Tuple[List[str], Dict[str, str]]:
    holds = cfg.get("holds")
    if isinstance(holds, dict):
        env_key = _get_env_key(cfg)
        entry = holds.get(env_key)
        if isinstance(entry, dict):
            hold_packages = entry.get("hold_packages")
            pin_packages = entry.get("pin_packages")
            hold_list = hold_packages if isinstance(hold_packages, list) else []
            pin_map = pin_packages if isinstance(pin_packages, dict) else {}
            return hold_list, pin_map
    hold_packages = cfg.get("hold_packages")
    pin_packages = cfg.get("pin_packages")
    hold_list = hold_packages if isinstance(hold_packages, list) else []
    pin_map = pin_packages if isinstance(pin_packages, dict) else {}
    return hold_list, pin_map


def _upsert_hold_entry(cfg: dict, package: str) -> None:
    env_key = _get_env_key(cfg)
    holds = cfg.setdefault("holds", {})
    if not isinstance(holds, dict):
        holds = {}
        cfg["holds"] = holds
    entry = holds.setdefault(env_key, {})
    if not isinstance(entry, dict):
        entry = {}
        holds[env_key] = entry
    lst = entry.setdefault("hold_packages", [])
    if not isinstance(lst, list):
        lst = []
        entry["hold_packages"] = lst
    pkg = canonicalize_name(package)
    if pkg not in [canonicalize_name(p) for p in lst if isinstance(p, str)]:
        lst.append(pkg)


def _upsert_pin_entry(cfg: dict, package: str, version: str) -> None:
    env_key = _get_env_key(cfg)
    holds = cfg.setdefault("holds", {})
    if not isinstance(holds, dict):
        holds = {}
        cfg["holds"] = holds
    entry = holds.setdefault(env_key, {})
    if not isinstance(entry, dict):
        entry = {}
        holds[env_key] = entry
    pins = entry.setdefault("pin_packages", {})
    if not isinstance(pins, dict):
        pins = {}
        entry["pin_packages"] = pins
    pins[canonicalize_name(package)] = version


def _confirm_default_env(env_key: str) -> None:
    if env_key != "default":
        return
    if not sys.stdin.isatty():
        raise SystemExit("Environment not selected (default). Run interactively or set env in config.json.")
    print("No environment selected (using default).")
    ans = input("Continue and write to default hold/pin? [y/N]: ").strip().lower()
    if ans not in ("y", "yes"):
        raise SystemExit("Cancelled by user.")


def select_venv(cfg: dict, config_path: str) -> dict:
    paths = _load_venv_paths(cfg)
    _sync_venv_name_by_path(cfg, paths)
    current = _current_venv_path(cfg, paths)
    current_norm = os.path.normcase(os.path.normpath(current)) if current else ""
    if not paths:
        print("No venv paths found in config.")

    while True:
        missing = [p for p in paths if not os.path.isdir(p)]
        if missing:
            print("Missing venv paths in config:")
            for p in missing:
                print(f" - {p}")
            ans = input("Remove missing entries? [Y/n]: ").strip().lower()
            if ans in ("", "y", "yes"):
                for mp in missing:
                    paths = [x for x in paths if os.path.normcase(os.path.normpath(x)) != os.path.normcase(os.path.normpath(mp))]
                    _remove_venv_comment(cfg, mp)
                cfg["venv_paths"] = paths
                if current and os.path.normcase(os.path.normpath(current)) in {
                    os.path.normcase(os.path.normpath(m)) for m in missing
                }:
                    current = ""
                    current_norm = ""

        print(Fore.MAGENTA + Style.BRIGHT + "--> Select venv for updates <--" + Style.RESET_ALL)
        _print_venv_table(cfg, paths, current_norm)
        print("\nA=add new, D=delete, C=comment")
        if paths:
            default_idx = 1
            if current_norm:
                for i, p in enumerate(paths, 1):
                    if os.path.normcase(os.path.normpath(p)) == current_norm:
                        default_idx = i
                        break
            choice = input(f"Choice [{default_idx}]: ").strip()
        else:
            choice = input("Choice [A]: ").strip()

        if not choice:
            if paths:
                selected = paths[default_idx - 1]
                if _confirm_venv_path(selected):
                    break
                continue
            choice = "A"

        if choice.lower() in ("a", "add", "n", "new"):
            new_path = input("New venv path: ").strip()
            if not new_path:
                print("No path provided.")
                continue
            new_path = _norm_path(new_path)
            if not _confirm_venv_path(new_path):
                continue
            paths = unique_paths(paths + [new_path])
            new_comment = input("Comment (optional): ").strip()
            if new_comment:
                _set_venv_comment(cfg, new_path, new_comment)
            selected = new_path
            break

        if choice.lower() in ("d", "del", "delete"):
            if not paths:
                print("No entries to delete.")
                continue
            raw = input("Index to delete: ").strip()
            if not raw.isdigit():
                print("Invalid index.")
                continue
            idx = int(raw)
            if idx < 1 or idx > len(paths):
                print("Invalid index.")
                continue
            removed = paths[idx - 1]
            paths = [p for i, p in enumerate(paths, 1) if i != idx]
            _remove_venv_comment(cfg, removed)
            if current_norm and os.path.normcase(os.path.normpath(removed)) == current_norm:
                current = ""
                current_norm = ""
            continue

        if choice.lower() in ("c", "comment"):
            if not paths:
                print("No entries to edit.")
                continue
            raw = input("Index for comment: ").strip()
            if not raw.isdigit():
                print("Invalid index.")
                continue
            idx = int(raw)
            if idx < 1 or idx > len(paths):
                print("Invalid index.")
                continue
            target = paths[idx - 1]
            new_comment = input("New comment (empty to clear): ").strip()
            _set_venv_comment(cfg, target, new_comment)
            continue

        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(paths):
                selected = paths[idx - 1]
                if not _confirm_venv_path(selected):
                    continue
                break

        print("Invalid choice, try again.")

    cfg["venv_path"] = selected
    cfg["venv_paths"] = paths
    name_map = _sync_venv_name_by_path(cfg, paths)
    cfg["venv_selected_name"] = name_map.get(_norm_path(selected), _venv_name_from_path(selected))
    try:
        _save_config(config_path, cfg)
    except Exception as e:
        print(f"Warning: failed to update config.json: {e}")
    return cfg


def progress(label: str, i: int, n: int, suffix: str = "") -> None:
    # Render a single-line progress bar and clean up leftovers from longer prior lines
    if not hasattr(progress, "_last_len"):
        progress._last_len = 0  # type: ignore[attr-defined]

    w = 28
    i = max(0, min(i, n))  # clamp
    pct = 0 if not n else int((i / n) * 100)
    filled = 0 if not n else int((i / n) * w)
    bar = "#" * filled + "-" * (w - filled)
    if suffix:
        line = f"{label} [{bar}] {i}/{n} ({pct}%) ({suffix})"
    else:
        line = f"{label} [{bar}] {i}/{n} ({pct}%)"

    # Clear any remnants of a previous longer line
    pad = progress._last_len - len(line)  # type: ignore[attr-defined]
    if pad < 0:
        pad = 0
    sys.stdout.write("\r" + line + (" " * pad))
    sys.stdout.flush()
    progress._last_len = len(line)  # type: ignore[attr-defined]

    if i >= n and n:
        sys.stdout.write("\n")
        sys.stdout.flush()
        progress._last_len = 0  # type: ignore[attr-defined]


# --------- main ---------
def main() -> None:
    here = os.path.abspath(os.path.dirname(__file__))
    cfg_path = os.path.join(here, "config.json")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--hold",
        dest="hold_pkg",
        nargs="+",
        default=None,
        help="Add package(s) to hold list",
    )
    parser.add_argument(
        "--pin",
        dest="pin_pkg",
        nargs="+",
        default=None,
        help="Pin package(s), use name or name==ver",
    )
    parser.add_argument(
        "--classify-workers",
        dest="classify_workers",
        type=int,
        default=None,
        help="Max parallel workers for pip --dry-run candidate checks (default: auto)",
    )
    parser.add_argument(
        "--pip",
        dest="force_pip",
        action="store_true",
        help="Use pip instead of uv for --dry-run checks and printed install commands",
    )
    parser.add_argument(
        "--hide-unchanged",
        dest="hide_unchanged",
        action="store_true",
        help="Do not print packages that need no action",
    )
    parser.add_argument(
        "-c",
        "--show-commented",
        dest="show_commented",
        action="store_true",
        help="Show commented package entries from requirements in the final log",
    )
    args, _ = parser.parse_known_args()

    cfg = load_or_init_config(cfg_path)
    comfy_root = resolve_comfyui_root(cfg_path, start_path=Path(here))
    if not cfg.get("custom_nodes_path") and not cfg.get("custom_nodes_paths"):
        cn_path = default_custom_nodes(comfy_root)
        if cn_path.is_dir():
            cfg["custom_nodes_path"] = str(cn_path)
            try:
                _save_config(cfg_path, cfg)
            except Exception:
                pass
    if (cfg.get("env_type") or "").lower() == "venv":
        cfg = select_venv(cfg, cfg_path)
    comfy_root, custom_nodes_paths = guess_paths(cfg, comfy_root=str(comfy_root))
    pip = install_prefix(cfg, force_pip=args.force_pip)
    installed_map = load_installed_dists(cfg)

    if args.hold_pkg or args.pin_pkg:
        env_key = _get_env_key(cfg)
        _confirm_default_env(env_key)
        if args.hold_pkg:
            added: List[str] = []
            for raw in args.hold_pkg:
                for item in [p.strip() for p in raw.split(",") if p.strip()]:
                    _upsert_hold_entry(cfg, item)
                    added.append(item)
            _save_config(cfg_path, cfg)
            if added:
                print(f"Hold added for env '{env_key}': " + ", ".join(added))
            return
        if args.pin_pkg:
            pinned: List[str] = []
            for raw_group in args.pin_pkg:
                for raw in [p.strip() for p in raw_group.split(",") if p.strip()]:
                    if "==" in raw:
                        pkg, ver = raw.split("==", 1)
                        pkg = pkg.strip()
                        ver = ver.strip()
                    else:
                        pkg = raw
                        inst = inst_ver_from_map(installed_map, pkg)
                        if not inst:
                            raise SystemExit(f"Package not installed, cannot pin without version: {pkg}")
                        ver = str(inst)
                    _upsert_pin_entry(cfg, pkg, ver)
                    pinned.append(f"{pkg}=={ver}")
            _save_config(cfg_path, cfg)
            if pinned:
                print(f"Pin set for env '{env_key}': " + ", ".join(pinned))
            return

    hold_list, pin_map_raw = _load_hold_config(cfg)
    hold_set = {canonicalize_name(p) for p in hold_list if isinstance(p, str) and p.strip()}
    pin_map: Dict[str, str] = {}
    for k, v in pin_map_raw.items():
        if not isinstance(k, str) or not k.strip():
            continue
        if not isinstance(v, str) or not v.strip():
            continue
        pin_map[canonicalize_name(k)] = v.strip()

    script_path = str(Path(__file__).resolve())
    wheel_local_suffix = wheel_local_suffix_for_env(cfg)
    print(Fore.MAGENTA + Style.BRIGHT + "--> Some useful commands <--" + Style.RESET_ALL)
    print("wheel local suffix - " + Fore.BLUE + wheel_local_suffix + Style.RESET_ALL)
    print("uv is preferred: target a venv with --python <path to python.exe>, or run inside an activated venv\n")
    useful = [
        ("check package info", "pip show <PACKAGE>", "uv pip show <PACKAGE>"),
        ("list installed packages", "pip list", "uv pip list"),
        ("get all available versions of package", "pip index versions <PACKAGE>", "(no uv equivalent, use pip)"),
        ("try resolver without installing", "pip install <PACKAGE> --dry-run", "uv pip install <PACKAGE> --dry-run"),
        ("lock the current set of packages", "pip freeze > constraints.txt", "uv pip freeze > constraints.txt"),
        ("install without touching locked packages", "pip install <PACKAGE> -c constraints.txt --upgrade-strategy only-if-needed", "uv pip install <PACKAGE> -c constraints.txt"),
        ("update one package, dependencies untouched unless required (uv --upgrade would upgrade them all)", "pip install <PACKAGE>==<VERSION>", "uv pip install <PACKAGE>==<VERSION>"),
        ("install a prebuilt wheel without dependencies", "pip install --no-deps <file.whl>", "uv pip install --no-deps <file.whl>"),
        ("install torch for a CUDA version", "pip install torch --index-url https://download.pytorch.org/whl/cu130", "uv pip install torch --torch-backend cu130"),
        ("resolve requirements into a lock without installing", "-", "uv pip compile requirements.txt -o lock.txt"),
        ("check all errors in venv", "pip check", "uv pip check"),
        ("deep dependencies of a package", "pipdeptree -p <PACKAGE>", "uv pip tree --package <PACKAGE>"),
        ("reverse dependencies of a package", "pipdeptree --reverse --packages <PACKAGE>", "uv pip tree --invert --package <PACKAGE>"),
        ("remove all items from the cache", "pip cache purge", "uv cache clean"),
    ]
    for label, pip_c, uv_c in useful:
        print(f"{label}:\n  pip - " + Fore.BLUE + pip_c + Style.RESET_ALL + "\n  uv  - " + Fore.BLUE + uv_c + Style.RESET_ALL)
    print()
    print(Fore.MAGENTA + Style.BRIGHT + "--> Hold / Risk logic <--" + Style.RESET_ALL)
    print("Hold: packages listed in config.json are excluded from updates for the current env.")
    print("Pin: packages listed in config.json are fixed to a specific version.")
    print("Hold flugs (--hold | --unhold):\n  " + Fore.BLUE + f"python \"{script_path}\" --hold pkg1 pkg2" + Style.RESET_ALL)
    print("Pin flugs (--pin | --unpin):\n  " + Fore.BLUE + f"python \"{script_path}\" --pin pkg1==1.2.3 pkg2" + Style.RESET_ALL + "\n")

    # Activation hints (conda/venv)
    env_type = (cfg.get("env_type") or "").lower()
    print(Fore.MAGENTA + Style.BRIGHT + "--> Quick environment activation hints <--" + Style.RESET_ALL)
    if env_type == "venv":
        venv_folder = cfg.get("venv_path") or cfg.get("conda_env_folder") or cfg.get("conda_env") or "<venv_folder>"
        vcmd_cmd = f"cd /d {venv_folder}\ncall Scripts\\activate.bat"
        vcmd_ps = f"Set-Location -Path '{venv_folder}'; .\\Scripts\\Activate.ps1"
        print("venv (CMD):\n  " + Fore.BLUE + vcmd_cmd + Style.RESET_ALL)
        print("venv (PowerShell):\n  " + Fore.BLUE + vcmd_ps + Style.RESET_ALL + "\n")
        if os.path.isabs(venv_folder):
            py = os.path.join(venv_folder, "Scripts", "python.exe")
            pip_call = f"& \"{py}\" -m pip"
            print("venv python:\n  " + Fore.BLUE + f"& \"{py}\"" + Style.RESET_ALL)
            print("venv pip (via python):\n  " + Fore.BLUE + pip_call + Style.RESET_ALL)
            print("venv uv (no activation needed):\n  " + Fore.BLUE + f"uv pip install --python \"{py}\" <PACKAGE>" + Style.RESET_ALL + "\n")
    else:
        conda_path = cfg.get("conda_path") or "conda"
        conda_env_ref = cfg.get("conda_env") or cfg.get("conda_env_folder") or "<env_name_or_path>"
        if os.path.sep in conda_path:
            conda_root = os.path.dirname(os.path.dirname(conda_path))
            activate_bat = os.path.join(conda_root, "Scripts", "activate.bat")
            conda_cmd_cmd = f"call \"{activate_bat}\" \"{conda_env_ref}\""
            ps_hook = os.path.join(conda_root, "shell", "condabin", "conda-hook.ps1")
            conda_cmd_ps = f"& \"{ps_hook}\"; conda activate \"{conda_env_ref}\""
        else:
            conda_cmd_cmd = f"conda activate \"{conda_env_ref}\""
            conda_cmd_ps = "conda init powershell; " f"conda activate \"{conda_env_ref}\""
        print("conda (CMD):\n  " + Fore.BLUE + conda_cmd_cmd + Style.RESET_ALL)
        print("conda (PowerShell):\n  " + Fore.BLUE + conda_cmd_ps + Style.RESET_ALL + "\n")
        if os.path.isabs(conda_env_ref):
            py = os.path.join(conda_env_ref, "Scripts", "python.exe")
            pip_call = f"\"{py}\" -m pip"
            print("conda python:\n  " + Fore.BLUE + f"\"{py}\"" + Style.RESET_ALL)
            print("conda pip (via python):\n  " + Fore.BLUE + pip_call + Style.RESET_ALL + "\n")
        else:
            print("conda python (after activate):\n  " + Fore.BLUE + "python" + Style.RESET_ALL)
            print("conda pip (via python):\n  " + Fore.BLUE + "python -m pip" + Style.RESET_ALL + "\n")

    repo_custom_nodes_path = _get_custom_nodes_repo_path(cfg)
    include_repo_path, prompt_show_commented = _ask_include_custom_nodes_repo_path(
        repo_custom_nodes_path, show_commented=args.show_commented
    )
    args.show_commented = bool(args.show_commented or prompt_show_commented)
    if include_repo_path:
        custom_nodes_paths = dedupe_dirs(custom_nodes_paths + [repo_custom_nodes_path])
        print("Included custom_nodes_repo_path in requirements scan for this run.")

    result = run_audit(
        cfg, comfy_root, custom_nodes_paths, hold_set, pin_map,
        force_pip=args.force_pip, classify_workers=args.classify_workers, progress=progress,
    )
    names, reports, extra_reqs, vcs_reports = result.names, result.reports, result.extra_reqs, result.extra_reqs
    comfy_env_reports, commented_reqs = result.comfy_env_reports, result.commented_reqs
    safe, risky, unknown, missing = result.safe, result.risky, result.unknown, result.missing
    downgrades, held, pinned_mismatch = result.downgrades, result.held, result.pinned_mismatch

    # Print per package
    for n in names:
        r = reports[n]
        tgt = r.installable_max or r.max_allowed
        action = package_action(r)
        pin_off = n in pin_map and str(r.installed) != pin_map[n]
        if args.hide_unchanged and package_unchanged(r, pin_off):
            continue

        # Installed color
        if not r.installed:
            col = Fore.RED
        elif action == "upgrade":
            col = Fore.CYAN
        elif action == "downgrade":
            col = Fore.YELLOW
        else:
            col = Fore.GREEN

        print(Fore.MAGENTA + Style.BRIGHT + f"--- {n} ---" + Style.RESET_ALL)
        print(" - Installed:", col, (r.installed or "-"), Style.RESET_ALL)
        print(" - Used in:")
        for c in r.constraints:
            s = str(c.spec) if str(c.spec) else "(no specifier)"
            print(f"    - {c.repo} [requirements.txt] requires {Fore.YELLOW}{s}{Style.RESET_ALL}")
        print(" - Max allowed:", Fore.CYAN if tgt else Fore.RED, (tgt or "-"), Style.RESET_ALL)
        if r.max_allowed and r.installable_max and r.installable_max != r.max_allowed:
            print(" - PyPI max allowed:", Fore.CYAN + str(r.max_allowed) + Style.RESET_ALL)
        if r.py_incompatible:
            print(" - Skipped incompatible releases:", Fore.YELLOW + ", ".join(r.py_incompatible) + Style.RESET_ALL)
        if r.prerelease_filtered:
            print(" - Filtered pre-release versions:", Fore.YELLOW + ", ".join(r.prerelease_filtered) + Style.RESET_ALL)
        if r.availability_issue:
            print(" - Availability issue:", Fore.YELLOW + r.availability_issue + Style.RESET_ALL)
        if r.update_error:
            print(" - Constraint issue:", Fore.RED + (r.update_error or "") + Style.RESET_ALL)

        if n in hold_set:
            print(" - Hold:", Fore.YELLOW + "Updates disabled" + Style.RESET_ALL)
        elif n in pin_map:
            pin_ver = pin_map[n]
            if r.installed and str(r.installed) == pin_ver:
                print(" - Pin:", Fore.GREEN + f"Locked to {pin_ver}" + Style.RESET_ALL)
            else:
                print(" - Pin:", Fore.YELLOW + f"Locked to {pin_ver} (installed {r.installed or '-'})" + Style.RESET_ALL)
        elif action == "install":
            print(" - Update: " + Fore.RED + f"Not installed; will be added at {tgt}" + Style.RESET_ALL)
        elif action == "upgrade":
            if r.safe_update and r.risky_update and r.safe_update != r.risky_update:
                print(" - Update (safe): " + Fore.GREEN + f"Upgrade to {r.safe_update} suggested" + Style.RESET_ALL)
                print(" - Update (risky): " + Fore.YELLOW + f"Newer risky upgrade exists: {r.risky_update}" + Style.RESET_ALL)
            elif r.safe_update:
                print(" - Update: " + Fore.GREEN + f"Upgrade to {r.safe_update} suggested" + Style.RESET_ALL)
            elif r.risky_update:
                print(" - Update: " + Fore.YELLOW + f"Risky upgrade to {r.risky_update} (see Constraint issue)" + Style.RESET_ALL)
            else:
                print(" - Update: " + Fore.GREEN + f"Upgrade to {tgt} suggested" + Style.RESET_ALL)
        elif action == "downgrade":
            print(" - Update: " + Fore.YELLOW + f"Installed {r.installed} ABOVE allowed {tgt}; consider downgrade" + Style.RESET_ALL)
        print()

    if vcs_reports:
        print(Style.BRIGHT + "=== VCS / URL requirements ===" + Style.RESET_ALL)
        for v in vcs_reports:
            col = Fore.GREEN if v.installed else Fore.RED
            name_disp = v.name or "(unknown package)"
            print(Fore.MAGENTA + Style.BRIGHT + f"--- {name_disp} ---" + Style.RESET_ALL)
            print(" - Installed:", col, (v.installed or "-"), Style.RESET_ALL)
            print(f" - Source: {v.repo} [{v.file}]")
            print(f" - Raw: {v.raw}")
            print(f" - URL: {v.url or '-'}")
            print(f" - Ref: {v.ref or '(none)'}")
            if v.ref_ok is not None:
                if v.ref_ok:
                    print(" - Ref check:", Fore.GREEN + "ok" + Style.RESET_ALL, v.ref_error or "")
                else:
                    print(" - Ref check:", Fore.RED + "failed" + Style.RESET_ALL, v.ref_error or "")
                if need_vcs_install(v):
                    print(" - Update:", Fore.RED + "Will be installed/reinstalled via Missing command" + Style.RESET_ALL)
                else:
                    print(" - Update:", Fore.GREEN + "Installed; no reinstall planned" + Style.RESET_ALL)
            else:
                if need_vcs_install(v):
                    print(" - Update:", Fore.RED + "Will be installed via Missing command" + Style.RESET_ALL)
                else:
                    print(" - Update:", Fore.GREEN + "Installed (no ref check); no reinstall planned" + Style.RESET_ALL)
            print()

    if comfy_env_reports:
        print(Style.BRIGHT + "=== comfy-env plugin isolation ===" + Style.RESET_ALL)
        workspace_install_needed = [
            rep
            for rep in comfy_env_reports
            if rep.install_py and any((not iso.built) or iso.stale for iso in rep.isolations)
        ]
        for rep in comfy_env_reports:
            print(Fore.MAGENTA + Style.BRIGHT + f"--- {rep.repo} ---" + Style.RESET_ALL)
            if rep.root_config:
                print(f" - Root config: {rep.root_config}")
                if not rep.isolations:
                    print(
                        " - Isolation:",
                        Fore.YELLOW + "No comfy-env.toml found; root config does not isolate host pip deps by itself" + Style.RESET_ALL,
                    )
            if rep.isolations:
                print(" - Isolation envs:")
                for iso in rep.isolations:
                    if not iso.built:
                        status = Fore.RED + "MISSING" + Style.RESET_ALL
                    elif iso.stale:
                        status = Fore.YELLOW + "STALE" + Style.RESET_ALL
                    else:
                        status = Fore.GREEN + "OK" + Style.RESET_ALL
                    print(f"    - {iso.subdir} -> {iso.env_name} [{status}]")
                    print(f"      config: {iso.config}")
                    print(f"      env: {iso.env_path}")
                    if iso.stale_sources:
                        print("      newer sources:")
                        for src in iso.stale_sources:
                            print(f"        - {src}")
            if rep.isolations and not rep.install_py:
                print(" - Install/rerun command:", Fore.RED + "install.py not found; build command is unknown" + Style.RESET_ALL)
            print()
        if workspace_install_needed:
            rep = workspace_install_needed[0]
            print(
                " - Workspace install/rerun command:",
                Fore.BLUE
                + format_comfy_env_workspace_install_cmd(python_cmd(cfg), comfy_root, rep.plugin_dir)
                + Style.RESET_ALL,
            )
            print("   One run rebuilds every active comfy-env workspace environment.")
            print()
        else:
            print(" - Workspace install/rerun command:", Fore.GREEN + "not needed; all detected envs are current" + Style.RESET_ALL)
            print()

    def cmdline(lst: List[Tuple[str, Version]]) -> str:
        return " ".join(f"{n}=={v}" for n, v in lst)

    print(Style.BRIGHT + "=== Final commands ===" + Style.RESET_ALL)
    safe = dedupe_pkg_versions(safe)
    risky = dedupe_pkg_versions(risky)
    unknown = dedupe_pkg_versions(unknown)
    extra_unique = sorted(set(v.raw for v in extra_reqs))
    pip_prefix = " ".join(quote_cmd_part(p) for p in pip)
    safe_cmd = pip_prefix + " " + cmdline(safe)
    risky_cmd = pip_prefix + " " + cmdline(risky)
    unknown_cmd = pip_prefix + " " + cmdline(unknown)
    pinned_cmd = pip_prefix + " " + " ".join(
        f"{n}=={v}" for n, v in pinned_mismatch
    ) if pinned_mismatch else ""
    missing_cmd_parts = [cmdline(missing)] if missing else []
    extra_to_install = sorted(set(v.raw for v in extra_reqs if need_vcs_install(v)))
    if extra_to_install:
        missing_cmd_parts.append(" ".join(extra_to_install))
    missing_cmd = pip_prefix + " " + " ".join([p for p in missing_cmd_parts if p])
    if safe:
        print("Safe updates:\n  " + Fore.BLUE + safe_cmd + Style.RESET_ALL)
    if risky:
        print("Risky updates:\n  " + Fore.BLUE + risky_cmd + Style.RESET_ALL)
    if risky:
        print("Risky reasons:")
        for n, v in risky:
            rpt = reports.get(n)
            if rpt and rpt.update_error:
                inst = str(rpt.installed) if rpt.installed else "-"
                print(f"  - {n} {inst} -> {v}: {rpt.update_error}")
            else:
                inst = str(rpt.installed) if rpt and rpt.installed else "-"
                print(f"  - {n} {inst} -> {v}: unknown reason")
    if unknown:
        print("Unknown updates:\n  " + Fore.BLUE + unknown_cmd + Style.RESET_ALL)
    if missing_cmd_parts:
        print("Missing:\n  " + Fore.BLUE + missing_cmd + Style.RESET_ALL)
    if pinned_mismatch:
        print("Pinned fixes:\n  " + Fore.BLUE + pinned_cmd + Style.RESET_ALL)
    if held:
        print("Held packages:\n  " + Fore.BLUE + ", ".join(sorted(held)) + Style.RESET_ALL)
    if extra_to_install:
        print("  Included VCS/URL entries:")
        for v in extra_reqs:
            if v.raw in extra_to_install:
                print(f"    - {v.repo} [{v.file}] -> {v.raw}")
    comfy_env_install_reruns = [
        rep
        for rep in comfy_env_reports
        if rep.install_py and any((not iso.built) or iso.stale for iso in rep.isolations)
    ]
    if comfy_env_install_reruns:
        trigger_names: List[str] = []
        for rep in comfy_env_install_reruns:
            for iso in rep.isolations:
                if not iso.built:
                    trigger_names.append(f"{iso.env_name}:missing")
                elif iso.stale:
                    trigger_names.append(f"{iso.env_name}:stale")
        rep = comfy_env_install_reruns[0]
        print("comfy-env workspace install/rerun:")
        print("  " + Fore.BLUE + format_comfy_env_workspace_install_cmd(python_cmd(cfg), comfy_root, rep.plugin_dir) + Style.RESET_ALL)
        print("  Triggers: " + ", ".join(trigger_names))
    if downgrades:
        down_cmd = pip_prefix + " " + cmdline(downgrades)
        print("Downgrades:\n  " + Fore.BLUE + down_cmd + Style.RESET_ALL)

    if args.show_commented and commented_reqs:
        print("Commented package entries from requirements:")
        grouped: Dict[str, List[str]] = {}
        seen_lines_by_repo: Dict[str, set[str]] = {}
        for repo_name, _reqf, raw in commented_reqs:
            bucket = grouped.setdefault(repo_name, [])
            seen = seen_lines_by_repo.setdefault(repo_name, set())
            key = raw.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            bucket.append(key)
        for repo_name in sorted(grouped):
            print(f"  [{repo_name}]")
            for ln in grouped[repo_name]:
                print(f"    {ln}")

    unsatisfiable_constraints: List[Tuple[str, str, str]] = []
    for n in names:
        r = reports[n]
        if r.max_allowed is not None:
            continue
        if not r.update_error:
            continue
        msg = (r.update_error or "").strip()
        if not msg.lower().startswith("no release satisfies"):
            continue
        inst = str(r.installed) if r.installed else "-"
        unsatisfiable_constraints.append((n, inst, msg))

    if unsatisfiable_constraints:
        print("Unsatisfiable constraints:")
        for n, inst, msg in unsatisfiable_constraints:
            print(f"  - {n} (installed {inst}): {msg}")


if __name__ == "__main__":
    main()
