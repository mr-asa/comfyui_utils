#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build a fresh ComfyUI venv with uv: pick the Python / CUDA / torch combination for which
all custom-node requirements resolve and the most prebuilt wheels (sageattention, flash-attn, ...) exist.

  python build_venv.py plan  [--remote]            # rank candidates, change nothing
  python build_venv.py build [--remote] [--python 3.13 --cuda cu130 --torch 2.11] [--name .venv_x]
  python build_venv.py wheels [--remote]           # list wheel catalog (local + remote)

Wheel folder and remote wheel sources: wheel_sources.json (next to this script).
Requirements are collected from the ComfyUI root and every enabled custom node (*.disable skipped).
"""

from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

import config_schema
from comfyui_root import resolve_comfyui_root
from wheels import Wheel, best_wheels, download_wheel, load_catalog, tup
from envtools import enabled_node_dirs, find_requirements_files, installed_pythons, parse_requirements_file, run_uv as uv

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
LOCKS_DIR = HERE / "locks"
TORCH_FAMILY = {"torch", "torchvision", "torchaudio", "triton", "triton-windows"}
PY_CANDIDATES = ["3.13", "3.12", "3.11", "3.10"]
CUDA_CANDIDATES = ["cu130", "cu128"]
SMOKE_IMPORTS = {"sageattention": "sageattention", "sageattn3": "sageattn3", "flash-attn": "flash_attn", "llama-cpp-python": "llama_cpp", "xformers": "xformers"}


# ---------------------------------------------------------------- requirements

def custom_nodes_dirs(root: Path) -> List[Path]:
    v2, _ = config_schema.load_v2(str(CONFIG_PATH), auto_migrate=False)
    paths = v2.get("paths", {})
    raw = [paths.get("custom_nodes_path", "")] + list(paths.get("custom_nodes_paths", []))
    dirs = [Path(p) for p in raw if p] or [root / "custom_nodes"]
    return [d for d in dirs if d.is_dir()]


def collect_requirements(root: Path, skip: set) -> Tuple[Dict[str, List[str]], List[str]]:
    """Return ({source: [requirement lines]}, [vcs/url lines])."""
    files = [(root.name, [str(root / "requirements.txt")]), ("manager", [str(root / "manager_requirements.txt")])]
    for d in custom_nodes_dirs(root):
        for node in enabled_node_dirs(str(d)):
            if os.path.basename(node) not in skip:
                files.append((os.path.basename(node), find_requirements_files(node)))
    by_src: Dict[str, List[str]] = {}
    vcs: List[str] = []
    for src, paths in files:
        lines = []
        for path in paths:
            reqs, unparsed, _ = parse_requirements_file(path, evaluate_markers=False)
            vcs += unparsed
            for r in reqs:
                if r.url:
                    vcs.append(str(r))
                elif canonicalize_name(r.name) not in TORCH_FAMILY:
                    lines.append(str(r))
        if lines or any(os.path.isfile(p) for p in paths):
            by_src[src] = lines
    return by_src, vcs


# ---------------------------------------------------------------- uv

def triton_spec(torch_mm: Tuple[int, int]) -> str:
    # triton-windows minor follows torch minor: torch 2.9 -> 3.5, 2.11 -> 3.7, 2.12 -> 3.8
    return f"triton-windows==3.{torch_mm[1] - 4}.*"


def write_inputs(tmp: Path, lines: List[str], torch_mm: Tuple[int, int]) -> Tuple[Path, Path]:
    t = f"{torch_mm[0]}.{torch_mm[1]}.*"
    pins = [f"torch=={t}", "torchvision", "torchaudio", triton_spec(torch_mm), "pip"]  # pip: run_comfyui.bat and nodes call python -m pip
    inp = tmp / "requirements.in"
    inp.write_text("\n".join(pins + lines) + "\n", encoding="utf-8")
    con = tmp / "constraints.txt"
    con.write_text(f"torch=={t}\n", encoding="utf-8")
    return inp, con


def compile_reqs(inp: Path, con: Path, py: str, cuda: str, out: Path) -> Tuple[bool, str]:
    r = uv("pip", "compile", str(inp), "-c", str(con), "--python-version", py, "--torch-backend", cuda,
           "--no-header", "--no-annotate", "-o", str(out), check=False, capture=True)
    return r.returncode == 0, (r.stderr or "")[-1500:]


def latest_torch(py: str, cuda: str, tmp: Path) -> Optional[Tuple[int, int]]:
    f = tmp / f"latest_{py}_{cuda}.in"
    f.write_text("torch\n", encoding="utf-8")
    out = tmp / f"latest_{py}_{cuda}.txt"
    r = uv("pip", "compile", str(f), "--python-version", py, "--torch-backend", cuda, "--no-header", "-o", str(out), check=False, capture=True)
    if r.returncode:
        return None
    m = re.search(r"^torch==(\d+)\.(\d+)", out.read_text(), re.M)
    return (int(m[1]), int(m[2])) if m else None


# ---------------------------------------------------------------- planning

@dataclass
class Candidate:
    py: str
    cuda: str
    torch_mm: Tuple[int, int]
    wheels: Dict[str, Tuple[Wheel, int]]
    resolved: Optional[bool] = None
    error: str = ""
    lines: Optional[List[str]] = None
    dropped: Optional[List[str]] = None
    loosened: Optional[List[str]] = None

    @property
    def score(self) -> Tuple:
        return (bool(self.resolved), -len(self.dropped or []), -len(self.loosened or []), len(self.wheels), sum(q for _, q in self.wheels.values()), tup(self.py), self.torch_mm)

    def label(self) -> str:
        return f"py{self.py} {self.cuda} torch{self.torch_mm[0]}.{self.torch_mm[1]}"


def cuda_tuple(cuda: str) -> Tuple[int, int]:
    return int(cuda[2:4]), int(cuda[4:])


def make_candidates(catalog: List[Wheel], wanted: List[str], pythons: List[str], tmp: Path) -> List[Candidate]:
    torch_seen = {w.torch[:2] for w in catalog if w.torch and w.torch[:2] >= (2, 7)}
    cands = []
    for py in pythons:
        for cuda in CUDA_CANDIDATES:
            torch_mms = set(torch_seen)
            lt = latest_torch(py, cuda, tmp)
            if lt:
                torch_mms.add(lt)
            for mm in torch_mms:
                if lt and mm > lt:
                    continue
                cands.append(Candidate(py, cuda, mm, best_wheels(catalog, wanted, py, mm, cuda_tuple(cuda))))
    return cands


def loosen(line: str) -> str:
    r = Requirement(line)
    return r.name + (f"[{','.join(sorted(r.extras))}]" if r.extras else "")


def why_failed(err: str) -> str:
    m = re.findall(r"Because.*?(?:unsatisfiable|conclude[^.]*)", err, re.S)
    return re.sub(r"\s+", " ", m[0])[:160] if m else ""


def lock_names(lock: Path) -> set:
    return {canonicalize_name(m[1]) for m in re.finditer(r"^([A-Za-z0-9_.\-]+)==", lock.read_text(), re.M)} if lock.exists() else set()


def verify(c: Candidate, ordered: List[Tuple[str, List[str]]], seeds: List[str], tmp: Path) -> None:
    """Add node requirements in priority order. A node that conflicts first gets its version pins loosened,
    then is added line by line; only lines that still cannot resolve are dropped."""
    d = tmp / c.label().replace(" ", "_")
    d.mkdir(exist_ok=True)
    lock = d / "lock.txt"
    accepted: List[str] = []
    c.dropped, c.loosened = [], []

    def attempt(lines: List[str]) -> Tuple[bool, str]:
        inp, con = write_inputs(d, sorted(set(accepted) | set(lines)), c.torch_mm)
        return compile_reqs(inp, con, c.py, c.cuda, lock)

    for i, (src, lines) in enumerate(ordered):
        if not lines:
            continue
        ok, err = attempt(lines)
        if ok:
            accepted = sorted(set(accepted) | set(lines))
            continue
        if i < 2:
            c.resolved, c.error = False, err
            return
        for line in lines:
            if attempt([line])[0]:
                accepted = sorted(set(accepted) | {line})
            elif attempt([loosen(line)])[0]:
                accepted = sorted(set(accepted) | {loosen(line)})
                c.loosened.append(f"{src}: {line}")
            else:
                c.dropped.append(f"{src}: {line} [{why_failed(attempt([line])[1])}]")
    attempt([])
    have = lock_names(lock)
    extra = [x for x in seeds if canonicalize_name(x) not in have]
    if extra and attempt(extra)[0]:
        accepted = sorted(set(accepted) | set(extra))
    else:
        for x in extra:
            if attempt([x])[0]:
                accepted = sorted(set(accepted) | {x})
            else:
                c.dropped.append(f"seed: {x}")
    c.resolved, c.lines = True, accepted


def seed_packages(source: Optional[str], known: set) -> List[str]:
    """Names installed in existing venvs (all not made by this script, or just `source`) that no requirements.txt declares."""
    v2, _ = config_schema.load_v2(str(CONFIG_PATH), auto_migrate=False)
    venvs = v2.get("environments", {}).get("venvs", {})
    if source:
        paths = [venvs.get(source, {}).get("path") or source]
    else:
        paths = [e["path"] for e in venvs.values() if e.get("path") and not str(e.get("comment", "")).startswith("build_venv.py")]
    names = set()
    for path in paths:
        py = config_schema.venv_python_path(path)
        if os.path.isfile(py):
            out = uv("pip", "list", "--python", py, "--format", "json", capture=True).stdout
            names |= {canonicalize_name(p["name"]) for p in json.loads(out)}
    skip = known | TORCH_FAMILY | {canonicalize_name(x) for x in SMOKE_IMPORTS} | {"pip", "setuptools", "wheel", "sam-2"}
    return sorted(n for n in names if n not in skip and not n.startswith(("nvidia-", "cumm", "spconv")))


def run_plan(args, tmp: Path):
    root = resolve_comfyui_root(str(CONFIG_PATH))
    by_src, vcs = collect_requirements(root, set(args.skip_nodes or []))
    excl = {canonicalize_name(x) for x in (args.exclude or [])}
    prefer = [p for p in (args.prefer or []) if p in by_src]
    head = [root.name, "manager"]
    order = head + prefer + sorted(k for k in by_src if k not in head + prefer)
    ordered = [(k, [l for l in by_src[k] if canonicalize_name(Requirement(l).name) not in excl]) for k in order if k in by_src]
    catalog, wheel_dir, wanted = load_catalog(args.remote)
    pythons = [args.python] if args.python else installed_pythons(PY_CANDIDATES)
    print(f"ComfyUI: {root}\nNodes with requirements: {sum(1 for v in by_src.values() if v)}, vcs/url: {len(vcs)}")
    print(f"Wheel catalog: {len(catalog)} for {', '.join(wanted)} (dir: {wheel_dir}); pythons: {', '.join(pythons)}")

    known = {canonicalize_name(Requirement(l).name) for _, ls in ordered for l in ls}
    seeds = [] if args.no_seed else seed_packages(args.seed_from, known)
    print(f"Seed packages from existing venvs (not in any requirements.txt): {len(seeds)}")
    cands = make_candidates(catalog, wanted, pythons, tmp)
    if args.cuda:
        cands = [c for c in cands if c.cuda == args.cuda]
    if args.torch:
        cands = [c for c in cands if f"{c.torch_mm[0]}.{c.torch_mm[1]}" == args.torch]
    cands.sort(key=lambda c: (len(c.wheels), sum(q for _, q in c.wheels.values()), tup(c.py), c.torch_mm), reverse=True)
    checked = cands[:args.top]
    print(f"Resolving {len(checked)} candidates (adding nodes one by one, this takes a while) ...", flush=True)
    with ThreadPoolExecutor(max_workers=len(checked) or 1) as ex:
        list(ex.map(lambda c: verify(c, ordered, seeds, tmp), checked))
    checked.sort(key=lambda c: c.score, reverse=True)

    print("\nRanked candidates:")
    for c in checked:
        have = ", ".join(f"{n}{'' if q == 3 else '?' if q == 1 else '~'}" for n, (_, q) in sorted(c.wheels.items()))
        status = f"loose {len(c.loosened)} drop {len(c.dropped)}" if c.resolved else "FAILED"
        print(f"  {c.label():26} {status:11} wheels {len(c.wheels)}/{len(wanted)}: {have or '-'}")
    print("  (? = untagged wheel, unverified; ~ = other CUDA minor, same major)")
    best = next((c for c in checked if c.resolved), None)
    if not best:
        print(f"\nNothing resolves. Error for {checked[0].label()}:\n{checked[0].error}")
        return None
    if best.loosened:
        print("\nVersion pins loosened (conflict with earlier requirements):\n  " + "\n  ".join(best.loosened))
    if best.dropped:
        print("\nRequirements that cannot be resolved (seed ones are retried with --no-deps on build):\n  " + "\n  ".join(best.dropped))
    if vcs:
        print(f"VCS/URL requirements (installed after the main set): {len(vcs)}")
    missing = [w for w in wanted if w not in best.wheels]
    print(f"\nBest: {best.label()}" + (f"; no wheel for: {', '.join(missing)}" if missing else ""))
    return best, vcs, wheel_dir


# ---------------------------------------------------------------- build

def run_build(args, tmp: Path):
    plan = run_plan(args, tmp)
    if not plan:
        return 1
    best, vcs, wheel_dir = plan
    root = resolve_comfyui_root(str(CONFIG_PATH))
    name = args.name or f".venv{best.py.replace('.', '')}_{best.cuda}_torch{best.torch_mm[0]}{best.torch_mm[1]}"
    venv = root / name
    if venv.exists():
        sys.exit(f"{venv} already exists; pass --name.")
    if not args.yes and input(f"\nCreate {venv}? [y/N] ").strip().lower() != "y":
        return 0

    d = tmp / "final"
    d.mkdir()
    inp, con = write_inputs(d, best.lines, best.torch_mm)
    lock = d / "lock.txt"
    ok, err = compile_reqs(inp, con, best.py, best.cuda, lock)
    if not ok:
        sys.exit(err)
    uv("venv", str(venv), "--python", best.py)
    py = str(venv / "Scripts" / "python.exe")
    while True:  # sdist-only packages that cannot build on Windows (e.g. cgal) are skipped
        r = uv("pip", "install", "--python", py, "-r", str(lock), "--torch-backend", best.cuda, check=False, capture=True)
        sys.stdout.write(r.stderr[-3000:] if r.returncode else "")
        bad = re.search(r"Failed to build `([A-Za-z0-9_.\-]+)==", r.stderr)
        if not r.returncode:
            break
        if not bad:
            sys.exit(r.stderr[-3000:])
        print(f"  ! cannot build {bad[1]}, skipped")
        lock.write_text("".join(l for l in lock.read_text().splitlines(True) if canonicalize_name(l.split("==")[0].strip()) != canonicalize_name(bad[1])), encoding="utf-8")
    (d / "installed.txt").write_text("".join(l for l in lock.read_text().splitlines(True) if not l.lower().startswith(("setuptools==", "wheel=="))), encoding="utf-8")

    wheel_paths = []
    for w, _ in best.wheels.values():
        src = w.path or download_wheel(w, wheel_dir, lambda m: print("  " + m))
        if src.name != w.filename:  # uv rejects url-quoted names such as %2B
            src = Path(shutil.copy(src, d / w.filename))
        wheel_paths.append(str(src))
    if wheel_paths:
        uv("pip", "install", "--python", py, "--no-deps", *wheel_paths)
    pip_install = ["pip", "install", "--python", py, "-c", str(d / "installed.txt"), "--torch-backend", best.cuda]
    if vcs:
        uv("pip", "install", "--python", py, "setuptools<70", "wheel")
    for line in vcs:
        # sources that import torch in setup.py need the venv's torch, so retry without build isolation
        if uv(*pip_install, line, check=False).returncode and uv(*pip_install, "--no-build-isolation", line, check=False).returncode:
            print(f"  ! failed: {line}")
    for line in [x[6:] for x in best.dropped if x.startswith("seed: ")]:
        if uv("pip", "install", "--python", py, "--no-deps", line, check=False).returncode:
            print(f"  ! failed (seed, --no-deps): {line}")

    uv("pip", "check", "--python", py, check=False)
    imports = [SMOKE_IMPORTS[n.replace("_", "-")] for n in best.wheels if n.replace("_", "-") in SMOKE_IMPORTS]
    code = "import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available());" + "".join(f"import {m};print('ok {m}');" for m in imports)
    subprocess.run([py, "-c", code], check=False)

    LOCKS_DIR.mkdir(exist_ok=True)
    frozen = uv("pip", "freeze", "--python", py, capture=True).stdout
    (LOCKS_DIR / f"{name}.txt").write_text(frozen, encoding="utf-8")
    v2, _ = config_schema.load_v2(str(CONFIG_PATH))
    previous = v2["runtime"]["selected"]
    v2 = config_schema.add_or_update_venv(v2, str(venv), name, f"build_venv.py: {best.label()}")
    if not args.select:
        v2["runtime"]["selected"] = previous
    config_schema.save_v2(str(CONFIG_PATH), v2)
    print(f"\nDone: {venv}\nLock: {LOCKS_DIR / (name + '.txt')}\nAdded to config.json" + (" and selected." if args.select else " (not selected, use --select)."))
    return 0


def run_wheels(args, tmp: Path):
    catalog, wheel_dir, wanted = load_catalog(args.remote)
    for w in sorted(catalog, key=lambda w: (w.name, w.pep_version())):
        cu = f"cu{w.cuda[0]}{w.cuda[1]}" if w.cuda else "-"
        t = ".".join(map(str, w.torch)) + ("+" if w.torch_min else "") if w.torch else "-"
        print(f"{'local ' if w.path else 'remote'} {w.filename:80} {cu:6} torch {t}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "build", "wheels"], nargs="?", default="plan")
    ap.add_argument("--remote", action="store_true", help="also list wheels from remote sources")
    ap.add_argument("--python", help="restrict to this Python, e.g. 3.13")
    ap.add_argument("--cuda", help="restrict to cu128 / cu130")
    ap.add_argument("--torch", help="restrict to torch major.minor, e.g. 2.11")
    ap.add_argument("--top", type=int, default=6, help="how many candidates to resolve with uv")
    ap.add_argument("--exclude", nargs="+", help="package names to drop from the requirements")
    ap.add_argument("--prefer", nargs="+", help="node folder names whose requirements win conflicts (after ComfyUI itself)")
    ap.add_argument("--skip-nodes", nargs="+", help="custom node folder names to ignore")
    ap.add_argument("--name", help="venv folder name under the ComfyUI root")
    ap.add_argument("--seed-from", help="venv name/path whose extra packages are carried over (default: all venvs not built by this script)")
    ap.add_argument("--no-seed", action="store_true")
    ap.add_argument("--select", action="store_true", help="make the new venv the selected one in config.json")
    ap.add_argument("-y", "--yes", action="store_true")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        if args.command == "wheels":
            return run_wheels(args, tmp)
        if args.command == "build":
            return run_build(args, tmp)
        return 0 if run_plan(args, tmp) else 1


if __name__ == "__main__":
    sys.exit(main())
