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
import re
import shutil
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
        if not _is_reparse_point(path):
            continue
        if os.path.exists(path) and not os.path.isdir(path):
            continue
        target = _real(path)
        target_exists = os.path.exists(target)
        target_in_repo = target == repo_norm or target.startswith(repo_norm + os.sep)
        out.append(LinkedNode(name, path, target, target_exists, target_in_repo))
    return out


def _print_panels(node_names: List[str], links: List[LinkedNode], node_tags: Optional[Dict[str, List[str]]] = None) -> None:
    link_set = {ln.name for ln in links}
    link_map = {ln.name: ln for ln in links}
    idx_width = 3
    prefix_width = idx_width + 6
    node_tags = node_tags or {}

    def _red(text: str) -> str:
        return f"\x1b[31m{text}\x1b[0m"

    def _clip_name(name: str, max_len: int) -> str:
        if max_len <= 0:
            return ""
        if len(name) <= max_len:
            return name
        if max_len <= 3:
            return "." * max_len
        return name[: max_len - 3] + "..."

    def _entry_text(i: int, label: str, marker: str, is_junk: bool, name_max: Optional[int] = None) -> Tuple[str, str]:
        shown_name = _clip_name(label, name_max) if name_max is not None else label
        plain = f"{marker} [{i:>{idx_width}}] {shown_name}"
        colored = f"{_red('!!')} [{i:>{idx_width}}] {shown_name}" if is_junk else plain
        return plain, colored

    entries: List[Tuple[int, str, str, bool]] = []
    for i, name in enumerate(node_names, 1):
        is_junk = name in link_map and not link_map[name].target_exists
        tags = node_tags.get(name, [])
        label = name if not tags else f"{name} [{', '.join(tags)}]"
        if is_junk:
            marker = "!!"
        elif name in link_set:
            marker = "=>"
        else:
            marker = "  "
        entries.append((i, label, marker, is_junk))
    rows = (len(entries) + 1) // 2
    left_entries = entries[:rows]
    right_entries = entries[rows:]
    left_plain_w = max([prefix_width + len(label) for _, label, _, _ in left_entries], default=0)
    right_plain_w = max([prefix_width + len(label) for _, label, _, _ in right_entries], default=0)

    term_w = shutil.get_terminal_size((120, 20)).columns
    gap = 3
    min_col_w = prefix_width + 8
    two_cols = bool(right_entries) and term_w >= (min_col_w * 2 + gap)

    left_w = left_plain_w
    right_w = right_plain_w
    if two_cols:
        left_w = min(left_plain_w, max(min_col_w, term_w // 2 - gap))
        right_w = max(min_col_w, term_w - left_w - gap)
    else:
        right_entries = []

    print()
    print("Nodes ('=>' is linked)")
    for i in range(rows):
        li, llabel, lmarker, ljunk = left_entries[i]
        left_plain, left_colored = _entry_text(li, llabel, lmarker, ljunk, max(1, left_w - prefix_width))
        right = ""
        right_colored = ""
        if i < len(right_entries):
            ri, rlabel, rmarker, rjunk = right_entries[i]
            right, right_colored = _entry_text(ri, rlabel, rmarker, rjunk, max(1, right_w - prefix_width))
        if right:
            pad = max(1, left_w - len(left_plain) + gap)
            print(left_colored + (" " * pad) + right_colored)
        else:
            print(left_colored)
    print()
    print("create links: a (add) | r (remove) | i (invert) [n|n-m|n,n|text|re:regex]")
    print("show links: f (filter) [text|re:regex], f=clear, ?+=linked, ?-=unlinked, ?*=all")
    print("tags: t (list), tn <tag> (new), t+ <tag> [sel], t- <tag> [sel], ta|tr|ti <tag|idx>")
    print("other: s (sync), j (remove failed junk), p (presets), w (save preset), ? (help), q (quit), Enter (refresh)")


def _filter_display_nodes(display_nodes: List[str], links: List[LinkedNode], mode: str) -> List[str]:
    link_set = {ln.name for ln in links}
    if mode == "linked":
        return [n for n in display_nodes if n in link_set]
    if mode == "unlinked":
        return [n for n in display_nodes if n not in link_set]
    return display_nodes


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


def _remove_junk_links(custom_nodes_dir: str, links: List[LinkedNode]) -> List[str]:
    junk_links = [ln for ln in links if not ln.target_exists]
    if not junk_links:
        return ["No junk links."]
    return [_remove_link(custom_nodes_dir, ln) for ln in junk_links]


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


def _parse_name_filter(selection: str, names: List[str]) -> Tuple[List[str], Optional[str]]:
    raw = selection.strip()
    if not raw:
        return [], "Empty filter."

    low = raw.lower()
    if low.startswith("re:") or low.startswith("regex:"):
        _, _, pattern = raw.partition(":")
        pattern = pattern.strip()
        if not pattern:
            return [], "Regex is empty."
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return [], f"Invalid regex: {e}"
        return [n for n in names if rx.search(n)], None

    token = raw.lower()
    return [n for n in names if token in n.lower()], None


def _normalize_tags(raw: object) -> Dict[str, List[str]]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, List[str]] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        tag = k.strip()
        if not tag:
            continue
        items = v if isinstance(v, list) else []
        seen: set[str] = set()
        clean: List[str] = []
        for item in items:
            if not isinstance(item, str):
                continue
            name = item.strip()
            if not name:
                continue
            low = name.lower()
            if low in seen:
                continue
            seen.add(low)
            clean.append(name)
        out[tag] = clean
    return out


def _sorted_tags(tags: Dict[str, List[str]]) -> List[str]:
    return sorted(tags.keys(), key=lambda s: s.lower())


def _resolve_tag_token(tags: Dict[str, List[str]], token: str) -> Tuple[Optional[str], Optional[str]]:
    names = _sorted_tags(tags)
    raw = token.strip()
    if not raw:
        return None, "Tag is empty."
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(names):
            return names[idx - 1], None
        return None, f"Tag index out of range: {idx}"
    low = raw.lower()
    for name in names:
        if name.lower() == low:
            return name, None
    return None, f"Unknown tag: {raw}"


def _save_tags(cfg_path: str, cfg: Dict[str, object], tags: Dict[str, List[str]]) -> None:
    cfg["junk_links_tags"] = tags
    _save_json(cfg_path, cfg)


def _parse_selection_names(selection: str, display_nodes: List[str]) -> Tuple[List[str], Optional[str]]:
    idxs = _parse_indices(selection, len(display_nodes))
    if idxs:
        return [display_nodes[idx - 1] for idx in idxs], None
    names, err = _parse_name_filter(selection, display_nodes)
    if err:
        return [], err
    if not names:
        return [], "No matches."
    return names, None


def _apply_filter_expr(expr: str, names: List[str], tags: Dict[str, List[str]]) -> Tuple[List[str], str, Optional[str]]:
    raw = expr.strip()
    if not raw:
        return names, "*", None

    tag_name, tag_err = _resolve_tag_token(tags, raw)
    if tag_err is None and tag_name is not None:
        tagged_lows = {n.lower() for n in tags.get(tag_name, [])}
        matched = [n for n in names if n.lower() in tagged_lows]
        return matched, f"tag:{tag_name}", None

    matched, err = _parse_name_filter(raw, names)
    if err:
        return [], raw, err
    return matched, raw, None


def _print_tags(tags: Dict[str, List[str]], repo_nodes: List[str], links: List[LinkedNode]) -> None:
    names = _sorted_tags(tags)
    if not names:
        print("No tags. Use: tn <tag>")
        return
    repo_set = set(repo_nodes)
    linked_set = {ln.name for ln in links}
    idx_w = max(2, len(str(len(names))))
    tag_w = max([len(t) for t in names], default=3)
    total_vals: List[int] = []
    in_repo_vals: List[int] = []
    linked_vals: List[int] = []
    rows: List[Tuple[str, int, int, int]] = []
    for tag in names:
        items = tags.get(tag, [])
        in_repo = [n for n in items if n in repo_set]
        linked = [n for n in in_repo if n in linked_set]
        total_n = len(items)
        in_repo_n = len(in_repo)
        linked_n = len(linked)
        total_vals.append(total_n)
        in_repo_vals.append(in_repo_n)
        linked_vals.append(linked_n)
        rows.append((tag, total_n, in_repo_n, linked_n))
    total_w = max(1, len(str(max(total_vals) if total_vals else 0)))
    in_repo_w = max(1, len(str(max(in_repo_vals) if in_repo_vals else 0)))
    linked_w = max(1, len(str(max(linked_vals) if linked_vals else 0)))

    print("Tags:")
    for i, row in enumerate(rows, 1):
        tag, total_n, in_repo_n, linked_n = row
        print(
            f"  [{i:>{idx_w}}] {tag:<{tag_w}}  "
            f"total={total_n:>{total_w}}  "
            f"in_repo={in_repo_n:>{in_repo_w}}  "
            f"linked={linked_n:>{linked_w}}"
        )


def _build_node_tag_map(tags: Dict[str, List[str]], node_names: List[str]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    if not tags or not node_names:
        return out
    by_low = {n.lower(): n for n in node_names}
    for tag in _sorted_tags(tags):
        items = tags.get(tag, [])
        for item in items:
            real = by_low.get(item.lower())
            if not real:
                continue
            arr = out.setdefault(real, [])
            if tag not in arr:
                arr.append(tag)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage custom_nodes junction links.")
    parser.add_argument("--repo", help="Path to custom_nodes_repo")
    parser.add_argument("--custom", help="Path to custom_nodes")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    cfg_path = str(here / "config.json")
    presets_path = str(here / "run_comfyui_presets_config.json")
    repo_dir, custom_nodes_dir, cfg = _resolve_paths(cfg_path, args)
    tags = _normalize_tags(cfg.get("junk_links_tags"))
    if cfg.get("junk_links_tags") != tags:
        _save_tags(cfg_path, cfg, tags)

    filter_mode = "all"
    name_filter = ""

    while True:
        repo_nodes = _scan_repo(repo_dir)
        links = _scan_links(custom_nodes_dir, repo_dir)
        repo_set = set(repo_nodes)
        extra_nodes = [ln.name for ln in links if ln.name not in repo_set]
        display_nodes_all = sorted(repo_nodes + extra_nodes)
        display_nodes = _filter_display_nodes(display_nodes_all, links, filter_mode)
        filter_label = "*"
        if name_filter:
            display_nodes, filter_label, _ = _apply_filter_expr(name_filter, display_nodes, tags)
        print(f"\nFilter: {filter_mode} | name: {filter_label} | shown {len(display_nodes)}/{len(display_nodes_all)}")
        node_tag_map = _build_node_tag_map(tags, display_nodes)
        _print_panels(display_nodes, links, node_tag_map)

        cmd = input("> ").strip()
        if not cmd:
            continue
        low = cmd.lower()
        if low in ("?+", "f+", "f +", "f linked", "filter linked"):
            filter_mode = "linked"
            continue
        if low in ("?-", "f-", "f -", "f unlinked", "filter unlinked"):
            filter_mode = "unlinked"
            continue
        if low in ("?*", "f*", "f *", "f all", "filter all"):
            filter_mode = "all"
            continue
        if low in ("f", "filter", "fc", "f clear", "filter clear", "filter reset"):
            name_filter = ""
            continue
        if low.startswith("f ") or low.startswith("filter "):
            _, _, expr = cmd.partition(" ")
            expr = expr.strip()
            matches, _label, err = _apply_filter_expr(expr, display_nodes_all, tags)
            if err:
                print(err)
                continue
            name_filter = expr
            print(f"Name filter set: {expr} (matches {len(matches)})")
            continue
        if low in ("t", "tags"):
            _print_tags(tags, repo_nodes, links)
            continue
        if low.startswith("tn "):
            _, _, raw_tag = cmd.partition(" ")
            new_tag = raw_tag.strip()
            if not new_tag:
                print("Tag name is empty.")
                continue
            existing, _ = _resolve_tag_token(tags, new_tag)
            if existing is not None:
                print(f"Tag already exists: {existing}")
                continue
            tags[new_tag] = []
            _save_tags(cfg_path, cfg, tags)
            print(f"Tag created: {new_tag}")
            continue
        if low.startswith("t+ ") or low.startswith("t- "):
            pieces = cmd.split(maxsplit=2)
            if len(pieces) < 2:
                print("Usage: t+ <tag|idx> [selection] or t- <tag|idx> [selection]")
                continue
            op = pieces[0].lower()
            tag_token = pieces[1]
            selection = pieces[2] if len(pieces) >= 3 else ""
            tag_name, err = _resolve_tag_token(tags, tag_token)
            if err:
                print(err)
                continue
            if not selection.strip():
                print("Selection is required. Example: t+ 3d __3d or t+ 3d re:.*")
                continue
            selected_names, err = _parse_selection_names(selection, display_nodes)
            if err:
                print(err)
                continue
            repo_set = set(repo_nodes)
            selected_repo = [n for n in selected_names if n in repo_set]
            if not selected_repo:
                print("No repo packages selected.")
                continue
            current = tags.get(tag_name, [])
            current_lows = {n.lower() for n in current}
            changed = 0
            if op == "t+":
                for name in selected_repo:
                    if name.lower() not in current_lows:
                        current.append(name)
                        current_lows.add(name.lower())
                        changed += 1
                tags[tag_name] = current
                _save_tags(cfg_path, cfg, tags)
                print(f"Tag '{tag_name}': added {changed} package(s).")
            else:
                remove_lows = {n.lower() for n in selected_repo}
                next_items = [n for n in current if n.lower() not in remove_lows]
                changed = len(current) - len(next_items)
                tags[tag_name] = next_items
                _save_tags(cfg_path, cfg, tags)
                print(f"Tag '{tag_name}': removed {changed} package(s).")
            continue
        if low.startswith("ta ") or low.startswith("tr ") or low.startswith("ti "):
            pieces = cmd.split(maxsplit=1)
            if len(pieces) < 2:
                print("Usage: ta|tr|ti <tag|idx>")
                continue
            op = pieces[0].lower()
            tag_name, err = _resolve_tag_token(tags, pieces[1])
            if err:
                print(err)
                continue
            tagged = tags.get(tag_name, [])
            if not tagged:
                print(f"Tag '{tag_name}' is empty.")
                continue
            node_set = set(display_nodes_all)
            selected_names = [n for n in tagged if n in node_set]
            if not selected_names:
                print(f"No nodes from tag '{tag_name}' are present in current list.")
                continue
            link_map = {ln.name: ln for ln in links}
            if op == "ta":
                for name in selected_names:
                    print(_add_link(repo_dir, custom_nodes_dir, name))
            elif op == "tr":
                for name in selected_names:
                    ln = link_map.get(name)
                    if not ln:
                        print(f"Not linked: {name}")
                        continue
                    print(_remove_link(custom_nodes_dir, ln))
            else:
                for msg in _invert(repo_dir, custom_nodes_dir, repo_nodes, links, selected_names):
                    print(msg)
            continue
        if cmd.lower() in ("q", "quit", "exit"):
            return 0
        if cmd in ("?", "h", "help"):
            print("create links: a (add) | r (remove) | i (invert) [n|n-m|n,n|text|re:regex]")
            print("show links: f (filter) [text|re:regex|tag|tag_idx], f=clear, ?+=linked, ?-=unlinked, ?*=all")
            print("tags: t (list), tn <tag> (new), t+ <tag> <selection>, t- <tag> <selection>, ta|tr|ti <tag|idx>")
            print("presets: p (choose+apply), w (save current)")
            print("maint: s (sync repo<->custom_nodes), j (remove broken/junk links)")
            print("app: q (quit), Enter (refresh)")
            print("examples: f SAMPLER, f 7, tn 3d, t+ 3d __3d, ta 3d, t, tr 1")
            print("    - adds links for repo nodes missing in custom_nodes")
            print("    - removes junctions that are not present in repo")
            continue
        if cmd.lower() == "s":
            for msg in _sync(repo_dir, custom_nodes_dir, repo_nodes, links):
                print(msg)
            continue
        if cmd.lower() == "j":
            for msg in _remove_junk_links(custom_nodes_dir, links):
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
        selected_names, err = _parse_selection_names(selection, display_nodes)
        if err:
            print(err)
            continue

        link_map = {ln.name: ln for ln in links}
        if action == "a":
            for name in selected_names:
                print(_add_link(repo_dir, custom_nodes_dir, name))
        elif action == "r":
            for name in selected_names:
                ln = link_map.get(name)
                if not ln:
                    print(f"Not linked: {name}")
                    continue
                print(_remove_link(custom_nodes_dir, ln))
        elif action == "i":
            for msg in _invert(repo_dir, custom_nodes_dir, repo_nodes, links, selected_names):
                print(msg)
        else:
            print("Unknown command.")


if __name__ == "__main__":
    raise SystemExit(main())
