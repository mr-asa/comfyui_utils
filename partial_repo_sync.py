#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Partial repo sync utility (Windows-oriented).

Sync selected files/folders from a git repo into a target folder.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
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


def _split_github_tree_url(url: str) -> Optional[Tuple[str, str, str]]:
    m = re.match(r"^https?://github\.com/([^/]+)/([^/]+)/(tree|blob)/([^/]+)(?:/(.+))?$", url.strip())
    if not m:
        return None
    owner, repo, _, branch, subpath = m.groups()
    base = f"https://github.com/{owner}/{repo}"
    return base, branch, (subpath or "").strip("/")


def _maybe_fix_repo_url(
    repo: str, paths: List[str], branch: str
) -> Tuple[str, List[str], str]:
    info = _split_github_tree_url(repo)
    if not info:
        return repo, paths, branch
    base, branch_from_url, subpath = info
    print("Detected GitHub tree/blob URL.")
    ans = input("Convert to repo + path? [Y/n]: ").strip().lower()
    if ans in ("n", "no"):
        return repo, paths, branch
    repo = base
    if subpath and subpath not in paths:
        paths = paths + [subpath]
    if branch_from_url and not branch:
        branch = branch_from_url
    return repo, paths, branch


def _parse_indices(text: str, max_index: int) -> List[int]:
    if not text:
        return []
    items: List[int] = []
    for token in text.replace(",", " ").split():
        if "-" in token:
            a, b = token.split("-", 1)
            if a.isdigit() and b.isdigit():
                start, end = int(a), int(b)
                if start > end:
                    start, end = end, start
                for i in range(start, end + 1):
                    if 1 <= i <= max_index:
                        items.append(i)
        elif token.isdigit():
            i = int(token)
            if 1 <= i <= max_index:
                items.append(i)
    seen: set[int] = set()
    out: List[int] = []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _detect_default_branch(git_exe: str, repo_url: str) -> str:
    ok, out = _run([git_exe, "ls-remote", "--symref", repo_url, "HEAD"])
    if not ok:
        return "main"
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("ref:") and "HEAD" in line:
            # ref: refs/heads/main	HEAD
            parts = line.split()
            if parts:
                ref = parts[1] if len(parts) > 1 else parts[0].replace("ref:", "").strip()
                if ref.startswith("refs/heads/"):
                    return ref.replace("refs/heads/", "").strip() or "main"
    return "main"


def _resolve_branch(git_exe: str, job: Dict[str, object]) -> str:
    branch = str(job.get("branch") or "").strip()
    if branch:
        return branch
    repo = str(job.get("repo") or "").strip()
    if not repo:
        return "main"
    branch = _detect_default_branch(git_exe, repo)
    job["branch"] = branch
    return branch


def _ensure_repo_cache(
    git_exe: str,
    cache_dir: Path,
    repo_url: str,
) -> Tuple[bool, str]:
    if (cache_dir / ".git").exists():
        return True, "ok"

    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    ok, out = _run([git_exe, "clone", "--filter=blob:none", "--no-checkout", repo_url, str(cache_dir)])
    if not ok:
        ok, out = _run([git_exe, "clone", "--no-checkout", repo_url, str(cache_dir)])
    if not ok:
        return False, out or "git clone failed"
    return True, "ok"


def _expand_paths(
    git_exe: str, cache_dir: Path, ref: str, raw_paths: List[str]
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

    ok, out = _run([git_exe, "ls-tree", "-r", "--name-only", ref], cwd=cache_dir)
    if not ok:
        return False, out or f"git ls-tree {ref} failed", [], True
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
    seen: set[str] = set()
    out_paths: List[str] = []
    for p in all_paths:
        if p in seen:
            continue
        seen.add(p)
        out_paths.append(p)
    use_cone = not any(p in file_set for p in out_paths)
    return True, "", out_paths, use_cone


def _set_sparse_paths(
    git_exe: str, cache_dir: Path, paths: List[str], use_cone: bool
) -> Tuple[bool, str]:
    init_args = ["sparse-checkout", "init", "--cone" if use_cone else "--no-cone"]
    ok, out = _run([git_exe, *init_args], cwd=cache_dir)
    if not ok and "already enabled" not in out.lower():
        return False, out or "git sparse-checkout init failed"

    sparse_paths = [_to_posix(p) for p in paths]
    set_args = ["sparse-checkout", "set"]
    if not use_cone:
        set_args += ["--no-cone", "--skip-checks"]
    set_args += sparse_paths
    ok, out = _run([git_exe, *set_args], cwd=cache_dir)
    if not ok:
        return False, out or "git sparse-checkout set failed"
    return True, "ok"


def _ensure_branch_checked_out(
    git_exe: str, cache_dir: Path, branch: str
) -> Tuple[bool, str]:
    ok, _ = _run([git_exe, "rev-parse", "--verify", branch], cwd=cache_dir)
    if ok:
        ok, out = _run([git_exe, "checkout", branch], cwd=cache_dir)
        return ok, out or f"git checkout {branch} failed"
    ok, out = _run([git_exe, "checkout", "-B", branch, f"origin/{branch}"], cwd=cache_dir)
    return ok, out or f"git checkout -B {branch} failed"


def _get_commit(git_exe: str, cache_dir: Path, ref: str) -> Optional[str]:
    ok, out = _run([git_exe, "rev-parse", ref], cwd=cache_dir)
    return out.strip() if ok and out.strip() else None


def _diff_names(
    git_exe: str, cache_dir: Path, a: str, b: str, paths: List[str]
) -> Tuple[bool, List[str]]:
    cmd = [git_exe, "diff", "--name-only", a, b, "--"]
    cmd += [_to_posix(p) for p in paths]
    ok, out = _run(cmd, cwd=cache_dir)
    if not ok:
        return False, []
    files = [line.strip() for line in out.splitlines() if line.strip()]
    return True, files


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


def _missing_targets(target_dir: Path, rel_paths: List[str]) -> List[str]:
    missing: List[str] = []
    for rel in rel_paths:
        dst = target_dir / Path(rel)
        if not dst.exists():
            missing.append(rel)
    return missing


def _load_jobs(jobs_path: Path) -> List[Dict[str, object]]:
    data = _load_json(jobs_path)
    if isinstance(data.get("jobs"), list):
        return [j for j in data["jobs"] if isinstance(j, dict)]
    if isinstance(data, list):
        return [j for j in data if isinstance(j, dict)]
    return []


def _save_jobs(jobs_path: Path, jobs: List[Dict[str, object]]) -> None:
    _save_json(jobs_path, {"jobs": jobs})


def _prompt_job(git_exe: str) -> Dict[str, object]:
    print("Create a sync job (type NO to cancel).")
    name = input("Job name: ").strip()
    if name.upper() == "NO":
        raise SystemExit("Cancelled by user.")
    repo = input("Repo URL (https://... or git@...): ").strip()
    if repo.upper() == "NO":
        raise SystemExit("Cancelled by user.")
    target = input("Target folder (absolute path): ").strip()
    if target.upper() == "NO":
        raise SystemExit("Cancelled by user.")
    paths_raw = input("Paths to sync (comma-separated): ").strip()
    if paths_raw.upper() == "NO":
        raise SystemExit("Cancelled by user.")
    paths = [p.strip() for p in paths_raw.split(",") if p.strip()]
    repo, paths, branch = _maybe_fix_repo_url(repo, paths, "")
    job = {
        "name": name or _repo_name_from_url(repo),
        "repo": repo,
        "target": target,
        "paths": paths,
    }
    if branch:
        job["branch"] = branch
    job["branch"] = _resolve_branch(git_exe, job)
    return job


def _ensure_jobs_interactive(jobs_path: Path, git_exe: str) -> List[Dict[str, object]]:
    jobs = _load_jobs(jobs_path)
    if jobs:
        return jobs
    if jobs_path.exists():
        print(f"Jobs file is empty or invalid: {jobs_path}")
    else:
        print(f"Jobs file not found: {jobs_path}")
    job = _prompt_job(git_exe)
    data = {"jobs": [job]}
    _save_json(jobs_path, data)
    print(f"Saved jobs file: {jobs_path}")
    return [job]


def _print_jobs(jobs: List[Dict[str, object]]) -> None:
    if not jobs:
        print("No jobs configured.")
        return
    print("Jobs:")
    for i, job in enumerate(jobs, 1):
        name = str(job.get("name") or "")
        repo = str(job.get("repo") or "")
        target = str(job.get("target") or "")
        paths = job.get("paths") if isinstance(job.get("paths"), list) else []
        print(f"  [{i}] {name} | {repo} -> {target} | paths: {len(paths)}")


def _prompt_edit_field(label: str, value: str) -> str:
    raw = input(f"{label} [{value}]: ").strip()
    return value if not raw else raw


def _edit_paths(paths: List[str]) -> List[str]:
    items = list(paths)
    while True:
        print("Paths:")
        if not items:
            print("  (empty)")
        else:
            for i, p in enumerate(items, 1):
                print(f"  [{i}] {p}")
        print("Commands: + add, - n|n-m remove, c clear, q back")
        cmd = input("paths> ").strip()
        if not cmd:
            continue
        if cmd.lower() in ("q", "quit", "back"):
            return items
        if cmd == "c":
            items = []
            continue
        if cmd.startswith("+"):
            raw = cmd[1:].strip() or input("Add path: ").strip()
            if not raw:
                continue
            if raw.startswith("re:") or raw.startswith("regex:"):
                pat = raw.split(":", 1)[1]
                try:
                    re.compile(pat)
                except re.error as e:
                    print(f"Invalid regex: {e}")
                    continue
            items.append(raw)
            continue
        if cmd.startswith("-"):
            sel = cmd[1:].strip() or input("Remove indices: ").strip()
            idxs = _parse_indices(sel, len(items))
            if not idxs:
                print("No valid indices.")
                continue
            items = [p for i, p in enumerate(items, 1) if i not in set(idxs)]
            continue
        print("Unknown command.")


def _edit_job(git_exe: str, job: Dict[str, object]) -> Dict[str, object]:
    name = _prompt_edit_field("Name", str(job.get("name") or ""))
    repo = _prompt_edit_field("Repo", str(job.get("repo") or ""))
    target = _prompt_edit_field("Target", str(job.get("target") or ""))
    paths = job.get("paths") if isinstance(job.get("paths"), list) else []
    repo, paths, branch = _maybe_fix_repo_url(repo, [str(p) for p in paths], "")
    job["name"] = name or _repo_name_from_url(repo)
    job["repo"] = repo
    job["target"] = target
    job["paths"] = paths
    if branch:
        job["branch"] = branch
    job["branch"] = _resolve_branch(git_exe, job)
    return job


def _sync_job(
    git_exe: str,
    cache_root: Path,
    job: Dict[str, object],
    dry_run: bool,
) -> bool:
    name = str(job.get("name") or _repo_name_from_url(str(job.get("repo") or "")))
    repo = str(job.get("repo") or "").strip()
    target = str(job.get("target") or "").strip()
    branch = _resolve_branch(git_exe, job)
    paths = job.get("paths") if isinstance(job.get("paths"), list) else []
    paths = [str(p).strip() for p in paths if isinstance(p, str) and str(p).strip()]

    if not repo or not target or not paths:
        print(f"[{name}] Invalid job config (repo/target/paths required).")
        return False

    safe_name = _safe_name(name)
    cache_dir = cache_root / safe_name
    print(f"[{name}] Repo: {repo}")
    print(f"[{name}] Branch: {branch}")
    print(f"[{name}] Target: {target}")

    ok, info = _ensure_repo_cache(git_exe, cache_dir, repo)
    if not ok:
        print(f"[{name}] ERROR: {info}")
        return False

    ok, out = _run([git_exe, "fetch", "--all", "--tags"], cwd=cache_dir)
    if not ok:
        print(f"[{name}] ERROR: {out or 'git fetch failed'}")
        return False

    ref = f"origin/{branch}"
    ok, info, resolved_paths, use_cone = _expand_paths(git_exe, cache_dir, ref, paths)
    if not ok:
        print(f"[{name}] ERROR: {info}")
        return False
    if not resolved_paths:
        print(f"[{name}] No matching paths after expansion.")
        return False

    ok, out = _set_sparse_paths(git_exe, cache_dir, resolved_paths, use_cone)
    if not ok:
        print(f"[{name}] ERROR: {out}")
        return False

    ok, out = _ensure_branch_checked_out(git_exe, cache_dir, branch)
    if not ok:
        print(f"[{name}] ERROR: {out}")
        return False

    local_head = _get_commit(git_exe, cache_dir, "HEAD")
    remote_head = _get_commit(git_exe, cache_dir, ref)
    if not local_head or not remote_head:
        print(f"[{name}] ERROR: Failed to resolve HEAD commits.")
        return False

    changed: List[str] = []
    if local_head != remote_head:
        ok, changed = _diff_names(git_exe, cache_dir, local_head, remote_head, resolved_paths)
        if not ok:
            print(f"[{name}] ERROR: Failed to diff changes.")
            return False
        ok, out = _run([git_exe, "pull", "--ff-only"], cwd=cache_dir)
        if not ok:
            print(f"[{name}] ERROR: {out or 'git pull failed'}")
            return False

    target_dir = Path(target).resolve()
    missing = _missing_targets(target_dir, resolved_paths)
    if not changed and not missing:
        print(f"[{name}] No changes. Skipping sync.")
        print()
        return True
    if not changed and missing:
        print(f"[{name}] Target missing paths: {len(missing)}. Copying.")
        changed = missing

    for rel in changed:
        src = cache_dir / Path(rel)
        dst = target_dir / Path(rel)
        msg = _copy_path(src, dst, dry_run)
        print(f"[{name}] {msg}")
    print()
    return True


def _interactive_loop(
    jobs_path: Path,
    git_exe: str,
    cache_root: Path,
    dry_run: bool,
) -> int:
    jobs = _ensure_jobs_interactive(jobs_path, git_exe)
    any_errors = False
    while True:
        _print_jobs(jobs)
        print("Commands: l=list, a=add, e n=edit, d n=delete, p n=paths, s n|all=sync, q=quit, enter=refresh")
        cmd = input("> ").strip()
        if not cmd:
            continue
        parts = cmd.split()
        action = parts[0].lower()
        if action in ("q", "quit", "exit"):
            _save_jobs(jobs_path, jobs)
            return 1 if any_errors else 0
        if action in ("l", "list"):
            continue
        if action == "a":
            job = _prompt_job(git_exe)
            jobs.append(job)
            _save_jobs(jobs_path, jobs)
            continue
        if action in ("e", "d", "p", "s"):
            if len(parts) < 2:
                if action == "s":
                    for job in jobs:
                        ok = _sync_job(git_exe, cache_root, job, dry_run)
                        if not ok:
                            any_errors = True
                    _save_jobs(jobs_path, jobs)
                    continue
                print("Specify index or 'all'.")
                continue
            sel = parts[1].lower()
            if action == "s" and sel == "all":
                for job in jobs:
                    ok = _sync_job(git_exe, cache_root, job, dry_run)
                    if not ok:
                        any_errors = True
                _save_jobs(jobs_path, jobs)
                continue
            idxs = _parse_indices(sel, len(jobs))
            if not idxs:
                print("No valid indices.")
                continue
            if action == "d":
                jobs = [j for i, j in enumerate(jobs, 1) if i not in set(idxs)]
                _save_jobs(jobs_path, jobs)
                continue
            if action == "e":
                for i in idxs:
                    jobs[i - 1] = _edit_job(git_exe, jobs[i - 1])
                _save_jobs(jobs_path, jobs)
                continue
            if action == "p":
                for i in idxs:
                    paths = jobs[i - 1].get("paths") if isinstance(jobs[i - 1].get("paths"), list) else []
                    jobs[i - 1]["paths"] = _edit_paths([str(p) for p in paths])
                _save_jobs(jobs_path, jobs)
                continue
            if action == "s":
                for i in idxs:
                    ok = _sync_job(git_exe, cache_root, jobs[i - 1], dry_run)
                    if not ok:
                        any_errors = True
                _save_jobs(jobs_path, jobs)
                continue
        print("Unknown command.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync selected paths from git repos.")
    parser.add_argument("--jobs", default="partial_repo_sync_config.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    jobs_path = (here / args.jobs).resolve()

    _ensure_file(
        jobs_path,
        {
            "jobs": []
        },
    )

    git_exe = "git"
    cache_root = (here / ".partial_repo_cache").resolve()
    cache_root.mkdir(parents=True, exist_ok=True)

    if args.interactive:
        return _interactive_loop(jobs_path, git_exe, cache_root, args.dry_run)

    jobs = _ensure_jobs_interactive(jobs_path, git_exe)
    any_errors = False
    for job in jobs:
        ok = _sync_job(git_exe, cache_root, job, args.dry_run)
        if not ok:
            any_errors = True
    _save_jobs(jobs_path, jobs)
    return 1 if any_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
