#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Workflow Repo Updater
=====================

Updates every Git repository located in the relative
``../user/default/workflows`` directory (relative to this script).
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
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple


@dataclass
class RepoReport:
    name: str
    path: Path
    branch: str
    changed: bool
    commit_messages: List[str]
    numstat: List[Tuple[int, int, str]]
    skipped: str | None = None
    error: str | None = None


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"


def run_git(args: List[str], cwd: Path) -> tuple[bool, str, str]:
    result = subprocess.run(
        ["git", *args], cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    return result.returncode == 0, result.stdout.strip(), result.stderr.strip()


def get_branch(path: Path) -> str:
    ok, out, _ = run_git(["rev-parse", "--abbrev-ref", "HEAD"], path)
    if ok:
        return out.strip()
    return ""


def get_head(path: Path) -> str:
    ok, out, _ = run_git(["rev-parse", "HEAD"], path)
    return out.strip() if ok else ""


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
        return RepoReport(path.name, path, "", False, [], [], skipped="Not a directory")

    git_dir = path / ".git"
    if not git_dir.exists():
        return RepoReport(path.name, path, "", False, [], [], skipped="No .git directory")

    before = get_head(path)
    branch = get_branch(path) or "DETACHED"

    ok, _, err = run_git(["pull", "--ff-only"], path)
    if not ok:
        return RepoReport(path.name, path, branch, False, [], [], error=err or "git pull failed")

    after = get_head(path)
    changed = before != after
    commits = collect_commits(path, before, after) if changed else []
    numstat = collect_numstat(path, before, after) if changed else []

    return RepoReport(path.name, path, branch, changed, commits, numstat)


def print_report(report: RepoReport) -> None:
    print(f"{C.BOLD}{C.CYAN}--- {report.name} ---{C.RESET}")
    print(f" path: {report.path}")
    if report.branch:
        print(f" branch: {C.YELLOW}{report.branch}{C.RESET}")

    if report.skipped:
        print(f" {C.GRAY}Skipped: {report.skipped}{C.RESET}\n")
        return
    if report.error:
        print(f" {C.RED}ERROR:{C.RESET} {report.error}\n")
        return

    if report.changed:
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
        print(f" {C.GREEN}No changes{C.RESET}\n")


def resolve_workflows_dir(custom_path: str | None) -> Path:
    if custom_path:
        return Path(custom_path).expanduser().resolve()
    base_dir = Path(__file__).resolve().parents[2]
    return base_dir / "user" / "default" / "workflows"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update Git repos in workflows directory")
    parser.add_argument(
        "--root",
        dest="root",
        help="Optional path to workflows directory (defaults to ../user/default/workflows)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workflows_dir = resolve_workflows_dir(args.root)

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
