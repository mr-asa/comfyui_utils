#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Local browser GUI for the ComfyUI utils (stdlib only, listens on 127.0.0.1).

  python utils_gui.py [--port 8765] [--no-browser]

Tabs: pip audit (filters, hold/pin, install selected). Long jobs run in threads and are polled by the page.
POST requests need the per-run token embedded in the served page, so other sites cannot call the API.
"""

from __future__ import annotations
import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import socket
import webbrowser
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List
from urllib.parse import unquote

import requests
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

import config_schema
from comfyui_root import resolve_comfyui_root, validate_root
from envtools import custom_nodes_paths, dedupe_dirs, install_prefix, installed_pythons, run_uv
from build_venv import CUDA_CANDIDATES, PY_CANDIDATES, Candidate, PlanCancelled, PlanOptions, build_venv, default_venv_name, describe_plan, make_plan
import custom_nodes_link_manager as nodes_mgr
import update_workflow_repos as wf
from comfyui_root import default_workflows_dir
from workflow_sources import parse_workflow_url, partial_changes, partial_remote_status, read_metadata, sync_partial_source, write_metadata
from workflow_sources import _cache_key as _partial_cache_key
from update_comfy_repos import NO_PROMPT_ENV, collect_repos, incoming_changes, repo_status, result_to_dict, run_git, to_web_url, update_repo
from wheels import download_wheel, listed_packages, load_catalog, match_quality, mismatch, scan_local, scan_source
from pip_audit_core import SIDE_EFFECT_PROTECTED, TORCH_INDEX_PACKAGES, TORCH_INDEX_URL, _torch_cuda, audit_rows, build_reverse_constraints, diff_versions, fetch_release_list, fetch_requires_dist, fetch_torch_index, find_reverse_conflicts, forward_needs, load_installed_dists, need_vcs_install, parse_install_changes, run_audit, target_marker_env, torch_index_tag

HERE = Path(__file__).resolve().parent
CONFIG_PATH = str(HERE / "config.json")
LOG_DIR = HERE / "install_logs"
PARTIAL_CACHE = (HERE / ".partial_repo_cache").resolve()
PRESETS_PATH = str(HERE / "run_comfyui_presets_config.json")
TOKEN = secrets.token_urlsafe(16)
CONFIG_LOCK = threading.Lock()
JOBS: Dict[str, dict] = {}
REPO_KIND: Dict[str, str] = {}  # repositories found by the last scans (path -> "nodes" | "workflows"); the only paths the update endpoints accept
PLANS: Dict[str, tuple] = {}  # plan id -> (Plan, temp dir kept alive for the build that follows)


class JobCancelled(Exception):
    pass


def console(text: str) -> None:
    """Print to the server window; a console that cannot show a character gets a ? instead of killing the job."""
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc), flush=True)


class JobLog(list):
    """The log a job shows in the GUI; every line is also printed in the server window."""

    def __init__(self, tag: str):
        super().__init__()
        self.tag = tag

    def append(self, line: str) -> None:
        super().append(line)
        for part in str(line).splitlines() or [""]:
            console(f"[{self.tag}] {part}")

    def __iadd__(self, lines):
        for line in lines:
            self.append(line)
        return self


def new_job(kind: str) -> dict:
    job_id = secrets.token_hex(4)
    job = {"id": job_id, "kind": kind, "status": "running", "progress": None, "log": JobLog(f"{kind} {job_id}"), "result": None, "error": "", "cancel": threading.Event()}
    JOBS[job_id] = job
    console(f"[{kind} {job_id}] started")
    return job


def run_in_thread(job: dict, fn, *args) -> None:
    def target() -> None:
        try:
            job["result"] = fn(job, *args)
            job["status"] = "done"
        except (JobCancelled, PlanCancelled):
            job["log"].append("cancelled")
            job["status"] = "cancelled"
        except BaseException as e:  # SystemExit from the shared helpers must not kill the server
            job["error"] = f"{type(e).__name__}: {e}"
            job["log"].append(traceback.format_exc())
            job["status"] = "error"
        console(f"[{job['kind']} {job['id']}] {job['status']}")

    threading.Thread(target=target, daemon=True).start()


def venv_rows() -> dict:
    v2, _ = config_schema.load_v2(CONFIG_PATH, auto_migrate=False)
    return {
        "venvs": config_schema.list_venvs(v2),
        "comfyui_root": config_schema.get_value(v2, "comfyui_root"),
        "uv": bool(shutil.which("uv")),
        "pythons": installed_pythons(PY_CANDIDATES),
        "cuda": CUDA_CANDIDATES,
    }


def venv_entry(name: str) -> dict:
    v2, _ = config_schema.load_v2(CONFIG_PATH, auto_migrate=False)
    row = next((r for r in config_schema.list_venvs(v2) if r["name"] == name), None)
    if not row or row["exists"] != "1":
        raise ValueError(f"venv '{name}' not found")
    return row


def audit_job(job: dict, name: str, include_repo: bool, force_pip: bool, remote_wheels: bool) -> dict:
    row = venv_entry(name)
    with CONFIG_LOCK:
        v2, _ = config_schema.load_v2(CONFIG_PATH)
        v2 = config_schema.set_selected_venv(v2, row["path"], name)
        config_schema.save_v2(CONFIG_PATH, v2)
        hold, pins = config_schema.get_pip_rules(v2, name)
        cfg, _ = config_schema.load_legacy_compat(CONFIG_PATH)
    hold_set = {canonicalize_name(h) for h in hold}
    pin_map = {canonicalize_name(k): v for k, v in pins.items()}
    root = str(resolve_comfyui_root(CONFIG_PATH))
    paths = custom_nodes_paths(cfg, root)
    repo = str(cfg.get("custom_nodes_repo_path") or "").strip()
    if include_repo and repo:
        paths = dedupe_dirs(paths + [repo])

    def progress(label: str, i: int, n: int, suffix: str = "") -> None:
        if job["cancel"].is_set():
            raise JobCancelled()
        job["progress"] = {"label": label, "i": i, "n": n, "suffix": suffix}

    res = run_audit(cfg, root, paths, hold_set, pin_map, force_pip=force_pip, progress=progress, remote_wheels=remote_wheels)
    return {
        "rows": audit_rows(res, hold_set, pin_map),
        "vcs": [
            {"name": v.name or "", "repo": v.repo, "installed": str(v.installed or ""), "raw": v.raw, "ref": v.ref or "", "need_install": need_vcs_install(v)}
            for v in res.extra_reqs
        ],
        "comfy_env": [
            {"repo": r.repo, "isolations": [{"env": i.env_name, "built": i.built, "stale": i.stale} for i in r.isolations]}
            for r in res.comfy_env_reports
        ],
        "install_cmd": res.install_cmd,
        "wheel_search": res.wheel_search,
        "venv": name,
        "hold": sorted(hold_set),
        "pins": pin_map,
    }


def installed_versions(py: str) -> Dict[str, str]:
    listing = run_uv("pip", "list", "--python", py, "--format", "json", capture=True).stdout
    return {canonicalize_name(x["name"]): x["version"] for x in json.loads(listing)}


def stream(job: dict, cmd: List[str], cancellable: bool = False, env: dict | None = None) -> tuple:
    """Run a command, appending its output to the job log. Returns (exit code, output lines).
    A cancellable command is killed when the job is cancelled (only safe for dry runs)."""
    job["log"].append("> " + " ".join(cmd))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", env=env)
    lines = []
    for line in p.stdout:
        lines.append(line.rstrip())
        if "%" in line and job["log"] and job["log"][-1].split(":")[0] == line.split(":")[0]:
            job["log"][-1] = lines[-1]  # git progress ("Receiving objects: 12%") rewrites one line instead of flooding the log
        else:
            job["log"].append(lines[-1])
        if cancellable and job["cancel"].is_set():
            p.kill()
            raise JobCancelled()
    code = p.wait()
    job["log"].append(f"exit code {code}")
    return code, lines


def record_install(job: dict, name: str, stamp: str, before: Dict[str, str], after: Dict[str, str], code: int) -> dict:
    """Keep the old state next to the log, so a bad update can be rolled back with `uv pip install -r <before>`."""
    changes = diff_versions(before, after)
    LOG_DIR.mkdir(exist_ok=True)
    (LOG_DIR / f"{stamp}_{name}_before.txt").write_text("".join(f"{n}=={v}\n" for n, v in sorted(before.items())), encoding="utf-8")
    (LOG_DIR / f"{stamp}_{name}.log").write_text("\n".join(job["log"]) + "\n\nchanges:\n" + "".join(f"  {c['name']} {c['from'] or '-'} -> {c['to'] or '-'}\n" for c in changes), encoding="utf-8")
    job["log"].append(f"saved: {LOG_DIR / (stamp + '_' + name + '.log')}")
    return {"exit_code": code, "installed": after, "changes": changes}


def install_job(job: dict, name: str, packages: List[str], force_pip: bool, protect: str, dry_run: bool, allow: List[str]) -> dict:
    """Install (or only simulate) the packages.

    protect: "all" keeps every other installed package at its version, "held" keeps the venv's held and pinned
    packages, "none" lets the resolver change anything. Protection goes through a constraints file, so a
    conflict fails loudly instead of silently changing numpy & co. `allow` lists packages released from the
    protection for this install only."""
    row = venv_entry(name)
    py = config_schema.venv_python_path(row["path"])
    before = installed_versions(py)
    cmd = install_prefix({"env_type": "venv", "venv_path": row["path"]}, force_pip=force_pip)  # no --upgrade: with uv it upgrades every dependency too
    if dry_run:
        cmd.append("--dry-run")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    LOG_DIR.mkdir(exist_ok=True)
    requested = {canonicalize_name(re.split(r"[=<>!~ ]", p, maxsplit=1)[0]) for p in packages} | {canonicalize_name(a) for a in allow}
    tag = next((t for t in (torch_index_tag({"version": before[n]}) for n in requested & TORCH_INDEX_PACKAGES if n in before) if t), "")
    if tag:  # a torch-family build comes from the PyTorch index, PyPI only has other builds of it
        cmd += ["--extra-index-url", TORCH_INDEX_URL + tag]
    plain = list(cmd)
    keep: Dict[str, str] = {}
    if protect == "all":
        keep = {n: v for n, v in before.items() if n not in requested}
    elif protect == "held":
        with CONFIG_LOCK:
            v2, _ = config_schema.load_v2(CONFIG_PATH, auto_migrate=False)
        hold, pins = config_schema.get_pip_rules(v2, name)
        keep = {n: before[n] for n in (canonicalize_name(h) for h in hold) if n in before and n not in requested}
        keep.update({canonicalize_name(n): v for n, v in pins.items() if canonicalize_name(n) not in requested})
    if keep:
        con = LOG_DIR / f"{stamp}_{name}_constraints.txt"
        con.write_text("".join(f"{n}=={v}\n" for n, v in sorted(keep.items())), encoding="utf-8")
        cmd += ["-c", str(con)]
    code, lines = stream(job, cmd + packages, cancellable=dry_run)
    if dry_run:
        result = {"exit_code": code, "changes": parse_install_changes(lines) if code == 0 else [], "output": lines[-14:]}
        if code and keep:  # run again without the protection: what it would change is what stands in the way
            code2, lines2 = stream(job, plain + packages, cancellable=True)
            changes2 = parse_install_changes(lines2) if code2 == 0 else []
            result["unprotected"] = {"exit_code": code2, "changes": changes2, "output": lines2[-14:]}
            result["released"] = [c for c in changes2 if c["name"] in keep]
        return result
    return record_install(job, name, stamp, before, installed_versions(py), code)


CHECK_UV = re.compile(r"The package `(?P<pkg>[^`]+)` requires `(?P<req>[^`]+)`, but (?:`(?P<ver>[^`]+)` is installed|it's not installed)")
CHECK_PIP = re.compile(r"^(?P<pkg>\S+) \S+ (?:has requirement (?P<req>.+), but you have \S+ (?P<ver>\S+)\.|requires (?P<req2>.+), which is not installed\.)$")


def parse_check(lines: List[str]) -> List[dict]:
    """The broken requirements printed by `pip check` / `uv pip check` (Python version mismatches are left out)."""
    problems = []
    for line in lines:
        m = CHECK_UV.search(line) or CHECK_PIP.match(line.strip())
        if m:
            problems.append({"package": m["pkg"], "requirement": m["req"] or m["req2"], "installed": m["ver"] or ""})
    return problems


def dir_size(path: str) -> int:
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file()) if Path(path).is_dir() else 0


def pip_tool_job(job: dict, name: str, action: str, force_pip: bool) -> dict:
    """`check` verifies the venv's dependencies, `cache_purge` empties the package cache of uv (or of pip)."""
    py = config_schema.venv_python_path(venv_entry(name)["path"])
    use_uv = not force_pip and shutil.which("uv")
    if action == "check":
        code, lines = stream(job, ["uv", "pip", "check", "--python", py] if use_uv else [py, "-m", "pip", "check"])
        return {"exit_code": code, "problems": parse_check(lines)}
    if action != "cache_purge":
        raise ValueError(f"unknown action '{action}'")
    where = subprocess.run(["uv", "cache", "dir"] if use_uv else [py, "-m", "pip", "cache", "dir"], capture_output=True, text=True).stdout.strip()
    before = dir_size(where)
    code, _ = stream(job, ["uv", "cache", "clean"] if use_uv else [py, "-m", "pip", "cache", "purge"])
    freed = max(before - dir_size(where), 0)
    job["log"].append(f"freed {freed / 2**20:.1f} MiB ({where})")
    return {"exit_code": code, "freed": freed}


def wheel_install_job(job: dict, name: str, filenames: List[str], force_pip: bool) -> dict:
    """Download (if needed) and install prebuilt wheels with --no-deps. Only files listed in the wheel sources are accepted."""
    row = venv_entry(name)
    py = config_schema.venv_python_path(row["path"])
    catalog, wheel_dir, _wanted = load_catalog(True)
    by_name = {w.filename: w for w in catalog}
    before = installed_versions(py)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    code = 0
    for fn in filenames:
        w = by_name.get(fn)
        if not w:
            raise ValueError(f"{fn} is not in the wheel sources")
        path = w.path or download_wheel(w, wheel_dir, job["log"].append)
        with tempfile.TemporaryDirectory() as tmp:
            if path.name != w.filename:  # uv rejects url-quoted names such as %2B
                path = Path(shutil.copy(path, Path(tmp) / w.filename))
            code, _ = stream(job, install_prefix({"env_type": "venv", "venv_path": row["path"]}, force_pip=force_pip) + ["--no-deps", str(path)])
        if code:
            break
    if not code:
        job["log"].append("checking dependencies of the venv:")
        check = run_uv("pip", "check", "--python", py, check=False, capture=True)
        job["log"] += [l for l in (check.stdout + check.stderr).splitlines() if l.strip()]
    return record_install(job, name, stamp, before, installed_versions(py), code)


def candidate_json(i: int, c: Candidate, wanted: List[str]) -> dict:
    return {
        "index": i, "label": c.label(), "py": c.py, "cuda": c.cuda, "torch": f"{c.torch_mm[0]}.{c.torch_mm[1]}",
        "resolved": bool(c.resolved), "error": (c.error or "")[-1500:], "loosened": c.loosened or [], "dropped": c.dropped or [],
        "wheels": [{"name": n, "filename": w.filename, "quality": q, "local": bool(w.path)} for n, (w, q) in sorted(c.wheels.items())],
        "missing_wheels": [w for w in wanted if w not in c.wheels], "default_name": default_venv_name(c),
    }


def plan_job(job: dict, opts: dict) -> dict:
    def text_list(key: str) -> List[str]:
        return [x for x in re.split(r"[\s,]+", str(opts.get(key) or "")) if x]

    options = PlanOptions(
        remote=bool(opts.get("remote")), python=str(opts.get("python") or "") or None, cuda=str(opts.get("cuda") or "") or None,
        torch=str(opts.get("torch") or "") or None, top=max(1, min(int(opts.get("top") or 6), 12)),
        exclude=text_list("exclude"), prefer=text_list("prefer"), skip_nodes=text_list("skip_nodes"), no_seed=not opts.get("seed", True),
    )
    for old in list(PLANS):  # only the latest plan is kept
        PLANS.pop(old)[1].cleanup()
    tmp = tempfile.TemporaryDirectory()
    try:
        plan = make_plan(options, Path(tmp.name), lambda m: job["log"].append(m), job["cancel"].is_set)
    except BaseException:
        tmp.cleanup()
        raise
    PLANS[job["id"]] = (plan, tmp)
    best = plan.best
    job["log"].append(describe_plan(plan))
    return {
        "plan_id": job["id"], "best": plan.candidates.index(best) if best else -1,
        "candidates": [candidate_json(i, c, plan.wanted) for i, c in enumerate(plan.candidates)],
        "info": {"nodes": plan.nodes_with_reqs, "seeds": plan.seeds, "catalog": plan.catalog_size, "pythons": plan.pythons, "vcs": len(plan.vcs), "wanted": plan.wanted},
    }


def build_job(job: dict, plan_id: str, index: int, name: str, select: bool) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or name in (".", ".."):
        raise ValueError("venv name may only contain letters, digits, '.', '_' and '-'")
    if plan_id not in PLANS:
        raise ValueError("this plan is gone, plan again")
    plan, tmp = PLANS[plan_id]
    cand = plan.candidates[index]
    if not cand.resolved:
        raise ValueError("this candidate did not resolve")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    try:
        return build_venv(plan, cand, name, Path(tmp.name), job["log"].append, select)
    finally:
        LOG_DIR.mkdir(exist_ok=True)
        (LOG_DIR / f"{stamp}_build_{name}.log").write_text("\n".join(job["log"]) + "\n", encoding="utf-8")


def partial_incoming(path: str) -> dict:
    source = read_metadata(Path(path))
    if source is None or not source.synced_commit:
        return {"commits": [], "files": [], "error": "no sync record yet: update once, later checks can show what changed"}
    ok, out, _ = run_git(["rev-parse", f"origin/{source.branch or 'main'}"], cwd=str(PARTIAL_CACHE / _partial_cache_key(source)))
    if not ok:
        return {"commits": [], "files": [], "error": "run a scan with fetch first"}
    commits, stat = partial_changes(source, PARTIAL_CACHE, source.synced_commit, out.strip().splitlines()[0])
    return {"commits": commits, "files": [{"added": a, "deleted": d, "path": p} for a, d, p in stat], "error": ""}


def source_status(path: str, fetch: bool) -> dict:
    """repo_status, plus the workflow sources that are not git repos (partial syncs or plain folders)."""
    if REPO_KIND.get(path) == "workflows" and not os.path.isdir(os.path.join(path, ".git")):
        row = repo_status(path, fetch=False)
        source = read_metadata(Path(path))
        if source is None or source.mode != "partial":
            row["state"] = "not-git"
            return row
        row["web_url"] = source.source_url or source.repo_url
        row["commit_base"] = to_web_url(source.repo_url)
        row["branch"] = source.branch or ""
        row["partial"] = True
        row["last_subject"] = f"partial: {', '.join(source.paths or [])}" + (f" (synced {source.synced_commit[:8]})" if source.synced_commit else " (no sync record yet)")
        status = partial_remote_status(source, PARTIAL_CACHE) if fetch else {"state": "up-to-date" if source.synced_commit else "untracked"}
        row["state"] = status["state"]
        row["behind"] = status.get("files", 0)
        row["error"] = status.get("error", "")
        return row
    return repo_status(path, fetch)


def repos_scan_job(job: dict, kind: str, fetch: bool, include_disabled: bool) -> dict:
    with CONFIG_LOCK:
        cfg, _ = config_schema.load_legacy_compat(CONFIG_PATH)
    root = resolve_comfyui_root(CONFIG_PATH)
    if kind == "workflows":
        wdir = default_workflows_dir(root)
        repos = [str(p) for p in wf.collect_workflow_sources(wdir)] if wdir.is_dir() else []
    else:
        repos = collect_repos(root, custom_nodes_paths(cfg, str(root)), include_disabled)
    for old in [p for p, k in REPO_KIND.items() if k == kind]:  # each tab rescans only its own kind
        del REPO_KIND[old]
    REPO_KIND.update({r: kind for r in repos})
    rows, done, running = [], 0, set()

    def scan(path: str) -> dict:
        running.add(path)
        try:
            return source_status(path, fetch)
        finally:
            running.discard(path)

    with ThreadPoolExecutor(max_workers=8) as ex:
        pending = {ex.submit(scan, r) for r in repos}
        try:
            while pending:
                finished, pending = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                for fut in finished:
                    rows.append(fut.result())
                    done += 1
                waiting = sorted(os.path.basename(p) for p in running)
                job["progress"] = {"label": "Repositories", "i": done, "n": len(repos), "suffix": ("waiting for " + ", ".join(waiting[:4])) if pending and waiting else ""}
                if job["cancel"].is_set():
                    raise JobCancelled()
        except BaseException:
            ex.shutdown(wait=False, cancel_futures=True)
            raise
    counts = Counter(r["state"] for r in rows)
    job["log"].append(f"{len(rows)} repositories: " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
    return {"rows": rows, "root": str(root)}


def repos_update_job(job: dict, paths: List[str], on_local: str, on_conflict: str, dry_run: bool) -> dict:
    """Nodes: local changes stash/replace/skip, conflicts skip/reset. Workflows: local changes also fetch-only / move edited JSONs,
    "conflicts" is the Windows-incompatible-paths answer (skip / fetch + sparse checkout)."""
    unknown = [p for p in paths if p not in REPO_KIND]
    if unknown:
        raise ValueError("not a scanned repository: " + unknown[0])
    local = {"stash": "2", "replace": "1", "skip": "3", "fetch": "5", "move_json": "6"}[on_local]
    resolve = {"skip": "skip", "reset": "reset", "sparse": "sparse"}[on_conflict]
    cache_root = PARTIAL_CACHE
    results, done = [], 0

    def one(path: str) -> dict:
        if REPO_KIND.get(path) == "workflows":
            if dry_run:
                res = {"name": os.path.basename(path), "path": path, "branch": "", "changed": False, "skipped": "", "error": "", "commits": [], "files": [], "notes": ["[dry-run] not supported for workflow sources"], "last_update_at": ""}
            elif os.path.isdir(os.path.join(path, ".git")):
                res = wf.report_to_dict(wf.update_repo(Path(path), on_local=local, on_incompatible="5" if resolve == "sparse" else "3"))
            else:
                cache_root.mkdir(exist_ok=True)
                res = wf.report_to_dict(wf.update_partial_repo(Path(path), cache_root))
        else:
            res = result_to_dict(update_repo(path, dry_run=dry_run, on_local=local, on_conflict=resolve))
        res["row"] = source_status(path, False)
        return res

    root_first = [p for p in paths if p == str(resolve_comfyui_root(CONFIG_PATH))]
    rest = [p for p in paths if p not in root_first]

    def report(r: dict) -> None:
        tag = "ERROR " + r["error"][:200] if r["error"] else "skipped: " + r["skipped"] if r["skipped"] else f"updated, {len(r['commits'])} commit(s)" if r["changed"] else "no changes"
        job["log"].append(f"{r['name']}: {tag}")
        job["log"] += [f"    · {c}" for c in r["commits"][:8]]
        job["log"] += [f"    {n}" for n in r["notes"][:30]]

    for p in root_first:  # ComfyUI itself goes first and alone
        results.append(one(p))
        report(results[-1])
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(one, p) for p in rest]
        try:
            for fut in as_completed(futs):
                results.append(fut.result())
                report(results[-1])
                done += 1
                job["progress"] = {"label": "Updating", "i": len(results), "n": len(paths), "suffix": results[-1]["name"]}
                if job["cancel"].is_set():
                    raise JobCancelled()
        except BaseException:
            ex.shutdown(wait=False, cancel_futures=True)
            raise
    return {"results": results, "dry_run": dry_run}


def nodes_context() -> tuple:
    with CONFIG_LOCK:
        cfg, _ = config_schema.load_legacy_compat(CONFIG_PATH)
    repo = str(cfg.get("custom_nodes_repo_path") or "").strip()
    if not repo or not os.path.isdir(repo):
        raise ValueError("custom_nodes_repo_path is not set or missing in config.json")
    root = resolve_comfyui_root(CONFIG_PATH)
    custom = next(iter(custom_nodes_paths(cfg, str(root))), "")
    if not custom:
        raise ValueError("custom_nodes folder not found")
    return repo, custom, nodes_mgr._normalize_tags(cfg.get("junk_links_tags"))


def save_tags(tags: Dict[str, List[str]]) -> None:
    with CONFIG_LOCK:
        v2, _ = config_schema.load_v2(CONFIG_PATH)
        v2.setdefault("extras", {})["junk_links_tags"] = tags
        config_schema.save_v2(CONFIG_PATH, v2)


def nodes_state() -> dict:
    repo, custom, tags = nodes_context()
    presets = nodes_mgr._load_presets(PRESETS_PATH)
    return {
        "repo": repo, "custom": custom, "rows": nodes_mgr.node_inventory(repo, custom, tags), "tags": sorted(tags, key=str.lower),
        "presets": {n: {"mode": v.get("mode", "whitelist"), "count": len(v.get("nodes", []))} for n, v in presets.items() if isinstance(v, dict) and n != "current"},
    }


def nodes_action(body: dict) -> dict:
    """Link / unlink / preset / tag operations. Returns messages plus the fresh state."""
    repo, custom, tags = nodes_context()
    action = str(body["action"])
    rows = {r["name"]: r for r in nodes_mgr.node_inventory(repo, custom, tags)}
    names = [str(n) for n in body.get("names", [])]
    bad = [n for n in names if n not in rows]
    if bad:
        raise ValueError("unknown node: " + bad[0])
    links = nodes_mgr._scan_links(custom, repo)
    link_map = {ln.name: ln for ln in links}
    msgs: List[str] = []
    if action == "link":
        msgs = [nodes_mgr._add_link(repo, custom, n) for n in names if rows[n]["kind"] == "unlinked"]
    elif action == "unlink":
        msgs = [nodes_mgr._remove_link(custom, link_map[n]) for n in names if n in link_map]
    elif action == "invert":
        msgs = [nodes_mgr._remove_link(custom, link_map[n]) if n in link_map else nodes_mgr._add_link(repo, custom, n) for n in names if rows[n]["in_repo"]]
    elif action == "remove_junk":
        msgs = nodes_mgr._remove_junk_links(custom, links)
    elif action == "move_direct":
        direct = {dn.name: dn for dn in nodes_mgr._scan_direct_nodes(custom)}
        msgs = [nodes_mgr._move_installed_to_repo(repo, custom, direct[n]) for n in names if n in direct]
    elif action in ("preset_preview", "preset_apply"):
        presets = nodes_mgr._load_presets(PRESETS_PATH)
        name = str(body["preset"])
        if name == "current" or not isinstance(presets.get(name), dict):
            raise ValueError("unknown preset")
        plan = nodes_mgr.preset_plan(presets[name], repo, custom)
        if action == "preset_preview":
            return {"plan": plan}
        msgs = [nodes_mgr._remove_link(custom, link_map[n]) for n in plan["remove"] if n in link_map] + [nodes_mgr._add_link(repo, custom, n) for n in plan["add"]]
    elif action == "preset_save":
        name = str(body["preset"]).strip()
        if not name or name.lower() == "current":
            raise ValueError("invalid preset name")
        presets = nodes_mgr._load_presets(PRESETS_PATH)
        if name in presets and not body.get("overwrite"):
            return {"exists": True}
        linked = {r["name"] for r in rows.values() if r["kind"] == "linked"}
        in_repo = [n for n in rows if rows[n]["in_repo"]]
        enabled, disabled = [n for n in in_repo if n in linked], [n for n in in_repo if n not in linked]
        presets[name] = {"mode": "blacklist", "nodes": disabled} if len(disabled) <= len(enabled) else {"mode": "whitelist", "nodes": enabled}
        nodes_mgr._save_json(PRESETS_PATH, presets)
        msgs = [f"Saved preset '{name}' ({presets[name]['mode']}, {len(presets[name]['nodes'])} nodes)"]
    elif action == "preset_delete":
        presets = nodes_mgr._load_presets(PRESETS_PATH)
        name = str(body["preset"])
        if name == "current" or name not in presets:
            raise ValueError("unknown preset")
        presets.pop(name)
        nodes_mgr._save_json(PRESETS_PATH, presets)
        msgs = [f"Deleted preset '{name}'"]
    elif action in ("tag_add", "tag_remove"):
        tag = str(body["tag"]).strip()
        if not tag:
            raise ValueError("empty tag")
        cur = tags.setdefault(tag, [])
        if action == "tag_add":
            cur += [n for n in names if n.lower() not in {x.lower() for x in cur}]
        else:
            tags[tag] = [x for x in cur if x.lower() not in {n.lower() for n in names}]
            if not tags[tag]:
                tags.pop(tag)
        save_tags(tags)
        msgs = [f"Tag '{tag}': {len(names)} node(s) {'added' if action == 'tag_add' else 'removed'}"]
    else:
        raise ValueError("unknown action")
    return {"messages": msgs, "state": nodes_state()}


def venv_python_version(venv_path: str) -> str:
    cfg = Path(venv_path) / "pyvenv.cfg"
    if cfg.is_file():
        m = re.search(r"^version(?:_info)?\s*=\s*([\d.]+)", cfg.read_text(encoding="utf-8", errors="ignore"), re.M)
        if m:
            return m[1]
    return ""


KEY_CONFIG = HERE / "compare_venv_versions_config.json"


def load_key_config() -> dict:
    return json.loads(KEY_CONFIG.read_text(encoding="utf-8-sig")) if KEY_CONFIG.is_file() else {}


KEY_FLAGS = ("include_python", "include_cuda_from_torch")


def _key_snapshot(cfg: dict) -> dict:
    return {"modules": list(cfg.get("modules", [])), "custom_checks": list(cfg.get("custom_checks", [])), **{f: cfg.get(f, True) for f in KEY_FLAGS}}


def _write_key_config(cfg: dict) -> None:
    if KEY_CONFIG.is_file():
        shutil.copy(KEY_CONFIG, KEY_CONFIG.with_name(KEY_CONFIG.name + ".bak"))  # one step back is always on disk
    KEY_CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False).replace("\n", "\r\n") + "\r\n", encoding="utf-8")


def set_key_module(name: str, on: bool) -> dict:
    """Add or remove a package in the favorites list that compare_venv_versions.ps1 and the Compare tab share.
    Returns what was removed, so the page can offer an undo."""
    cfg = load_key_config()
    cfg.setdefault("modules", [])
    before = _key_snapshot(cfg)
    canon = canonicalize_name(name)
    if canon == "python":
        cfg["include_python"] = on
    elif canon == "cuda-from-torch":
        cfg["include_cuda_from_torch"] = on
    elif on:
        if canon not in {canonicalize_name(m) for m in cfg["modules"]} and not any(canon in {canonicalize_name(c) for c in chk.get("candidates", [])} for chk in cfg.get("custom_checks", [])):
            cfg["modules"].append(name)
    else:
        cfg["modules"] = [m for m in cfg["modules"] if canonicalize_name(m) != canon]
        cfg["custom_checks"] = [chk for chk in cfg.get("custom_checks", []) if canon not in {canonicalize_name(c) for c in chk.get("candidates", [])}]
    after = _key_snapshot(cfg)
    _write_key_config(cfg)
    return {"removed": {
        "modules": [m for m in before["modules"] if m not in after["modules"]],
        "custom_checks": [c for c in before["custom_checks"] if c not in after["custom_checks"]],
        "flags": {f: before[f] for f in KEY_FLAGS if before[f] != after[f]},
    }}


def restore_key_parts(removed: dict) -> None:
    """Put back what set_key_module reported as removed (what is already there stays untouched)."""
    cfg = load_key_config()
    mods = cfg.setdefault("modules", [])
    have = {canonicalize_name(m) for m in mods}
    mods += [m for m in removed.get("modules", []) if canonicalize_name(m) not in have]
    checks = cfg.setdefault("custom_checks", [])
    names = {c.get("name") for c in checks}
    checks += [c for c in removed.get("custom_checks", []) if c.get("name") not in names]
    for flag, value in removed.get("flags", {}).items():
        if flag in KEY_FLAGS:
            cfg[flag] = bool(value)
    _write_key_config(cfg)


def compare_venvs(names: List[str], scope: str) -> dict:
    """Package versions side by side. scope "key" keeps the modules listed in compare_venv_versions_config.json."""
    if len(names) < 2:
        raise ValueError("select at least two venvs")
    per: Dict[str, Dict[str, str]] = {}
    for n in names:
        row = venv_entry(n)
        versions = installed_versions(config_schema.venv_python_path(row["path"]))
        versions["python"] = venv_python_version(row["path"])
        torch = versions.get("torch", "")
        versions["cuda (from torch)"] = (re.search(r"\+cu(\d+)", torch) or [None, ""])[1]
        per[n] = versions
    key_cfg = load_key_config()
    key_names = {canonicalize_name(m) for m in key_cfg.get("modules", [])}
    if key_cfg.get("include_python", True):
        key_names.add("python")
    if key_cfg.get("include_cuda_from_torch", True):
        key_names.add("cuda (from torch)")
    for chk in key_cfg.get("custom_checks", []):
        for cand in chk.get("candidates", []):
            key_names.add(canonicalize_name(cand))
    all_names = sorted(set().union(*(set(v) for v in per.values())), key=lambda x: (x not in ("python", "cuda (from torch)"), x))
    rows = []
    for pkg in all_names:
        vals = {n: per[n].get(pkg, "") for n in names}
        if scope == "key" and pkg not in key_names:
            continue
        if scope == "key" and not any(vals.values()):
            continue
        rows.append({"name": pkg, "versions": vals, "differs": len(set(vals.values())) > 1, "key": pkg in key_names})
    return {"venvs": names, "rows": rows}


REPO_URL_RE = re.compile(r"(https://[\w.\-]+/[\w.\-]+/[\w.\-]+|git@[\w.\-]+:[\w.\-]+/[\w.\-]+?)(\.git)?/?")


def repo_add_job(job: dict, urls: List[str], link: bool) -> dict:
    """Clone custom nodes one after another; one bad URL does not stop the rest.
    With custom_nodes_repo_path configured they go there and (optionally) get a junction in custom_nodes."""
    with CONFIG_LOCK:
        cfg, _ = config_schema.load_legacy_compat(CONFIG_PATH)
    root = resolve_comfyui_root(CONFIG_PATH)
    custom = next(iter(custom_nodes_paths(cfg, str(root))), "")
    repo = str(cfg.get("custom_nodes_repo_path") or "").strip()
    base = repo if repo and os.path.isdir(repo) else custom
    if not base:
        raise ValueError("custom_nodes folder not found")
    added, failed = [], []
    for url in dict.fromkeys(u.strip() for u in urls if u.strip()):
        try:
            added.append(repo_add_one(job, url, link, base, custom))
        except (ValueError, RuntimeError) as e:
            job["log"].append(f"Failed: {url}: {e}")
            failed.append({"url": url, "error": str(e)})
    if failed and not added:
        raise RuntimeError(f"nothing was added ({len(failed)} failed)")
    job["log"].append(f"Added {len(added)}" + (f", failed {len(failed)}" if failed else ""))
    return {"added": added, "failed": failed}


def repo_add_one(job: dict, url: str, link: bool, base: str, custom: str) -> dict:
    if not REPO_URL_RE.fullmatch(url):
        raise ValueError("expected a repository URL like https://github.com/user/name or git@github.com:user/name")
    name = re.sub(r"\.git$", "", re.split(r"[/:]", url.rstrip("/"))[-1])
    dest = os.path.join(base, name)
    if os.path.exists(dest) or os.path.exists(os.path.join(custom, name)):
        raise ValueError(f"{name} already exists")
    code, _ = stream(job, ["git", "clone", "--progress", url, dest], env=NO_PROMPT_ENV)
    if code:
        raise RuntimeError(f"git clone failed (exit code {code})")
    if link and base != custom:
        job["log"].append(nodes_mgr._add_link(base, custom, name))
    if os.path.isfile(os.path.join(dest, "requirements.txt")):
        job["log"].append("This node has a requirements.txt: run the Pip audit to install what it needs.")
    job["log"].append(f"Added: {dest}")
    return {"path": dest, "name": name}


def _rmtree(path: str) -> None:
    """rmtree that also deletes read-only files (git objects on Windows)."""
    def clear(func, p, _exc):
        os.chmod(p, 0o700)
        func(p)
    shutil.rmtree(path, **({"onexc": clear} if sys.version_info >= (3, 12) else {"onerror": clear}))


def delete_risk(real: str) -> str:
    """What deleting a git folder would lose: changed, untracked and ignored files (models, configs...) and unpushed commits."""
    if not os.path.isdir(os.path.join(real, ".git")):
        return ""
    ok, out, _ = run_git(["status", "--porcelain", "--ignored"], cwd=real)
    lines = [l for l in out.splitlines() if l.strip() and "__pycache__" not in l and not l.rstrip("/").endswith(".pyc")] if ok else []
    parts = []
    if lines:
        parts.append(f"{len(lines)} modified/untracked/ignored path(s), e.g. {lines[0][3:63]}")
    st = repo_status(real, fetch=False)
    if st["ahead"]:
        parts.append(f"{st['ahead']} unpushed commit(s)")
    if st["state"] == "no-upstream":
        parts.append("no upstream, cannot tell whether its commits are pushed")
    return "; ".join(parts)


def repo_delete_job(job: dict, paths: List[str], mode: str, force: bool) -> dict:
    """mode "unlink" removes only the junction in custom_nodes, "delete" removes the folder (and the junction) for good.
    A repository with local changes or unpushed commits is kept unless `force`."""
    unknown = [p for p in paths if p not in REPO_KIND]
    if unknown:
        raise ValueError("not a scanned repository: " + unknown[0])
    with CONFIG_LOCK:
        cfg, _ = config_schema.load_legacy_compat(CONFIG_PATH)
    root = resolve_comfyui_root(CONFIG_PATH)
    managed = [os.path.realpath(d) for d in custom_nodes_paths(cfg, str(root))]
    repo = str(cfg.get("custom_nodes_repo_path") or "").strip()
    if repo and os.path.isdir(repo):
        managed.append(os.path.realpath(repo))
    wdir = default_workflows_dir(root)
    if wdir.is_dir():
        managed.append(os.path.realpath(str(wdir)))
    inside = lambda real: any(real.startswith(b + os.sep) for b in managed)
    results = []
    for p in paths:
        name = os.path.basename(p.rstrip("\\/"))
        real = os.path.realpath(p)
        is_link = nodes_mgr._is_reparse_point(p)
        problem, risky = "", False
        if os.path.normcase(real) == os.path.normcase(os.path.realpath(str(root))):
            problem = "this is the ComfyUI folder itself"
        elif not inside(real) and not (is_link and mode == "unlink"):
            problem = "outside the managed folders"
        elif mode == "unlink" and not is_link:
            problem = "not a junction (use delete)"
        elif mode == "delete" and not force:
            risk = delete_risk(real)
            if risk:
                problem, risky = risk, True
        if problem:
            job["log"].append(f"{name}: kept, {problem}")
            results.append({"path": p, "name": name, "ok": False, "message": problem, "risk": risky})
            continue
        try:
            if is_link and not nodes_mgr._remove_dir(p):
                raise OSError("could not remove the junction")
            if mode == "delete":
                _rmtree(real if is_link else p)
            job["log"].append(f"{name}: " + ("junction removed, repo folder kept" if mode == "unlink" else "deleted"))
            results.append({"path": p, "name": name, "ok": True, "message": ""})
            REPO_KIND.pop(p, None)
        except OSError as e:
            job["log"].append(f"{name}: failed, {e}")
            results.append({"path": p, "name": name, "ok": False, "message": str(e)})
    return {"results": results}


def add_workflow_job(job: dict, urls: List[str]) -> dict:
    """Add workflow sources one after another; one bad URL does not stop the rest."""
    added, failed = [], []
    for url in dict.fromkeys(u.strip() for u in urls if u.strip()):
        try:
            added.append(add_workflow_one(job, url))
        except (ValueError, RuntimeError) as e:
            job["log"].append(f"Failed: {url}: {e}")
            failed.append({"url": url, "error": str(e)})
    if failed and not added:
        raise RuntimeError(f"nothing was added ({len(failed)} failed)")
    job["log"].append(f"Added {len(added)}" + (f", failed {len(failed)}" if failed else ""))
    return {"added": added, "failed": failed}


def add_workflow_one(job: dict, url: str) -> dict:
    """A whole repository is cloned, a folder URL becomes a partial (sparse) source."""
    source = parse_workflow_url(url)
    if source is None:
        raise ValueError("invalid or unsupported URL (GitHub repository / folder or Hugging Face)")
    wdir = default_workflows_dir(resolve_comfyui_root(CONFIG_PATH))
    wdir.mkdir(parents=True, exist_ok=True)
    dest = wdir / source.folder_name
    if dest.exists():
        raise ValueError(f"{dest} already exists")
    if source.mode == "full":
        code, _ = stream(job, ["git", "clone", "--progress", source.repo_url, str(dest)], env=NO_PROMPT_ENV)
        if code:
            raise RuntimeError(f"git clone failed (exit code {code})")
    else:
        cache_root = PARTIAL_CACHE
        cache_root.mkdir(exist_ok=True)
        ok, messages = sync_partial_source(source, dest, cache_root)
        job["log"] += list(messages)
        if not ok:
            raise RuntimeError("partial sync failed")
        write_metadata(dest, source)
    job["log"].append(f"Added {source.mode} source: {dest}")
    return {"path": str(dest), "mode": source.mode}


def wheel_env(venv_path: str, installed: Dict[str, dict]) -> tuple:
    """Python (3.12), torch (major, minor) and CUDA (major, minor) of a venv, the three things a wheel has to match."""
    py = ".".join(venv_python_version(venv_path).split(".")[:2])
    torch_mm, cuda = _torch_cuda(installed["torch"]["version"]) if "torch" in installed else (None, None)
    return py, torch_mm, cuda


def judge_wheel(w, env: tuple) -> tuple:
    """(quality, verdict text) of a wheel for the venv env from wheel_env."""
    py, torch_mm, cuda = env
    if not (torch_mm and cuda):
        return 0, "this venv has no CUDA torch to compare with"
    q = match_quality(w, py, torch_mm, cuda)
    return q, mismatch(w, py, torch_mm, cuda) or {3: "fits", 2: "fits (other CUDA minor, same major)", 1: "untagged, not verified"}[q]


def versions_info(venv: str, name: str, specs: List[dict]) -> dict:
    """All releases of a package with the verdict of the requirements files, of the installed packages that depend on it
    and of the release's own requirements, plus every constraint on the package, the newest release each one alone
    would allow and the prebuilt wheels of the package."""
    row = venv_entry(venv)
    canon = canonicalize_name(name)
    py_ver = venv_python_version(row["path"])
    releases, error = fetch_release_list(canon, py_ver or None)
    installed_map = load_installed_dists({"env_type": "venv", "venv_path": row["path"]})
    info = installed_map.get(canon) or {}
    current = info.get("version", "")
    current_public = Version(Version(current).public) if current else None  # 0.26.0+cu130 is release 0.26.0
    direct = info.get("direct_url") or ""
    wheel = unquote(direct.rsplit("/", 1)[-1]) if direct.lower().endswith(".whl") else ""
    marker_env = target_marker_env(py_ver or ".".join(map(str, sys.version_info[:3])))
    reverse = build_reverse_constraints([canon], installed_map, marker_env)
    caps = [{"kind": "requirements.txt", "who": x["repo"], "spec": x["spec"]} for x in specs if x["spec"].strip()]
    caps += [{"kind": "installed", "who": d, "spec": str(s)} for d, s in reverse.get(canon, [])]
    spec_sets = [(s, SpecifierSet(s)) for s in dict.fromkeys(c["spec"] for c in caps if c["kind"] == "requirements.txt")]
    tag = torch_index_tag(info) if canon in TORCH_INDEX_PACKAGES else ""
    index_versions, index_error = fetch_torch_index(canon, tag, py_ver or ".".join(map(str, sys.version_info[:2]))) if tag else ([], "")
    for rel in releases:
        v = Version(rel["version"])
        rel["blocked_by"] = [s for s, ss in spec_sets if not ss.contains(v, prereleases=True)]
        rel["conflicts"] = find_reverse_conflicts(reverse, canon, v)
        rel["installed"] = v == current_public
        rel["off_index"] = bool(index_versions) and Version(v.public) not in index_versions  # only another build of it exists on PyPI
        rel["needs"] = None
    stable = [r for r in releases if not r["pre"] and r["python_ok"] and not r["yanked"]]
    for c in caps:
        ss = SpecifierSet(c["spec"])
        c["top"] = next((r["version"] for r in stable if ss.contains(Version(r["version"]), prereleases=True)), "")
    allowed = next((r["version"] for r in stable if not r["blocked_by"] and not r["conflicts"] and not r["off_index"]), "")
    # the newest candidates: which installed packages their own requirements would force to change
    cands = [r for r in stable if not r["blocked_by"] and not r["conflicts"] and not r["off_index"] and (current_public is None or Version(r["version"]) > current_public)][:12]
    with ThreadPoolExecutor(max_workers=6) as ex:
        for rel, requires in zip(cands, ex.map(lambda r: fetch_requires_dist(canon, r["version"]), cands)):
            if requires is not None:
                rel["needs"] = forward_needs(requires, installed_map, marker_env)
    wheels = []
    if canon in listed_packages():
        env = wheel_env(row["path"], installed_map)
        seen = set()
        for w in load_catalog(True)[0]:
            if w.name != canon or w.filename in seen:
                continue
            seen.add(w.filename)
            q, verdict = judge_wheel(w, env)
            wheels.append({"version": str(w.pep_version()), "filename": w.filename, "url": w.url or "", "path": str(w.path) if w.path else "", "quality": q, "verdict": verdict,
                           "installed": bool(current) and w.version == current, "newer": bool(current) and w.pep_version() > current_public})
        wheels.sort(key=lambda x: (x["quality"] >= 2, Version(x["version"])), reverse=True)
    return {"name": canon, "installed": current, "newest": stable[0]["version"] if stable else "", "allowed": allowed, "wheel": wheel, "protected": canon in SIDE_EFFECT_PROTECTED,
            "index_tag": tag, "index_error": index_error, "error": error, "caps": caps, "versions": releases[:400], "wheels": wheels[:60]}


def wheels_check(venv: str, wheel_dir: str, packages: List[str], sources: List[dict]) -> dict:
    """Scan every wheel source (the settings as typed, saved or not) and judge each wheel against the venv's Python / torch / CUDA."""
    row = venv_entry(venv)
    installed = load_installed_dists({"env_type": "venv", "venv_path": row["path"]})
    env = wheel_env(row["path"], installed)
    py, torch_mm, cuda = env
    wanted = [canonicalize_name(p) for p in packages]

    def scan(src: dict) -> tuple:
        try:
            return src, *scan_source(src), ""
        except (requests.RequestException, KeyError, ValueError) as e:
            return src, [], [], str(e)

    found = [(f"local: {wheel_dir}", w) for w in (scan_local(Path(wheel_dir)) if wheel_dir else [])]
    report = []
    with ThreadPoolExecutor(max_workers=max(len(sources), 1)) as ex:
        for src, wheels, unparsed, error in ex.map(scan, sources):
            label = src["repo"] + (f"/{src['path']}" if src.get("path") else "")
            found += [(label, w) for w in wheels]
            report.append({"source": label, "ok": not error, "error": error, "wheels": len(wheels), "unparsed": unparsed[:20], "unparsed_count": len(unparsed),
                           "listed": len([w for w in wheels if w.name in wanted]), "names": sorted({w.name for w in wheels})})

    per_pkg = {n: [] for n in wanted}
    seen = set()
    for label, w in found:
        if w.name in per_pkg and (w.filename, label) not in seen:
            seen.add((w.filename, label))
            q, verdict = judge_wheel(w, env)
            per_pkg[w.name].append({"filename": w.filename, "version": str(w.pep_version()), "source": label, "quality": q, "verdict": verdict})
    packages_out = []
    for n, ws in per_pkg.items():
        ws.sort(key=lambda x: (x["quality"] >= 2, Version(x["version"])), reverse=True)
        packages_out.append({"name": n, "installed": (installed.get(n) or {}).get("version", ""), "wheels": ws})
    listed = set(wanted)
    suggest = sorted({n for r in report for n in r["names"] if n not in listed and n in installed})
    return {"env": {"python": py, "torch": f"{torch_mm[0]}.{torch_mm[1]}" if torch_mm else "", "cuda": f"{cuda[0]}.{cuda[1]}" if cuda else ""},
            "sources": [{k: v for k, v in r.items() if k != "names"} for r in report], "packages": packages_out, "suggest": suggest}


UPDATE_CONFIG = HERE / "update_comfy_repos_config.json"
WHEEL_CONFIG = HERE / "wheel_sources.json"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")


def settings_state() -> dict:
    with CONFIG_LOCK:
        v2, _ = config_schema.load_v2(CONFIG_PATH, auto_migrate=False)
    paths = v2["paths"]
    root = paths.get("comfyui_root", "")
    wheel = _read_json(WHEEL_CONFIG)
    venvs = [
        {"name": n, "path": e.get("path", ""), "exists": os.path.isfile(config_schema.venv_python_path(e.get("path", ""))),
         "comment": e.get("comment", ""), "cuda_path": e.get("cuda_path", ""), "env_vars": e.get("env_vars", {})}
        for n, e in v2["environments"]["venvs"].items()
    ]
    return {
        "paths": {
            "comfyui_root": root, "custom_nodes_path": paths.get("custom_nodes_path", ""), "custom_nodes_paths": paths.get("custom_nodes_paths", []),
            "custom_nodes_repo_path": paths.get("custom_nodes_repo_path", ""), "workflows_dir": str((v2.get("extras") or {}).get("workflows_dir") or ""),
            "workflows_default": str(Path(root) / "user" / "default" / "workflows" / "github") if root else "",
            "wheel_dir": str(wheel.get("wheel_dir") or ""),
        },
        "venvs": venvs, "selected": v2["runtime"]["selected"].get("name", ""),
        "env_vars": v2["defaults"].get("env_vars", {}),
        "wheels": {"packages": wheel.get("packages", []), "sources": wheel.get("sources", [])},
        "updates": {"parallel_jobs": _read_json(UPDATE_CONFIG).get("parallel_jobs", "")},
    }


def path_checks(paths: List[str]) -> List[bool]:
    return [bool(p.strip()) and os.path.isdir(p.strip()) for p in paths]


def find_unregistered_venvs() -> List[dict]:
    with CONFIG_LOCK:
        v2, _ = config_schema.load_v2(CONFIG_PATH, auto_migrate=False)
    root = Path(v2["paths"].get("comfyui_root", ""))
    known = {os.path.normcase(os.path.normpath(e.get("path", ""))) for e in v2["environments"]["venvs"].values()}
    out = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if d.is_dir() and (d / "pyvenv.cfg").is_file() and os.path.normcase(os.path.normpath(str(d))) not in known:
                out.append({"name": d.name, "path": str(d)})
    return out


def save_settings(body: dict) -> dict:
    """Apply the whole Settings dialog. Returns warnings (paths that do not exist, ...)."""
    warnings: List[str] = []
    paths = body["paths"]
    root = str(paths.get("comfyui_root", "")).strip()
    if root:
        ok, reasons = validate_root(Path(root))
        if not ok:
            warnings.append(f"ComfyUI root does not look like a ComfyUI folder ({', '.join(reasons)})")
    for label, value in (("custom_nodes folder", paths.get("custom_nodes_path")), ("custom_nodes repo folder", paths.get("custom_nodes_repo_path")),
                         *(("extra custom_nodes folder", p) for p in paths.get("custom_nodes_paths", []))):
        if str(value or "").strip() and not os.path.isdir(str(value).strip()):
            warnings.append(f"{label} does not exist: {value}")
    for key in ("workflows_dir", "wheel_dir"):
        value = str(paths.get(key) or "").strip()
        if value and not os.path.isdir(value):
            os.makedirs(value, exist_ok=True)
            warnings.append(f"created {value}")
    venvs = body["venvs"]
    names = [str(v["name"]).strip() for v in venvs]
    if len(set(names)) != len(names) or any(not n for n in names):
        raise ValueError("venv names must be unique and not empty")
    with CONFIG_LOCK:
        v2, _ = config_schema.load_v2(CONFIG_PATH)
        v2["paths"].update({
            "comfyui_root": root, "custom_nodes_path": str(paths.get("custom_nodes_path", "")).strip(),
            "custom_nodes_repo_path": str(paths.get("custom_nodes_repo_path", "")).strip(),
            "custom_nodes_paths": [str(p).strip() for p in paths.get("custom_nodes_paths", []) if str(p).strip()],
        })
        extras = v2.setdefault("extras", {})
        if str(paths.get("workflows_dir") or "").strip():
            extras["workflows_dir"] = str(paths["workflows_dir"]).strip()
        else:
            extras.pop("workflows_dir", None)
        old = v2["environments"]["venvs"]
        new = {}
        for v in venvs:
            name = str(v["name"]).strip()
            keep = old.get(name) or old.get(str(v.get("orig") or ""), {})
            new[name] = {
                "path": str(v["path"]).strip(), "comment": str(v.get("comment", "")).strip(), "cuda_path": str(v.get("cuda_path", "")).strip(),
                "env_vars": {str(k).strip(): str(x) for k, x in dict(v.get("env_vars", {})).items() if str(k).strip()},
                "pip": keep.get("pip", {"hold_packages": [], "pin_packages": {}}),
            }
            if v["path"] and not os.path.isfile(config_schema.venv_python_path(str(v["path"]).strip())):
                warnings.append(f"venv {name}: no python found in {v['path']}")
        v2["environments"]["venvs"] = new
        selected = str(body.get("selected") or "")
        v2["runtime"]["selected"] = {"kind": "venv", "name": selected if selected in new else next(iter(new), "")}
        v2["defaults"]["env_vars"] = {str(k).strip(): str(x) for k, x in dict(body.get("env_vars", {})).items() if str(k).strip()}
        config_schema.save_v2(CONFIG_PATH, v2)
    wheel = _read_json(WHEEL_CONFIG)
    wheel["wheel_dir"] = str(paths.get("wheel_dir") or "").strip()
    wheel["packages"] = [str(p).strip() for p in body["wheels"].get("packages", []) if str(p).strip()]
    wheel["sources"] = [{k: v for k, v in {"type": s["type"], "repo": str(s["repo"]).strip(), "path": str(s.get("path") or "").strip()}.items() if v}
                        for s in body["wheels"].get("sources", []) if s.get("type") in ("github", "hf") and str(s.get("repo") or "").strip()]
    _write_json(WHEEL_CONFIG, wheel)
    upd = _read_json(UPDATE_CONFIG)
    jobs = str(body.get("updates", {}).get("parallel_jobs", "")).strip()
    if jobs.isdigit() and int(jobs) > 0:
        upd["parallel_jobs"] = int(jobs)
    else:
        upd.pop("parallel_jobs", None)
    _write_json(UPDATE_CONFIG, upd)
    return {"warnings": warnings}


def save_rules(name: str, hold: List[str], pins: Dict[str, str]) -> None:
    venv_entry(name)
    hold = [canonicalize_name(h) for h in hold if h.strip()]
    pins = {canonicalize_name(k): v.strip() for k, v in pins.items() if k.strip() and v.strip()}
    with CONFIG_LOCK:
        v2, _ = config_schema.load_v2(CONFIG_PATH)
        config_schema.save_v2(CONFIG_PATH, config_schema.set_pip_rules(v2, name, hold, pins))


class Handler(BaseHTTPRequestHandler):
    server_version = "ComfyUtilsGUI"

    def log_message(self, fmt, *args) -> None:  # keep the console quiet
        pass

    def send_json(self, data, code: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def host_ok(self) -> bool:
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_GET(self) -> None:
        if not self.host_ok():
            return self.send_json({"error": "bad host"}, 403)
        path, _, query = self.path.partition("?")
        if path == "/":
            html = (HERE / "gui" / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        elif re.fullmatch(r"/ico/[A-Za-z0-9_]+\.ico", path) and (HERE / path.lstrip("/")).is_file():
            data = (HERE / path.lstrip("/")).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/x-icon")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(data)
        elif path == "/api/settings":
            self.send_json(settings_state())
        elif path == "/api/settings_venvs":
            self.send_json({"found": find_unregistered_venvs()})
        elif path == "/api/nodes_state":
            try:
                self.send_json(nodes_state())
            except ValueError as e:
                self.send_json({"error": str(e)}, 400)
        elif path == "/api/state":
            self.send_json(venv_rows())
        elif path.startswith("/api/job/"):
            job = JOBS.get(path.rsplit("/", 1)[1])
            if not job:
                return self.send_json({"error": "unknown job"}, 404)
            since = int(dict(q.split("=", 1) for q in query.split("&") if "=" in q).get("since", 0))
            out = {k: job[k] for k in ("id", "kind", "status", "progress", "error")}
            out["log"] = job["log"][since:]
            out["next"] = len(job["log"])
            out["result"] = job["result"] if job["status"] == "done" else None
            self.send_json(out)
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if not self.host_ok() or self.headers.get("X-Token") != TOKEN:
            return self.send_json({"error": "forbidden"}, 403)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/audit":
                job = new_job("audit")
                run_in_thread(job, audit_job, body["venv"], bool(body.get("include_repo")), bool(body.get("force_pip")), bool(body.get("remote_wheels")))
                self.send_json({"job": job["id"]})
            elif self.path == "/api/install":
                packages = [str(p) for p in body["packages"] if str(p).strip()]
                if not packages:
                    return self.send_json({"error": "no packages"}, 400)
                job = new_job("install")
                run_in_thread(job, install_job, body["venv"], packages, bool(body.get("force_pip")), str(body.get("protect", "held")), bool(body.get("dry_run")), [str(a) for a in body.get("allow", [])])
                self.send_json({"job": job["id"]})
            elif self.path == "/api/pip_tool":
                job = new_job("pip_tool")
                run_in_thread(job, pip_tool_job, body["venv"], str(body["action"]), bool(body.get("force_pip")))
                self.send_json({"job": job["id"]})
            elif self.path == "/api/install_wheels":
                names = [str(w["filename"]) for w in body["wheels"]]
                job = new_job("install")
                run_in_thread(job, wheel_install_job, body["venv"], names, bool(body.get("force_pip")))
                self.send_json({"job": job["id"]})
            elif self.path == "/api/plan":
                job = new_job("plan")
                run_in_thread(job, plan_job, dict(body))
                self.send_json({"job": job["id"]})
            elif self.path == "/api/build":
                job = new_job("build")
                run_in_thread(job, build_job, str(body["plan_id"]), int(body["index"]), str(body["name"]).strip(), bool(body.get("select")))
                self.send_json({"job": job["id"]})
            elif self.path == "/api/repos_scan":
                job = new_job("repos")
                run_in_thread(job, repos_scan_job, str(body.get("kind", "nodes")), bool(body.get("fetch", True)), bool(body.get("include_disabled")))
                self.send_json({"job": job["id"]})
            elif self.path == "/api/repo_add":
                job = new_job("repos")
                run_in_thread(job, repo_add_job, [str(u) for u in body["urls"]], bool(body.get("link", True)))
                self.send_json({"job": job["id"]})
            elif self.path == "/api/repo_delete":
                if str(body.get("mode")) not in ("unlink", "delete"):
                    return self.send_json({"error": "bad mode"}, 400)
                job = new_job("repos")
                run_in_thread(job, repo_delete_job, [str(x) for x in body["paths"]], str(body["mode"]), bool(body.get("force")))
                self.send_json({"job": job["id"]})
            elif self.path == "/api/add_workflow":
                job = new_job("repos")
                run_in_thread(job, add_workflow_job, [str(u) for u in body["urls"]])
                self.send_json({"job": job["id"]})
            elif self.path == "/api/repo_detail":
                if str(body["path"]) not in REPO_KIND:
                    return self.send_json({"error": "unknown repository"}, 400)
                path = str(body["path"])
                self.send_json(incoming_changes(path) if os.path.isdir(os.path.join(path, ".git")) else partial_incoming(path))
            elif self.path == "/api/repos_update":
                job = new_job("repos")
                run_in_thread(job, repos_update_job, [str(x) for x in body["paths"]], str(body.get("on_local", "stash")), str(body.get("on_conflict", "skip")), bool(body.get("dry_run")))
                self.send_json({"job": job["id"]})
            elif self.path == "/api/versions":
                self.send_json(versions_info(str(body["venv"]), str(body["name"]), [{"repo": str(x["repo"]), "spec": str(x["spec"])} for x in body.get("specs", [])]))
            elif self.path == "/api/wheels_check":
                sources = [{"type": x["type"], "repo": x["repo"].strip(), "path": (x.get("path") or "").strip()} for x in body["sources"] if x.get("repo", "").strip()]
                self.send_json(wheels_check(str(body["venv"]), str(body.get("wheel_dir") or "").strip(), [str(p) for p in body["packages"]], sources))
            elif self.path == "/api/settings":
                self.send_json(save_settings(body))
            elif self.path == "/api/path_check":
                self.send_json({"ok": path_checks([str(p) for p in body["paths"]])})
            elif self.path == "/api/compare_key":
                self.send_json(set_key_module(str(body["name"]), bool(body["on"])))
            elif self.path == "/api/compare_key_restore":
                restore_key_parts(dict(body["removed"]))
                self.send_json({"ok": True})
            elif self.path == "/api/compare":
                self.send_json(compare_venvs([str(n) for n in body["venvs"]], str(body.get("scope", "key"))))
            elif self.path == "/api/nodes":
                self.send_json(nodes_action(body))
            elif self.path == "/api/cancel":
                job = JOBS.get(str(body["job"]))
                if job:
                    job["cancel"].set()
                self.send_json({"ok": bool(job)})
            elif self.path == "/api/rules":
                save_rules(body["venv"], list(body.get("hold", [])), dict(body.get("pins", {})))
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "not found"}, 404)
        except (KeyError, ValueError, TypeError) as e:
            self.send_json({"error": f"{type(e).__name__}: {e}"}, 400)


def port_in_use(port: int) -> bool:
    # connect probe: on Windows SO_REUSEADDR lets bind() succeed on a port that is already listening
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> None:
    ap =argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    port = args.port
    while port_in_use(port):
        port += 1
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"ComfyUI utils GUI: {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
