#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ComfyUI environment updater and audit (pip) — streamlined

- Scans only requirements.txt files:
  * In the ComfyUI root (top level only)
  * In each top-level plugin directory (ignores *.disable; no subfolder recursion)
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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


def _confirm_venv_path_simple(path: str) -> bool:
    if not os.path.isdir(path):
        print("Path does not exist.")
        return False
    return True


def _select_venv_simple(cfg: dict) -> dict:
    paths = _load_venv_paths_simple(cfg)
    current = cfg.get("venv_path") or ""
    current_norm = os.path.normcase(os.path.normpath(current)) if current else ""
    if not paths and not sys.stdin.isatty():
        raise SystemExit("No venv paths found in config.json and input is non-interactive.")

    while True:
        print("--> Select venv for updates <--")
        for idx, p in enumerate(paths, 1):
            mark = "*" if current_norm and os.path.normcase(os.path.normpath(p)) == current_norm else " "
            print(f" {idx}){mark} {p}")
        print(" A) Add new venv")
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
            selected = new_path
            break

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
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


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

import requests
from packaging.requirements import Requirement
from packaging.markers import Marker
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from packaging.utils import canonicalize_name
from colorama import init as colorama_init, Fore, Style

from comfyui_root import default_custom_nodes, resolve_comfyui_root
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
        # Fallback: create an empty file if it doesn't exist
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump({}, f, indent=4)
            print(f"Config file created at {path}. Please fill required fields manually.")
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)

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
    paths = _load_custom_nodes_paths(cfg, root)
    if paths:
        return root or os.path.dirname(paths[0].rstrip("\\/")), paths
    sys.exit("custom_nodes_path missing in config.json")


def plugin_dirs(custom_nodes: str) -> List[str]:
    out: List[str] = []
    if not os.path.isdir(custom_nodes):
        print(f"custom_nodes_path is not a directory: {custom_nodes}")
        return out
    try:
        entries = sorted(os.listdir(custom_nodes))
    except OSError as e:
        print(f"Failed to list custom_nodes_path: {custom_nodes} ({e})")
        return out
    for n in entries:
        p = os.path.join(custom_nodes, n)
        try:
            if os.path.isdir(p) and not n.endswith(".disable"):
                out.append(p)
        except OSError:
            # Skip broken/inaccessible junctions or special entries
            continue
    return out


REQ_FILE_RE = re.compile(r"^requirements\.txt$", re.I)


def find_reqs(folder: str) -> List[str]:
    return [os.path.join(folder, f) for f in os.listdir(folder) if REQ_FILE_RE.match(f)]


def _marker_allows_raw(raw: str) -> bool:
    if ";" not in raw:
        return True
    marker_text = raw.split(";", 1)[1].strip()
    if not marker_text:
        return True
    try:
        return Marker(marker_text).evaluate()
    except Exception:
        # If marker parsing fails, keep the entry to avoid accidental drops
        return True


def parse_req_file(path: str) -> Tuple[List[Requirement], List[str]]:
    try:
        txt = open(path, encoding="utf-8").read()
    except Exception:
        return [], []
    reqs: List[Requirement] = []
    extras: List[str] = []
    for line in txt.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("-"):
            continue
        # Strip inline comments.
        if "#" in s:
            s = s.split("#", 1)[0].strip()
            if not s:
                continue
        try:
            req = Requirement(s)
            if req.marker and not req.marker.evaluate():
                continue
            reqs.append(req)
        except Exception:
            # Keep unparsed entries (e.g. VCS/URL requirements) so we can surface them later
            if _marker_allows_raw(s):
                extras.append(s)
    return reqs, extras


def _norm_path(path: str) -> str:
    return os.path.normpath(path.strip())


def _unique_paths(paths: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for p in paths:
        if not p:
            continue
        norm = os.path.normcase(os.path.normpath(p))
        if norm in seen:
            continue
        seen.add(norm)
        out.append(os.path.normpath(p))
    return out


def _norm_real_path(path: str) -> str:
    try:
        return os.path.normcase(os.path.normpath(os.path.realpath(path)))
    except Exception:
        return os.path.normcase(os.path.normpath(path))


def _dedupe_dirs(paths: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for p in paths:
        if not p:
            continue
        norm = os.path.normcase(os.path.normpath(p))
        real = _norm_real_path(p)
        if norm in seen or real in seen:
            continue
        if not os.path.isdir(p):
            continue
        out.append(os.path.normpath(p))
        seen.add(norm)
        seen.add(real)
    return out


def _load_custom_nodes_paths(cfg: dict, comfy_root: str = "") -> List[str]:
    paths: List[str] = []
    raw_list = cfg.get("custom_nodes_paths")
    if isinstance(raw_list, list):
        for p in raw_list:
            if isinstance(p, str) and p.strip():
                paths.append(p.strip())
    cn = cfg.get("custom_nodes_path")
    if isinstance(cn, str) and cn.strip():
        paths.append(cn.strip())
    if not paths and comfy_root:
        paths.append(str(default_custom_nodes(Path(comfy_root))))
    return _dedupe_dirs(paths)


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


def _ask_include_custom_nodes_repo_path(path: str) -> bool:
    if not path:
        return False
    if not sys.stdin.isatty():
        return False
    print(Fore.MAGENTA + Style.BRIGHT + "--> Optional requirements source <--" + Style.RESET_ALL)
    print(f"custom_nodes_repo_path found: {path}")
    while True:
        choice = input("Include this path in requirements scan? [+/-] [-]: ").strip()
        if not choice or choice == "-":
            return False
        if choice == "+":
            return True
        print("Please enter '+' to include or '-' to skip.")


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
    return _unique_paths(paths)


def _save_config(path: str, cfg: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)


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
    current = cfg.get("venv_path") or ""
    current_norm = os.path.normcase(os.path.normpath(current)) if current else ""
    if not paths:
        print("No venv paths found in config.")

    while True:
        print(Fore.MAGENTA + Style.BRIGHT + "--> Select venv for updates <--" + Style.RESET_ALL)
        for idx, p in enumerate(paths, 1):
            mark = "*" if current_norm and os.path.normcase(os.path.normpath(p)) == current_norm else " "
            print(f" {idx}){mark} {p}")
        print(" A) Add new venv")
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
            paths = _unique_paths(paths + [new_path])
            selected = new_path
            break

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
    try:
        _save_config(config_path, cfg)
    except Exception as e:
        print(f"Warning: failed to update config.json: {e}")
    return cfg


@dataclass
class SourceConstraint:
    repo: str
    file: str
    spec: SpecifierSet


@dataclass
class PackageReport:
    name: str
    installed: Optional[Version]
    constraints: List[SourceConstraint] = field(default_factory=list)
    available_versions: List[Version] = field(default_factory=list)
    py_incompatible: List[str] = field(default_factory=list)
    prerelease_filtered: List[str] = field(default_factory=list)
    max_allowed: Optional[Version] = None
    installable_max: Optional[Version] = None
    availability_issue: Optional[str] = None
    update_ok: Optional[bool] = None
    update_error: Optional[str] = None
    reverse_conflicts: List[str] = field(default_factory=list)


@dataclass
class VcsReport:
    repo: str  # source repo name (plugin or ComfyUI)
    file: str  # requirements.txt path
    raw: str   # original requirement line
    name: Optional[str] = None
    url: Optional[str] = None
    ref: Optional[str] = None
    installed: Optional[Version] = None
    installed_name: Optional[str] = None
    installed_url: Optional[str] = None
    installed_commit: Optional[str] = None
    ref_ok: Optional[bool] = None
    ref_error: Optional[str] = None


def _python_cmd(cfg: dict) -> List[str]:
    env_type = (cfg.get("env_type") or "").lower()
    if env_type == "venv":
        venv = cfg.get("venv_path") or ""
        if venv:
            c = os.path.join(venv, "Scripts", "python.exe")
            if os.path.exists(c):
                return [c]
            c = os.path.join(venv, "bin", "python")
            if os.path.exists(c):
                return [c]
    env = cfg.get("conda_env_folder") or ""
    if env:
        c = os.path.join(env, "python.exe")
        if os.path.exists(c):
            return [c]
        c = os.path.join(env, "bin", "python")
        if os.path.exists(c):
            return [c]
    return [sys.executable]


def _load_installed_from_python(py: List[str]) -> Optional[Dict[str, dict]]:
    code = (
        "import json, re, importlib.metadata as m;"
        "\ncanon=lambda s: re.sub(r'[-_.]+','-', s.strip().lower());"
        "\nout={};"
        "\nfor d in m.distributions():"
        "\n name=(d.metadata.get('Name') or d.name);"
        "\n key=canon(name);"
        "\n direct=d.read_text('direct_url.json');"
        "\n direct_url=None; direct_commit=None;"
        "\n if direct:"
        "\n  try:"
        "\n   data=json.loads(direct);"
        "\n   if isinstance(data, dict):"
        "\n    u=data.get('url');"
        "\n    if isinstance(u,str) and u.strip(): direct_url=u.strip();"
        "\n    vi=data.get('vcs_info');"
        "\n    if isinstance(vi, dict):"
        "\n     c=vi.get('commit_id');"
        "\n     if isinstance(c,str) and c.strip(): direct_commit=c.strip();"
        "\n  except Exception:"
        "\n   pass;"
        "\n out[key]={\"name\": name, \"version\": d.version, \"requires\": d.requires or [], \"direct_url\": direct_url, \"direct_commit\": direct_commit};"
        "\nprint(json.dumps(out))"
    )
    try:
        p = subprocess.run(py + ["-c", code], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=35)
        if p.returncode != 0 or not p.stdout:
            return None
        return json.loads(p.stdout)
    except Exception:
        return None


def load_installed_dists(cfg: dict) -> Dict[str, dict]:
    py = _python_cmd(cfg)
    data = _load_installed_from_python(py)
    if isinstance(data, dict) and data:
        return data
    # Fallback to current interpreter
    out: Dict[str, dict] = {}
    for dist in importlib_metadata.distributions():
        name = dist.metadata.get("Name") or dist.name
        key = canonicalize_name(name)
        direct_url = None
        direct_commit = None
        try:
            raw = dist.read_text("direct_url.json")
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    u = data.get("url")
                    if isinstance(u, str) and u.strip():
                        direct_url = u.strip()
                    vi = data.get("vcs_info")
                    if isinstance(vi, dict):
                        c = vi.get("commit_id")
                        if isinstance(c, str) and c.strip():
                            direct_commit = c.strip()
        except Exception:
            pass
        out[key] = {
            "name": name,
            "version": dist.version,
            "requires": dist.requires or [],
            "direct_url": direct_url,
            "direct_commit": direct_commit,
        }
    return out


def inst_ver_from_map(installed: Dict[str, dict], name: str) -> Optional[Version]:
    try:
        info = installed.get(canonicalize_name(name))
        if not info:
            return None
        ver = info.get("version") or ""
        return Version(ver) if ver else None
    except Exception:
        return None


def _normalize_vcs_url(url: str) -> str:
    s = (url or "").strip()
    if not s:
        return ""
    if s.startswith("git+"):
        s = s[4:]
    s = s.split(";", 1)[0].split("#", 1)[0].strip().rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    return s.lower()


def _resolve_vcs_installed(installed: Dict[str, dict], v: VcsReport) -> Tuple[Optional[Version], Optional[str], Optional[str], Optional[str]]:
    # 1) Try explicit/inferred package name match first.
    if v.name:
        key = canonicalize_name(v.name)
        info = installed.get(key)
        if info:
            ver = inst_ver_from_map(installed, v.name)
            return ver, info.get("name"), info.get("direct_url"), info.get("direct_commit")

    # 2) Fallback to VCS URL match from direct_url.json for name-mismatch cases (e.g. sam2 vs SAM-2).
    want = _normalize_vcs_url(v.url or "")
    if not want:
        return None, None, None, None
    for info in installed.values():
        durl = info.get("direct_url")
        if not isinstance(durl, str) or not durl.strip():
            continue
        if _normalize_vcs_url(durl) != want:
            continue
        ver_raw = info.get("version") or ""
        ver = None
        try:
            ver = Version(ver_raw) if ver_raw else None
        except Exception:
            ver = None
        return ver, info.get("name"), durl, info.get("direct_commit")
    return None, None, None, None


def fetch_pypi(name: str) -> Tuple[List[Version], List[str], List[str]]:
    """Return (stable versions compatible with current Python, skipped_incompatible_descriptions, filtered_prereleases)."""
    try:
        r = requests.get(f"https://pypi.org/pypi/{name}/json", timeout=12)
        if r.status_code != 200:
            return [], [], []
        data = r.json()
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        vs: List[Version] = []
        skipped: List[str] = []
        filtered: List[str] = []
        for v in data.get("releases", {}):
            try:
                ver = Version(v)
            except Exception:
                pass
            else:
                if ver.is_prerelease or ver.is_devrelease:
                    filtered.append(v)
                    continue
                files = data.get("releases", {}).get(v) or []
                compatible = False
                reason = None
                for f in files:
                    rp = f.get("requires_python")
                    if not rp:
                        compatible = True
                        break
                    try:
                        spec = SpecifierSet(rp)
                    except Exception:
                        compatible = True
                        break
                    if spec.contains(py_ver, prereleases=True):
                        compatible = True
                        break
                if not files:
                    compatible = True  # keep unknown entries just in case
                if compatible:
                    vs.append(ver)
                else:
                    reason = files[0].get("requires_python") if files else "Requires-Python mismatch"
                    skipped.append(f"{ver} ({reason})")
        return sorted(set(vs)), skipped, sorted(set(filtered))
    except Exception:
        return [], [], []


def build_reverse_constraints(
    target_names: List[str],
    installed: Dict[str, dict],
) -> Dict[str, List[Tuple[str, SpecifierSet]]]:
    target_set = {canonicalize_name(n) for n in target_names}
    out: Dict[str, List[Tuple[str, SpecifierSet]]] = {}
    for info in installed.values():
        dist_name = info.get("name") or info.get("Name") or ""
        reqs = info.get("requires") or []
        for raw in reqs:
            try:
                req = Requirement(raw)
            except Exception:
                continue
            if req.marker and not req.marker.evaluate():
                continue
            dep_name = canonicalize_name(req.name)
            if dep_name not in target_set:
                continue
            if str(req.specifier):
                out.setdefault(dep_name, []).append((dist_name, req.specifier))
    return out


def find_reverse_conflicts(
    reverse_map: Dict[str, List[Tuple[str, SpecifierSet]]],
    pkg_name: str,
    target_ver: Version,
) -> List[str]:
    conflicts: List[str] = []
    for dist_name, spec in reverse_map.get(pkg_name, []):
        if target_ver not in spec:
            conflicts.append(f"{dist_name} requires {spec}")
    return conflicts


def choose_max(versions: List[Version], specs: List[SpecifierSet]) -> Optional[Version]:
    if not versions:
        return None
    if not specs:
        return versions[-1]
    feas = [v for v in versions if all(v in s for s in specs if str(s))]
    return feas[-1] if feas else None


def pip_cmd(cfg: dict) -> List[str]:
    env_type = (cfg.get("env_type") or "").lower()
    if env_type == "venv":
        venv = cfg.get("venv_path") or ""
        if venv:
            c = os.path.join(venv, "Scripts", "pip.exe")
            if os.path.exists(c):
                return [c]
            c = os.path.join(venv, "bin", "pip")
            if os.path.exists(c):
                return [c]
    env = cfg.get("conda_env_folder") or cfg.get("conda_env") or ""
    if env:
        c = os.path.join(env, "Scripts", "pip.exe")
        if os.path.exists(c):
            return [c]
        c = os.path.join(env, "bin", "pip")
        if os.path.exists(c):
            return [c]
    return [sys.executable, "-m", "pip"]


def dry_run(pip: List[str], pkgs: List[Tuple[str, Version]], timeout_s: int = 60) -> Tuple[bool, str]:
    """Run a resolver simulation. Returns (ok, output).
    If --dry-run is unsupported on this pip, consider it ok to avoid losing items.
    """
    if not pkgs:
        return True, ""
    args = pip + ["install", "--dry-run"] + [f"{n}=={v}" for n, v in pkgs]
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout_s)
        out = p.stdout or ""
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout_s}s"
    if p.returncode == 0:
        return True, out
    lowered = out.lower()
    if ("no such option" in lowered) or ("unrecognized arguments" in lowered) or ("usage:" in lowered):
        return True, out
    return False, out


def classify_dry_run(ok: bool, out: str) -> Tuple[str, str]:
    if ok:
        return "safe", ""
    lowered = (out or "").lower()
    conflict_markers = (
        "resolutionimpossible",
        "conflicting dependencies",
        "cannot install",
        "is incompatible with",
        "depends on",
        "requires",
    )
    net_markers = (
        "timeout",
        "timed out",
        "connection",
        "temporary failure",
        "ssl",
        "certificate",
        "name or service not known",
        "proxy",
    )
    if any(m in lowered for m in conflict_markers):
        return "risky", out
    if any(m in lowered for m in net_markers):
        return "unknown", out
    return "unknown", out


def _is_no_matching_distribution(out: str) -> bool:
    lowered = (out or "").lower()
    return (
        "no matching distribution found for" in lowered
        or "could not find a version that satisfies the requirement" in lowered
    )


def _extract_versions_from_no_matching(out: str) -> List[Version]:
    """Parse pip 'from versions: ...' list for no-matching-distribution errors."""
    lowered = out or ""
    marker = "from versions:"
    if marker not in lowered.lower():
        return []
    idx = lowered.lower().rfind(marker)
    tail = lowered[idx + len(marker):]
    tail = tail.split("\n", 1)[0]
    raw = [p.strip() for p in tail.split(",") if p.strip()]
    versions: List[Version] = []
    for v in raw:
        try:
            versions.append(Version(v))
        except Exception:
            continue
    return sorted(set(versions))


def _strip_vcs_ref_from_url(url: str) -> Tuple[str, Optional[str]]:
    """
    Split VCS URL into (repo_url_without_ref, ref_if_present).
    Handles common forms:
    - git+https://host/org/repo.git@ref
    - git+ssh://git@host/org/repo.git@ref
    - git+git@host:org/repo.git@ref
    """
    src = (url or "").strip().split(";", 1)[0].strip()
    if not src:
        return "", None

    head, frag = (src.split("#", 1) + [""])[:2]
    has_frag = "#" in src
    has_git_prefix = head.startswith("git+")
    probe = head[4:] if has_git_prefix else head

    ref: Optional[str] = None
    split_at: Optional[int] = None

    if "://" in probe:
        # For URL form, an inline ref is the last "@" after the last "/".
        last_at = probe.rfind("@")
        last_slash = probe.rfind("/")
        if last_at > last_slash:
            split_at = last_at
    elif ".git@" in probe:
        # For SCP-like form (git@host:org/repo.git), only treat "@"
        # after ".git" as an inline ref.
        split_at = probe.rfind("@")

    if split_at is not None and split_at >= 0:
        ref_candidate = probe[split_at + 1:].strip()
        if ref_candidate:
            ref = ref_candidate
            probe = probe[:split_at]

    base = ("git+" if has_git_prefix else "") + probe
    if has_frag:
        base = base + "#" + frag
    return base, ref


def parse_vcs_line(raw: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Best-effort extraction of name, url, ref from a VCS/URL requirement line."""
    line = raw.strip()
    name: Optional[str] = None
    url_part = line

    # PEP 508 style: name @ URL
    if " @" in line:
        lhs, rhs = line.split(" @", 1)
        lhs = lhs.strip()
        rhs = rhs.strip()
        if lhs:
            name = lhs
        if rhs:
            url_part = rhs

    # egg= fragment
    if not name and "#egg=" in line:
        frag = line.split("#egg=", 1)[1]
        name_candidate = frag.split("&", 1)[0].split(";", 1)[0]
        if name_candidate:
            name = name_candidate

    cleaned_url, ref = _strip_vcs_ref_from_url(url_part)
    url = cleaned_url or url_part
    return name, url, ref


def infer_name_from_url(url: str) -> Optional[str]:
    """Infer package name from repo URL path (last segment without .git)."""
    try:
        cleaned, _ = _strip_vcs_ref_from_url(url)
        path = (cleaned or url).split(";", 1)[0].split("#", 1)[0].strip()
        if path.startswith("git+"):
            path = path[4:]
        path = path.rstrip("/")
        segment = path.rsplit("/", 1)[-1]
        if "/" not in path and ":" in path:
            segment = path.rsplit(":", 1)[-1]
        if segment.endswith(".git"):
            segment = segment[:-4]
        segment = segment.strip()
        return canonicalize_name(segment) if segment else None
    except Exception:
        return None


def check_vcs_ref(url: str, ref: Optional[str]) -> Tuple[bool, str]:
    """Use git ls-remote to verify the ref exists (or that the repo is reachable)."""
    target = ref or "HEAD"
    cleaned, inline_ref = _strip_vcs_ref_from_url(url)
    if cleaned.startswith("git+"):
        cleaned = cleaned[4:]
    cleaned = cleaned.split(";", 1)[0].split("#", 1)[0].strip()
    if not ref and inline_ref:
        target = inline_ref
    is_hex_ref = bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", target))
    cmd = ["git", "ls-remote", cleaned] if is_hex_ref else ["git", "ls-remote", cleaned, target]
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=25)
        out = p.stdout or ""
        if p.returncode != 0:
            return False, out.strip()
        if is_hex_ref:
            wanted = target.lower()
            lines = [ln for ln in out.splitlines() if ln.strip()]
            hashes = [ln.split()[0].lower() for ln in lines if ln.split()]
            for h in hashes:
                if h.startswith(wanted):
                    return True, h
            return False, f"commit {target} not found in remote refs"
        if not out.strip():
            return False, out.strip()
        return True, out.strip().splitlines()[0]
    except subprocess.TimeoutExpired:
        return False, f"timeout checking {target}"
    except Exception as e:
        return False, str(e)


def need_vcs_install(v: VcsReport) -> bool:
    """Decide if a VCS entry should be part of the install command."""
    if not v.installed:
        return True
    if v.ref_ok is False:
        return True
    return False


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
    pip = pip_cmd(cfg)
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
    print(Fore.MAGENTA + Style.BRIGHT + "--> Some useful commands <--" + Style.RESET_ALL + "\n" +
          "check package info - " + Fore.BLUE + "pip show <PACKAGE>\n" + Style.RESET_ALL +
          "get all available versions of package - " + Fore.BLUE + "pip index versions <PACKAGE>\n" + Style.RESET_ALL +
          "try resolver without installing - " + Fore.BLUE + "pip install <PACKAGE> --dry-run\n" + Style.RESET_ALL +
          "check all errors in venv - " + Fore.BLUE + "pip check\n" + Style.RESET_ALL +
          "get all deep dependencies from a package (need pipdeptree) - " + Fore.BLUE + "pipdeptree -p <PACKAGE>\n" + Style.RESET_ALL +
          "get all reverse dependencies from a package (need pipdeptree) - " + Fore.BLUE + "pipdeptree --reverse --packages <PACKAGE>\n" + Style.RESET_ALL +
          "remove all items from the cache - " + Fore.BLUE + "pip cache purge\n" + Style.RESET_ALL)
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
            print("venv pip (via python):\n  " + Fore.BLUE + pip_call + Style.RESET_ALL + "\n")
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

    # Collect constraints from requirements
    allc: Dict[str, List[SourceConstraint]] = {}
    extra_reqs: List[VcsReport] = []
    for reqf in find_reqs(comfy_root):
        reqs, extras = parse_req_file(reqf)
        for r in reqs:
            n = canonicalize_name(r.name)
            allc.setdefault(n, []).append(SourceConstraint("ComfyUI", reqf, r.specifier))
        for raw in extras:
            nm, url, ref = parse_vcs_line(raw)
            nm_c = canonicalize_name(nm) if nm else None
            if not nm_c and url:
                nm_c = infer_name_from_url(url)
            extra_reqs.append(VcsReport("ComfyUI", reqf, raw, nm_c, url, ref))
    repo_custom_nodes_path = _get_custom_nodes_repo_path(cfg)
    if _ask_include_custom_nodes_repo_path(repo_custom_nodes_path):
        custom_nodes_paths = _dedupe_dirs(custom_nodes_paths + [repo_custom_nodes_path])
        print("Included custom_nodes_repo_path in requirements scan for this run.")

    plugin_paths: List[str] = []
    for cn in custom_nodes_paths:
        plugin_paths.extend(plugin_dirs(cn))
    plugin_paths = _dedupe_dirs(plugin_paths)
    for pl in plugin_paths:
        for reqf in find_reqs(pl):
            reqs, extras = parse_req_file(reqf)
            for r in reqs:
                n = canonicalize_name(r.name)
                allc.setdefault(n, []).append(SourceConstraint(os.path.basename(pl), reqf, r.specifier))
            for raw in extras:
                nm, url, ref = parse_vcs_line(raw)
                nm_c = canonicalize_name(nm) if nm else None
                if not nm_c and url:
                    nm_c = infer_name_from_url(url)
                extra_reqs.append(VcsReport(os.path.basename(pl), reqf, raw, nm_c, url, ref))

    names = sorted(allc)
    reports: Dict[str, PackageReport] = {}
    vcs_reports: List[VcsReport] = []

    # Installed
    for i, n in enumerate(names, 1):
        reports[n] = PackageReport(n, inst_ver_from_map(installed_map, n), allc[n])
        progress("Installed", i, len(names))
    for i, v in enumerate(extra_reqs, 1):
        inst_ver, inst_name, inst_url, inst_commit = _resolve_vcs_installed(installed_map, v)
        v.installed = inst_ver
        v.installed_name = inst_name
        v.installed_url = inst_url
        v.installed_commit = inst_commit
        progress("Installed (VCS)", i, len(extra_reqs))

    # PyPI info
    for i, n in enumerate(names, 1):
        vs, skipped, filtered = fetch_pypi(n)
        rpt = reports[n]
        rpt.available_versions = vs
        rpt.py_incompatible = skipped
        rpt.prerelease_filtered = filtered
        specs = [c.spec for c in rpt.constraints if str(c.spec)]
        rpt.max_allowed = choose_max(vs, specs)
        rpt.installable_max = rpt.max_allowed
        if specs and not rpt.max_allowed:
            uniq_specs = ", ".join(sorted(set(str(s) for s in specs)))
            if not vs:
                rpt.update_error = f"No releases found on PyPI; constraint(s): {uniq_specs}"
            else:
                rpt.update_error = f"No release satisfies {uniq_specs}; latest available is {vs[-1]}"
        elif not vs and skipped:
            rpt.update_error = "All available releases require a different Python version"
        progress("PyPI", i, len(names), n)

    # VCS refs check
    for i, v in enumerate(extra_reqs, 1):
        local_commit = (v.installed_commit or "").strip().lower()
        target_ref = (v.ref or "").strip().lower()
        if target_ref and local_commit and local_commit.startswith(target_ref):
            v.ref_ok = True
            v.ref_error = f"matched installed commit {v.installed_commit}"
        elif v.url:
            ok, info = check_vcs_ref(v.url, v.ref)
            v.ref_ok = ok
            v.ref_error = None if ok else (info or "unknown error")
        progress("VCS refs", i, len(extra_reqs))
    vcs_reports = extra_reqs

    # Candidates
    cand: List[Tuple[str, Version]] = []
    missing: List[Tuple[str, Version]] = []
    downgrades: List[Tuple[str, Version]] = []
    held: List[str] = []
    pinned_ok: List[Tuple[str, str]] = []
    pinned_mismatch: List[Tuple[str, str]] = []
    for n, r in reports.items():
        tgt = r.installable_max or r.max_allowed
        if not tgt:
            continue
        if n in pin_map:
            pinned_ver = pin_map[n]
            if r.installed and str(r.installed) == pinned_ver:
                pinned_ok.append((n, pinned_ver))
            else:
                pinned_mismatch.append((n, pinned_ver))
            continue
        if n in hold_set:
            held.append(n)
            continue
        if not r.installed:
            missing.append((n, tgt))
        elif r.installed < tgt:
            cand.append((n, tgt))
        elif r.installed > tgt:
            downgrades.append((n, tgt))

    reverse_map = build_reverse_constraints([n for n, _ in cand], installed_map)

    # Classify candidates individually via pip --dry-run
    safe: List[Tuple[str, Version]] = []
    risky: List[Tuple[str, Version]] = []
    unknown: List[Tuple[str, Version]] = []
    total = len(cand)
    for idx, (n, v) in enumerate(cand, 1):
        conflicts = find_reverse_conflicts(reverse_map, n, v)
        if conflicts:
            cls = "risky"
            err = "Reverse dependency conflict: " + "; ".join(conflicts)
            ok = False
        else:
            ok, out = dry_run(pip, [(n, v)])
            cls, err = classify_dry_run(ok, out)
            rpt = reports.get(n)
            if (not ok) and _is_no_matching_distribution(out) and rpt is not None:
                specs = [c.spec for c in rpt.constraints if str(c.spec)]
                available = _extract_versions_from_no_matching(out)
                fallback = choose_max(available, specs) if available else None
                if fallback:
                    rpt.installable_max = fallback
                    rpt.availability_issue = (
                        f"{n}=={v} not available for this Python/platform; "
                        f"highest installable is {fallback}"
                    )
                    if rpt.installed and rpt.installed >= fallback:
                        rpt.update_ok = True
                        rpt.update_error = None
                        progress("Classify", idx, total, n)
                        continue
                    v = fallback
                    conflicts2 = find_reverse_conflicts(reverse_map, n, v)
                    if conflicts2:
                        cls = "risky"
                        err = "Reverse dependency conflict: " + "; ".join(conflicts2)
                        conflicts = conflicts2
                    else:
                        ok, out = dry_run(pip, [(n, v)])
                        cls, err = classify_dry_run(ok, out)
                else:
                    rpt.installable_max = rpt.installed
                    rpt.availability_issue = f"{n}=={v} not available for this Python/platform"
                    rpt.update_ok = False
                    rpt.update_error = out or "No matching distribution found"
                    progress("Classify", idx, total, n)
                    continue
        rpt = reports.get(n)
        if rpt is not None:
            rpt.update_ok = cls == "safe"
            rpt.update_error = None if cls == "safe" else (err or "")
            rpt.reverse_conflicts = conflicts
        if cls == "safe":
            safe.append((n, v))
        elif cls == "risky":
            risky.append((n, v))
        else:
            unknown.append((n, v))
        progress("Classify", idx, total, n)
    if total:
        progress("Classify", total, total)

    # Print per package
    for n in names:
        r = reports[n]
        tgt = r.installable_max or r.max_allowed
        action: Optional[str] = None
        if not r.installed and tgt:
            action = "install"
        elif tgt and r.installed and r.installed < tgt:
            action = "upgrade"
        elif tgt and r.installed and r.installed > tgt:
            action = "downgrade"

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

    def cmdline(lst: List[Tuple[str, Version]]) -> str:
        return " ".join(f"{n}=={v}" for n, v in lst)

    print(Style.BRIGHT + "=== Final commands ===" + Style.RESET_ALL)
    extra_unique = sorted(set(v.raw for v in extra_reqs))
    safe_cmd = " ".join(pip) + " install --upgrade " + cmdline(safe)
    risky_cmd = " ".join(pip) + " install --upgrade " + cmdline(risky)
    unknown_cmd = " ".join(pip) + " install --upgrade " + cmdline(unknown)
    pinned_cmd = " ".join(pip) + " install " + " ".join(
        f"{n}=={v}" for n, v in pinned_mismatch
    ) if pinned_mismatch else ""
    missing_cmd_parts = [cmdline(missing)] if missing else []
    extra_to_install = sorted(set(v.raw for v in extra_reqs if need_vcs_install(v)))
    if extra_to_install:
        missing_cmd_parts.append(" ".join(extra_to_install))
    missing_cmd = " ".join(pip) + " install " + " ".join([p for p in missing_cmd_parts if p])
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
    if downgrades:
        down_cmd = " ".join(pip) + " install " + cmdline(downgrades)
        print("Downgrades:\n  " + Fore.BLUE + down_cmd + Style.RESET_ALL)


if __name__ == "__main__":
    main()
