#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ComfyUI Repo Updater
====================

Updates the main ComfyUI repo first, then all plugins in the custom_nodes
directory (disabled folders are skipped by default).

Log format per repository:

--- <Repository Name> ---
<web URL to repository>
branch: <branch name or DETACHED>
<update log>
  - if changes: two blocks
      * Commit messages (old..new HEAD)
      * Changed files: +<added> -<deleted> <path>
  - if no changes: "No changes."

Examples:
    python update_comfy_repos.py --root "F:/ComfyUI/ComfyUI"
    python update_comfy_repos.py --root "/opt/ComfyUI/ComfyUI" --plugins-dir custom_nodes

Options and overrides:
  - POLICIES: configurable behavior for local changes and pull method.
  - REMOTE_OVERRIDES: set origin URL for repos where it’s missing.
  - BRANCH_OVERRIDES: set branch when HEAD is detached or a custom branch is needed.

Requires only Git installed in PATH.
"""

from __future__ import annotations
import argparse
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

try:
    from requirements_checker.config_manager import ConfigManager  # type: ignore
except Exception:
    ConfigManager = None  # type: ignore


# ANSI colors
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"
    GRAY = "\033[90m"


@dataclass
class UpdateResult:
    name: str
    path: str
    web_url: str
    branch: str
    changed: bool
    commit_messages: List[str]
    numstat: List[Tuple[int, int, str]]
    notes: List[str]
    error: Optional[str] = None

IGNORED_DIRS = {"__pycache__", ".idea", ".vscode", "venv", "env", ".disabled"}

# ===================== User Configuration =====================

# Default policy. Supported values:
#   on_local_changes: "stash" | "commit" | "reset" | "skip" | "abort"
#   pull_method:      "merge" | "rebase"
#   pull_from:        "origin" | "upstream"
#   set_remote_if_missing: True/False — add origin from REMOTE_OVERRIDES if missing
#   auto_stash_pop:   True/False — automatically run stash pop after a successful pull
POLICIES: Dict[str, Dict[str, object]] = {
    "default": {
        "on_local_changes": "stash",
        "pull_method": "rebase",
        "pull_from": "origin",
        "set_remote_if_missing": True,
        "auto_stash_pop": True,
    },
    # Examples of targeted overrides by folder/path name (regex):
    # r"ComfyUI$": {"pull_method": "rebase"},
    # r"MyForkedNode$": {"on_local_changes": "skip", "pull_from": "origin"},
}

# If a repository is missing origin.url, set it here.
# Key — repository folder name or absolute path; value — URL.
REMOTE_OVERRIDES: Dict[str, str] = {
    # "FooNode": "https://github.com/user/FooNode.git",
    # r".*BarNode$": "git@github.com:user/BarNode.git",
}

# Branch overrides. Useful for detached HEAD or non-standard branches.
BRANCH_OVERRIDES: Dict[str, str] = {
    # "ComfyUI": "master",
}

# If True, repositories without .git will be flagged with a helpful hint.
# Safer to keep False. If set to True, the script may try auto-initialization
# (git init + remote add), but by default this is disabled as risky.
AUTO_INIT_MISSING_GIT = False

# ========================================================================

@dataclass
class UpdateResult:
    name: str
    path: str
    web_url: str
    branch: str
    changed: bool
    commit_messages: List[str]
    numstat: List[Tuple[int, int, str]]  # (added, deleted, path)
    notes: List[str]
    error: Optional[str] = None


def load_or_init_config(path: str) -> Dict[str, object]:
    """Ensure config exists; prompt for custom_nodes_path if possible."""
    if ConfigManager is None:
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write("{}")
            print(f"Config file created at {path}. Please fill in custom_nodes_path.")
        try:
            import json
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            return {}

    cm = ConfigManager(path)
    try:
        cfg = cm.read_config()
    except Exception:
        cfg = {}
        cm.write_config(cfg)
    cm.get_value("custom_nodes_path")
    return cm.read_config()


def main() -> int:
    here = os.path.abspath(os.path.dirname(__file__))
    cfg = load_or_init_config(os.path.join(here, "config.json"))
    default_custom_nodes = cfg.get("custom_nodes_path")
    default_root = os.path.dirname(default_custom_nodes) if default_custom_nodes else None
    default_plugins_dir = os.path.basename(default_custom_nodes) if default_custom_nodes else "custom_nodes"

    parser = argparse.ArgumentParser(description="Update ComfyUI and its plugins.")
    parser.add_argument("--root", default=None, help="Path to the ComfyUI repository root")
    parser.add_argument("--plugins-dir", default=default_plugins_dir, help="Plugins directory relative to root")
    parser.add_argument("--include-disabled", action="store_true", help="Do not ignore folders marked as disabled")
    parser.add_argument("--only", nargs="*", default=None, help="Update only repositories matching these substrings/regex")
    parser.add_argument("--skip", nargs="*", default=None, help="Skip repositories matching these substrings/regex")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making git changes")
    args = parser.parse_args()

    root_arg = args.root or default_root
    if not root_arg:
        parser.error("--root is required if config.json lacks custom_nodes_path")

    root = os.path.abspath(root_arg)
    plugins_dir = os.path.join(root, args.plugins_dir)

    repos: List[str] = []

    # 1) Add the main ComfyUI repo (root)
    if os.path.isdir(os.path.join(root, ".git")):
        repos.append(root)
    else:
        print("Warning: root path is not a git repository:", root)

    # 2) Then scan plugins from plugins_dir (top-level directories only)
    if os.path.isdir(plugins_dir):
        for name in sorted(os.listdir(plugins_dir)):
            if name in IGNORED_DIRS:
                continue
            path = os.path.join(plugins_dir, name)
            if not os.path.isdir(path):
                continue
            if not args.include_disabled and (name.lower().startswith("disabled") or name.lower().endswith(".disabled")):
                continue
            repos.append(path)

    repos = apply_filters(repos, only=args.only, skip=args.skip)

    any_errors = False
    for repo_path in repos:
        res = update_repo(repo_path, dry_run=args.dry_run)
        print_report(res)
        if res.error:
            any_errors = True

    return 1 if any_errors else 0


def apply_filters(paths: List[str], only: Optional[List[str]], skip: Optional[List[str]]) -> List[str]:
    def match_any(patterns: List[str], text: str) -> bool:
        for p in patterns:
            # Support simple substrings or regex if wrapped as r/.../
            if p.startswith("r/") and p.endswith("/"):
                if re.search(p[2:-1], text):
                    return True
            elif p in text:
                return True
        return False

    out: List[str] = []
    for p in paths:
        if only and not match_any(only, p):
            continue
        if skip and match_any(skip, p):
            continue
        out.append(p)
    return out


# ------------------------- Repo update -------------------------

def update_repo(path: str, dry_run: bool = False) -> UpdateResult:
    name = os.path.basename(path.rstrip(os.sep))

    web_url = to_web_url(get_remote_url(path) or "")
    branch = get_current_branch(path) or "DETACHED"

    if not os.path.isdir(os.path.join(path, ".git")):
        notes = remedy_not_git_repo(name, path)
        return UpdateResult(name, path, web_url, branch, False, [], [], notes, error="Not a git repository")

    # Save original HEAD
    old_head = get_head_commit(path)
    notes: List[str] = []

    # Fetch
    ok, out, err = run_git(["fetch", "--all", "--tags"], cwd=path)
    if not ok:
        return UpdateResult(name, path, web_url, branch, False, [], [], ["git fetch failed"], error=(err or out).strip())

    # Pull
    pull_args = ["pull", "--rebase"]
    if dry_run:
        notes.append("[dry-run] Would run: git " + " ".join(shlex.quote(a) for a in pull_args))
        changed = False
        commit_msgs: List[str] = []
        numstat: List[Tuple[int, int, str]] = []
    else:
        ok, out, err = run_git(pull_args, cwd=path)
        if not ok:
            tips = remedy_pull_failure(out, err)
            return UpdateResult(name, path, web_url, branch, False, [], [], tips, error=f"git pull failed: {(err or out).strip()}")

        # New HEAD
        new_head = get_head_commit(path)
        changed = (new_head and new_head != old_head)
        if changed:
            commit_msgs = get_commit_messages(path, old_head, new_head)
            numstat = get_numstat(path, old_head, new_head)
        else:
            commit_msgs = []
            numstat = []

    return UpdateResult(name, path, web_url, branch or "", changed, commit_msgs, numstat, notes, error=None)


# ------------------------- Git helpers -------------------------

def run_git(args: List[str], cwd: str) -> Tuple[bool, str, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        out = proc.stdout.decode("utf-8", errors="replace")
        err = proc.stderr.decode("utf-8", errors="replace")
        return proc.returncode == 0, out, err
    except FileNotFoundError:
        return False, "", "Git not found in PATH. Install Git."


def get_remote_url(path: str, remote: str = "origin") -> str:
    ok, out, _ = run_git(["config", f"--get", f"remote.{remote}.url"], cwd=path)
    return out.strip() if ok else ""


def to_web_url(remote_url: str) -> str:
    u = remote_url.strip()
    if u.startswith("git@github.com:"):
        u = u.replace("git@github.com:", "https://github.com/")
    if u.endswith(".git"):
        u = u[:-4]
    return u


def get_current_branch(path: str) -> str:
    ok, out, _ = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    if ok:
        return out.strip()
    return ""


def get_head_commit(path: str) -> str:
    ok, out, _ = run_git(["rev-parse", "HEAD"], cwd=path)
    return out.strip() if ok else ""


def get_commit_messages(path: str, old: str, new: str) -> List[str]:
    if not old or not new:
        return []
    ok, out, _ = run_git(["log", "--pretty=format:%s", f"{old}..{new}"], cwd=path)
    if not ok:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def get_numstat(path: str, old: str, new: str) -> List[Tuple[int, int, str]]:
    if not old or not new:
        return []
    ok, out, _ = run_git(["diff", "--numstat", f"{old}..{new}"], cwd=path)
    if not ok:
        return []
    items: List[Tuple[int, int, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            a, d, p = parts
            try:
                added = int(a) if a.isdigit() else 0
                deleted = int(d) if d.isdigit() else 0
            except ValueError:
                added, deleted = 0, 0
            items.append((added, deleted, p))
    return items


# ------------------------- Remedies -------------------------

def remedy_not_git_repo(name: str, path: str) -> List[str]:
    return [
        "Fix suggestions:",
        "  • Initialize repository: git init; git remote add origin <URL>; git fetch; git checkout <branch>",
        "  • Or set REMOTE_OVERRIDES for this name/path and clone/init manually.",
        "  • If it's just an unpacked folder without git, consider deleting and reinstall via git clone.",
        'Script hint: REMOTE_OVERRIDES["%s"] = "https://github.com/user/%s.git"' % (name, name),
    ]


def remedy_pull_failure(out: str, err: str) -> List[str]:
    text = (out + "\n" + err).lower()
    tips = ["What you can try:"]
    if "would be overwritten by merge" in text or "local changes" in text:
        tips += [
            "  • You have local edits. Set on_local_changes policy: 'stash' | 'commit' | 'reset' | 'skip' | 'abort'",
        ]
    if "divergent branches" in text or "rebase" in text:
        tips += [
            "  • Branches diverged. Try pull_method='rebase' or resolve conflicts manually.",
        ]
    if "couldn't find remote ref" in text or "repository not found" in text:
        tips += [
            "  • Check branch/URL existence. Consider BRANCH_OVERRIDES or REMOTE_OVERRIDES.",
        ]
    if "permission denied" in text or "authenticat" in text:
        tips += [
            "  • Auth issues. Verify SSH keys/tokens or use https URL.",
        ]
    tips += [
        "  • If it's a fork, pull from 'upstream' to get upstream changes.",
        "  • Or skip the problematic repo and return later.",
    ]
    return tips


# ------------------------- Reporting -------------------------

def print_report(res: UpdateResult) -> None:
    title = f"{C.BOLD}{C.CYAN}{res.name}{C.RESET}"
    print(title)

    if res.web_url:
        print(f"\t→ {C.MAGENTA}{res.web_url}{C.RESET}")
    else:
        print(f"\t(local path: {res.path})")

    if res.branch:
        print(f"\t➡️  branch: {C.YELLOW}{res.branch}{C.RESET}")

    if res.error:
        print(f"\t❌ {C.RED}ERROR: {res.error}{C.RESET}")
        for n in res.notes:
            print(f"\t   {C.GRAY}{n}{C.RESET}")
        print()
        return

    if res.changed:
        if res.commit_messages:
            print(f"\t📌 Commit messages:")
            for msg in res.commit_messages:
                print(f"\t   - {msg}")
        if res.numstat:
            print(f"\n\t⚠️  Changed files:")
            for added, deleted, path in res.numstat:
                print(f"\t   +{added} -{deleted}  {path}")
        print(f"\t✅ Updated")
    else:
        print(f"\t✅ No changes")

    for n in res.notes:
        print(f"\t{C.GRAY}{n}{C.RESET}")

    print()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(130)
