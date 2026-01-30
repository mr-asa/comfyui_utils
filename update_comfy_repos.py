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
  - REMOTE_OVERRIDES: set origin URL for repos where it's missing.
  - BRANCH_OVERRIDES: set branch when HEAD is detached or a custom branch is needed.

Requires only Git installed in PATH.
"""

from __future__ import annotations
import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from requirements_checker.config_manager import ConfigManager  # type: ignore
except Exception:
    ConfigManager = None  # type: ignore

from comfyui_root import default_custom_nodes, resolve_comfyui_root


def _configure_console_encoding() -> None:
    # Avoid UnicodeEncodeError on Windows consoles with non-UTF encodings (e.g. cp1251).
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


_configure_console_encoding()


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

_PRINTED_HEADERS: set[str] = set()


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
    skipped: Optional[str] = None
    error: Optional[str] = None

IGNORED_DIRS = {"__pycache__", ".idea", ".vscode", "venv", "env", ".disabled"}

# ===================== User Configuration =====================

# Default policy. Supported values:
#   on_local_changes: "stash" | "commit" | "reset" | "skip" | "abort"
#   pull_method:      "merge" | "rebase"
#   pull_from:        "origin" | "upstream"
#   set_remote_if_missing: True/False -- add origin from REMOTE_OVERRIDES if missing
#   auto_stash_pop:   True/False -- automatically run stash pop after a successful pull
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

# Local-change ignore patterns. Matching paths are auto-discarded before pull.
# Use regex patterns that match paths shown by `git status --porcelain`.
LOCAL_CHANGES_IGNORE_PATTERNS: List[str] = [
    r"^__pycache__/.*\.pyc$",
    r"\.pyc$",
]

# Local-change auto-merge patterns. If ALL local changes in a repo match these patterns,
# the script will auto-select "merge with local changes" (stash + pull + pop).
# Loaded from update_comfy_repos_config.json.
LOCAL_CHANGES_AUTO_MERGE: Dict[str, List[str]] = {}
UPDATE_CONFIG_PATH = ""
UPDATE_CONFIG: Dict[str, object] = {}

# If a repository is missing origin.url, set it here.
# Key -- repository folder name or absolute path; value -- URL.
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

def _norm_real_path(path: str) -> str:
    try:
        return os.path.normcase(os.path.normpath(os.path.realpath(path)))
    except Exception:
        return os.path.normcase(os.path.normpath(path))


def _dedupe_dirs(paths: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for p in paths:
        if not p:
            continue
        norm = os.path.normcase(os.path.normpath(p))
        real = _norm_real_path(p)
        if norm in seen or real in seen:
            continue
        if not os.path.isdir(p):
            continue
        out.append(os.path.normpath(p))
        seen.add(norm)
        seen.add(real)
    return out


def _load_plugins_dirs(cfg: Dict[str, object], comfy_root: Path, override_dir: Optional[str]) -> List[str]:
    if override_dir:
        return _dedupe_dirs([os.path.join(str(comfy_root), override_dir)])

    paths: List[str] = []
    raw_list = cfg.get("custom_nodes_paths")
    if isinstance(raw_list, list):
        for p in raw_list:
            if isinstance(p, str) and p.strip():
                paths.append(p.strip())
    raw_single = cfg.get("custom_nodes_path")
    if isinstance(raw_single, str) and raw_single.strip():
        paths.append(raw_single.strip())
    repo_path = cfg.get("custom_nodes_repo_path")
    if isinstance(repo_path, str) and repo_path.strip():
        paths.append(repo_path.strip())
    if not paths:
        paths.append(str(default_custom_nodes(comfy_root)))
    return _dedupe_dirs(paths)


def load_or_init_config(path: str) -> Dict[str, object]:
    """Ensure config exists and return current settings."""
    if ConfigManager is None:
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write("{}")
            print(f"Config file created at {path}. Please fill in custom_nodes_path.")
        try:
            import json
            return json.load(open(path, encoding="utf-8-sig"))
        except Exception:
            return {}

    cm = ConfigManager(path)
    try:
        cfg = cm.read_config()
    except Exception:
        cfg = {}
        cm.write_config(cfg)
    return cfg


def load_update_config(path: str) -> Dict[str, object]:
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write('{\n  "local_changes_auto_merge": {}\n}\n')
        except Exception:
            return {}
    try:
        import json
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def apply_update_config(cfg: Dict[str, object]) -> None:
    global LOCAL_CHANGES_AUTO_MERGE
    raw = cfg.get("local_changes_auto_merge")
    merged: Dict[str, List[str]] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if not isinstance(k, str) or not k.strip():
                continue
            if isinstance(v, list):
                patterns = [p for p in v if isinstance(p, str) and p.strip()]
                if patterns:
                    merged[k] = patterns
    LOCAL_CHANGES_AUTO_MERGE = merged
    _compile_auto_merge_patterns()


def write_update_config(path: str, cfg: Dict[str, object]) -> None:
    try:
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)
    except Exception:
        pass


def main() -> int:
    here = os.path.abspath(os.path.dirname(__file__))
    config_path = os.path.join(here, "config.json")
    update_config_path = os.path.join(here, "update_comfy_repos_config.json")
    cfg = load_or_init_config(config_path)
    update_cfg = load_update_config(update_config_path)
    apply_update_config(update_cfg)
    global UPDATE_CONFIG_PATH, UPDATE_CONFIG
    UPDATE_CONFIG_PATH = update_config_path
    UPDATE_CONFIG = update_cfg
    parser = argparse.ArgumentParser(description="Update ComfyUI and its plugins.")
    parser.add_argument("--root", default=None, help="Path to the ComfyUI repository root")
    parser.add_argument("--plugins-dir", default=None, help="Plugins directory relative to root")
    parser.add_argument("--include-disabled", action="store_true", help="Do not ignore folders marked as disabled")
    parser.add_argument("--only", nargs="*", default=None, help="Update only repositories matching these substrings/regex")
    parser.add_argument("--skip", nargs="*", default=None, help="Skip repositories matching these substrings/regex")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making git changes")
    args = parser.parse_args()

    comfy_root = resolve_comfyui_root(config_path, cli_root=args.root, start_path=Path(here))
    plugins_dirs = _load_plugins_dirs(cfg, comfy_root, args.plugins_dir)

    repos: List[str] = []

    # 1) Add the main ComfyUI repo (root)
    if os.path.isdir(os.path.join(str(comfy_root), ".git")):
        repos.append(str(comfy_root))
    else:
        print("Warning: root path is not a git repository:", comfy_root)

    # 2) Then scan plugins from plugins_dir (top-level directories only)
    seen_repos: set[str] = set()
    for plugins_dir in plugins_dirs:
        if not os.path.isdir(plugins_dir):
            continue
        for name in sorted(os.listdir(plugins_dir)):
            if name in IGNORED_DIRS:
                continue
            path = os.path.join(plugins_dir, name)
            if not os.path.isdir(path):
                continue
            if not args.include_disabled and (name.lower().startswith("disabled") or name.lower().endswith(".disabled")):
                continue
            real = _norm_real_path(path)
            if real in seen_repos:
                continue
            seen_repos.add(real)
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

    # Local changes: ask what to do before pulling (so we don't spam an error log)
    dirty, status_out, ignored_paths = repo_dirty(path)
    stashed = False
    if dirty and not dry_run:
        if name not in _PRINTED_HEADERS:
            print_repo_header(name, path, web_url, branch)
        local_lines = summarize_porcelain(status_out, limit=30)
        if local_lines:
            notes.append("Local changes (git status --porcelain):")
            notes.extend(["  " + ln for ln in local_lines])
        if _should_auto_merge(path, name, status_out):
            action = "2"
            notes.append("Auto-merge local changes (matched configured patterns).")
        else:
            action = prompt_local_changes_action(name, path, web_url, branch, status_out)
        if action == "3":
            return UpdateResult(
                name,
                path,
                web_url,
                branch,
                False,
                [],
                [],
                notes + ["Skipped due to local changes (user choice)."],
                skipped="Local changes present",
                error=None,
            )
        if action == "4":
            remote_url = get_remote_url(path) or ""
            ok, info = reclone_repo(path, remote_url, branch)
            if not ok:
                return UpdateResult(
                    name,
                    path,
                    web_url,
                    branch,
                    False,
                    [],
                    [],
                    notes + ["Re-clone failed."],
                    error=info,
                )

            # After re-clone, compute changes vs old_head when possible
            new_head = get_head_commit(path)
            changed = (new_head and new_head != old_head)
            commit_msgs = get_commit_messages(path, old_head, new_head) if changed else []
            numstat = get_numstat(path, old_head, new_head) if changed else []
            notes.append(info)
            return UpdateResult(name, path, web_url, branch, changed, commit_msgs, numstat, notes, error=None)
        if action == "1":
            ok, out, err = run_git(["reset", "--hard"], cwd=path)
            if not ok:
                return UpdateResult(
                    name, path, web_url, branch, False, [], [], notes, error=f"git reset --hard failed: {(err or out).strip()}"
                )
            ok, out, err = run_git(["clean", "-fd"], cwd=path)
            if not ok:
                return UpdateResult(
                    name, path, web_url, branch, False, [], [], notes, error=f"git clean -fd failed: {(err or out).strip()}"
                )
            notes.append("Local changes discarded (reset --hard + clean -fd).")
        if action == "2":
            stash_msg = f"comfyui-updater {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ok, out, err = run_git(["stash", "push", "-u", "-m", stash_msg], cwd=path)
            if not ok:
                return UpdateResult(
                    name, path, web_url, branch, False, [], [], notes, error=f"git stash failed: {(err or out).strip()}"
                )
            if "no local changes" in (out + err).lower():
                notes.append("No stash created (no local changes to save).")
            else:
                stashed = True
                notes.append(f"Stashed local changes: {stash_msg}")
    elif ignored_paths and not dry_run:
        ok, err_msg = clean_ignored_changes(path, ignored_paths)
        if ok:
            notes.append("Ignored local changes discarded (cache files).")
        elif err_msg:
            notes.append("Failed to discard ignored local changes: " + err_msg)

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
            combined = (out or "") + "\n" + (err or "")
            conflict_paths = extract_conflict_paths(combined)
            if conflict_paths:
                tips = tips + ["Conflicting paths:"] + [f"  {p}" for p in conflict_paths]
            return UpdateResult(
                name,
                path,
                web_url,
                branch,
                False,
                [],
                [],
                tips,
                error=f"git pull failed: {(err or out).strip()}",
            )

        if stashed:
            ok, out, err = run_git(["stash", "pop"], cwd=path)
            if not ok:
                notes.append("Stash pop reported conflicts; resolve manually (stash likely kept).")
                details = (err or out).strip()
                if details:
                    notes.append("stash pop output: " + details)
                conflict_paths = extract_conflict_paths((out or "") + "\n" + (err or ""))
                if conflict_paths:
                    notes.append("stash pop conflicting paths:")
                    notes.extend(["  " + p for p in conflict_paths])
            else:
                notes.append("Restored local changes (stash pop).")

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


def repo_dirty(path: str) -> Tuple[bool, str, List[str]]:
    # Ignore untracked files in the "local changes" prompt to keep logs short.
    # Conflicting/untracked paths that block a pull are still captured from git pull output.
    ok, out, err = run_git(["status", "--porcelain", "--untracked-files=no"], cwd=path)
    if not ok:
        return False, (err or out).strip(), []
    kept_lines, _ignored_lines, ignored_paths = filter_porcelain(out)
    return bool(kept_lines), "\n".join(kept_lines).strip(), ignored_paths


def summarize_porcelain(status_out: str, limit: int = 30) -> List[str]:
    lines = [ln.rstrip() for ln in status_out.splitlines() if ln.strip()]
    if not lines:
        return []
    if len(lines) <= limit:
        return lines
    return lines[:limit] + [f"... (+{len(lines) - limit} more)"]


_IGNORED_STATUS_REGEX = [re.compile(p) for p in LOCAL_CHANGES_IGNORE_PATTERNS]
_AUTO_MERGE_REPOS: List[Tuple[re.Pattern, List[re.Pattern]]] = []


def _extract_porcelain_path(line: str) -> str:
    if len(line) < 3:
        return ""
    # Porcelain format: XY<space>path (status is 2 chars). Some outputs may omit leading space.
    if len(line) >= 4 and line[2] == " ":
        raw = line[3:].strip()
    else:
        parts = line.strip().split(maxsplit=1)
        if len(parts) < 2:
            return ""
        raw = parts[1].strip()
    if " -> " in raw:
        raw = raw.split(" -> ", 1)[-1].strip()
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        raw = raw[1:-1]
    return raw


def _is_ignored_status_path(path: str) -> bool:
    if not path:
        return False
    norm = path.replace("\\", "/")
    return any(rx.search(norm) for rx in _IGNORED_STATUS_REGEX)


def _compile_auto_merge_patterns() -> None:
    global _AUTO_MERGE_REPOS
    compiled: List[Tuple[re.Pattern, List[re.Pattern]]] = []
    for k, v in LOCAL_CHANGES_AUTO_MERGE.items():
        try:
            repo_rx = re.compile(k)
        except re.error:
            continue
        path_rxs: List[re.Pattern] = []
        for p in v:
            try:
                path_rxs.append(re.compile(p))
            except re.error:
                continue
        if path_rxs:
            compiled.append((repo_rx, path_rxs))
    _AUTO_MERGE_REPOS = compiled


def _get_auto_merge_patterns(repo_path: str, repo_name: str) -> List[re.Pattern]:
    patterns: List[re.Pattern] = []
    for rx, pats in _AUTO_MERGE_REPOS:
        if rx.search(repo_name) or rx.search(repo_path):
            patterns.extend(pats)
    return patterns


def _should_auto_merge(repo_path: str, repo_name: str, status_out: str) -> bool:
    patterns = _get_auto_merge_patterns(repo_path, repo_name)
    if not patterns:
        return False
    paths: List[str] = []
    for line in status_out.splitlines():
        if not line.strip():
            continue
        p = _extract_porcelain_path(line)
        if p:
            paths.append(p.replace("\\", "/"))
    if not paths:
        return False
    for p in paths:
        if not any(rx.search(p) for rx in patterns):
            return False
    return True


def _extract_status_paths(status_out: str) -> List[str]:
    paths: List[str] = []
    for line in status_out.splitlines():
        if not line.strip():
            continue
        p = _extract_porcelain_path(line)
        if p:
            paths.append(p.replace("\\", "/"))
    seen: set[str] = set()
    uniq: List[str] = []
    for p in paths:
        if p not in seen:
            uniq.append(p)
            seen.add(p)
    return uniq


def _maybe_prompt_add_auto_merge(repo_name: str, repo_path: str, status_out: str) -> None:
    if not sys.stdin.isatty():
        return
    if not UPDATE_CONFIG_PATH:
        return
    paths = _extract_status_paths(status_out)
    if not paths:
        return
    repo_rx = re.escape(repo_name)

    path_patterns: List[str] = []
    for p in paths:
        default_path = p
        print(f"\tPath: {p}")
        try:
            prx = input("\tPath regex (Enter=use exact, '-'=skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if prx == "-":
            continue
        if prx == "":
            prx = default_path
        if prx.strip() == "":
            continue
        path_patterns.append(prx)

    if not path_patterns:
        return

    cfg = UPDATE_CONFIG if isinstance(UPDATE_CONFIG, dict) else {}
    raw = cfg.get("local_changes_auto_merge")
    if not isinstance(raw, dict):
        raw = {}
    existing = raw.get(repo_rx)
    if isinstance(existing, list):
        merged = [p for p in existing if isinstance(p, str)]
    else:
        merged = []
    for p in path_patterns:
        if p not in merged:
            merged.append(p)
    raw[repo_rx] = merged
    cfg["local_changes_auto_merge"] = raw
    UPDATE_CONFIG.update(cfg)
    write_update_config(UPDATE_CONFIG_PATH, UPDATE_CONFIG)
    apply_update_config(UPDATE_CONFIG)
    print("\tSaved auto-merge rule.")


def filter_porcelain(status_out: str) -> Tuple[List[str], List[str], List[str]]:
    kept: List[str] = []
    ignored: List[str] = []
    ignored_paths: List[str] = []
    for line in status_out.splitlines():
        if not line.strip():
            continue
        p = _extract_porcelain_path(line)
        if _is_ignored_status_path(p):
            ignored.append(line.rstrip())
            ignored_paths.append(p)
        else:
            kept.append(line.rstrip())
    return kept, ignored, ignored_paths


def clean_ignored_changes(path: str, ignored_paths: List[str]) -> Tuple[bool, str]:
    if not ignored_paths:
        return True, ""
    ok, out, err = run_git(["checkout", "--", *ignored_paths], cwd=path)
    return ok, (err or out).strip()


def extract_conflict_paths(git_output: str, limit: int = 40) -> List[str]:
    """
    Best-effort extraction of file paths from common Git conflict messages.
    Useful to show which files are blocking the update.
    """
    out = git_output or ""
    paths: List[str] = []

    if "would be overwritten by merge" in out.lower():
        in_list = False
        for line in out.splitlines():
            l = line.rstrip("\r\n")
            if "would be overwritten by merge" in l.lower():
                in_list = True
                continue
            if in_list:
                if not l.strip():
                    break
                if l.lstrip().lower().startswith(("please", "aborting", "hint:")):
                    break
                candidate = l.strip()
                if candidate:
                    paths.append(candidate)
                    if len(paths) >= limit:
                        break

    if len(paths) < limit:
        m = re.compile(r"^CONFLICT .* in (.+)$")
        for line in out.splitlines():
            mm = m.match(line.strip())
            if mm:
                paths.append(mm.group(1).strip())
                if len(paths) >= limit:
                    break

    if len(paths) < limit and "untracked working tree files would be overwritten" in out.lower():
        in_list = False
        for line in out.splitlines():
            l = line.rstrip("\r\n")
            if "untracked working tree files would be overwritten" in l.lower():
                in_list = True
                continue
            if in_list:
                if not l.strip():
                    break
                candidate = l.strip()
                if candidate and not candidate.lower().startswith(("please", "aborting", "hint:")):
                    paths.append(candidate)
                    if len(paths) >= limit:
                        break

    seen = set()
    uniq: List[str] = []
    for p in paths:
        if p not in seen:
            uniq.append(p)
            seen.add(p)
    return uniq


def print_repo_header(name: str, path: str, web_url: str, branch: str) -> None:
    title = f"{C.BOLD}{C.CYAN}{name}{C.RESET}"
    print(title)

    if web_url:
        print(f"\t-> {C.MAGENTA}{web_url}{C.RESET}")
    else:
        print(f"\t(local path: {path})")

    if branch:
        print(f"\t-> branch: {C.YELLOW}{branch}{C.RESET}")
    _PRINTED_HEADERS.add(name)


def prompt_local_changes_action(name: str, path: str, web_url: str, branch: str, status_out: str) -> str:
    if name not in _PRINTED_HEADERS:
        print_repo_header(name, path, web_url, branch)
    print(f"\t{C.YELLOW}Local changes detected.{C.RESET}")
    print(f"\t   Path: {path}")
    preview = summarize_porcelain(status_out, limit=12)
    if preview:
        print(f"\t   {C.GRAY}git status --porcelain:{C.RESET}")
        for ln in preview:
            print(f"\t   {C.GRAY}{ln}{C.RESET}")

    print("\tChoose an action:")
    print("\t  1 - replace from remote (discard local changes)")
    print("\t  2 - merge with local changes (update, then re-apply my changes)")
    print("\t  3 - skip this repository and continue")
    print("\t  4 - re-clone repository (overwrite folder completely)")
    print("\t  7 - add auto-merge rules for these files")

    if not sys.stdin.isatty():
        print(f"\t{C.GRAY}stdin is not interactive; defaulting to option 3 (skip).{C.RESET}")
        return "3"

    while True:
        try:
            choice = input("\tYour choice (default 3): ").strip() or "3"
        except (EOFError, KeyboardInterrupt):
            return "3"
        if choice == "7":
            _maybe_prompt_add_auto_merge(name, path, status_out)
            if _should_auto_merge(path, name, status_out):
                return "2"
            continue
        if choice in {"1", "2", "3", "4"}:
            return choice
        print("\tPlease enter 1, 2, 3, 4, or 7.")

def reclone_repo(path: str, remote_url: str, branch: str) -> Tuple[bool, str]:
    """
    Re-clone the repository into the same folder name:
      1) clone into a temp folder in parent dir
      2) rename current folder to .bak_<timestamp>
      3) rename temp -> original
      4) delete backup
    """
    if not remote_url:
        return False, "Missing remote URL (origin)."

    parent = os.path.dirname(path.rstrip(os.sep))
    base = os.path.basename(path.rstrip(os.sep))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp = os.path.join(parent, f"{base}.tmp_clone_{ts}")
    bak = os.path.join(parent, f"{base}.bak_{ts}")

    clone_args = ["clone", "--no-tags", "--depth", "1"]
    if branch and branch != "DETACHED":
        clone_args += ["--branch", branch]
    clone_args += [remote_url, tmp]

    ok, out, err = run_git(clone_args, cwd=parent)
    if not ok:
        try:
            if os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
        return False, (err or out).strip() or "git clone failed"

    try:
        os.replace(path, bak)
    except Exception as e:
        try:
            if os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
        return False, f"Failed to rename existing folder to backup: {e}"

    try:
        os.replace(tmp, path)
    except Exception as e:
        # Attempt rollback
        try:
            os.replace(bak, path)
        except Exception:
            pass
        try:
            if os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
        return False, f"Failed to move fresh clone into place: {e}"

    try:
        shutil.rmtree(bak, ignore_errors=True)
    except Exception:
        pass

    return True, "Repository re-cloned (local folder overwritten)."


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
        "  * Initialize repository: git init; git remote add origin <URL>; git fetch; git checkout <branch>",
        "  * Or set REMOTE_OVERRIDES for this name/path and clone/init manually.",
        "  * If it's just an unpacked folder without git, consider deleting and reinstall via git clone.",
        'Script hint: REMOTE_OVERRIDES["%s"] = "https://github.com/user/%s.git"' % (name, name),
    ]


def remedy_pull_failure(out: str, err: str) -> List[str]:
    text = (out + "\n" + err).lower()
    tips = ["What you can try:"]
    if "would be overwritten by merge" in text or "local changes" in text:
        tips += [
            "  * You have local edits. Set on_local_changes policy: 'stash' | 'commit' | 'reset' | 'skip' | 'abort'",
        ]
    if "divergent branches" in text or "rebase" in text:
        tips += [
            "  * Branches diverged. Try pull_method='rebase' or resolve conflicts manually.",
        ]
    if "couldn't find remote ref" in text or "repository not found" in text:
        tips += [
            "  * Check branch/URL existence. Consider BRANCH_OVERRIDES or REMOTE_OVERRIDES.",
        ]
    if "permission denied" in text or "authenticat" in text:
        tips += [
            "  * Auth issues. Verify SSH keys/tokens or use https URL.",
        ]
    tips += [
        "  * If it's a fork, pull from 'upstream' to get upstream changes.",
        "  * Or skip the problematic repo and return later.",
    ]
    return tips


# ------------------------- Reporting -------------------------

def print_report(res: UpdateResult) -> None:
    header_printed = res.name in _PRINTED_HEADERS
    if header_printed:
        _PRINTED_HEADERS.discard(res.name)

    if not header_printed:
        title = f"{C.BOLD}{C.CYAN}{res.name}{C.RESET}"
        print(title)

        if res.web_url:
            print(f"\t-> {C.MAGENTA}{res.web_url}{C.RESET}")
        else:
            print(f"\t(local path: {res.path})")

        if res.branch:
            print(f"\t-> branch: {C.YELLOW}{res.branch}{C.RESET}")

    if res.skipped:
        print(f"\t{C.GRAY}SKIP: {res.skipped}{C.RESET}")
        for n in res.notes:
            print(f"\t{C.GRAY}{n}{C.RESET}")
        print()
        return

    if res.error:
        print(f"\t{C.RED}ERROR: {res.error}{C.RESET}")
        for n in res.notes:
            print(f"\t   {C.GRAY}{n}{C.RESET}")
        print()
        return

    if res.changed:
        if res.commit_messages:
            print(f"\tCommits:")
            for msg in res.commit_messages:
                print(f"\t   - {msg}")
        if res.numstat:
            print(f"\n\tChanged files:")
            for added, deleted, path in res.numstat:
                print(f"\t   +{added} -{deleted}  {path}")
        print(f"\t{C.GREEN}OK{C.RESET} Updated")
    else:
        print(f"\t{C.GREEN}OK{C.RESET} No changes")

    for n in res.notes:
        print(f"\t{C.GRAY}{n}{C.RESET}")

    print()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(130)
