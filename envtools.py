"""Shared helpers for the ComfyUI utils: paths, requirements files, venv python and the uv/pip command.

Nothing here prints or prompts, so the CLI scripts and a GUI can both build on it.
"""

from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Tuple

from packaging.markers import Marker
from packaging.requirements import Requirement

from comfyui_root import default_custom_nodes

REQ_FILE_RE = re.compile(r"^requirements\.txt$", re.I)


def norm_real_path(path: str) -> str:
    try:
        return os.path.normcase(os.path.normpath(os.path.realpath(path)))
    except Exception:
        return os.path.normcase(os.path.normpath(path))


def dedupe_dirs(paths: List[str]) -> List[str]:
    """Existing directories only; junctions and duplicates pointing at the same place are dropped."""
    out: List[str] = []
    seen: set[str] = set()
    for p in paths:
        if not p:
            continue
        norm = os.path.normcase(os.path.normpath(p))
        real = norm_real_path(p)
        if norm in seen or real in seen:
            continue
        if not os.path.isdir(p):
            continue
        out.append(os.path.normpath(p))
        seen.add(norm)
        seen.add(real)
    return out


def unique_paths(paths: List[str]) -> List[str]:
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


def quote_cmd_part(part: str) -> str:
    if not part:
        return '""'
    if re.search(r"\s", part):
        return f'"{part}"'
    return part


def custom_nodes_paths(cfg: dict, comfy_root: str = "") -> List[str]:
    """custom_nodes folders from a legacy-style config (falls back to <root>/custom_nodes)."""
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
    return dedupe_dirs(paths)


def enabled_node_dirs(custom_nodes: str) -> List[str]:
    """Top-level plugin folders of a custom_nodes directory, `*.disable` skipped."""
    out: List[str] = []
    try:
        entries = sorted(os.listdir(custom_nodes))
    except OSError:
        return out
    for n in entries:
        p = os.path.join(custom_nodes, n)
        try:
            if os.path.isdir(p) and not n.endswith(".disable"):
                out.append(p)
        except OSError:
            # broken or inaccessible junction
            continue
    return out


def find_requirements_files(folder: str) -> List[str]:
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
        # keep the entry when the marker cannot be parsed
        return True


def parse_requirements_file(path: str, evaluate_markers: bool = True) -> Tuple[List[Requirement], List[str], List[str]]:
    """Return (requirements, unparsed lines such as VCS/URL entries, commented lines).

    With evaluate_markers the host interpreter decides which entries apply;
    pass False when the target Python differs and the markers must be kept.
    """
    try:
        txt = open(path, encoding="utf-8").read()
    except Exception:
        return [], [], []
    reqs: List[Requirement] = []
    extras: List[str] = []
    commented_lines: List[str] = []
    for line in txt.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            c = s[1:].strip()
            if not c:
                continue
            if "#" in c:
                c = c.split("#", 1)[0].strip()
                if not c:
                    continue
            commented_lines.append(c)
            continue
        if s.startswith("-"):
            continue
        if "#" in s:
            s = s.split("#", 1)[0].strip()
            if not s:
                continue
        try:
            req = Requirement(s)
            if evaluate_markers and req.marker and not req.marker.evaluate():
                continue
            reqs.append(req)
        except Exception:
            # VCS/URL requirements and anything else packaging cannot parse
            if not evaluate_markers or _marker_allows_raw(s):
                extras.append(s)
    return reqs, extras, commented_lines


def python_cmd(cfg: dict) -> List[str]:
    """Python executable of the venv/conda env described by a legacy-style config."""
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


def _env_pip(cfg: dict) -> List[str]:
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


def install_prefix(cfg: dict, force_pip: bool = False) -> List[str]:
    """`uv pip install --python <py>` when uv is on PATH, else the env's `pip install`."""
    if not force_pip and shutil.which("uv"):
        return ["uv", "pip", "install", "--python", python_cmd(cfg)[0]]
    return _env_pip(cfg) + ["install"]


def uv_command() -> List[str]:
    exe = shutil.which("uv")
    return [exe] if exe else [sys.executable, "-m", "uv"]


def run_uv(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(uv_command() + list(args), check=check, text=True, capture_output=capture)


def run_logged(cmd: List[str], log: Callable[[str], None]) -> int:
    """Run a command and send its output line by line to `log`. Returns the exit code."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    for line in p.stdout:
        log(line.rstrip())
    return p.wait()


def installed_pythons(candidates: List[str]) -> List[str]:
    """Which of the "major.minor" candidates uv can find on this machine."""
    out = run_uv("python", "list", "--only-installed", "--output-format", "json", capture=True).stdout
    found = {f"{p['version_parts']['major']}.{p['version_parts']['minor']}" for p in json.loads(out)}
    return [p for p in candidates if p in found]
