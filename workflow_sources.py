#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import filecmp
import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse


METADATA_FILE = ".workflow_source.json"
HF_REPO_TYPES = {"datasets", "spaces"}


@dataclass
class WorkflowSource:
    mode: str
    provider: str
    owner: str
    repo: str
    repo_url: str
    source_url: str
    folder_name: str
    branch: Optional[str] = None
    paths: Optional[List[str]] = None
    repo_type: Optional[str] = None
    synced_commit: Optional[str] = None  # commit of the branch the files were last copied from


def run_git(args: List[str], cwd: Optional[Path] = None) -> Tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        return False, "git command not found"
    return proc.returncode == 0, (proc.stdout or "").strip()


def safe_folder_name(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in name.strip())
    cleaned = cleaned.strip(" ._")
    return cleaned or "workflow_repo"


def workflow_folder_name(owner: str, repo: str) -> str:
    return safe_folder_name(f"{repo}_{owner}")


def _strip_git_suffix(value: str) -> str:
    return value[:-4] if value.endswith(".git") else value


def _split_branch_path(parts: List[str]) -> Tuple[Optional[str], str]:
    if not parts:
        return None, ""
    branch = parts[0]
    subpath = "/".join(parts[1:]).strip("/")
    return branch, subpath


def _parse_github(raw_url: str, parts: List[str]) -> Optional[WorkflowSource]:
    if len(parts) < 2:
        return None
    owner = parts[0]
    repo = _strip_git_suffix(parts[1])
    if not owner or not repo:
        return None

    repo_url = f"https://github.com/{owner}/{repo}"
    folder = workflow_folder_name(owner, repo)
    if len(parts) >= 4 and parts[2] in {"tree", "blob"}:
        branch, subpath = _split_branch_path(parts[3:])
        if not branch or not subpath:
            return None
        return WorkflowSource(
            mode="partial",
            provider="github",
            owner=owner,
            repo=repo,
            repo_url=repo_url,
            source_url=raw_url,
            folder_name=folder,
            branch=branch,
            paths=[subpath],
        )

    return WorkflowSource(
        mode="full",
        provider="github",
        owner=owner,
        repo=repo,
        repo_url=repo_url,
        source_url=raw_url,
        folder_name=folder,
    )


def _parse_huggingface(raw_url: str, parts: List[str]) -> Optional[WorkflowSource]:
    if not parts:
        return None

    repo_type: Optional[str] = None
    offset = 0
    if parts[0] in HF_REPO_TYPES:
        repo_type = parts[0]
        offset = 1

    if len(parts) < offset + 2:
        return None

    owner = parts[offset]
    repo = _strip_git_suffix(parts[offset + 1])
    if not owner or not repo:
        return None

    prefix = f"/{repo_type}" if repo_type else ""
    repo_url = f"https://huggingface.co{prefix}/{owner}/{repo}"
    folder = workflow_folder_name(owner, repo)
    rest = parts[offset + 2 :]

    if len(rest) >= 3 and rest[0] in {"tree", "blob"}:
        branch, subpath = _split_branch_path(rest[1:])
        if not branch or not subpath:
            return None
        return WorkflowSource(
            mode="partial",
            provider="huggingface",
            owner=owner,
            repo=repo,
            repo_url=repo_url,
            source_url=raw_url,
            folder_name=folder,
            branch=branch,
            paths=[subpath],
            repo_type=repo_type,
        )

    return WorkflowSource(
        mode="full",
        provider="huggingface",
        owner=owner,
        repo=repo,
        repo_url=repo_url,
        source_url=raw_url,
        folder_name=folder,
        repo_type=repo_type,
    )


def parse_workflow_url(raw_url: str) -> Optional[WorkflowSource]:
    raw_url = raw_url.strip()
    if not raw_url:
        return None
    try:
        parsed = urlparse(raw_url)
    except Exception:
        return None

    host = parsed.netloc.lower()
    parts = [p for p in parsed.path.split("/") if p]
    if host == "github.com":
        return _parse_github(raw_url, parts)
    if host == "huggingface.co":
        return _parse_huggingface(raw_url, parts)
    return None


def read_metadata(target_dir: Path) -> Optional[WorkflowSource]:
    path = target_dir / METADATA_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return WorkflowSource(
            mode=str(data.get("mode") or ""),
            provider=str(data.get("provider") or ""),
            owner=str(data.get("owner") or ""),
            repo=str(data.get("repo") or ""),
            repo_url=str(data.get("repo_url") or ""),
            source_url=str(data.get("source_url") or data.get("repo_url") or ""),
            folder_name=str(data.get("folder_name") or target_dir.name),
            branch=str(data.get("branch") or "") or None,
            paths=[str(p) for p in data.get("paths", []) if isinstance(p, str)],
            repo_type=str(data.get("repo_type") or "") or None,
            synced_commit=str(data.get("synced_commit") or "") or None,
        )
    except Exception:
        return None


def write_metadata(target_dir: Path, source: WorkflowSource) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    data: Dict[str, Any] = asdict(source)
    data["version"] = 1
    (target_dir / METADATA_FILE).write_text(
        json.dumps(data, indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def detect_default_branch(repo_url: str) -> str:
    ok, out = run_git(["ls-remote", "--symref", repo_url, "HEAD"])
    if not ok:
        return "main"
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("ref:") and "HEAD" in line:
            parts = line.split()
            if len(parts) >= 2 and parts[1].startswith("refs/heads/"):
                return parts[1].replace("refs/heads/", "", 1) or "main"
    return "main"


def _cache_key(source: WorkflowSource) -> str:
    type_part = f"_{source.repo_type}" if source.repo_type else ""
    return safe_folder_name(f"{source.provider}{type_part}_{source.repo}_{source.owner}")


def _to_posix(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def _ensure_cache(cache_dir: Path, repo_url: str) -> Tuple[bool, str]:
    if (cache_dir / ".git").exists():
        return True, "ok"
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    ok, out = run_git(["clone", "--filter=blob:none", "--no-checkout", repo_url, str(cache_dir)])
    if not ok:
        ok, out = run_git(["clone", "--no-checkout", repo_url, str(cache_dir)])
    return ok, out or "ok"


def _path_is_file(cache_dir: Path, ref: str, rel_path: str) -> bool:
    ok, out = run_git(["ls-tree", "-r", "--name-only", ref, "--", rel_path], cwd=cache_dir)
    if not ok:
        return False
    files = {line.strip() for line in out.splitlines() if line.strip()}
    return rel_path in files


def _set_sparse_paths(cache_dir: Path, ref: str, paths: List[str]) -> Tuple[bool, str]:
    file_paths = [p for p in paths if _path_is_file(cache_dir, ref, p)]
    use_cone = not file_paths
    init_args = ["sparse-checkout", "init", "--cone" if use_cone else "--no-cone"]
    ok, out = run_git(init_args, cwd=cache_dir)
    if not ok and "already enabled" not in out.lower():
        return False, out or "git sparse-checkout init failed"

    set_args = ["sparse-checkout", "set"]
    if not use_cone:
        set_args += ["--no-cone", "--skip-checks"]
    set_args += paths
    ok, out = run_git(set_args, cwd=cache_dir)
    return ok, out or "ok"


def _checkout_branch(cache_dir: Path, branch: str) -> Tuple[bool, str]:
    ok, out = run_git(["checkout", "-B", branch, f"origin/{branch}"], cwd=cache_dir)
    return ok, out or "ok"


def _backup_edited_jsons(src: Path, dst: Path, target_dir: Path, backup_dir: Path) -> List[str]:
    """Copy local JSONs that differ from the repo version (or are not in the repo) to backup_dir before dst is overwritten."""
    local = [dst] if dst.is_file() else sorted(p for p in dst.rglob("*") if p.is_file()) if dst.is_dir() else []
    saved: List[str] = []
    for f in local:
        if f.suffix.lower() != ".json":
            continue
        repo_file = src if dst.is_file() else src / f.relative_to(dst)
        if repo_file.is_file() and filecmp.cmp(f, repo_file, shallow=False):
            continue
        backup_dir.mkdir(parents=True, exist_ok=True)
        base, ext = os.path.splitext(re.sub(r"[\\/]+", "_", f.relative_to(target_dir).as_posix()))
        out, i = backup_dir / f"{base}{ext}", 1
        while out.exists():
            out = backup_dir / f"{base}_{i}{ext}"
            i += 1
        shutil.copy2(f, out)
        saved.append(f"Edited JSON saved: {f.relative_to(target_dir).as_posix()} -> {out}")
    return saved


def _copy_managed_path(src: Path, dst: Path, dry_run: bool) -> str:
    if not src.exists():
        return f"Missing in repo: {src}"
    if dry_run:
        return f"Would sync: {src} -> {dst}"
    if dst.exists():
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return f"Synced: {src} -> {dst}"


def sync_partial_source(
    source: WorkflowSource,
    target_dir: Path,
    cache_root: Path,
    dry_run: bool = False,
) -> Tuple[bool, List[str]]:
    if source.mode != "partial":
        return False, ["Source is not partial."]
    paths = [_to_posix(p) for p in (source.paths or []) if _to_posix(p)]
    if not source.repo_url or not paths:
        return False, ["Partial source requires repo_url and paths."]

    branch = source.branch or detect_default_branch(source.repo_url)
    source.branch = branch
    source.paths = paths

    messages: List[str] = []
    cache_dir = cache_root / _cache_key(source)
    ok, out = _ensure_cache(cache_dir, source.repo_url)
    if not ok:
        return False, [out or "git clone failed"]

    ok, out = run_git(["fetch", "--all", "--tags", "--prune"], cwd=cache_dir)
    if not ok:
        return False, [out or "git fetch failed"]

    ref = f"origin/{branch}"
    ok, out = _set_sparse_paths(cache_dir, ref, paths)
    if not ok:
        return False, [out or "git sparse-checkout set failed"]

    ok, out = _checkout_branch(cache_dir, branch)
    if not ok:
        return False, [out or f"git checkout {branch} failed"]

    target_dir.mkdir(parents=True, exist_ok=True)
    for rel in paths:
        src = cache_dir / Path(rel)
        dst = target_dir / Path(rel)
        if not dry_run and src.exists():
            messages += _backup_edited_jsons(src, dst, target_dir, target_dir.parent.parent / "github_edits")
        messages.append(_copy_managed_path(src, dst, dry_run))
    if not dry_run:
        ok, head = run_git(["rev-parse", "HEAD"], cwd=cache_dir)
        if ok and head.strip():
            source.synced_commit = head.strip().splitlines()[0]
    return True, messages


def partial_remote_status(source: WorkflowSource, cache_root: Path) -> Dict[str, Any]:
    """up-to-date / behind / untracked (never synced with a recorded commit) / fetch-failed for a partial source.
    A moved branch only counts as behind when the tracked paths really differ (checked in the sparse cache)."""
    branch = source.branch or detect_default_branch(source.repo_url)
    ok, out = run_git(["ls-remote", source.repo_url, f"refs/heads/{branch}"])
    if not ok or not out.strip():
        return {"state": "fetch-failed", "error": (out or "branch not found")[-200:], "branch": branch}
    remote = out.split()[0]
    synced = source.synced_commit
    if not synced:
        return {"state": "untracked", "remote": remote, "branch": branch}
    if remote == synced:
        return {"state": "up-to-date", "remote": remote, "branch": branch}
    cache_dir = cache_root / _cache_key(source)
    paths = [_to_posix(p) for p in (source.paths or []) if _to_posix(p)]
    if (cache_dir / ".git").exists():
        run_git(["fetch", "origin", branch], cwd=cache_dir)
        ok, diff = run_git(["diff", "--name-only", synced, f"origin/{branch}", "--", *paths], cwd=cache_dir)
        if ok:
            files = [l for l in diff.splitlines() if l.strip()]
            return {"state": "behind" if files else "up-to-date", "remote": remote, "branch": branch, "files": len(files)}
    return {"state": "behind", "remote": remote, "branch": branch, "files": 0}


def partial_changes(source: WorkflowSource, cache_root: Path, old: str, new: str, limit: int = 60) -> Tuple[List[str], List[Tuple[int, int, str]]]:
    """Commit lines and numstat of the tracked paths between two synced commits (empty when unknown)."""
    cache_dir = cache_root / _cache_key(source)
    paths = [_to_posix(p) for p in (source.paths or []) if _to_posix(p)]
    if not old or not new or old == new or not (cache_dir / ".git").exists():
        return [], []
    ok, out = run_git(["log", f"--max-count={limit}", "--format=%h %cs %s", f"{old}..{new}", "--", *paths], cwd=cache_dir)
    commits = [l for l in out.splitlines() if l.strip()] if ok else []
    ok, out = run_git(["diff", "--numstat", old, new, "--", *paths], cwd=cache_dir)
    stat: List[Tuple[int, int, str]] = []
    for line in (out.splitlines() if ok else []):
        parts = line.split("\t", 2)
        if len(parts) == 3:
            stat.append((int(parts[0]) if parts[0].isdigit() else 0, int(parts[1]) if parts[1].isdigit() else 0, parts[2]))
    return commits, stat


def source_from_partial_job(job: Dict[str, Any], target_dir: Path) -> Optional[WorkflowSource]:
    repo_url = str(job.get("repo") or "").strip()
    paths = job.get("paths")
    if not repo_url or not isinstance(paths, list):
        return None
    parsed = parse_workflow_url(repo_url)
    if parsed is None:
        repo_name = _strip_git_suffix(repo_url.rstrip("/").split("/")[-1] or target_dir.name)
        owner = "unknown"
        m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$", repo_url)
        if m:
            owner, repo_name = m.group(1), _strip_git_suffix(m.group(2))
        parsed = WorkflowSource(
            mode="partial",
            provider="git",
            owner=owner,
            repo=repo_name,
            repo_url=repo_url,
            source_url=repo_url,
            folder_name=target_dir.name,
        )

    parsed.mode = "partial"
    parsed.branch = str(job.get("branch") or parsed.branch or "").strip() or None
    parsed.paths = [str(p).strip() for p in paths if isinstance(p, str) and str(p).strip()]
    parsed.folder_name = target_dir.name
    return parsed
