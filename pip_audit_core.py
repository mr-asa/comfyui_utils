"""Data side of the pip audit: scan requirements, compare with the installed set, classify updates.

run_audit() returns an AuditResult and never prints or prompts, so the console script and a GUI can share it.
"""

from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import requests
from packaging.requirements import Requirement
from packaging.specifiers import Specifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from envtools import (
    dedupe_dirs,
    enabled_node_dirs,
    find_requirements_files,
    install_prefix,
    parse_requirements_file,
    python_cmd,
    quote_cmd_part,
    unique_paths,
)

try:
    from importlib import metadata as importlib_metadata
except ImportError:  # python<3.8
    import importlib_metadata  # type: ignore

COMFY_ENV_FILE = "comfy-env.toml"
COMFY_ENV_ROOT_FILE = "comfy-env-root.toml"


def _is_ignored_walk_dir(name: str) -> bool:
    low = name.lower()
    return (
        low in {".git", "__pycache__", ".pixi", ".venv", "venv", "env", "node_modules"}
        or low.endswith(".disable")
    )


def _find_comfy_env_configs(plugin_dir: str) -> List[str]:
    out: List[str] = []
    for root, dirs, files in os.walk(plugin_dir):
        dirs[:] = [d for d in dirs if not _is_ignored_walk_dir(d)]
        if COMFY_ENV_FILE in files:
            out.append(os.path.join(root, COMFY_ENV_FILE))
    return sorted(out)


def _comfy_env_workspace() -> str:
    override = os.environ.get("COMFY_ENV_ROOT", "").strip()
    if override:
        return os.path.normpath(override)
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        if local:
            return os.path.normpath(os.path.join(local, "Programs", "comfy-env"))
    return os.path.normpath(os.path.expanduser("~/.ce"))


def _comfy_env_base_plugin_name(plugin_dir: str) -> str:
    name = os.path.basename(os.path.normpath(plugin_dir)).strip().lower()
    if name.startswith("comfyui-"):
        name = name[len("comfyui-") :]
    return name


def _comfy_env_name(plugin_dir: str, config_path: str) -> str:
    base = _comfy_env_base_plugin_name(plugin_dir)
    cfg_dir = os.path.dirname(os.path.normpath(config_path))
    rel = os.path.relpath(cfg_dir, plugin_dir)
    if rel in ("", "."):
        return base
    parts = [
        re.sub(r"[^a-z0-9]+", "-", p.strip().lower()).strip("-")
        for p in rel.split(os.sep)
        if p.strip()
    ]
    suffix = "-".join(p for p in parts if p)
    return f"{base}-{suffix}" if suffix else base


def _format_python_script_cmd(py: List[str], script_path: str) -> str:
    return " ".join([quote_cmd_part(p) for p in py + [script_path]])


def format_comfy_env_workspace_install_cmd(py: List[str], comfy_root: str, node_dir: str) -> str:
    code = (
        "import sys; "
        f"sys.path.insert(0, r'{comfy_root}'); "
        "from pathlib import Path; "
        "from comfy_env import install; "
        f"install(node_dir=Path(r'{node_dir}'))"
    )
    return " ".join([quote_cmd_part(p) for p in py + ["-c", code]])



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
    safe_update: Optional[Version] = None
    risky_update: Optional[Version] = None


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


@dataclass
class ComfyEnvIsolation:
    config: str
    subdir: str
    env_name: str
    env_path: str
    built: bool
    stale: bool = False
    stale_sources: List[str] = field(default_factory=list)


@dataclass
class ComfyEnvReport:
    repo: str
    plugin_dir: str
    root_config: Optional[str] = None
    install_py: Optional[str] = None
    isolations: List[ComfyEnvIsolation] = field(default_factory=list)


def _path_mtime(path: str) -> Optional[float]:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _comfy_env_rebuild_sources(plugin_dir: str, root_config: Optional[str], install_py: Optional[str], cfg_path: str) -> List[str]:
    sources = [cfg_path]
    if root_config:
        sources.append(root_config)
    if install_py:
        sources.append(install_py)
    req_path = os.path.join(plugin_dir, "requirements.txt")
    if os.path.isfile(req_path):
        sources.append(req_path)
    return unique_paths(sources)


def inspect_comfy_env_plugin(plugin_dir: str) -> Optional[ComfyEnvReport]:
    repo = os.path.basename(os.path.normpath(plugin_dir))
    root_config = os.path.join(plugin_dir, COMFY_ENV_ROOT_FILE)
    has_root_config = os.path.isfile(root_config)
    configs = _find_comfy_env_configs(plugin_dir)
    if not has_root_config and not configs:
        return None

    workspace = _comfy_env_workspace()
    install_py = os.path.join(plugin_dir, "install.py")
    report = ComfyEnvReport(
        repo=repo,
        plugin_dir=plugin_dir,
        root_config=root_config if has_root_config else None,
        install_py=install_py if os.path.isfile(install_py) else None,
    )
    for cfg_path in configs:
        env_name = _comfy_env_name(plugin_dir, cfg_path)
        env_path = os.path.join(workspace, "envs", env_name, ".pixi", "envs", "default")
        cfg_dir = os.path.dirname(os.path.normpath(cfg_path))
        built = os.path.isdir(env_path)
        stale_sources: List[str] = []
        env_mtime = _path_mtime(env_path) if built else None
        if env_mtime is not None:
            for src in _comfy_env_rebuild_sources(plugin_dir, report.root_config, report.install_py, cfg_path):
                src_mtime = _path_mtime(src)
                if src_mtime is not None and src_mtime > env_mtime:
                    stale_sources.append(src)
        report.isolations.append(
            ComfyEnvIsolation(
                config=cfg_path,
                subdir=os.path.relpath(cfg_dir, plugin_dir),
                env_name=env_name,
                env_path=env_path,
                built=built,
                stale=bool(stale_sources),
                stale_sources=stale_sources,
            )
        )
    return report


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
    py = python_cmd(cfg)
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


def _target_python_version(cfg: dict) -> str:
    """
    Return target environment Python version (major.minor.micro) used for compatibility checks.
    Falls back to current interpreter version on any failure.
    """
    fallback = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py = python_cmd(cfg)
    try:
        p = subprocess.run(
            py + ["-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
        )
        if p.returncode == 0 and p.stdout:
            val = p.stdout.strip()
            if re.match(r"^\d+\.\d+\.\d+$", val):
                return val
    except Exception:
        pass
    return fallback


def inst_ver_from_map(installed: Dict[str, dict], name: str) -> Optional[Version]:
    try:
        info = installed.get(canonicalize_name(name))
        if not info:
            return None
        ver = info.get("version") or ""
        return Version(ver) if ver else None
    except Exception:
        return None


def wheel_local_suffix_for_env(cfg: dict) -> str:
    base = "cp-"
    py = python_cmd(cfg)
    code = (
        "import json, sys\n"
        "data={\"cu\":\"\",\"torch\":\"\",\"rocm\":\"\",\"cp\":f\"cp{sys.version_info.major}{sys.version_info.minor}\"}\n"
        "try:\n"
        " import torch\n"
        " tv=getattr(torch,'__version__',None)\n"
        " if isinstance(tv,str) and tv.strip():\n"
        "  data['torch']='torch'+tv.strip().split('+',1)[0]\n"
        " tver=getattr(torch,'version',None)\n"
        " if tver is not None:\n"
        "  cu=getattr(tver,'cuda',None)\n"
        "  if isinstance(cu,str) and cu.strip():\n"
        "   data['cu']='cu'+cu.replace('.','').strip()\n"
        "  hip=getattr(tver,'hip',None)\n"
        "  if isinstance(hip,str) and hip.strip():\n"
        "   data['rocm']='rocm'+hip.strip()\n"
        "except Exception:\n"
        " pass\n"
        "print(json.dumps(data))\n"
    )
    try:
        p = subprocess.run(py + ["-c", code], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=20)
        if p.returncode != 0 or not p.stdout:
            return base
        lines = [ln.strip() for ln in p.stdout.splitlines() if ln.strip()]
        if not lines:
            return base
        data = json.loads(lines[-1])
        cu = str(data.get("cu") or "").strip()
        torch_tag = str(data.get("torch") or "").strip()
        rocm = str(data.get("rocm") or "").strip()
        cp = str(data.get("cp") or "").strip()
        parts = [p for p in (cu, torch_tag, rocm, cp) if p]
        return " ".join(parts) if parts else base
    except Exception:
        return base


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


def fetch_pypi(name: str, py_ver: Optional[str] = None) -> Tuple[List[Version], List[str], List[str]]:
    """Return (stable versions compatible with current Python, skipped_incompatible_descriptions, filtered_prereleases)."""
    try:
        r = requests.get(f"https://pypi.org/pypi/{name}/json", timeout=12)
        if r.status_code != 200:
            return [], [], []
        data = r.json()
        if not py_ver:
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


def _simplify_constraint_specs(specs: List[SpecifierSet]) -> str:
    """
    Build a compact human-readable constraints string:
    - de-duplicate clauses
    - collapse multiple lower bounds (>=, >) to the strictest one
    - collapse multiple upper bounds (<=, <) to the strictest one
    """
    raw_parts: List[str] = []
    seen_raw: set[str] = set()
    for spec_set in specs:
        text = str(spec_set).strip()
        if not text:
            continue
        for part in [p.strip() for p in text.split(",") if p.strip()]:
            if part in seen_raw:
                continue
            seen_raw.add(part)
            raw_parts.append(part)

    lower_specs: List[Tuple[Specifier, Version]] = []
    upper_specs: List[Tuple[Specifier, Version]] = []
    passthrough: List[str] = []

    for part in raw_parts:
        try:
            sp = Specifier(part)
            v = Version(sp.version)
        except Exception:
            passthrough.append(part)
            continue
        if sp.operator in {">", ">="}:
            lower_specs.append((sp, v))
        elif sp.operator in {"<", "<="}:
            upper_specs.append((sp, v))
        else:
            passthrough.append(part)

    out: List[str] = []
    out.extend(passthrough)

    if lower_specs:
        # Strictest lower bound = highest version; for equal version, '>' is stricter than '>='.
        best_lower, _ = max(lower_specs, key=lambda x: (x[1], 1 if x[0].operator == ">" else 0))
        out.append(str(best_lower))

    if upper_specs:
        # Strictest upper bound = lowest version; for equal version, '<' is stricter than '<='.
        best_upper, _ = min(upper_specs, key=lambda x: (x[1], 0 if x[0].operator == "<" else 1))
        out.append(str(best_upper))

    if not out:
        return ""
    return ", ".join(out)


def dedupe_pkg_versions(items: List[Tuple[str, Version]]) -> List[Tuple[str, Version]]:
    by_name: Dict[str, Version] = {}
    for n, v in items:
        prev = by_name.get(n)
        if prev is None or v > prev:
            by_name[n] = v
    return sorted(by_name.items(), key=lambda x: x[0])


def _find_safe_upgrade_fallback(
    pkg_name: str,
    rpt: PackageReport,
    reverse_map: Dict[str, List[Tuple[str, SpecifierSet]]],
    pip: List[str],
    upper_exclusive: Optional[Version] = None,
    max_dry_run_checks: int = 12,
) -> Optional[Version]:
    """
    Find highest safe upgrade for an installed package:
    - above installed
    - below upper_exclusive (if set)
    - satisfies direct requirement constraints
    - satisfies reverse dependency constraints
    - passes pip --dry-run as safe
    """
    if not rpt.installed:
        return None
    versions = sorted(set(rpt.available_versions), reverse=True)
    if not versions:
        return None
    direct_specs = [c.spec for c in rpt.constraints if str(c.spec)]
    checks = 0
    for cand in versions:
        if cand <= rpt.installed:
            continue
        if upper_exclusive and cand >= upper_exclusive:
            continue
        if rpt.max_allowed and cand > rpt.max_allowed:
            continue
        if direct_specs and not all(cand in s for s in direct_specs):
            continue
        if find_reverse_conflicts(reverse_map, pkg_name, cand):
            continue
        ok, out = dry_run(pip, [(pkg_name, cand)])
        cls, _err = classify_dry_run(ok, out)
        checks += 1
        if cls == "safe":
            return cand
        if checks >= max_dry_run_checks:
            break
    return None


def dry_run(pip: List[str], pkgs: List[Tuple[str, Version]], timeout_s: int = 60) -> Tuple[bool, str]:
    """Run a resolver simulation. Returns (ok, output).
    If --dry-run is unsupported on this pip, consider it ok to avoid losing items.
    """
    if not pkgs:
        return True, ""
    args = pip + ["--dry-run"] + [f"{n}=={v}" for n, v in pkgs]
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
        "no solution found",
        "unsatisfiable",
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
        or "there is no version of" in lowered
        or "no wheels with a matching" in lowered
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


@dataclass
class AuditResult:
    names: List[str]
    reports: Dict[str, PackageReport]
    extra_reqs: List[VcsReport]
    comfy_env_reports: List[ComfyEnvReport]
    commented_reqs: List[Tuple[str, str, str]]
    safe: List[Tuple[str, Version]]
    risky: List[Tuple[str, Version]]
    unknown: List[Tuple[str, Version]]
    missing: List[Tuple[str, Version]]
    downgrades: List[Tuple[str, Version]]
    held: List[str]
    pinned_ok: List[Tuple[str, str]]
    pinned_mismatch: List[Tuple[str, str]]
    install_cmd: List[str]


def package_action(r: PackageReport) -> Optional[str]:
    """install / upgrade / downgrade, or None when the package already matches its constraints."""
    tgt = r.installable_max or r.max_allowed
    if not r.installed and tgt:
        return "install"
    if tgt and r.installed and r.installed < tgt:
        return "upgrade"
    if tgt and r.installed and r.installed > tgt:
        return "downgrade"
    return None


def package_unchanged(r: PackageReport, pin_off: bool) -> bool:
    """Nothing to do and nothing to report for this package."""
    return not (package_action(r) or r.update_error or r.availability_issue or pin_off)


def audit_rows(result: AuditResult, hold_set: set, pin_map: Dict[str, str]) -> List[dict]:
    """One JSON-friendly row per package: what a table (or a GUI) needs without touching the dataclasses."""
    safe = dict(result.safe)
    risky = dict(result.risky)
    unknown = dict(result.unknown)
    rows = []
    for n in result.names:
        r = result.reports[n]
        action = package_action(r)
        pin_off = n in pin_map and str(r.installed) != pin_map[n]
        if n in pin_map:
            state = "pin-mismatch" if pin_off else "pinned"
        elif n in hold_set:
            state = "held"
        elif action == "install":
            state = "missing"
        elif action == "downgrade":
            state = "downgrade"
        elif action == "upgrade":
            state = "safe" if n in safe else "risky" if n in risky else "unknown" if n in unknown else "upgrade"
        else:
            state = "ok"
        rows.append({
            "name": n,
            "installed": str(r.installed) if r.installed else "",
            "target": str(r.installable_max or r.max_allowed or ""),
            "safe_update": str(safe.get(n) or r.safe_update or ""),
            "risky_update": str(risky.get(n) or r.risky_update or ""),
            "action": action or "",
            "state": state,
            "unchanged": package_unchanged(r, pin_off),
            "issue": r.update_error or r.availability_issue or "",
            "pin": pin_map.get(n, ""),
            "used_in": [{"repo": c.repo, "spec": str(c.spec)} for c in r.constraints],
        })
    return rows


def run_audit(
    cfg: dict,
    comfy_root: str,
    custom_nodes_paths: List[str],
    hold_set: set,
    pin_map: Dict[str, str],
    force_pip: bool = False,
    classify_workers: Optional[int] = None,
    installed_map: Optional[Dict[str, dict]] = None,
    progress: Optional[Callable[[str, int, int, str], None]] = None,
) -> AuditResult:
    def report(label: str, i: int, n: int, suffix: str = "") -> None:
        if progress:
            progress(label, i, n, suffix)

    pip = install_prefix(cfg, force_pip=force_pip)
    if installed_map is None:
        installed_map = load_installed_dists(cfg)
    # Collect constraints from requirements
    allc: Dict[str, List[SourceConstraint]] = {}
    extra_reqs: List[VcsReport] = []
    commented_reqs: List[Tuple[str, str, str]] = []
    for reqf in find_requirements_files(comfy_root):
        reqs, extras, commented = parse_requirements_file(reqf)
        for r in reqs:
            n = canonicalize_name(r.name)
            allc.setdefault(n, []).append(SourceConstraint("ComfyUI", reqf, r.specifier))
        for raw in extras:
            nm, url, ref = parse_vcs_line(raw)
            nm_c = canonicalize_name(nm) if nm else None
            if not nm_c and url:
                nm_c = infer_name_from_url(url)
            extra_reqs.append(VcsReport("ComfyUI", reqf, raw, nm_c, url, ref))
        for raw in commented:
            commented_reqs.append(("ComfyUI", reqf, raw))
    plugin_paths: List[str] = []
    for cn in custom_nodes_paths:
        plugin_paths.extend(enabled_node_dirs(cn))
    plugin_paths = dedupe_dirs(plugin_paths)
    comfy_env_reports: List[ComfyEnvReport] = []
    for pl in plugin_paths:
        ce_report = inspect_comfy_env_plugin(pl)
        if ce_report:
            comfy_env_reports.append(ce_report)
        for reqf in find_requirements_files(pl):
            reqs, extras, commented = parse_requirements_file(reqf)
            for r in reqs:
                n = canonicalize_name(r.name)
                allc.setdefault(n, []).append(SourceConstraint(os.path.basename(pl), reqf, r.specifier))
            for raw in extras:
                nm, url, ref = parse_vcs_line(raw)
                nm_c = canonicalize_name(nm) if nm else None
                if not nm_c and url:
                    nm_c = infer_name_from_url(url)
                extra_reqs.append(VcsReport(os.path.basename(pl), reqf, raw, nm_c, url, ref))
            for raw in commented:
                commented_reqs.append((os.path.basename(pl), reqf, raw))

    names = sorted(allc)
    reports: Dict[str, PackageReport] = {}
    vcs_reports: List[VcsReport] = []

    # Installed
    for i, n in enumerate(names, 1):
        reports[n] = PackageReport(n, inst_ver_from_map(installed_map, n), allc[n])
        report("Installed", i, len(names))
    for i, v in enumerate(extra_reqs, 1):
        inst_ver, inst_name, inst_url, inst_commit = _resolve_vcs_installed(installed_map, v)
        v.installed = inst_ver
        v.installed_name = inst_name
        v.installed_url = inst_url
        v.installed_commit = inst_commit
        report("Installed (VCS)", i, len(extra_reqs))

    # PyPI info
    target_py_ver = _target_python_version(cfg)
    for i, n in enumerate(names, 1):
        vs, skipped, filtered = fetch_pypi(n, py_ver=target_py_ver)
        rpt = reports[n]
        rpt.available_versions = vs
        rpt.py_incompatible = skipped
        rpt.prerelease_filtered = filtered
        specs = [c.spec for c in rpt.constraints if str(c.spec)]
        rpt.max_allowed = choose_max(vs, specs)
        rpt.installable_max = rpt.max_allowed
        if specs and not rpt.max_allowed:
            uniq_specs = _simplify_constraint_specs(specs)
            if not vs:
                rpt.update_error = f"No releases found on PyPI; constraint(s): {uniq_specs}"
            else:
                rpt.update_error = f"No release satisfies {uniq_specs}; latest available is {vs[-1]}"
        elif not vs and skipped:
            rpt.update_error = "All available releases require a different Python version"
        report("PyPI", i, len(names), n)

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
        report("VCS refs", i, len(extra_reqs))
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
    cfg_workers = cfg.get("pip_audit_classify_workers")
    requested_workers = classify_workers
    if requested_workers is None and isinstance(cfg_workers, int):
        requested_workers = cfg_workers
    auto_workers = max(4, min(16, (os.cpu_count() or 4) * 2))
    classify_workers = requested_workers if isinstance(requested_workers, int) else auto_workers
    if classify_workers < 1:
        classify_workers = 1

    initial_results: Dict[str, Tuple[Version, List[str], bool, str, str, str]] = {}

    def _initial_classify(name: str, ver: Version) -> Tuple[Version, List[str], bool, str, str, str]:
        conflicts_local = find_reverse_conflicts(reverse_map, name, ver)
        if conflicts_local:
            return (
                ver,
                conflicts_local,
                False,
                "",
                "risky",
                "Reverse dependency conflict: " + "; ".join(conflicts_local),
            )
        ok_local, out_local = dry_run(pip, [(name, ver)])
        cls_local, err_local = classify_dry_run(ok_local, out_local)
        return ver, [], ok_local, out_local, cls_local, err_local or ""

    if total <= 1 or classify_workers == 1:
        for idx, (n, v) in enumerate(cand, 1):
            initial_results[n] = _initial_classify(n, v)
            report("Classify", idx, total, n)
    else:
        done = 0
        max_workers = min(classify_workers, total)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            fut_to_name = {ex.submit(_initial_classify, n, v): n for n, v in cand}
            for fut in as_completed(fut_to_name):
                n = fut_to_name[fut]
                try:
                    initial_results[n] = fut.result()
                except Exception as e:
                    cur_ver = next((vv for nn, vv in cand if nn == n), Version("0"))
                    initial_results[n] = (cur_ver, [], False, str(e), "unknown", str(e))
                done += 1
                report("Classify", done, total, n)
    if total:
        report("Classify", total, total)

    for n, v in cand:
        ver_checked, conflicts, ok, out, cls, err = initial_results.get(
            n, (v, [], False, "", "unknown", "internal classification error")
        )
        v = ver_checked
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
                continue
        rpt = reports.get(n)
        if rpt is not None:
            rpt.update_ok = cls == "safe"
            rpt.update_error = None if cls == "safe" else (err or "")
            rpt.reverse_conflicts = conflicts
            if cls == "safe":
                rpt.safe_update = v
            elif cls == "risky":
                rpt.risky_update = v
        if cls == "safe":
            safe.append((n, v))
        elif cls == "risky":
            risky.append((n, v))
        else:
            unknown.append((n, v))

        # If latest allowed is risky/unknown, try to find best lower safe upgrade too.
        if rpt is not None and cls != "safe":
            fallback = _find_safe_upgrade_fallback(
                n,
                rpt,
                reverse_map,
                pip,
                upper_exclusive=v,
            )
            if fallback:
                safe.append((n, fallback))
                rpt.safe_update = fallback
                rpt.update_ok = True

    return AuditResult(
        names, reports, extra_reqs, comfy_env_reports, commented_reqs,
        dedupe_pkg_versions(safe), dedupe_pkg_versions(risky), dedupe_pkg_versions(unknown),
        missing, downgrades, held, pinned_ok, pinned_mismatch, pip,
    )
