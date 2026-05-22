#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
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
        messages.append(_copy_managed_path(src, dst, dry_run))
    return True, messages


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
