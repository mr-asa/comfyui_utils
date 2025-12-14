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
import json
import os
import re
import sys
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

try:
    from importlib import metadata as importlib_metadata
except ImportError:  # python<3.8
    import importlib_metadata  # type: ignore

import requests
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from packaging.utils import canonicalize_name
from colorama import init as colorama_init, Fore, Style

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
        with open(path, "r", encoding="utf-8") as f:
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
        cm.get_value("venv_path")

    cm.get_value("custom_nodes_path")
    return cm.read_config()


def guess_paths(cfg: dict) -> Tuple[str, str]:
    cn = cfg.get("custom_nodes_path")
    if not cn:
        sys.exit("custom_nodes_path missing in config.json")
    return os.path.dirname(str(cn).rstrip("\\/")), str(cn)


def plugin_dirs(custom_nodes: str) -> List[str]:
    out: List[str] = []
    for n in sorted(os.listdir(custom_nodes)):
        p = os.path.join(custom_nodes, n)
        if os.path.isdir(p) and not n.endswith(".disable"):
            out.append(p)
    return out


REQ_FILE_RE = re.compile(r"^requirements\.txt$", re.I)


def find_reqs(folder: str) -> List[str]:
    return [os.path.join(folder, f) for f in os.listdir(folder) if REQ_FILE_RE.match(f)]


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
        try:
            reqs.append(Requirement(s))
        except Exception:
            # Keep unparsed entries (e.g. VCS/URL requirements) so we can surface them later
            extras.append(s)
    return reqs, extras


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
    max_allowed: Optional[Version] = None
    update_ok: Optional[bool] = None
    update_error: Optional[str] = None


def inst_ver(name: str) -> Optional[Version]:
    try:
        return Version(importlib_metadata.version(name))
    except Exception:
        return None


def fetch_pypi(name: str) -> List[Version]:
    try:
        r = requests.get(f"https://pypi.org/pypi/{name}/json", timeout=12)
        if r.status_code != 200:
            return []
        vs: List[Version] = []
        for v in r.json().get("releases", {}):
            try:
                vs.append(Version(v))
            except Exception:
                pass
        return sorted(set(vs))
    except Exception:
        return []


def choose_max(versions: List[Version], specs: List[SpecifierSet]) -> Optional[Version]:
    if not versions:
        return None
    if not specs:
        return versions[-1]
    feas = [v for v in versions if all(v in s for s in specs if str(s))]
    return feas[-1] if feas else None


def pip_cmd(cfg: dict) -> List[str]:
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


def progress(label: str, i: int, n: int) -> None:
    # Render a single-line progress bar and clean up leftovers from longer prior lines
    if not hasattr(progress, "_last_len"):
        progress._last_len = 0  # type: ignore[attr-defined]

    w = 28
    i = max(0, min(i, n))  # clamp
    pct = 0 if not n else int((i / n) * 100)
    filled = 0 if not n else int((i / n) * w)
    bar = "#" * filled + "-" * (w - filled)
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
    cfg = load_or_init_config(os.path.join(here, "config.json"))
    comfy_root, custom_nodes = guess_paths(cfg)
    pip = pip_cmd(cfg)

    print("--> Some useful commands <--\n" +
          "check package info - " + Fore.BLUE + "pip show <PACKAGE_NAME>\n" + Style.RESET_ALL +
          "get all available versions of package - " + Fore.BLUE + "pip index versions <PACKAGE_NAME>\n" + Style.RESET_ALL +
          "get all deep dependencies from a package (need pipdeptree) - " + Fore.BLUE + "pipdeptree -p <PACKAGE_NAME>\n" + Style.RESET_ALL +
          "get all reverse dependencies from a package (need pipdeptree) - " + Fore.BLUE + "pipdeptree --reverse --packages <PACKAGE_NAME>\n" + Style.RESET_ALL +
          "remove all items from the cache - " + Fore.BLUE + "pip cache purge\n" + Style.RESET_ALL)

    # Activation hints (conda/venv)
    env_type = (cfg.get("env_type") or "").lower()
    print(Style.BRIGHT + "--> Quick environment activation hints <--" + Style.RESET_ALL)
    if env_type == "venv":
        venv_folder = cfg.get("venv_path") or cfg.get("conda_env_folder") or cfg.get("conda_env") or "<venv_folder>"
        vcmd_cmd = f"cd /d {venv_folder}\ncall Scripts\\activate.bat"
        vcmd_ps = f"Set-Location -Path '{venv_folder}'; .\\Scripts\\Activate.ps1"
        print("venv (CMD):\n  " + Fore.BLUE + vcmd_cmd + Style.RESET_ALL)
        print("venv (PowerShell):\n  " + Fore.BLUE + vcmd_ps + Style.RESET_ALL + "\n")
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

    # Collect constraints from requirements
    allc: Dict[str, List[SourceConstraint]] = {}
    extra_reqs: List[Tuple[str, str, str]] = []  # repo, file, raw line
    for reqf in find_reqs(comfy_root):
        reqs, extras = parse_req_file(reqf)
        for r in reqs:
            n = canonicalize_name(r.name)
            allc.setdefault(n, []).append(SourceConstraint("ComfyUI", reqf, r.specifier))
        for raw in extras:
            extra_reqs.append(("ComfyUI", reqf, raw))
    for pl in plugin_dirs(custom_nodes):
        for reqf in find_reqs(pl):
            reqs, extras = parse_req_file(reqf)
            for r in reqs:
                n = canonicalize_name(r.name)
                allc.setdefault(n, []).append(SourceConstraint(os.path.basename(pl), reqf, r.specifier))
            for raw in extras:
                extra_reqs.append((os.path.basename(pl), reqf, raw))

    names = sorted(allc)
    reports: Dict[str, PackageReport] = {}

    # Installed
    for i, n in enumerate(names, 1):
        reports[n] = PackageReport(n, inst_ver(n), allc[n])
        progress("Installed", i, len(names))

    # PyPI info
    for i, n in enumerate(names, 1):
        vs = fetch_pypi(n)
        rpt = reports[n]
        rpt.available_versions = vs
        specs = [c.spec for c in rpt.constraints if str(c.spec)]
        rpt.max_allowed = choose_max(vs, specs)
        if specs and not rpt.max_allowed:
            uniq_specs = ", ".join(sorted(set(str(s) for s in specs)))
            if not vs:
                rpt.update_error = f"No releases found on PyPI; constraint(s): {uniq_specs}"
            else:
                rpt.update_error = f"No release satisfies {uniq_specs}; latest available is {vs[-1]}"
        progress(f"PyPI ({n})", i, len(names))

    # Candidates
    cand: List[Tuple[str, Version]] = []
    missing: List[Tuple[str, Version]] = []
    downgrades: List[Tuple[str, Version]] = []
    for n, r in reports.items():
        if not r.max_allowed:
            continue
        if not r.installed:
            missing.append((n, r.max_allowed))
        elif r.installed < r.max_allowed:
            cand.append((n, r.max_allowed))
        elif r.installed > r.max_allowed:
            downgrades.append((n, r.max_allowed))

    # Classify candidates individually via pip --dry-run
    safe: List[Tuple[str, Version]] = []
    risky: List[Tuple[str, Version]] = []
    total = len(cand)
    for idx, (n, v) in enumerate(cand, 1):
        ok, out = dry_run(pip, [(n, v)])
        rpt = reports.get(n)
        if rpt is not None:
            rpt.update_ok = bool(ok)
            rpt.update_error = None if ok else (out or "")
        if ok:
            safe.append((n, v))
        else:
            risky.append((n, v))
        progress(f"Classify ({n})", idx, total)
    if total:
        progress(f"Classify ({total})", total, total)

    # Print per package
    for n in names:
        r = reports[n]
        tgt = r.max_allowed
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
        if r.update_error:
            print(" - Constraint issue:", Fore.RED + (r.update_error or "") + Style.RESET_ALL)

        if action == "install":
            print(" - Update: " + Fore.RED + f"Not installed; will be added at {tgt}" + Style.RESET_ALL)
        elif action == "upgrade":
            print(" - Update: " + Fore.GREEN + f"Upgrade to {tgt} suggested" + Style.RESET_ALL)
        elif action == "downgrade":
            print(" - Update: " + Fore.YELLOW + f"Installed {r.installed} ABOVE allowed {tgt}; consider downgrade" + Style.RESET_ALL)
        print()

    def cmdline(lst: List[Tuple[str, Version]]) -> str:
        return " ".join(f"{n}=={v}" for n, v in lst) if lst else "(nothing)"

    print(Style.BRIGHT + "=== Final commands ===" + Style.RESET_ALL)
    safe_cmd = " ".join(pip) + " install --upgrade " + cmdline(safe)
    risky_cmd = " ".join(pip) + " install --upgrade " + cmdline(risky)
    missing_cmd = " ".join(pip) + " install " + cmdline(missing)
    print("Safe updates:\n  " + Fore.BLUE + safe_cmd + Style.RESET_ALL)
    print("Risky updates:\n  " + Fore.BLUE + risky_cmd + Style.RESET_ALL)
    print("Missing:\n  " + Fore.BLUE + missing_cmd + Style.RESET_ALL)
    if downgrades:
        down_cmd = " ".join(pip) + " install " + cmdline(downgrades)
        print("Downgrades:\n  " + Fore.BLUE + down_cmd + Style.RESET_ALL)
    if extra_reqs:
        extra_unique = sorted(set(raw for _, _, raw in extra_reqs))
        print("VCS/URL requirements:")
        for repo, file, raw in extra_reqs:
            print(f"  - {repo} [{file}] -> {raw}")
        extra_cmd = " ".join(pip) + " install " + " ".join(extra_unique)
        print("Install VCS/URL entries:\n  " + Fore.BLUE + extra_cmd + Style.RESET_ALL)


if __name__ == "__main__":
    main()
