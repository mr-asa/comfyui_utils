#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Partial repo sync utility (Windows-oriented).

Sync selected files/folders from a git repo into a target folder.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _load_json(path: Path) -> Dict[str, object]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        try:
            with path.open("r", encoding="utf-8-sig") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    except Exception:
        return {}


def _save_json(path: Path, data: Dict[str, object]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def _ensure_file(path: Path, data: Dict[str, object]) -> None:
    if path.exists():
        return
    _save_json(path, data)


def _copy_if_missing(dst: Path, src: Path) -> bool:
    if dst.exists():
        return False
    if not src.exists():
        return False
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return True


def _run(cmd: List[str], cwd: Optional[Path] = None) -> Tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        out = proc.stdout.decode("utf-8", errors="replace").strip()
        return proc.returncode == 0, out
    except FileNotFoundError:
        return False, "Command not found"


def _to_posix(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def _repo_name_from_url(url: str) -> str:
    base = url.rstrip("/").split("/")[-1]
    if base.endswith(".git"):
        base = base[:-4]
    return base or "repo"


def _safe_name(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name.strip())
    cleaned = cleaned.strip("_")
    return cleaned or "job"


def _ensure_repo_cache(
    git_exe: str,
    cache_dir: Path,
    repo_url: str,
    branch: str,
    paths: List[str],
) -> Tuple[bool, str, List[str]]:
    if not (cache_dir / ".git").exists():
        ok, out = _run([git_exe, "clone", "--no-checkout", repo_url, str(cache_dir)])
        if not ok:
            return False, out or "git clone failed", []

    ok, out = _run([git_exe, "fetch", "--all", "--tags"], cwd=cache_dir)
    if not ok:
        return False, out or "git fetch failed", []

    ok, info, resolved_paths, use_cone = _expand_paths(git_exe, cache_dir, branch, paths)
    if not ok:
        return False, info or "path expansion failed", []
    if not resolved_paths:
        return False, "No matching paths after expansion.", []

    init_args = ["sparse-checkout", "init", "--cone" if use_cone else "--no-cone"]
    ok, out = _run([git_exe, *init_args], cwd=cache_dir)
    if not ok and "already enabled" not in out.lower():
        return False, out or "git sparse-checkout init failed", []

    sparse_paths = [_to_posix(p) for p in resolved_paths]
    set_args = ["sparse-checkout", "set"]
    if not use_cone:
        set_args += ["--no-cone", "--skip-checks"]
    set_args += sparse_paths
    ok, out = _run([git_exe, *set_args], cwd=cache_dir)
    if not ok:
        return False, out or "git sparse-checkout set failed", []

    ok, out = _run([git_exe, "checkout", branch], cwd=cache_dir)
    if not ok:
        return False, out or f"git checkout {branch} failed", []

    ok, out = _run([git_exe, "pull", "--ff-only"], cwd=cache_dir)
    if not ok:
        return False, out or "git pull failed", []

    return True, "ok", resolved_paths


def _expand_paths(
    git_exe: str, cache_dir: Path, branch: str, raw_paths: List[str]
) -> Tuple[bool, str, List[str], bool]:
    static_paths: List[str] = []
    regex_patterns: List[str] = []
    for p in raw_paths:
        if p.startswith("re:"):
            regex_patterns.append(p[3:])
        elif p.startswith("regex:"):
            regex_patterns.append(p[6:])
        else:
            static_paths.append(_to_posix(p))

    ok, out = _run([git_exe, "ls-tree", "-r", "--name-only", branch], cwd=cache_dir)
    if not ok:
        return False, out or f"git ls-tree {branch} failed", [], True
    files = [line.strip() for line in out.splitlines() if line.strip()]
    file_set = set(files)

    matched: List[str] = []
    for pat in regex_patterns:
        try:
            rx = re.compile(pat)
        except re.error as e:
            return False, f"Invalid regex '{pat}': {e}", [], True
        for f in files:
            if rx.search(f) or rx.search(f.replace("/", "\\")):
                matched.append(f)

    all_paths = static_paths + matched
    # de-dup
    seen: set[str] = set()
    out_paths: List[str] = []
    for p in all_paths:
        if p in seen:
            continue
        seen.add(p)
        out_paths.append(p)
    use_cone = not any(p in file_set for p in out_paths)
    return True, "", out_paths, use_cone


def _copy_path(src: Path, dst: Path, dry_run: bool) -> str:
    if not src.exists():
        return f"Missing in repo: {src}"
    if dry_run:
        return f"Would copy: {src} -> {dst}"
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return f"Copied: {src} -> {dst}"


def _load_jobs(jobs_path: Path) -> List[Dict[str, object]]:
    data = _load_json(jobs_path)
    if isinstance(data.get("jobs"), list):
        return [j for j in data["jobs"] if isinstance(j, dict)]
    if isinstance(data, list):
        return [j for j in data if isinstance(j, dict)]
    return []


def _prompt_job() -> Dict[str, object]:
    print("Create a sync job (type NO to cancel).")
    name = input("Job name: ").strip()
    if name.upper() == "NO":
        raise SystemExit("Cancelled by user.")
    repo = input("Repo URL (https://... or git@...): ").strip()
    if repo.upper() == "NO":
        raise SystemExit("Cancelled by user.")
    branch = input("Branch [main]: ").strip() or "main"
    if branch.upper() == "NO":
        raise SystemExit("Cancelled by user.")
    target = input("Target folder (absolute path): ").strip()
    if target.upper() == "NO":
        raise SystemExit("Cancelled by user.")
    paths_raw = input("Paths to sync (comma-separated): ").strip()
    if paths_raw.upper() == "NO":
        raise SystemExit("Cancelled by user.")
    paths = [p.strip() for p in paths_raw.split(",") if p.strip()]
    return {
        "name": name or _repo_name_from_url(repo),
        "repo": repo,
        "branch": branch,
        "target": target,
        "paths": paths,
    }


def _ensure_jobs_interactive(jobs_path: Path) -> List[Dict[str, object]]:
    jobs = _load_jobs(jobs_path)
    if jobs:
        return jobs
    if jobs_path.exists():
        print(f"Jobs file is empty or invalid: {jobs_path}")
    else:
        print(f"Jobs file not found: {jobs_path}")
    job = _prompt_job()
    data = {"jobs": [job]}
    _save_json(jobs_path, data)
    print(f"Saved jobs file: {jobs_path}")
    return [job]


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync selected paths from git repos.")
    parser.add_argument("--jobs", default="partial_repo_sync_config.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    jobs_path = (here / args.jobs).resolve()

    _ensure_file(
        jobs_path,
        {
            "jobs": []
        },
    )

    jobs = _ensure_jobs_interactive(jobs_path)

    git_exe = "git"
    cache_root = (here / ".partial_repo_cache").resolve()
    default_branch = "main"
    cache_root.mkdir(parents=True, exist_ok=True)

    any_errors = False
    for job in jobs:
        name = str(job.get("name") or _repo_name_from_url(str(job.get("repo") or "")))
        repo = str(job.get("repo") or "").strip()
        target = str(job.get("target") or "").strip()
        branch = str(job.get("branch") or default_branch).strip()
        paths = job.get("paths") if isinstance(job.get("paths"), list) else []
        paths = [str(p).strip() for p in paths if isinstance(p, str) and str(p).strip()]

        if not repo or not target or not paths:
            print(f"[{name}] Invalid job config (repo/target/paths required).")
            any_errors = True
            continue

        safe_name = _safe_name(name)
        cache_dir = cache_root / safe_name
        print(f"[{name}] Repo: {repo}")
        print(f"[{name}] Branch: {branch}")
        print(f"[{name}] Target: {target}")

        ok, info, resolved_paths = _ensure_repo_cache(git_exe, cache_dir, repo, branch, paths)
        if not ok:
            print(f"[{name}] ERROR: {info}")
            any_errors = True
            continue

        target_dir = Path(target).resolve()
        for rel in resolved_paths:
            src = cache_dir / Path(rel)
            dst = target_dir / Path(rel)
            msg = _copy_path(src, dst, args.dry_run)
            print(f"[{name}] {msg}")
        print()

    return 1 if any_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
