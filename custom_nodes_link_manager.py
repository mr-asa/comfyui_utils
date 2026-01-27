#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Custom nodes link manager (Windows).

Shows repo nodes vs junctions in custom_nodes and lets you add/remove links.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


REPARSE_POINT = 0x0400


@dataclass
class LinkedNode:
    name: str
    path: str
    target: str
    target_exists: bool
    target_in_repo: bool


def _load_json(path: str) -> Dict[str, object]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    except Exception:
        return {}


def _save_json(path: str, data: Dict[str, object]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def _is_reparse_point(path: str) -> bool:
    try:
        st = os.stat(path, follow_symlinks=False)
        return bool(getattr(st, "st_file_attributes", 0) & REPARSE_POINT)
    except Exception:
        return False


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _real(path: str) -> str:
    try:
        return os.path.normcase(os.path.normpath(os.path.realpath(path)))
    except Exception:
        return _norm(path)


def _prompt_path(label: str) -> str:
    while True:
        raw = input(f"{label} (or 'NO' to exit): ").strip()
        if not raw:
            continue
        if raw.upper() == "NO":
            raise SystemExit("Cancelled by user.")
        if os.path.isdir(raw):
            return raw
        print("Path not found, try again.")


def _choose_custom_nodes_path(cfg: Dict[str, object]) -> str:
    paths: List[str] = []
    raw_list = cfg.get("custom_nodes_paths")
    if isinstance(raw_list, list):
        for p in raw_list:
            if isinstance(p, str) and p.strip():
                paths.append(p.strip())
    raw_one = cfg.get("custom_nodes_path")
    if isinstance(raw_one, str) and raw_one.strip():
        paths.append(raw_one.strip())

    paths = [p for p in paths if os.path.isdir(p)]
    if not paths:
        return _prompt_path("Enter custom_nodes path")
    if len(paths) == 1:
        return paths[0]

    print("Select custom_nodes path:")
    for i, p in enumerate(paths, 1):
        print(f"  [{i}] {p}")
    while True:
        choice = input("Choice [1]: ").strip()
        if not choice:
            return paths[0]
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(paths):
                return paths[idx - 1]
        print("Invalid choice.")


def _resolve_paths(cfg_path: str, args: argparse.Namespace) -> Tuple[str, str, Dict[str, object]]:
    cfg = _load_json(cfg_path)
    if not cfg:
        print(f"Config not loaded or empty: {cfg_path}")
    repo_raw = args.repo or (cfg.get("custom_nodes_repo_path") if isinstance(cfg.get("custom_nodes_repo_path"), str) else "")
    repo = repo_raw.strip() if isinstance(repo_raw, str) else ""
    custom_raw = args.custom or ""
    custom_nodes = custom_raw.strip() if isinstance(custom_raw, str) else ""

    if repo and not os.path.isdir(repo):
        print(f"custom_nodes_repo_path not found: {repo}")
        print(f"Config file: {cfg_path}")
    if not repo or not os.path.isdir(repo):
        if not repo:
            print("custom_nodes_repo_path is missing in config.")
            print(f"Config file: {cfg_path}")
        repo = _prompt_path("Enter custom_nodes_repo path")
        cfg["custom_nodes_repo_path"] = repo
        _save_json(cfg_path, cfg)

    if custom_nodes and not os.path.isdir(custom_nodes):
        print(f"custom_nodes_path not found: {custom_nodes}")
        print(f"Config file: {cfg_path}")
    if not custom_nodes or not os.path.isdir(custom_nodes):
        if not custom_nodes:
            print("custom_nodes_path is missing in config.")
            print(f"Config file: {cfg_path}")
        custom_nodes = _choose_custom_nodes_path(cfg)

    return repo, custom_nodes, cfg


def _scan_repo(repo_dir: str) -> List[str]:
    out: List[str] = []
    for name in sorted(os.listdir(repo_dir)):
        if name in (".disabled",) or name.endswith(".disable") or name.endswith(".disabled"):
            continue
        path = os.path.join(repo_dir, name)
        if os.path.isdir(path):
            out.append(name)
    return out


def _scan_links(custom_nodes_dir: str, repo_dir: str) -> List[LinkedNode]:
    repo_norm = _real(repo_dir)
    out: List[LinkedNode] = []
    for name in sorted(os.listdir(custom_nodes_dir)):
        path = os.path.join(custom_nodes_dir, name)
        if not os.path.isdir(path):
            continue
        if not _is_reparse_point(path):
            continue
        target = _real(path)
        target_exists = os.path.exists(target)
        target_in_repo = target == repo_norm or target.startswith(repo_norm + os.sep)
        out.append(LinkedNode(name, path, target, target_exists, target_in_repo))
    return out


def _print_panels(repo_nodes: List[str], links: List[LinkedNode]) -> None:
    link_set = {ln.name for ln in links}
    entries = [
        f"{'=> ' if name in link_set else '   '}[{i}] {name}"
        for i, name in enumerate(repo_nodes, 1)
    ]
    rows = (len(entries) + 1) // 2
    left_entries = entries[:rows]
    right_entries = entries[rows:]
    left_w = max([len(s) for s in left_entries], default=0)

    print()
    print("Nodes ('=>' is linked)")
    for i in range(rows):
        left = left_entries[i]
        right = right_entries[i] if i < len(right_entries) else ""
        if right:
            print(left.ljust(left_w + 4) + right)
        else:
            print(left)
    print()
    print("Commands: a [n|n-m|n,n]=add, r [n|n-m|n,n]=remove, i [n|n-m|n,n]=invert, s=sync, p=presets, w=save preset, q=quit, ?=help, enter=refresh")


def _ensure_presets_config(path: str) -> None:
    if os.path.isfile(path):
        return
    default_cfg = {
        "current": {},
        "all": {"mode": "blacklist", "nodes": []},
        "minimal": {"mode": "whitelist", "nodes": ["ComfyUI-Manager"]},
    }
    _save_json(path, default_cfg)


def _load_presets(path: str) -> Dict[str, object]:
    data = _load_json(path)
    if not data:
        print(f"Presets file not loaded or invalid JSON: {path}")
    return data if isinstance(data, dict) else {}


def _list_preset_names(presets: Dict[str, object]) -> List[str]:
    return [k for k in presets.keys() if isinstance(k, str) and k.strip()]


def _prompt_preset_choice(preset_names: List[str]) -> Optional[str]:
    if not preset_names:
        print("No presets available.")
        return None
    print("Presets:")
    for i, name in enumerate(preset_names, 1):
        print(f"  [{i}] {name}")
    while True:
        raw = input("Select preset number (Enter to cancel): ").strip()
        if not raw:
            return None
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(preset_names):
                return preset_names[idx - 1]
        print("Invalid choice.")


def _resolve_preset_nodes(preset: Dict[str, object], repo_nodes: List[str]) -> Tuple[str, List[str]]:
    mode = "whitelist"
    if isinstance(preset.get("mode"), str) and preset.get("mode").strip():
        mode = preset.get("mode").strip().lower()
    raw_nodes = preset.get("nodes")
    nodes = [n for n in raw_nodes if isinstance(n, str) and n.strip()] if isinstance(raw_nodes, list) else []
    if mode == "blacklist":
        selected = [n for n in repo_nodes if n not in nodes]
    else:
        selected = [n for n in repo_nodes if n in nodes]
        mode = "whitelist"
    return mode, selected


def _apply_preset(
    repo_dir: str,
    custom_nodes_dir: str,
    repo_nodes: List[str],
    links: List[LinkedNode],
    preset_name: str,
    preset: Dict[str, object],
) -> List[str]:
    if preset_name.lower() == "current":
        return ["Preset 'current' is a no-op."]
    mode, selected = _resolve_preset_nodes(preset, repo_nodes)
    selected_set = set(selected)
    link_map = {ln.name: ln for ln in links if ln.target_in_repo}
    to_add = [n for n in selected if n not in link_map]
    to_remove = [ln for ln in link_map.values() if ln.name not in selected_set]

    print(f"Preset '{preset_name}' ({mode}): adding {len(to_add)}, removing {len(to_remove)}")

    msgs: List[str] = []
    for ln in to_remove:
        msgs.append(_remove_link(custom_nodes_dir, ln))
    for name in to_add:
        msgs.append(_add_link(repo_dir, custom_nodes_dir, name))
    return msgs


def _save_preset_from_links(
    presets_path: str,
    presets: Dict[str, object],
    repo_nodes: List[str],
    links: List[LinkedNode],
) -> Optional[str]:
    name = input("Preset name to save (Enter to cancel, 0=list presets): ").strip()
    if not name:
        return None
    if name == "0":
        preset_names = [n for n in _list_preset_names(presets) if n.lower() != "current"]
        if not preset_names:
            print("No presets available to overwrite.")
            return None
        print("Presets:")
        for i, pname in enumerate(preset_names, 1):
            print(f"  [{i}] {pname}")
        while True:
            choice = input("Select preset number to overwrite (Enter to cancel): ").strip()
            if not choice:
                return None
            if choice.isdigit():
                idx = int(choice)
                if 1 <= idx <= len(preset_names):
                    name = preset_names[idx - 1]
                    break
            print("Invalid choice.")

    if name.lower() == "current":
        print("Preset name 'current' is reserved.")
        return None
    if name in presets and name != "0":
        ans = input(f"Preset '{name}' exists. Overwrite? [y/N]: ").strip().lower()
        if ans not in ("y", "yes"):
            return None

    linked_names = {ln.name for ln in links if ln.target_in_repo}
    enabled = [n for n in repo_nodes if n in linked_names]
    disabled = [n for n in repo_nodes if n not in linked_names]
    if len(disabled) <= len(enabled):
        mode = "blacklist"
        nodes = disabled
    else:
        mode = "whitelist"
        nodes = enabled

    presets[name] = {"mode": mode, "nodes": nodes}
    _save_json(presets_path, presets)
    return name


def _mklink_junction(src: str, dst: str) -> bool:
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        subprocess.check_call(["cmd", "/c", "mklink", "/J", dst, src], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _remove_dir(path: str) -> bool:
    try:
        subprocess.check_call(["cmd", "/c", "rmdir", "/s", "/q", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _add_link(repo_dir: str, custom_nodes_dir: str, name: str) -> str:
    src = os.path.join(repo_dir, name)
    dst = os.path.join(custom_nodes_dir, name)
    if not os.path.isdir(src):
        return f"Missing in repo: {name}"
    if os.path.exists(dst):
        if _is_reparse_point(dst):
            _remove_dir(dst)
        else:
            return f"Exists as real dir (skip): {name}"
    ok = _mklink_junction(src, dst)
    return f"Added: {name}" if ok else f"Failed to add: {name}"


def _remove_link(custom_nodes_dir: str, link: LinkedNode) -> str:
    if not _is_reparse_point(link.path):
        return f"Not a junction (skip): {link.name}"
    ok = _remove_dir(link.path)
    return f"Removed: {link.name}" if ok else f"Failed to remove: {link.name}"


def _sync(repo_dir: str, custom_nodes_dir: str, repo_nodes: List[str], links: List[LinkedNode]) -> List[str]:
    repo_set = set(repo_nodes)
    link_map = {ln.name: ln for ln in links}
    to_add = [n for n in repo_nodes if n not in link_map]
    to_remove = [ln for ln in links if ln.name not in repo_set]

    print(f"Will add: {len(to_add)}, remove: {len(to_remove)}")
    ans = input("Proceed? [y/N]: ").strip().lower()
    if ans not in ("y", "yes"):
        return ["Sync cancelled."]

    msgs: List[str] = []
    for name in to_add:
        msgs.append(_add_link(repo_dir, custom_nodes_dir, name))
    for ln in to_remove:
        msgs.append(_remove_link(custom_nodes_dir, ln))
    return msgs


def _invert(
    repo_dir: str,
    custom_nodes_dir: str,
    repo_nodes: List[str],
    links: List[LinkedNode],
    names: Optional[List[str]] = None,
) -> List[str]:
    link_map = {ln.name: ln for ln in links}
    scope = names if names is not None else repo_nodes
    to_add = [n for n in scope if n not in link_map]
    to_remove = [link_map[n] for n in scope if n in link_map]

    print(f"Will add: {len(to_add)}, remove: {len(to_remove)}")
    msgs: List[str] = []
    for ln in to_remove:
        msgs.append(_remove_link(custom_nodes_dir, ln))
    for name in to_add:
        msgs.append(_add_link(repo_dir, custom_nodes_dir, name))
    return msgs


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage custom_nodes junction links.")
    parser.add_argument("--repo", help="Path to custom_nodes_repo")
    parser.add_argument("--custom", help="Path to custom_nodes")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    cfg_path = str(here / "config.json")
    presets_path = str(here / "run_comfyui_presets_config.json")
    repo_dir, custom_nodes_dir, _cfg = _resolve_paths(cfg_path, args)

    while True:
        repo_nodes = _scan_repo(repo_dir)
        links = _scan_links(custom_nodes_dir, repo_dir)
        _print_panels(repo_nodes, links)

        cmd = input("> ").strip()
        if not cmd:
            continue
        if cmd.lower() in ("q", "quit", "exit"):
            return 0
        if cmd in ("?", "h", "help"):
            print("a [n]: add repo nodes (all or by index)")
            print("r [n]: remove linked nodes (all or by index)")
            print("i [n]: invert (add unlinked, remove linked) for repo nodes")
            print("s: sync (mirror repo -> custom_nodes via junctions)")
            print("p: choose preset and apply")
            print("w: save preset from current links")
            print("    - adds links for repo nodes missing in custom_nodes")
            print("    - removes junctions that are not present in repo")
            print("q: quit")
            continue
        if cmd.lower() == "s":
            for msg in _sync(repo_dir, custom_nodes_dir, repo_nodes, links):
                print(msg)
            continue
        if cmd.lower() == "p":
            _ensure_presets_config(presets_path)
            presets = _load_presets(presets_path)
            preset_names = _list_preset_names(presets)
            chosen = _prompt_preset_choice(preset_names)
            if chosen is None:
                continue
            preset = presets.get(chosen)
            if not isinstance(preset, dict):
                print(f"Invalid preset format: {chosen}")
                continue
            for msg in _apply_preset(repo_dir, custom_nodes_dir, repo_nodes, links, chosen, preset):
                print(msg)
            continue
        if cmd.lower() == "w":
            _ensure_presets_config(presets_path)
            presets = _load_presets(presets_path)
            if not presets:
                continue
            saved = _save_preset_from_links(presets_path, presets, repo_nodes, links)
            if saved:
                print(f"Saved preset: {saved}")
            continue
        parts = cmd.split()
        if parts[0].lower() in ("a", "r", "i") and len(parts) == 1:
            if parts[0].lower() == "a":
                for name in repo_nodes:
                    print(_add_link(repo_dir, custom_nodes_dir, name))
            elif parts[0].lower() == "r":
                for ln in links:
                    print(_remove_link(custom_nodes_dir, ln))
            else:
                for msg in _invert(repo_dir, custom_nodes_dir, repo_nodes, links):
                    print(msg)
            continue
        if len(parts) < 2:
            print("Invalid command. Example: a, r 10-23, i 1-5")
            continue
        action = parts[0].lower()
        selection = " ".join(parts[1:])
        idxs = _parse_indices(selection, len(repo_nodes))
        if not idxs:
            print("No valid indices.")
            continue
        link_map = {ln.name: ln for ln in links}
        if action == "a":
            for idx in idxs:
                print(_add_link(repo_dir, custom_nodes_dir, repo_nodes[idx - 1]))
        elif action == "r":
            for idx in idxs:
                name = repo_nodes[idx - 1]
                ln = link_map.get(name)
                if not ln:
                    print(f"Not linked: {name}")
                    continue
                print(_remove_link(custom_nodes_dir, ln))
        elif action == "i":
            names = [repo_nodes[idx - 1] for idx in idxs]
            for msg in _invert(repo_dir, custom_nodes_dir, repo_nodes, links, names):
                print(msg)
        else:
            print("Unknown command.")


if __name__ == "__main__":
    raise SystemExit(main())
