import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import git
from colorama import init, Fore, Style
from requirements_checker.config_manager import config_manager


# Initialize colorama
init(autoreset=True)


def get_default_branch(repo: git.Repo) -> Optional[str]:
    """Determine the repository's default branch."""
    try:
        # Try to read remote HEAD
        try:
            remote_head = repo.remotes.origin.refs.HEAD.reference
            return remote_head.name.replace('origin/', '')
        except Exception:
            pass

        # Fallback to common names among remote branches
        remote_branches = [ref.name.replace("origin/", "") for ref in repo.remotes.origin.refs if not ref.name.endswith('/HEAD')]
        for candidate in ["main", "master", "dev", "develop", "stable"]:
            if candidate in remote_branches:
                return candidate

        # As a last resort, first available remote branch
        if remote_branches:
            return remote_branches[0]
        return None
    except Exception as e:
        print(f"\t? Error determining default branch: {e}")
        return None


def get_latest_commit_info(repo: git.Repo, branch_name: str):
    """Get the latest commit and its datetime for a branch."""
    try:
        if f'origin/{branch_name}' in [ref.name for ref in repo.remotes.origin.refs]:
            commit = repo.commit(f'origin/{branch_name}')
        else:
            commit = repo.commit(branch_name)
        return commit, commit.committed_datetime
    except Exception:
        return None, None


def find_best_branch_to_update(repo: git.Repo, current_branch_name: str) -> Tuple[str, str, Optional[str]]:
    """Determine the best branch to update."""
    default_branch = get_default_branch(repo)
    if not default_branch:
        return current_branch_name, "unknown", "Could not determine default branch"

    # If we are already on the default branch
    if current_branch_name == default_branch:
        return current_branch_name, "default", None

    # Compare commit dates
    current_commit, current_date = get_latest_commit_info(repo, current_branch_name)
    default_commit, default_date = get_latest_commit_info(repo, default_branch)

    if not current_date or not default_date:
        return current_branch_name, "unknown", "Could not compare branch dates"

    # If default branch is newer, prefer switching
    if default_date > current_date:
        return default_branch, "switch", f"Switching to newer default branch ({default_branch})"
    else:
        return current_branch_name, "custom", f"Current branch ({current_branch_name}) is up to date"


def stash_local_changes(repo: git.Repo):
    """Stash local uncommitted changes if present."""
    if repo.is_dirty():
        try:
            repo.git.stash('push', '-m', f'Auto-stash {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
            return True, "→ Local changes stashed"
        except git.exc.GitCommandError as e:
            return False, f"! Failed to stash changes: {e}"
    return False, None


def is_git_repository(directory: str) -> bool:
    """Check whether a directory is a git repository."""
    return os.path.exists(os.path.join(directory, '.git'))


def should_skip_directory(directory: str) -> bool:
    """Check whether a directory should be skipped."""
    skip_patterns = ['__pycache__', '.git', 'node_modules', '.vscode', '.idea', '__MACOSX', '.DS_Store', 'Thumbs.db']
    dir_name = os.path.basename(directory)
    return any(pattern in dir_name for pattern in skip_patterns)


def initialize_git_repository(directory: str):
    """Initialize a git repository in the directory with a remote URL."""
    try:
        repo_name = os.path.basename(directory)
        print(f"\t→ Directory '{repo_name}' is not a git repository")

        repo_url = input(f"\tEnter git repository URL (or press Enter to skip): ").strip()
        if not repo_url:
            return False, "Skipped by user"

        if not (repo_url.startswith('http') or repo_url.startswith('git@')):
            return False, "Invalid repository URL format"

        existing_files = os.listdir(directory)
        if existing_files:
            print(f"\t→ Directory contains {len(existing_files)} files/folders")
            overwrite = input(f"\tReplace contents with git repository? (y/N): ").strip().lower()
            if overwrite != 'y':
                return False, "User chose not to overwrite existing contents"

        # Initialize and set remote
        repo = git.Repo.init(directory)
        repo.create_remote('origin', repo_url)
        return True, "Initialized git repository with remote origin"
    except Exception as e:
        return False, f"Failed to initialize repository: {e}"


def main():
    # Example usage of GitPython-based logic (kept minimal)
    cfg = config_manager.read_config()
    custom_nodes = cfg.get('custom_nodes_path')
    if not custom_nodes:
        print("custom_nodes_path is not set in config.json")
        return

    root = Path(custom_nodes).parent
    targets = [root] + [p for p in Path(custom_nodes).iterdir() if p.is_dir()]

    for path in targets:
        if should_skip_directory(str(path)):
            continue
        if not is_git_repository(str(path)):
            print(f"{Fore.YELLOW}Skipping non-git folder:{Style.RESET_ALL} {path}")
            continue
        try:
            repo = git.Repo(str(path))
            # Fetch
            for remote in repo.remotes:
                remote.fetch()
            # Determine branch
            current_branch = repo.active_branch.name if not repo.head.is_detached else "DETACHED"
            target_branch, branch_type, switch_reason = find_best_branch_to_update(repo, current_branch)

            # Display branch decision
            if target_branch != current_branch:
                branch_display = f"\t{Fore.RED}{current_branch} --> {target_branch}{Style.RESET_ALL}"
            elif branch_type == "custom":
                branch_display = f"\t{Fore.YELLOW}{current_branch} --> {target_branch}{Style.RESET_ALL}"
            else:
                branch_display = f"\t{current_branch} --> {target_branch}"
            print(branch_display)
            if switch_reason:
                print(f"\t→ {switch_reason}")

            # Stash local changes
            stashed, stash_msg = stash_local_changes(repo)
            if stashed:
                print(f"\t{stash_msg}")

            # Switch if needed
            if target_branch != current_branch and target_branch != "DETACHED":
                try:
                    if target_branch not in [b.name for b in repo.branches]:
                        repo.git.checkout('-b', target_branch, f'origin/{target_branch}')
                    else:
                        repo.git.checkout(target_branch)
                except git.exc.GitCommandError as e:
                    print(f"Failed to switch branch: {e}")
                    continue

            # Pull
            try:
                if repo.active_branch.tracking_branch():
                    repo.git.pull()
                else:
                    # Try to set upstream, otherwise pull with remote+branch
                    try:
                        repo.git.branch('--set-upstream-to', f'origin/{target_branch}', target_branch)
                        repo.git.pull()
                    except git.exc.GitCommandError:
                        repo.git.pull('origin', target_branch)
                print(f"\t{Fore.GREEN}Updated{Style.RESET_ALL}")
            except git.exc.GitCommandError as e:
                if "couldn't find remote ref" in str(e).lower():
                    print(f"\t? Local branch only (no remote updates available)")
                else:
                    print(f"\t! Pull failed: {e}")

        except Exception as e:
            print(f"{Fore.RED}Error:{Style.RESET_ALL} {e}")


if __name__ == "__main__":
    main()

