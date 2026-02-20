#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Workflow Repo Updater
=====================

Updates every Git repository located in the relative
``../user/default/workflows/github`` directory (relative to this script).
Non‑Git folders are detected and reported as skipped.

Log format per repository:

--- <Repository Name> ---
branch: <branch name or DETACHED>
<update log>
  - if changes: two blocks
      * Commit messages (old..new HEAD)
      * Changed files: +<added> -<deleted> <path>
  - if no changes: "No changes."
"""

from __future__ import annotations
import argparse
import os
import shutil
import subprocess
import sys
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from comfyui_root import default_workflows_dir, resolve_comfyui_root

def _configure_console_encoding() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


_configure_console_encoding()


@dataclass
class RepoReport:
    name: str
    path: Path
    web_url: str
    branch: str
    changed: bool
    commit_messages: List[str]
    numstat: List[Tuple[int, int, str]]
    notes: List[str]
    skipped: str | None = None
    error: str | None = None
    last_update_at: Optional[str] = None
    delta_from_local: Optional[str] = None


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"
    GRAY = "\033[90m"


def run_git(args: List[str], cwd: Path) -> tuple[bool, str, str]:
    result = subprocess.run(
        ["git", *args], cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    return result.returncode == 0, result.stdout.strip(), result.stderr.strip()


def get_remote_url(path: Path, remote: str = "origin") -> str:
    ok, out, _ = run_git(["config", "--get", f"remote.{remote}.url"], path)
    return out.strip() if ok else ""


def to_web_url(remote_url: str) -> str:
    u = remote_url.strip()
    if not u:
        return ""
    if u.startswith("git@github.com:"):
        u = u.replace("git@github.com:", "https://github.com/")
    if u.endswith(".git"):
        u = u[:-4]
    return u


def _is_windows() -> bool:
    return os.name == "nt"


def _windows_path_is_invalid(repo_rel_path: str) -> bool:
    """
    Best-effort check for filenames that Windows cannot represent.
    This is not exhaustive, but catches the common hard failures (e.g. '|', '*', '?', etc).
    """
    invalid_chars = set('<>:"|?*')
    parts = repo_rel_path.split("/")
    for part in parts:
        if not part:
            continue
        if any(c in invalid_chars for c in part):
            return True
        if part.endswith(" ") or part.endswith("."):
            return True
    return False


def find_windows_incompatible_paths(path: Path, ref: str = "HEAD", limit: int = 12) -> List[str]:
    if not _is_windows():
        return []
    # Use a raw, NUL-delimited listing with quoting disabled, otherwise Git may print
    # non-ASCII paths as escaped octal sequences like \343\200\220..., which would
    # look "Windows-invalid" even though the real filename is fine.
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotePath=false", "ls-tree", "-r", "-z", "--name-only", ref],
            cwd=path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []

    raw = proc.stdout or b""
    bad: List[str] = []
    for chunk in raw.split(b"\0"):
        if not chunk:
            continue
        try:
            rel = chunk.decode("utf-8", errors="surrogateescape")
        except Exception:
            # Fallback: treat undecodable entries as suspicious, but don't spam.
            rel = chunk.decode(errors="replace")
        if _windows_path_is_invalid(rel):
            bad.append(rel)
            if len(bad) >= limit:
                break
    return bad


def repo_dirty(path: Path) -> tuple[bool, str]:
    ok, out, err = run_git(["status", "--porcelain"], path)
    if not ok:
        return False, err or out
    return bool(out.strip()), out.strip()


def summarize_porcelain(status_out: str, limit: int = 30) -> List[str]:
    lines = [ln.rstrip() for ln in status_out.splitlines() if ln.strip()]
    if not lines:
        return []
    if len(lines) <= limit:
        return lines
    return lines[:limit] + [f"... (+{len(lines) - limit} more)"]


def _extract_porcelain_path(line: str) -> str:
    parts = line.split(None, 1)
    if len(parts) < 2:
        return ""
    raw = parts[1].strip()
    if " -> " in raw:
        raw = raw.split(" -> ", 1)[-1].strip()
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        raw = _decode_git_quoted_path(raw[1:-1])
    return raw


def _decode_git_quoted_path(value: str) -> str:
    """
    Decode git's C-style quoted path (used by `git status --porcelain`).
    Handles octal escapes like \\343\\200\\220 and common escaped chars.
    """
    chunks: List[int] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch != "\\":
            chunks.extend(ch.encode("utf-8", errors="surrogatepass"))
            i += 1
            continue
        if i + 1 >= len(value):
            chunks.append(ord("\\"))
            i += 1
            continue

        nxt = value[i + 1]
        # Git uses 3-digit octal escapes for non-ASCII bytes in quoted paths.
        if i + 3 < len(value) and value[i + 1:i + 4].isdigit() and all(c in "01234567" for c in value[i + 1:i + 4]):
            chunks.append(int(value[i + 1:i + 4], 8))
            i += 4
            continue
        if nxt == "n":
            chunks.append(ord("\n"))
            i += 2
            continue
        if nxt == "t":
            chunks.append(ord("\t"))
            i += 2
            continue
        if nxt in {'\\', '"'}:
            chunks.append(ord(nxt))
            i += 2
            continue

        # Unknown escape: keep literal char after backslash.
        chunks.extend(nxt.encode("utf-8", errors="surrogatepass"))
        i += 2

    return bytes(chunks).decode("utf-8", errors="replace")


def _flatten_rel_path(rel_path: str) -> str:
    return re.sub(r"[\\/]+", "_", rel_path.strip("\\/"))


def _unique_target_path(target_dir: Path, filename: str) -> Path:
    base, ext = os.path.splitext(filename)
    candidate = filename
    i = 1
    while (target_dir / candidate).exists():
        candidate = f"{base}_{i}{ext}"
        i += 1
    return target_dir / candidate


def _is_tracked_path(repo_path: Path, rel_path: str) -> bool:
    ok, _, _ = run_git(["ls-files", "--error-unmatch", "--", rel_path], repo_path)
    return ok


def move_local_jsons(repo_path: Path) -> tuple[List[str], List[str]]:
    ok, out, err = run_git(["status", "--porcelain"], repo_path)
    if not ok:
        return [], [f"git status failed: {(err or out).strip()}"]

    json_paths: List[str] = []
    seen: set[str] = set()
    for line in out.splitlines():
        rel = _extract_porcelain_path(line)
        if not rel or not rel.lower().endswith(".json"):
            continue
        if rel in seen:
            continue
        seen.add(rel)
        json_paths.append(rel)

    if not json_paths:
        return [], []

    target_dir = repo_path.parent.parent / "github_edits"
    target_dir.mkdir(parents=True, exist_ok=True)

    moved: List[str] = []
    errors: List[str] = []
    for rel in json_paths:
        src = repo_path / rel
        if not src.is_file():
            errors.append(f"Missing file: {rel}")
            continue
        target_name = _flatten_rel_path(rel)
        target_path = _unique_target_path(target_dir, target_name)
        tracked = _is_tracked_path(repo_path, rel)
        try:
            if tracked:
                shutil.copy2(src, target_path)
            else:
                shutil.move(src, target_path)
        except Exception as e:
            errors.append(f"Failed to move {rel}: {e}")
            continue

        if tracked:
            ok, out, err = run_git(["restore", "--staged", "--worktree", "--", rel], repo_path)
            if not ok:
                ok2, out2, err2 = run_git(["checkout", "--", rel], repo_path)
                if not ok2:
                    msg = (err or out or err2 or out2).strip()
                    errors.append(f"Failed to restore tracked file {rel}: {msg}")
        moved.append(f"{rel} -> {target_path}")

    return moved, errors


def prompt_local_changes_action(name: str, path: Path, branch: str, status_out: str) -> str:
    print(f" {C.YELLOW}⚠️  Local changes detected.{C.RESET}")
    print(f" Path: {path}")
    if branch:
        print(f" Branch: {branch}")
    preview = summarize_porcelain(status_out, limit=12)
    if preview:
        print(f" {C.GRAY}git status --porcelain:{C.RESET}")
        for ln in preview:
            print(f" {C.GRAY}{ln}{C.RESET}")

    print(" Choose an action:")
    print("  1 - replace from remote (discard local changes)")
    print("  2 - merge with local changes (update, then re-apply my changes)")
    print("  3 - skip this repository and continue")
    print("  4 - re-clone repository (overwrite folder completely)")
    print("  5 - fetch only (download updates, keep working tree as-is)")
    print("  6 - move edited JSONs to github_edits, discard local JSON changes")

    if not sys.stdin.isatty():
        print(f" {C.GRAY}stdin is not interactive; defaulting to option 3 (skip).{C.RESET}")
        return "3"

    while True:
        try:
            choice = input(" Your choice [1/2/3/4/5/6] (default 3): ").strip() or "3"
        except (EOFError, KeyboardInterrupt):
            return "3"
        if choice in {"1", "2", "3", "4", "5", "6"}:
            return choice
        print(" Please enter 1, 2, 3, 4, 5, or 6.")


def prompt_windows_incompatible_action(path: Path, branch: str, bad_paths: List[str]) -> str:
    print(f" {C.YELLOW}⚠️  Windows-incompatible paths detected in this repository.{C.RESET}")
    print(" Git cannot checkout/reset these paths on Windows (e.g. names containing '|').")
    print(f" Path: {path}")
    if branch:
        print(f" Branch: {branch}")
    if bad_paths:
        print(f" {C.GRAY}Examples:{C.RESET}")
        for p in bad_paths[:12]:
            print(f" {C.GRAY}{p}{C.RESET}")
        if len(bad_paths) > 12:
            print(f" {C.GRAY}... (+{len(bad_paths) - 12} more){C.RESET}")

    print(" Choose an action:")
    print("  3 - skip this repository and continue")
    print("  5 - fetch + sparse-checkout safe files (download updates, checkout what Windows can)")

    if not sys.stdin.isatty():
        print(f" {C.GRAY}stdin is not interactive; defaulting to option 3 (skip).{C.RESET}")
        return "3"

    while True:
        try:
            choice = input(" Your choice [3/5] (default 3): ").strip() or "3"
        except (EOFError, KeyboardInterrupt):
            return "3"
        if choice in {"3", "5"}:
            return choice
        print(" Please enter 3 or 5.")


def fetch_only(path: Path) -> tuple[bool, str]:
    ok, out, err = run_git(["fetch", "--all", "--tags", "--prune"], path)
    if ok:
        return True, (out or "").strip()
    return False, (err or out or "git fetch failed").strip()


def compute_windows_safe_sparse_patterns(
    path: Path,
    ref: str = "HEAD",
    limit: int = 0,
) -> tuple[List[str], int, int]:
    """
    Build a sparse-checkout pattern set that includes only Windows-safe file paths.
    This avoids checkout failures when any path component is invalid on Windows.
    """
    if not _is_windows():
        return [], 0, 0
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotePath=false", "ls-tree", "-r", "-z", "--name-only", ref],
            cwd=path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except Exception:
        return [], 0, 0
    if proc.returncode != 0:
        return [], 0, 0

    raw = proc.stdout or b""
    patterns: List[str] = []
    seen: set[str] = set()
    skipped = 0
    total = 0
    for chunk in raw.split(b"\0"):
        if not chunk:
            continue
        total += 1
        try:
            rel = chunk.decode("utf-8", errors="surrogateescape")
        except Exception:
            rel = chunk.decode(errors="replace")
        rel = rel.strip().lstrip("/")
        if not rel:
            continue
        if _windows_path_is_invalid(rel):
            skipped += 1
            continue
        if rel in seen:
            continue
        seen.add(rel)
        patterns.append(rel)
        if limit > 0 and len(patterns) >= limit:
            break

    return patterns, skipped, total


def sparse_checkout_windows_safe(path: Path, branch: str) -> tuple[bool, str]:
    patterns, skipped, total = compute_windows_safe_sparse_patterns(path, ref="HEAD")
    if not patterns:
        return False, "No Windows-safe paths found to checkout."

    # Initialize sparse-checkout (non-cone: supports file patterns too)
    ok, out, err = run_git(["sparse-checkout", "init", "--no-cone"], path)
    if not ok:
        return False, (err or out or "git sparse-checkout init failed").strip()

    # Prefer stdin to avoid command line length issues
    error: str | None = None
    proc = subprocess.run(
        ["git", "sparse-checkout", "set", "--no-cone", "--stdin"],
        cwd=path,
        input="\n".join(patterns) + "\n",
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        ok, out, err = run_git(["sparse-checkout", "set", "--no-cone", *patterns], path)
        if not ok:
            error = (err or out or proc.stderr or proc.stdout or "git sparse-checkout set failed").strip()

    # Reapply checkout on the current branch/HEAD; sparse-checkout will materialize only included files.
    target = branch if branch and branch != "DETACHED" else "HEAD"
    if error is None:
        ok, out, err = run_git(["checkout", "-f", target], path)
        if not ok:
            error = (err or out or "git checkout failed").strip()

    if error is None:
        safe = len(patterns)
        return True, (
            "Checked out Windows-safe paths via sparse-checkout. "
            f"Downloaded: {safe}, skipped: {skipped}, total: {total}."
        )

    extracted, extract_skipped, extract_errors = extract_windows_safe_files(path, target, patterns)
    extract_total = extracted + extract_skipped
    if extracted > 0:
        msg = (
            f"Sparse-checkout failed ({error}); extracted Windows-safe files manually. "
            f"Downloaded: {extracted}, skipped: {extract_skipped}, total: {extract_total}."
        )
        return True, msg
    detail = "; ".join(extract_errors[:3])
    if len(extract_errors) > 3:
        detail += f"; ... (+{len(extract_errors) - 3} more)"
    return False, (
        f"Sparse-checkout failed: {error}. "
        f"Manual extraction failed (downloaded: {extracted}, skipped: {extract_skipped}, total: {extract_total}). "
        f"{detail}".strip()
    )


def _is_rel_path_safe(rel: str) -> bool:
    if not rel:
        return False
    if rel.startswith(("/", "\\")):
        return False
    if "\\" in rel:
        return False
    parts = rel.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    return not _windows_path_is_invalid(rel)


def extract_windows_safe_files(path: Path, ref: str, files: List[str]) -> tuple[int, int, List[str]]:
    extracted = 0
    skipped = 0
    errors: List[str] = []
    for rel in files:
        if not _is_rel_path_safe(rel):
            skipped += 1
            continue
        target = path / rel
        try:
            proc = subprocess.run(
                ["git", "show", f"{ref}:{rel}"],
                cwd=path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if proc.returncode != 0:
                skipped += 1
                msg = (proc.stderr or proc.stdout or b"").decode(errors="replace").strip()
                errors.append(f"{rel}: {msg}" if msg else rel)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as f:
                f.write(proc.stdout or b"")
            extracted += 1
        except Exception as e:
            skipped += 1
            errors.append(f"{rel}: {e}")

    return extracted, skipped, errors


def reclone_repo(path: Path, remote_url: str, branch: str) -> tuple[bool, str]:
    if not remote_url:
        return False, "Missing remote URL (origin)."

    parent = path.parent
    base = path.name
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp = parent / f"{base}.tmp_clone_{ts}"
    bak = parent / f"{base}.bak_{ts}"

    clone_args: List[str] = ["clone", "--no-tags", "--depth", "1"]
    if branch and branch != "DETACHED":
        clone_args += ["--branch", branch]
    clone_args += [remote_url, str(tmp)]

    ok, out, err = run_git(clone_args, parent)
    if not ok:
        try:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
        return False, (err or out) or "git clone failed"

    try:
        path.rename(bak)
    except Exception as e:
        try:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
        return False, f"Failed to rename existing folder to backup: {e}"

    try:
        tmp.rename(path)
    except Exception as e:
        try:
            bak.rename(path)
        except Exception:
            pass
        try:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
        return False, f"Failed to move fresh clone into place: {e}"

    try:
        shutil.rmtree(bak, ignore_errors=True)
    except Exception:
        pass

    return True, "Repository re-cloned (local folder overwritten)."


def get_branch(path: Path) -> str:
    ok, out, _ = run_git(["rev-parse", "--abbrev-ref", "HEAD"], path)
    if ok:
        return out.strip()
    return ""


def get_head(path: Path) -> str:
    ok, out, _ = run_git(["rev-parse", "HEAD"], path)
    return out.strip() if ok else ""


def get_commit_datetime(path: Path, rev: str = "HEAD") -> Optional[datetime]:
    if not rev:
        return None
    ok, out, _ = run_git(["show", "-s", "--format=%cI", rev], path)
    if not ok:
        return None
    ts = out.strip()
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fmt_dt(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    try:
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt.strftime("%Y-%m-%d %H:%M")


def _human_delta_seconds(seconds: float) -> str:
    total = int(round(abs(seconds)))
    if total == 0:
        return "0 days"
    days = total // 86400
    if days >= 365:
        years = max(1, days // 365)
        return f"{years} year" if years == 1 else f"{years} years"
    if days >= 30:
        months = max(1, days // 30)
        return f"{months} month" if months == 1 else f"{months} months"
    if days >= 1:
        return f"{days} day" if days == 1 else f"{days} days"
    hours = total // 3600
    if hours >= 1:
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    mins = total // 60
    if mins >= 1:
        return f"{mins} min"
    return f"{total} sec"


def _human_gap(local_dt: Optional[datetime], latest_dt: Optional[datetime]) -> Optional[str]:
    if local_dt is None or latest_dt is None:
        return None
    return _human_delta_seconds((latest_dt - local_dt).total_seconds())


def collect_commits(path: Path, old: str, new: str) -> List[str]:
    if not old or not new:
        return []
    ok, out, _ = run_git(["log", "--pretty=format:%s", f"{old}..{new}"], path)
    if not ok:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def collect_numstat(path: Path, old: str, new: str) -> List[Tuple[int, int, str]]:
    if not old or not new:
        return []
    ok, out, _ = run_git(["diff", "--numstat", f"{old}..{new}"], path)
    if not ok:
        return []
    stats: List[Tuple[int, int, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, deleted, file_path = parts
        try:
            add_int = int(added) if added.isdigit() else 0
            del_int = int(deleted) if deleted.isdigit() else 0
        except ValueError:
            add_int, del_int = 0, 0
        stats.append((add_int, del_int, file_path))
    return stats


def update_repo(path: Path) -> RepoReport:
    if not path.is_dir():
        return RepoReport(path.name, path, "", "", False, [], [], [], skipped="Not a directory")

    git_dir = path / ".git"
    if not git_dir.exists():
        return RepoReport(path.name, path, "", "", False, [], [], [], skipped="No .git directory")

    before = get_head(path)
    before_dt = get_commit_datetime(path, before) if before else None
    branch = get_branch(path) or "DETACHED"
    remote_url = get_remote_url(path) or ""
    web_url = to_web_url(remote_url)
    notes: List[str] = []

    # Some repos contain filenames that Windows cannot check out/reset.
    # In this case, allow "fetch only" so we still download what we can.
    bad_paths = find_windows_incompatible_paths(path, ref="HEAD", limit=12)
    if bad_paths:
        choice = prompt_windows_incompatible_action(path, branch, bad_paths)
        if choice == "5":
            ok, info = fetch_only(path)
            if not ok:
                return RepoReport(path.name, path, web_url, branch, False, [], [], notes, error=info)

            ok2, info2 = sparse_checkout_windows_safe(path, branch)
            if ok2:
                return RepoReport(
                    path.name,
                    path,
                    web_url,
                    branch,
                    True,
                    [
                        "Fetched remote updates (no full checkout possible on Windows due to incompatible paths).",
                        info2,
                    ],
                    [],
                    notes,
                    last_update_at=_fmt_dt(get_commit_datetime(path, get_head(path))),
                    delta_from_local=_human_gap(before_dt, get_commit_datetime(path, get_head(path))),
                )
            return RepoReport(
                path.name,
                path,
                web_url,
                branch,
                True,
                [
                    "Fetched remote updates (no full checkout possible on Windows due to incompatible paths).",
                    f"Sparse-checkout failed: {info2}",
                ],
                [],
                notes,
                last_update_at=_fmt_dt(get_commit_datetime(path, get_head(path))),
                delta_from_local=_human_gap(before_dt, get_commit_datetime(path, get_head(path))),
            )
        msg = "Windows-incompatible paths in repository (cannot checkout/reset on Windows): " + "; ".join(bad_paths)
        return RepoReport(path.name, path, web_url, branch, False, [], [], notes, skipped=msg)

    dirty, status_out = repo_dirty(path)
    stashed = False
    if dirty:
        local_lines = summarize_porcelain(status_out, limit=30)
        if local_lines:
            # Include file list in the final report
            status_note = "Local changes (git status --porcelain): " + "; ".join(local_lines)
        else:
            status_note = "Local changes detected."
        action = prompt_local_changes_action(path.name, path, branch, status_out)
        if action == "3":
            return RepoReport(path.name, path, web_url, branch, False, [], [], notes, skipped=status_note)
        if action == "4":
            ok, info = reclone_repo(path, remote_url, branch)
            if not ok:
                return RepoReport(path.name, path, web_url, branch, False, [], [], notes, error=info)
            # Fresh clone is already up-to-date.
            after_head = get_head(path)
            after_dt = get_commit_datetime(path, after_head) if after_head else before_dt
            return RepoReport(
                path.name,
                path,
                web_url,
                branch,
                True,
                [info],
                [],
                notes,
                last_update_at=_fmt_dt(after_dt),
                delta_from_local=_human_gap(before_dt, after_dt),
            )
        if action == "5":
            ok, info = fetch_only(path)
            if not ok:
                return RepoReport(path.name, path, web_url, branch, False, [], [], notes, error=info)
            return RepoReport(
                path.name,
                path,
                web_url,
                branch,
                True,
                ["Fetched remote updates only (working tree left unchanged by user choice)."],
                [],
                notes,
                last_update_at=_fmt_dt(before_dt),
                delta_from_local="0 days" if before_dt else None,
            )
        if action == "6":
            moved, errors = move_local_jsons(path)
            if errors:
                return RepoReport(path.name, path, web_url, branch, False, [], [], notes, error="; ".join(errors))
            if moved:
                notes.append("Moved JSON files to github_edits:")
                notes.extend(moved)
            dirty_after, status_after = repo_dirty(path)
            if dirty_after:
                remaining = summarize_porcelain(status_after, limit=30)
                if remaining:
                    status_note = "Local changes remain after moving JSONs: " + "; ".join(remaining)
                else:
                    status_note = "Local changes remain after moving JSONs."
                return RepoReport(path.name, path, web_url, branch, False, [], [], notes, skipped=status_note)
        if action == "1":
            ok, out, err = run_git(["reset", "--hard"], path)
            if not ok:
                return RepoReport(path.name, path, web_url, branch, False, [], [], notes, error=err or out or "git reset --hard failed")
            ok, out, err = run_git(["clean", "-fd"], path)
            if not ok:
                return RepoReport(path.name, path, web_url, branch, False, [], [], notes, error=err or out or "git clean -fd failed")
        if action == "2":
            ok, out, err = run_git(["stash", "push", "-u", "-m", "workflow-updater"], path)
            if not ok:
                return RepoReport(path.name, path, web_url, branch, False, [], [], notes, error=err or out or "git stash failed")
            if "no local changes" not in (out + err).lower():
                stashed = True

    ok, _, err = run_git(["pull", "--ff-only"], path)
    if not ok:
        return RepoReport(path.name, path, web_url, branch, False, [], [], notes, error=err or "git pull failed")

    if stashed:
        ok, out, err2 = run_git(["stash", "pop"], path)
        if not ok:
            # Keep this as an error here because workflow updater output is simpler and needs attention.
            return RepoReport(path.name, path, web_url, branch, False, [], [], notes, error=(err2 or out) or "stash pop reported conflicts")

    after = get_head(path)
    after_dt = get_commit_datetime(path, after) if after else before_dt
    changed = before != after
    commits = collect_commits(path, before, after) if changed else []
    numstat = collect_numstat(path, before, after) if changed else []

    return RepoReport(
        path.name,
        path,
        web_url,
        branch,
        changed,
        commits,
        numstat,
        notes,
        last_update_at=_fmt_dt(after_dt),
        delta_from_local=_human_gap(before_dt, after_dt),
    )


def print_report(report: RepoReport) -> None:
    print(f"{C.BOLD}{C.CYAN}--- {report.name} ---{C.RESET}")
    print(f" path: {report.path}")
    if report.web_url:
        print(f" url: {C.MAGENTA}{report.web_url}{C.RESET}")
    if report.branch:
        print(f" branch: {C.YELLOW}{report.branch}{C.RESET}")
    if report.last_update_at:
        suffix = f" ({report.delta_from_local})" if report.delta_from_local else ""
        print(f" last update: {C.CYAN}{report.last_update_at}{C.RESET}{C.GRAY}{suffix}{C.RESET}")

    if report.skipped:
        for note in report.notes:
            print(f" {C.GRAY}{note}{C.RESET}")
        print(f" {C.GRAY}Skipped: {report.skipped}{C.RESET}\n")
        return
    if report.error:
        for note in report.notes:
            print(f" {C.GRAY}{note}{C.RESET}")
        print(f" {C.RED}ERROR:{C.RESET} {report.error}\n")
        return

    if report.changed:
        for note in report.notes:
            print(f" {C.GRAY}{note}{C.RESET}")
        if report.commit_messages:
            print(" Commits:")
            for msg in report.commit_messages:
                print(f"   - {msg}")
        if report.numstat:
            print(" Changed files:")
            for added, deleted, file_path in report.numstat:
                print(f"   +{added} -{deleted} {file_path}")
        print(f" {C.GREEN}Updated{C.RESET}\n")
    else:
        for note in report.notes:
            print(f" {C.GRAY}{note}{C.RESET}")
        print(f" {C.GREEN}No changes{C.RESET}\n")


def resolve_workflows_dir(
    custom_path: str | None,
    config_path: str,
    comfyui_root_arg: str | None,
    start_path: Path,
) -> Path:
    if custom_path:
        return Path(custom_path).expanduser().resolve()
    comfy_root = resolve_comfyui_root(config_path, cli_root=comfyui_root_arg, start_path=start_path)
    return default_workflows_dir(comfy_root)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update Git repos in workflows/github directory")
    parser.add_argument(
        "--root",
        dest="root",
        help="Optional path to workflows directory (defaults to ../user/default/workflows/github)",
    )
    parser.add_argument(
        "--comfyui-root",
        dest="comfyui_root",
        help="Optional path to ComfyUI root (used when --root is not provided)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    config_path = str(script_dir / "config.json")
    workflows_dir = resolve_workflows_dir(
        args.root, config_path, args.comfyui_root, script_dir
    )

    if not workflows_dir.exists():
        print(f"Workflows directory not found: {workflows_dir}")
        return 1

    print(f"Updating repositories in: {workflows_dir}\n")

    for child in sorted(workflows_dir.iterdir()):
        if not child.is_dir():
            continue
        if not (child / ".git").exists():
            continue
        report = update_repo(child)
        print_report(report)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(130)
