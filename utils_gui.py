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
import tempfile
import threading
import time
import traceback
import webbrowser
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List

from packaging.utils import canonicalize_name

import config_schema
from comfyui_root import resolve_comfyui_root
from envtools import custom_nodes_paths, dedupe_dirs, install_prefix, installed_pythons, run_uv
from build_venv import CUDA_CANDIDATES, PY_CANDIDATES, Candidate, PlanCancelled, PlanOptions, build_venv, default_venv_name, describe_plan, make_plan
import custom_nodes_link_manager as nodes_mgr
import update_workflow_repos as wf
from comfyui_root import default_workflows_dir
from workflow_sources import METADATA_FILE
from update_comfy_repos import collect_repos, incoming_changes, repo_status, result_to_dict, update_repo
from wheels import download_wheel, load_catalog
from pip_audit_core import audit_rows, diff_versions, need_vcs_install, parse_install_changes, run_audit

HERE = Path(__file__).resolve().parent
CONFIG_PATH = str(HERE / "config.json")
LOG_DIR = HERE / "install_logs"
PRESETS_PATH = str(HERE / "run_comfyui_presets_config.json")
TOKEN = secrets.token_urlsafe(16)
CONFIG_LOCK = threading.Lock()
JOBS: Dict[str, dict] = {}
REPO_KIND: Dict[str, str] = {}  # scanned path -> "nodes" | "workflows"
REPO_PATHS: set = set()  # repositories found by the last scan; the only paths the update endpoints accept
PLANS: Dict[str, tuple] = {}  # plan id -> (Plan, temp dir kept alive for the build that follows)


class JobCancelled(Exception):
    pass


def new_job(kind: str) -> dict:
    job = {"id": secrets.token_hex(4), "kind": kind, "status": "running", "progress": None, "log": [], "result": None, "error": "", "cancel": threading.Event()}
    JOBS[job["id"]] = job
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
        "venv": name,
        "hold": sorted(hold_set),
        "pins": pin_map,
    }


def installed_versions(py: str) -> Dict[str, str]:
    listing = run_uv("pip", "list", "--python", py, "--format", "json", capture=True).stdout
    return {canonicalize_name(x["name"]): x["version"] for x in json.loads(listing)}


def stream(job: dict, cmd: List[str], cancellable: bool = False) -> tuple:
    """Run a command, appending its output to the job log. Returns (exit code, output lines).
    A cancellable command is killed when the job is cancelled (only safe for dry runs)."""
    job["log"].append("> " + " ".join(cmd))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    lines = []
    for line in p.stdout:
        lines.append(line.rstrip())
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


def install_job(job: dict, name: str, packages: List[str], force_pip: bool, protect: str, dry_run: bool) -> dict:
    """Install (or only simulate) the packages.

    protect: "all" keeps every other installed package at its version, "held" keeps the venv's held and pinned
    packages, "none" lets the resolver change anything. Protection goes through a constraints file, so a
    conflict fails loudly instead of silently changing numpy & co."""
    row = venv_entry(name)
    py = config_schema.venv_python_path(row["path"])
    before = installed_versions(py)
    cmd = install_prefix({"env_type": "venv", "venv_path": row["path"]}, force_pip=force_pip)  # no --upgrade: with uv it upgrades every dependency too
    if dry_run:
        cmd.append("--dry-run")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    LOG_DIR.mkdir(exist_ok=True)
    requested = {canonicalize_name(re.split(r"[=<>!~ ]", p, maxsplit=1)[0]) for p in packages}
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
        return {"exit_code": code, "changes": parse_install_changes(lines) if code == 0 else []}
    return record_install(job, name, stamp, before, installed_versions(py), code)


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


def source_status(path: str, fetch: bool) -> dict:
    """repo_status, plus the workflow sources that are not git repos (partial syncs or plain folders)."""
    if REPO_KIND.get(path) == "workflows" and not os.path.isdir(os.path.join(path, ".git")):
        row = repo_status(path, fetch=False)
        row["state"] = "partial" if os.path.isfile(os.path.join(path, METADATA_FILE)) else "not-git"
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
    REPO_PATHS.clear()
    REPO_PATHS.update(repos)
    REPO_KIND.clear()
    REPO_KIND.update({r: kind for r in repos})
    rows, done = [], 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(source_status, r, fetch): r for r in repos}
        try:
            for fut in as_completed(futs):
                rows.append(fut.result())
                done += 1
                job["progress"] = {"label": "Repositories", "i": done, "n": len(repos), "suffix": rows[-1]["name"]}
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
    unknown = [p for p in paths if p not in REPO_PATHS]
    if unknown:
        raise ValueError("not a scanned repository: " + unknown[0])
    local = {"stash": "2", "replace": "1", "skip": "3", "fetch": "5", "move_json": "6"}[on_local]
    resolve = {"skip": "skip", "reset": "reset", "sparse": "sparse"}[on_conflict]
    cache_root = (HERE / ".partial_repo_cache").resolve()
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
        job["log"] += [f"    {c}" for c in r["commits"][:8]]

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
    key_cfg = {}
    cfg_file = HERE / "compare_venv_versions_config.json"
    if cfg_file.is_file():
        key_cfg = json.loads(cfg_file.read_text(encoding="utf-8-sig"))
    key_names = {"python", "cuda (from torch)"} | {canonicalize_name(m) for m in key_cfg.get("modules", [])}
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
                run_in_thread(job, install_job, body["venv"], packages, bool(body.get("force_pip")), str(body.get("protect", "held")), bool(body.get("dry_run")))
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
            elif self.path == "/api/repo_detail":
                if str(body["path"]) not in REPO_PATHS:
                    return self.send_json({"error": "unknown repository"}, 400)
                self.send_json(incoming_changes(str(body["path"])))
            elif self.path == "/api/repos_update":
                job = new_job("repos")
                run_in_thread(job, repos_update_job, [str(x) for x in body["paths"]], str(body.get("on_local", "stash")), str(body.get("on_conflict", "skip")), bool(body.get("dry_run")))
                self.send_json({"job": job["id"]})
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"ComfyUI utils GUI: {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
