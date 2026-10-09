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
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

import config_schema
from comfyui_root import resolve_comfyui_root
from wheels import Wheel, best_wheels, download_wheel, load_catalog, tup
from envtools import enabled_node_dirs, find_requirements_files, installed_pythons, parse_requirements_file, run_logged, run_uv as uv, uv_command

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


class PlanCancelled(Exception):
    pass


def verify(c: Candidate, ordered: List[Tuple[str, List[str]]], seeds: List[str], tmp: Path, cancelled: Callable[[], bool] = lambda: False) -> None:
    """Add node requirements in priority order. A node that conflicts first gets its version pins loosened,
    then is added line by line; only lines that still cannot resolve are dropped."""
    d = tmp / c.label().replace(" ", "_")
    d.mkdir(exist_ok=True)
    lock = d / "lock.txt"
    accepted: List[str] = []
    c.dropped, c.loosened = [], []

    def attempt(lines: List[str]) -> Tuple[bool, str]:
        if cancelled():
            raise PlanCancelled()
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


@dataclass
class PlanOptions:
    remote: bool = False
    python: Optional[str] = None
    cuda: Optional[str] = None
    torch: Optional[str] = None
    top: int = 6
    exclude: List[str] = field(default_factory=list)
    prefer: List[str] = field(default_factory=list)
    skip_nodes: List[str] = field(default_factory=list)
    seed_from: Optional[str] = None
    no_seed: bool = False


@dataclass
class Plan:
    root: Path
    vcs: List[str]
    wheel_dir: Path
    wanted: List[str]
    candidates: List[Candidate]  # best first
    nodes_with_reqs: int
    seeds: int
    catalog_size: int
    pythons: List[str]

    @property
    def best(self) -> Optional[Candidate]:
        return next((c for c in self.candidates if c.resolved), None)


def make_plan(opts: PlanOptions, tmp: Path, log: Callable[[str], None] = print, cancelled: Callable[[], bool] = lambda: False) -> Plan:
    root = resolve_comfyui_root(str(CONFIG_PATH))
    by_src, vcs = collect_requirements(root, set(opts.skip_nodes))
    excl = {canonicalize_name(x) for x in opts.exclude}
    prefer = [p for p in opts.prefer if p in by_src]
    head = [root.name, "manager"]
    order = head + prefer + sorted(k for k in by_src if k not in head + prefer)
    ordered = [(k, [l for l in by_src[k] if canonicalize_name(Requirement(l).name) not in excl]) for k in order if k in by_src]
    catalog, wheel_dir, wanted = load_catalog(opts.remote)
    pythons = [opts.python] if opts.python else installed_pythons(PY_CANDIDATES)
    nodes = sum(1 for v in by_src.values() if v)
    log(f"ComfyUI: {root}\nNodes with requirements: {nodes}, vcs/url: {len(vcs)}")
    log(f"Wheel catalog: {len(catalog)} for {', '.join(wanted)} (dir: {wheel_dir}); pythons: {', '.join(pythons)}")

    known = {canonicalize_name(Requirement(l).name) for _, ls in ordered for l in ls}
    seeds = [] if opts.no_seed else seed_packages(opts.seed_from, known)
    log(f"Seed packages from existing venvs (not in any requirements.txt): {len(seeds)}")
    cands = make_candidates(catalog, wanted, pythons, tmp)
    if opts.cuda:
        cands = [c for c in cands if c.cuda == opts.cuda]
    if opts.torch:
        cands = [c for c in cands if f"{c.torch_mm[0]}.{c.torch_mm[1]}" == opts.torch]
    cands.sort(key=lambda c: (len(c.wheels), sum(q for _, q in c.wheels.values()), tup(c.py), c.torch_mm), reverse=True)
    checked = cands[:opts.top]
    log(f"Resolving {len(checked)} candidates (adding nodes one by one, this takes a while) ...")
    with ThreadPoolExecutor(max_workers=len(checked) or 1) as ex:
        list(ex.map(lambda c: verify(c, ordered, seeds, tmp, cancelled), checked))
    checked.sort(key=lambda c: c.score, reverse=True)
    return Plan(root, vcs, wheel_dir, wanted, checked, nodes, len(seeds), len(catalog), pythons)


def describe_plan(plan: Plan) -> str:
    out = ["Ranked candidates:"]
    for c in plan.candidates:
        have = ", ".join(f"{n}{'' if q == 3 else '?' if q == 1 else '~'}" for n, (_, q) in sorted(c.wheels.items()))
        status = f"loose {len(c.loosened)} drop {len(c.dropped)}" if c.resolved else "FAILED"
        out.append(f"  {c.label():26} {status:11} wheels {len(c.wheels)}/{len(plan.wanted)}: {have or '-'}")
    out.append("  (? = untagged wheel, unverified; ~ = other CUDA minor, same major)")
    best = plan.best
    if not best:
        out.append(f"\nNothing resolves. Error for {plan.candidates[0].label()}:\n{plan.candidates[0].error}" if plan.candidates else "\nNo candidates.")
        return "\n".join(out)
    if best.loosened:
        out.append("\nVersion pins loosened (conflict with earlier requirements):\n  " + "\n  ".join(best.loosened))
    if best.dropped:
        out.append("\nRequirements that cannot be resolved (seed ones are retried with --no-deps on build):\n  " + "\n  ".join(best.dropped))
    if plan.vcs:
        out.append(f"VCS/URL requirements (installed after the main set): {len(plan.vcs)}")
    missing = [w for w in plan.wanted if w not in best.wheels]
    out.append(f"\nBest: {best.label()}" + (f"; no wheel for: {', '.join(missing)}" if missing else ""))
    return "\n".join(out)


def options_from_args(args) -> PlanOptions:
    return PlanOptions(args.remote, args.python, args.cuda, args.torch, args.top, args.exclude or [], args.prefer or [], args.skip_nodes or [], args.seed_from, args.no_seed)


def run_plan(args, tmp: Path) -> Optional[Plan]:
    plan = make_plan(options_from_args(args), tmp)
    print("\n" + describe_plan(plan))
    return plan if plan.best else None


# ---------------------------------------------------------------- build

def default_venv_name(c: Candidate) -> str:
    return f".venv{c.py.replace('.', '')}_{c.cuda}_torch{c.torch_mm[0]}{c.torch_mm[1]}"


def build_venv(plan: Plan, c: Candidate, name: str, tmp: Path, log: Callable[[str], None] = print, select: bool = False) -> dict:
    """Create the venv for a resolved candidate and register it in config.json. Raises RuntimeError on failure."""
    venv = plan.root / name
    if venv.exists():
        raise RuntimeError(f"{venv} already exists")
    d = tmp / "final"
    d.mkdir(exist_ok=True)
    inp, con = write_inputs(d, c.lines, c.torch_mm)
    lock = d / "lock.txt"
    ok, err = compile_reqs(inp, con, c.py, c.cuda, lock)
    if not ok:
        raise RuntimeError(err)
    U = uv_command()
    run = lambda *a: run_logged(U + list(a), log)
    if run("venv", str(venv), "--python", c.py):
        raise RuntimeError("uv venv failed")
    py = str(venv / "Scripts" / "python.exe")
    while True:  # sdist-only packages that cannot build on Windows (e.g. cgal) are skipped
        out: List[str] = []
        code = run_logged(U + ["pip", "install", "--python", py, "-r", str(lock), "--torch-backend", c.cuda], lambda l: (out.append(l), log(l)))
        if not code:
            break
        bad = re.search(r"Failed to build `([A-Za-z0-9_.\-]+)==", "\n".join(out))
        if not bad:
            raise RuntimeError("installing the resolved set failed, see the output above")
        log(f"! cannot build {bad[1]}, skipped")
        lock.write_text("".join(l for l in lock.read_text().splitlines(True) if canonicalize_name(l.split("==")[0].strip()) != canonicalize_name(bad[1])), encoding="utf-8")
    (d / "installed.txt").write_text("".join(l for l in lock.read_text().splitlines(True) if not l.lower().startswith(("setuptools==", "wheel=="))), encoding="utf-8")

    wheel_paths = []
    for w, _ in c.wheels.values():
        src = w.path or download_wheel(w, plan.wheel_dir, log)
        if src.name != w.filename:  # uv rejects url-quoted names such as %2B
            src = Path(shutil.copy(src, d / w.filename))
        wheel_paths.append(str(src))
    if wheel_paths:
        run("pip", "install", "--python", py, "--no-deps", *wheel_paths)
    pip_install = ["pip", "install", "--python", py, "-c", str(d / "installed.txt"), "--torch-backend", c.cuda]
    if plan.vcs:
        run("pip", "install", "--python", py, "setuptools<70", "wheel")
    for line in plan.vcs:
        # sources that import torch in setup.py need the venv's torch, so retry without build isolation
        if run(*pip_install, line) and run(*pip_install, "--no-build-isolation", line):
            log(f"! failed: {line}")
    if plan.vcs:  # the old setuptools above is only for building those sources; put the resolved one back
        pinned = [l.strip() for l in lock.read_text().splitlines() if l.lower().startswith(("setuptools==", "wheel=="))]
        if pinned:
            run("pip", "install", "--python", py, *pinned)
    for line in [x[6:] for x in c.dropped if x.startswith("seed: ")]:
        if run("pip", "install", "--python", py, "--no-deps", line):
            log(f"! failed (seed, --no-deps): {line}")

    run("pip", "check", "--python", py)
    imports = [SMOKE_IMPORTS[n.replace("_", "-")] for n in c.wheels if n.replace("_", "-") in SMOKE_IMPORTS]
    code = "import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available());" + "".join(f"import {m};print('ok {m}');" for m in imports)
    run_logged([py, "-c", code], log)

    LOCKS_DIR.mkdir(exist_ok=True)
    frozen = uv("pip", "freeze", "--python", py, capture=True).stdout
    (LOCKS_DIR / f"{name}.txt").write_text(frozen, encoding="utf-8")
    v2, _ = config_schema.load_v2(str(CONFIG_PATH))
    previous = v2["runtime"]["selected"]
    v2 = config_schema.add_or_update_venv(v2, str(venv), name, f"build_venv.py: {c.label()}")
    if not select:
        v2["runtime"]["selected"] = previous
    config_schema.save_v2(str(CONFIG_PATH), v2)
    log(f"Done: {venv}\nLock: {LOCKS_DIR / (name + '.txt')}\nAdded to config.json" + (" and selected." if select else " (not selected)."))
    return {"venv": str(venv), "name": name, "lock": str(LOCKS_DIR / (name + ".txt"))}


def run_build(args, tmp: Path):
    plan = run_plan(args, tmp)
    if not plan:
        return 1
    best = plan.best
    name = args.name or default_venv_name(best)
    if (plan.root / name).exists():
        sys.exit(f"{plan.root / name} already exists; pass --name.")
    if not args.yes and input(f"\nCreate {plan.root / name}? [y/N] ").strip().lower() != "y":
        return 0
    try:
        build_venv(plan, best, name, tmp, print, args.select)
    except RuntimeError as e:
        sys.exit(str(e))
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
