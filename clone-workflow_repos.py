from __future__ import annotations

import ctypes
import os
import subprocess
from pathlib import Path
from typing import List, Optional

from comfyui_root import default_workflows_dir, resolve_comfyui_root
from workflow_sources import (
    WorkflowSource,
    parse_workflow_url,
    sync_partial_source,
    write_metadata,
)


def _enable_ansi_on_windows() -> None:
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) != 0:
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


_enable_ansi_on_windows()

RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
GREEN = "\033[92m"
RESET = "\033[0m"


def info(message: str) -> None:
    print(f"{CYAN}{message}{RESET}")


def ok(message: str) -> None:
    print(f"{GREEN}{message}{RESET}")


def warn(message: str) -> None:
    print(f"{YELLOW}{message}{RESET}")


def error(message: str) -> None:
    print(f"{RED}{message}{RESET}")


def prompt_exit() -> None:
    input("Press Enter to exit...")


def pick_target_root(script_dir: Path) -> Optional[Path]:
    config_path = str(script_dir / "config.json")
    comfy_root = resolve_comfyui_root(config_path, start_path=script_dir)
    default_target = default_workflows_dir(comfy_root).resolve()

    info(f"Default clone path: {default_target}")
    print("1 - yes (default)")
    print("2 - enter custom path")
    choice = input("Use the default path? ").strip() or "1"

    if choice == "1":
        target_root = default_target
    elif choice == "2":
        custom_path = input("Enter clone path: ").strip()
        if not custom_path:
            error("Path not provided. Stopping.")
            return None
        target_root = Path(custom_path).expanduser().resolve()
    else:
        error("Unknown option. Stopping.")
        return None

    target_root.mkdir(parents=True, exist_ok=True)
    return target_root


def prompt_urls() -> List[str]:
    print()
    print("Enter workflow repo URLs. Empty line starts cloning.")
    print("Supported: GitHub/HuggingFace full repo URLs and tree/blob folder URLs.")
    urls: List[str] = []
    while True:
        raw = input("URL: ").strip()
        if not raw:
            break
        urls.append(raw)
    return urls


def clone_full_repo(source: WorkflowSource, dest: Path) -> bool:
    info(f"Cloning {source.repo_url} -> {dest}")
    result = subprocess.run(["git", "clone", source.repo_url, str(dest)])
    if result.returncode != 0:
        error(f"Clone failed (code {result.returncode}): {source.repo_url}")
        return False
    return True


def sync_partial_repo(source: WorkflowSource, dest: Path, script_dir: Path) -> bool:
    info(f"Syncing partial source {source.repo_url} -> {dest}")
    cache_root = (script_dir / ".partial_repo_cache").resolve()
    ok_sync, messages = sync_partial_source(source, dest, cache_root)
    if not ok_sync:
        for message in messages:
            error(message)
        return False
    write_metadata(dest, source)
    for message in messages:
        print(message)
    return True


def add_source(raw_url: str, target_root: Path, script_dir: Path) -> bool:
    source = parse_workflow_url(raw_url)
    if source is None:
        error(f"Invalid or unsupported URL, skipping: {raw_url}")
        return False

    folder_path = target_root / source.folder_name
    if folder_path.exists():
        warn(f"Folder already exists, skipping: {folder_path}")
        return False

    if source.mode == "full":
        cloned = clone_full_repo(source, folder_path)
        if cloned:
            ok(f"Added full repo: {folder_path}")
        return cloned

    if source.mode == "partial":
        synced = sync_partial_repo(source, folder_path, script_dir)
        if synced:
            ok(f"Added partial repo: {folder_path}")
        return synced

    error(f"Unsupported source mode '{source.mode}', skipping: {raw_url}")
    return False


def main() -> None:
    script_dir = Path(__file__).resolve().parent

    target_root = pick_target_root(script_dir)
    if target_root is None:
        return

    urls = prompt_urls()
    if not urls:
        warn("No URLs provided.")
        return

    added = 0
    failed = 0
    for raw_url in urls:
        if add_source(raw_url, target_root, script_dir):
            added += 1
        else:
            failed += 1

    print()
    ok(f"Done. Added: {added}, skipped/failed: {failed}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        error(f"Unexpected error: {exc}")
    finally:
        prompt_exit()
