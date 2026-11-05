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
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

import config_schema
from comfyui_root import resolve_comfyui_root
from wheels import Wheel, best_wheels, download_wheel, load_catalog, ranked_wheels, tup
from envtools import enabled_node_dirs, find_requirements_files, parse_requirements_file, run_logged, run_uv as uv, uv_command

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
LOCKS_DIR = HERE / "locks"
CACHE_PATH = LOCKS_DIR / "plan_cache.json"
CACHE_TTL = 24 * 3600
TORCH_FAMILY = {"torch", "torchvision", "torchaudio", "triton", "triton-windows"}
SEED_STEPS = 10  # share of the plan progress bar for the packages carried over from other venvs
PY_CANDIDATES = ["3.13", "3.12", "3.11", "3.10"]
CUDA_FALLBACK = ["cu130", "cu128"]
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


def write_inputs(tmp: Path, lines: List[str], torch_mm: Tuple[int, int], triton: bool = True) -> Tuple[Path, Path]:
    t = f"{torch_mm[0]}.{torch_mm[1]}.*"
    pins = [f"torch=={t}", "torchvision", "torchaudio", "pip"] + ([triton_spec(torch_mm)] if triton else [])  # pip: run_comfyui.bat and nodes call python -m pip
    inp = tmp / "requirements.in"
    inp.write_text("\n".join(pins + lines) + "\n", encoding="utf-8")
    con = tmp / "constraints.txt"
    con.write_text(f"torch=={t}\n", encoding="utf-8")
    return inp, con


@lru_cache(maxsize=None)
def torch_source(cuda: str) -> List[str]:
    """uv arguments that select the torch build. A CUDA tag newer than this uv knows is not accepted by
    --torch-backend, so the PyTorch index is passed directly."""
    with tempfile.TemporaryDirectory() as d:
        empty = Path(d) / "empty.in"
        empty.write_text("", encoding="utf-8")
        if uv("pip", "compile", str(empty), "--torch-backend", cuda, check=False, capture=True).returncode == 0:
            return ["--torch-backend", cuda]
    return ["--index", f"https://download.pytorch.org/whl/{cuda}"]


def uv_cancellable(args: List[str], cancelled: Callable[[], bool]) -> Tuple[int, str]:
    """Run uv and kill it as soon as `cancelled()` turns true. Returns (exit code, stderr)."""
    p = subprocess.Popen(uv_command() + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    while True:
        try:
            _, err = p.communicate(timeout=0.5)
            return p.returncode, err or ""
        except subprocess.TimeoutExpired:
            if cancelled():
                p.kill()
                p.communicate()
                raise PlanCancelled()


def compile_reqs(inp: Path, con: Path, py: str, cuda: str, out: Path, cached_only: bool = False, cancelled: Callable[[], bool] = lambda: False) -> Tuple[bool, str]:
    """cached_only: resolve from the uv cache without contacting the indexes; falls back to online when something is not cached."""
    args = ["pip", "compile", str(inp), "-c", str(con), "--python-version", py, *torch_source(cuda), "--no-header", "--no-annotate", "-o", str(out)]
    code, err = uv_cancellable(args + (["--offline"] if cached_only else []), cancelled)
    if code and cached_only and "offline" in err.lower():
        code, err = uv_cancellable(args, cancelled)
    return code == 0, err[-1500:]


@lru_cache(maxsize=1)
def uv_version() -> str:
    return uv("--version", capture=True).stdout.strip()


class AttemptCache:
    """Results of single resolves, kept on disk for a day: the same requirement set for the same python / CUDA / torch
    resolves the same way until the indexes change."""

    def __init__(self, path: Path = CACHE_PATH, ttl: float = CACHE_TTL):
        self.path = path
        self.lock = threading.Lock()
        self.data: Dict[str, list] = {}
        if path.exists():
            now = time.time()
            self.data = {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if now - v[0] < ttl}

    @staticmethod
    def key(c: "Candidate", reqs: tuple) -> str:
        return hashlib.sha1("\n".join([uv_version(), c.py, c.cuda, str(c.torch_mm), str(c.triton), *reqs]).encode()).hexdigest()

    def get(self, key: str) -> Optional[Tuple[bool, str]]:
        with self.lock:
            hit = self.data.get(key)
        return (hit[1], hit[2]) if hit else None

    def put(self, key: str, ok: bool, err: str) -> None:
        with self.lock:
            self.data[key] = [time.time(), ok, "" if ok else err]

    def save(self) -> None:
        with self.lock:
            self.path.parent.mkdir(exist_ok=True)
            self.path.write_text(json.dumps(self.data), encoding="utf-8")


def latest_torch(py: str, cuda: str, tmp: Path) -> Optional[Tuple[int, int]]:
    f = tmp / f"latest_{py}_{cuda}.in"
    f.write_text("torch\n", encoding="utf-8")
    out = tmp / f"latest_{py}_{cuda}.txt"
    r = uv("pip", "compile", str(f), "--python-version", py, *torch_source(cuda), "--no-header", "-o", str(out), check=False, capture=True)
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
    triton: bool = True  # False: no triton-windows release for this torch, resolved without it

    @property
    def score(self) -> Tuple:
        return (bool(self.resolved), -len(self.dropped or []), -len(self.loosened or []), len(self.wheels), sum(q for _, q in self.wheels.values()), tup(self.py), self.torch_mm)

    def label(self) -> str:
        return f"py{self.py} {self.cuda} torch{self.torch_mm[0]}.{self.torch_mm[1]}"


def cuda_tuple(cuda: str) -> Tuple[int, int]:
    return int(cuda[2:4]), int(cuda[4:])


@lru_cache(maxsize=1)
def cuda_candidates() -> List[str]:
    """CUDA builds (12.6+) PyTorch publishes wheels for, newest first; the fallback list if the index is unreachable."""
    try:
        html = requests.get("https://download.pytorch.org/whl/", timeout=15).text
    except requests.RequestException:
        return CUDA_FALLBACK
    tags = {t for t in re.findall(r">(cu\d{3})/?<", html) if cuda_tuple(t) >= (12, 6)}
    return sorted(tags, key=cuda_tuple, reverse=True) or CUDA_FALLBACK


def make_candidates(catalog: List[Wheel], wanted: List[str], pythons: List[str], cudas: List[str], tmp: Path) -> List[Candidate]:
    torch_seen = {w.torch[:2] for w in catalog if w.torch and w.torch[:2] >= (2, 7)}
    cands = []
    for py in pythons:
        for cuda in cudas:
            torch_mms = set(torch_seen)
            lt = latest_torch(py, cuda, tmp)
            if lt:  # releases newer than the newest wheel in the catalog still resolve, just without those wheels
                torch_mms |= {(lt[0], m) for m in range(max((mm[1] for mm in torch_mms if mm[0] == lt[0]), default=lt[1]), lt[1] + 1)}
            for mm in torch_mms:
                if lt and mm > lt:
                    continue
                cands.append(Candidate(py, cuda, mm, best_wheels(catalog, wanted, py, mm, cuda_tuple(cuda))))
    return cands


def version_filter(expr: str, value: str) -> bool:
    """Terms are separated by commas or spaces. Bare versions are alternatives ('2.12 2.14' = 2.12.x or 2.14.x),
    comparisons all have to hold ('>=2.12 <2.15'); both can be mixed. CUDA is written as cu128 or 12.8."""
    def norm(t: str) -> str:
        t = t.lower().removeprefix("cu")
        return f"{t[:2]}.{t[2:]}" if t.isdigit() and len(t) > 2 else t
    terms = [t for t in re.split(r"[,\s]+", expr.strip()) if t]
    ver = Version(norm(value))
    bare = [t for t in terms if t[0].isdigit() or t[:2].lower() == "cu"]
    cmp = [t for t in terms if t not in bare]
    ok_cmp = not cmp or ver in SpecifierSet(",".join(re.sub(r"^([<>=!~]+)(.+)$", lambda m: m[1] + norm(m[2]), t) for t in cmp))
    return ok_cmp and (not bare or any(ver in SpecifierSet(f"=={norm(t)}.*") for t in bare))


def loosen(line: str) -> str:
    r = Requirement(line)
    return r.name + (f"[{','.join(sorted(r.extras))}]" if r.extras else "")


def why_failed(err: str) -> str:
    m = re.findall(r"Because.*?(?:unsatisfiable|conclude[^.]*)", err, re.S)
    return re.sub(r"\s+", " ", m[0])[:160] if m else ""


def short_error(err: str) -> str:
    """The reason of a uv resolve failure: from the first 'Because' on, else the tail."""
    i = err.find("Because")
    return re.sub(r"\s+", " ", err[i:])[:360] if i >= 0 else re.sub(r"\s+", " ", err)[-360:]


def lock_names(lock: Path) -> set:
    return {canonicalize_name(m[1]) for m in re.finditer(r"^([A-Za-z0-9_.\-]+)==", lock.read_text(), re.M)} if lock.exists() else set()


class PlanCancelled(Exception):
    pass


def verify(c: Candidate, ordered: List[Tuple[str, List[str]]], seeds: List[str], tmp: Path, cache: AttemptCache, cancelled: Callable[[], bool] = lambda: False, log: Callable[[str], None] = print, progress: Callable[[int], None] = lambda n: None) -> None:
    """Add node requirements in priority order. A node that conflicts first gets its version pins loosened,
    then is added line by line; only lines that still cannot resolve are dropped."""
    d = tmp / c.label().replace(" ", "_")
    d.mkdir(exist_ok=True)
    lock = d / "lock.txt"
    accepted: List[str] = []
    c.dropped, c.loosened = [], []

    tag = f"[{c.py} {c.cuda} {c.torch_mm[0]}.{c.torch_mm[1]}]"

    resolved_once = False

    def attempt(lines: List[str], what: str = "", fresh: bool = False) -> Tuple[bool, str]:
        """Resolve accepted + lines. Sets resolved before (this plan or a plan within the last day) are answered from the cache;
        fresh=True resolves again to rewrite the lock."""
        nonlocal resolved_once
        if cancelled():
            raise PlanCancelled()
        reqs = tuple(sorted(set(accepted) | set(lines)))
        key = cache.key(c, reqs)
        hit = None if fresh else cache.get(key)
        if hit:
            return hit
        if what:
            log(f"{tag} {what}")
        inp, con = write_inputs(d, list(reqs), c.torch_mm, c.triton)
        ok, err = compile_reqs(inp, con, c.py, c.cuda, lock, cached_only=resolved_once, cancelled=cancelled)
        resolved_once = True
        cache.put(key, ok, err)
        return ok, err

    ok, err = attempt([], "torch pins")
    if not ok:  # torch pins alone do not resolve: retry without triton-windows, which may lack a release for this torch
        c.triton = False
        ok, err = attempt([], "torch pins without triton-windows")
        if not ok:
            c.resolved, c.error = False, err
            return
        c.dropped.append(f"torch pins: {triton_spec(c.torch_mm)} [no such triton-windows release, resolved without triton]")

    nodes = [(src, lines) for src, lines in ordered if lines]

    def extend(items: list, lines_of: Callable, what: str, fail: Callable, tick: Callable[[int], None]) -> None:
        """Accept the longest resolvable prefix of items (adding requirements can only break a resolve, so it is found
        by bisection), hand the first item that breaks it to `fail`, and go on with the rest."""
        nonlocal accepted
        i = 0
        while i < len(items):
            def run(k: int) -> List[str]:
                return [l for it in items[i:i + k] for l in lines_of(it)]
            lo, hi = 0, len(items) - i
            if attempt(run(hi), f"{what} {i + 1}-{len(items)} of {len(items)} together")[0]:
                accepted = sorted(set(accepted) | set(run(hi)))
                tick(len(items))
                return
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if attempt(run(mid), f"{what} {i + 1}-{i + mid} of {len(items)}")[0]:
                    lo = mid
                    tick(i + lo)
                else:
                    hi = mid
            accepted = sorted(set(accepted) | set(run(lo)))
            fail(items[i + lo])
            i += lo + 1
            tick(i)

    def line_failed(src: str) -> Callable:
        def fail(line: str) -> None:
            nonlocal accepted
            if attempt([loosen(line)])[0]:
                accepted = sorted(set(accepted) | {loosen(line)})
                c.loosened.append(f"{src}: {line}")
                log(f"{tag} loosened {line}")
            else:
                c.dropped.append(f"{src}: {line} [{why_failed(attempt([line])[1])}]")
                log(f"{tag} dropped {line}")
        return fail

    def node_failed(node: Tuple[str, List[str]]) -> None:
        src, lines = node
        log(f"{tag} conflict in {src}, checking its {len(lines)} lines")
        extend(lines, lambda l: [l], f"{src}: lines", line_failed(src), lambda k: None)

    progress(0)
    extend(nodes, lambda n: n[1], "nodes", node_failed, progress)
    attempt([], fresh=True)
    have = lock_names(lock)
    extra = [x for x in seeds if canonicalize_name(x) not in have]
    seed_tick = lambda k: progress(len(nodes) + SEED_STEPS * k // max(len(extra), 1))
    extend(extra, lambda x: [x], "packages from other venvs", lambda x: c.dropped.append(f"seed: {x}"), seed_tick)
    c.resolved, c.lines = True, accepted
    progress(len(nodes) + SEED_STEPS)
    log(f"{tag} done: loosened {len(c.loosened)}, dropped {len(c.dropped)}")


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
    catalog: List[Wheel]
    seconds: float

    @property
    def best(self) -> Optional[Candidate]:
        return next((c for c in self.candidates if c.resolved), None)


def make_plan(opts: PlanOptions, tmp: Path, log: Callable[[str], None] = print, cancelled: Callable[[], bool] = lambda: False, progress: Callable[[int, int], None] = lambda i, n: None) -> Plan:
    started = time.monotonic()
    root = resolve_comfyui_root(str(CONFIG_PATH))
    by_src, vcs = collect_requirements(root, set(opts.skip_nodes))
    excl = {canonicalize_name(x) for x in opts.exclude}
    prefer = [p for p in opts.prefer if p in by_src]
    head = [root.name, "manager"]
    order = head + prefer + sorted(k for k in by_src if k not in head + prefer)
    ordered = [(k, [l for l in by_src[k] if canonicalize_name(Requirement(l).name) not in excl]) for k in order if k in by_src]
    catalog, wheel_dir, wanted = load_catalog(opts.remote)
    pythons = [p for p in PY_CANDIDATES if not opts.python or version_filter(opts.python, p)]
    cudas = [x for x in cuda_candidates() if not opts.cuda or version_filter(opts.cuda, x)]
    nodes = sum(1 for v in by_src.values() if v)
    log(f"ComfyUI: {root}\nNodes with requirements: {nodes}, vcs/url: {len(vcs)}")
    log(f"Wheel catalog: {len(catalog)} for {', '.join(wanted)} (dir: {wheel_dir}); pythons: {', '.join(pythons)}")

    known = {canonicalize_name(Requirement(l).name) for _, ls in ordered for l in ls}
    seeds = [] if opts.no_seed else seed_packages(opts.seed_from, known)
    log(f"Seed packages from existing venvs (not in any requirements.txt): {len(seeds)}")
    steps = sum(1 for _, ls in ordered if ls) + SEED_STEPS
    progress(0, steps)
    cands = make_candidates(catalog, wanted, pythons, cudas, tmp)
    if opts.torch:
        cands = [c for c in cands if version_filter(opts.torch, f"{c.torch_mm[0]}.{c.torch_mm[1]}")]
        if not cands:
            log(f"No torch {opts.torch} release found for the chosen python/cuda")
    cands.sort(key=lambda c: (len(c.wheels), sum(q for _, q in c.wheels.values()), tup(c.py), c.torch_mm), reverse=True)
    checked: List[Candidate] = []
    done: Dict[str, int] = {}
    shown = 0
    cache = AttemptCache()

    def report(c: Candidate, k: int) -> None:
        nonlocal shown
        done[c.label()] = k
        shown = max(shown, sum(done.values()) // len(done))  # candidates added later must not move the bar back
        progress(shown, steps)

    try:
        with ThreadPoolExecutor(max_workers=max(opts.top, 1)) as ex:
            while cands and (need := opts.top - sum(1 for c in checked if c.resolved)) > 0:  # candidates that fail are replaced by the next ones
                batch, cands = cands[:need], cands[need:]
                checked += batch
                done.update({c.label(): 0 for c in batch})
                log(f"Resolving {len(batch)} candidates (this takes a while) ...")
                list(ex.map(lambda c: verify(c, ordered, seeds, tmp, cache, cancelled, log, lambda k: report(c, k)), batch))
    finally:
        cache.save()
    checked.sort(key=lambda c: c.score, reverse=True)
    return Plan(root, vcs, wheel_dir, wanted, checked, nodes, len(seeds), len(catalog), pythons, catalog, time.monotonic() - started)


def describe_plan(plan: Plan) -> str:
    out = ["Ranked candidates:"]
    for c in plan.candidates:
        have = ", ".join(f"{n}{'' if q == 3 else '?' if q == 1 else '~'}" for n, (_, q) in sorted(c.wheels.items()))
        status = f"loose {len(c.loosened)} drop {len(c.dropped)}" if c.resolved else "FAILED"
        out.append(f"  {c.label():26} {status:11} wheels {len(c.wheels)}/{len(plan.wanted)}: {have or '-'}")
    out.append("  (? = untagged wheel, unverified; ~ = other CUDA minor, same major)")
    for c in plan.candidates:
        if c.resolved is False:
            out.append(f"\n{c.label()} failed: {short_error(c.error)}")
    best = plan.best
    if not best:
        out.append(f"\nNothing resolves. Error for {plan.candidates[0].label()}:\n{plan.candidates[0].error}" if plan.candidates else "\nNo candidates.")
        out.append(f"Planned in {int(plan.seconds) // 60} min {int(plan.seconds) % 60} s")
        return "\n".join(out)
    others = [c for c in plan.candidates if c.resolved and c is not best]
    for c in others:
        mine, ref = set(c.loosened) | set(c.dropped), set(best.loosened) | set(best.dropped)
        if mine != ref:
            out.append(f"\n{c.label()} differs from the best: " + "; ".join([f"also {x}" for x in sorted(mine - ref)] + [f"not {x}" for x in sorted(ref - mine)]))
    if others and all(set(c.loosened) == set(best.loosened) and set(c.dropped) == set(best.dropped) for c in others):
        out.append(f"\nThe lists below are the same for all {len(others) + 1} resolved candidates: they come from conflicts between nodes, not from torch.")
    per_node: Dict[str, List[int]] = {}
    for n, x in enumerate([best.loosened, best.dropped]):
        for item in x:
            per_node.setdefault(item.split(": ", 1)[0], [0, 0])[n] += 1
    if per_node:
        out.append("\nSources of the conflicts:\n  " + "\n  ".join(f"{k}: {v[0]} loosened, {v[1]} dropped" for k, v in sorted(per_node.items(), key=lambda kv: -sum(kv[1]))))
    if best.loosened:
        out.append("\nVersion pins loosened (conflict with earlier requirements):\n  " + "\n  ".join(best.loosened))
    if best.dropped:
        out.append("\nRequirements that cannot be resolved (seed ones are retried with --no-deps on build):\n  " + "\n  ".join(best.dropped))
    if plan.vcs:
        out.append(f"VCS/URL requirements (installed after the main set): {len(plan.vcs)}")
    missing = [w for w in plan.wanted if w not in best.wheels]
    out.append(f"\nBest: {best.label()}" + (f"; no wheel for: {', '.join(missing)}" if missing else ""))
    out.append(f"Planned in {int(plan.seconds) // 60} min {int(plan.seconds) % 60} s")
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


def imports_ok(py: str, module: str) -> bool:
    """Import in its own process: a wheel with a mismatched binary interface kills the interpreter."""
    r = subprocess.run([py, "-c", f"import torch, {module}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    return r.returncode == 0


def build_venv(plan: Plan, c: Candidate, name: str, tmp: Path, log: Callable[[str], None] = print, select: bool = False) -> dict:
    """Create the venv for a resolved candidate and register it in config.json. Raises RuntimeError on failure."""
    venv = plan.root / name
    if venv.exists():
        raise RuntimeError(f"{venv} already exists")
    d = tmp / "final"
    d.mkdir(exist_ok=True)
    inp, con = write_inputs(d, c.lines, c.torch_mm, c.triton)
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
        code = run_logged(U + ["pip", "install", "--python", py, "-r", str(lock), *torch_source(c.cuda)], lambda l: (out.append(l), log(l)))
        if not code:
            break
        bad = re.search(r"Failed to build `([A-Za-z0-9_.\-]+)==", "\n".join(out))
        if not bad:
            raise RuntimeError("installing the resolved set failed, see the output above")
        log(f"! cannot build {bad[1]}, skipped")
        lock.write_text("".join(l for l in lock.read_text().splitlines(True) if canonicalize_name(l.split("==")[0].strip()) != canonicalize_name(bad[1])), encoding="utf-8")
    (d / "installed.txt").write_text("".join(l for l in lock.read_text().splitlines(True) if not l.lower().startswith(("setuptools==", "wheel=="))), encoding="utf-8")

    def wheel_file(w: Wheel) -> str:
        src = w.path or download_wheel(w, plan.wheel_dir, log)
        if src.name != w.filename:  # uv rejects url-quoted names such as %2B
            src = Path(shutil.copy(src, d / w.filename))
        return str(src)

    wheel_paths = [wheel_file(w) for w, _ in c.wheels.values()]
    if wheel_paths:
        run("pip", "install", "--python", py, "--no-deps", *wheel_paths)
    pip_install = ["pip", "install", "--python", py, "-c", str(d / "installed.txt"), *torch_source(c.cuda)]
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
    run_logged([py, "-c", "import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available())"], log)
    # a wheel built for another torch installs fine and then dies on import: try the other fitting wheels, else leave the package out
    for pkg, (w, _) in c.wheels.items():
        module = SMOKE_IMPORTS.get(pkg.replace("_", "-"))
        if not module:
            continue
        tried = [w]
        while not imports_ok(py, module):
            log(f"! {module} does not import with {tried[-1].filename} (built for another torch?)")
            nxt = next((x for x in ranked_wheels(plan.catalog, pkg, c.py, c.torch_mm, cuda_tuple(c.cuda)) if x not in tried), None)
            if not nxt:
                run("pip", "uninstall", "--python", py, pkg)
                log(f"! {pkg} removed: no wheel imports with torch {c.torch_mm[0]}.{c.torch_mm[1]}")
                break
            tried.append(nxt)
            run("pip", "install", "--python", py, "--no-deps", "--reinstall", wheel_file(nxt))
        else:
            log(f"ok {module}: {tried[-1].filename}")

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
    ap.add_argument("--python", help="restrict Python: 3.13, \"3.12 3.13\", \">=3.12\"")
    ap.add_argument("--cuda", help="restrict CUDA: cu130, \"cu128 cu130\", \">=cu128\"")
    ap.add_argument("--torch", help="restrict to torch major.minor: 2.11, or an expression like \">2.13\" or \">=2.12,<2.15\"")
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
