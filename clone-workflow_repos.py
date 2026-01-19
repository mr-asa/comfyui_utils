import subprocess
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional, Tuple, List
import os
import ctypes

from comfyui_root import default_workflows_dir, resolve_comfyui_root

# Simple ANSI color helper with Windows enablement
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
RESET = "\033[0m"


def info(message: str) -> None:
    print(f"{CYAN}{message}{RESET}")


def warn(message: str) -> None:
    print(f"{YELLOW}{message}{RESET}")


def error(message: str) -> None:
    print(f"{RED}{message}{RESET}")


def prompt_exit() -> None:
    input("Press Enter to exit...")


def pick_target_root(script_dir: Path) -> Optional[Path]:
    repos_file = script_dir / "clone-workflow_repos.txt"
    config_path = str(script_dir / "config.json")
    comfy_root = resolve_comfyui_root(config_path, start_path=script_dir)
    default_target = default_workflows_dir(comfy_root).resolve()

    info(f"Default clone path: {default_target}")
    print("1 - yes")
    print("2 - enter custom path")
    choice = input("Use the default path? ").strip()

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

    if not repos_file.exists():
        error(f"List file not found at: {repos_file}")
        return None

    target_root.mkdir(parents=True, exist_ok=True)
    return target_root


def load_repos_list(repos_file: Path) -> List[str]:
    lines = repos_file.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def parse_owner_repo(raw_url: str) -> Optional[Tuple[str, str]]:
    try:
        parsed = urlparse(raw_url)
    except Exception:
        return None

    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 2:
        return None

    owner, repo = segments[0], segments[1]
    if repo.endswith(".git"):
        repo = repo[:-4]

    return owner, repo


def clone_repo(url: str, dest: Path) -> bool:
    info(f"Cloning {url} -> {dest}")
    result = subprocess.run(["git", "clone", url, str(dest)])
    if result.returncode != 0:
        error(f"Clone failed (code {result.returncode}): {url}")
        return False
    return True


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    repos_file = script_dir / "clone-workflow_repos.txt"

    target_root = pick_target_root(script_dir)
    if target_root is None:
        return

    if not repos_file.exists():
        error(f"List file not found at: {repos_file}")
        return

    repos = load_repos_list(repos_file)
    if not repos:
        warn("No active URL lines in the list file.")
        return

    for raw_url in repos:
        parsed = parse_owner_repo(raw_url)
        if parsed is None:
            error(f"Invalid or unsupported URL, skipping: {raw_url}")
            continue

        owner, repo = parsed
        folder_name = f"{repo}_{owner}"
        folder_path = target_root / folder_name

        if folder_path.exists():
            warn(f"Folder '{folder_path}' already exists, skipping: {raw_url}")
            continue

        clone_repo(raw_url, folder_path)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        error(f"Unexpected error: {exc}")
    finally:
        prompt_exit()
