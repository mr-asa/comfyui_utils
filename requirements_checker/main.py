"""
Main Module

Entry point for the requirements checker application.
Coordinates the work of all other modules and provides the main execution flow.
"""

import os
import sys
import logging
import subprocess
from typing import Dict, List, Optional, Any
from collections import OrderedDict
from packaging import version as pkg_version
from pathlib import Path

from .config_manager import config_manager
from .environment_manager import env_manager
from .package_manager import package_manager
from .requirements_parser import (
    RequirementsParser,
    PackageRequirement
)
from .utils import (
    colors,
    print_command_help,
    print_section_header,
    log_operation_time,
    handle_error,
    check_dependencies,
    display_version_status,
    process_custom_entry
)

logger = logging.getLogger(__name__)

_CUSTOM_NODES_PATHS_CACHE: Optional[List[str]] = None

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


def get_custom_nodes_paths() -> List[str]:
    global _CUSTOM_NODES_PATHS_CACHE
    if _CUSTOM_NODES_PATHS_CACHE is not None:
        return _CUSTOM_NODES_PATHS_CACHE

    cfg = config_manager.read_config()
    paths: List[str] = []
    raw_list = cfg.get("custom_nodes_paths")
    if isinstance(raw_list, list):
        for p in raw_list:
            if isinstance(p, str) and p.strip():
                paths.append(p.strip())
    single = cfg.get("custom_nodes_path")
    if isinstance(single, str) and single.strip():
        paths.append(single.strip())

    _CUSTOM_NODES_PATHS_CACHE = _dedupe_dirs(paths)
    return _CUSTOM_NODES_PATHS_CACHE


def simplify_package_name(full_path: str) -> str:
    """Simplify package name based on its location relative to config paths."""
    custom_nodes_paths = get_custom_nodes_paths()
    if not custom_nodes_paths:
        return os.path.basename(full_path.rstrip(os.sep))  # Fallback
    
    full_path_norm = os.path.normcase(os.path.normpath(full_path))

    # Check if the path is the root requirements.txt
    for cn in custom_nodes_paths:
        base_path = os.path.dirname(cn)
        root_requirements = os.path.normcase(os.path.normpath(os.path.join(base_path, "requirements.txt")))
        if full_path_norm == root_requirements:
            return "ComfyUI"
    
    # Handle paths inside custom_nodes
    for cn in sorted(custom_nodes_paths, key=len, reverse=True):
        cn_norm = os.path.normcase(os.path.normpath(cn))
        if full_path_norm.startswith(cn_norm):
            relative_path = full_path_norm[len(cn_norm):].lstrip(os.sep)
            package_name = relative_path.split(os.sep)[0]
            return package_name
    
    # For other cases return the last path segment
    return os.path.basename(full_path.rstrip(os.sep))

def display_version_status(package_name: str, installed_version: Optional[str], 
                         latest_version: Optional[str], max_allowed_version: Optional[str], 
                         state_of_package: str, extra_info: str, limiting_package: Optional[str] = None) -> None:
    """Display the version status of a package with colored output."""
    # Choose color for Installed relative to max_allowed_version
    if installed_version is None:
        installed_color = colors.ERROR
        installed_text = "Not installed"
    elif max_allowed_version and pkg_version.parse(installed_version) < pkg_version.parse(max_allowed_version):
        installed_color = colors.WARNING
        installed_text = installed_version
    else:
        installed_color = colors.SUCCESS
        installed_text = installed_version

    print(f"\tInstalled: {installed_color}{installed_text}{colors.RESET}")
    print(f"\tLatest: {latest_version if latest_version else 'Unknown'}")
    print(f"\tMax Allowed: {max_allowed_version if max_allowed_version else 'Unknown'}")
    
    # Refine Required and validate consistency
    if state_of_package != "any" and limiting_package:
        print(f"\tRequired: {state_of_package} (limited by {limiting_package})")
        if installed_version and "==" in state_of_package:
            required_version = state_of_package.split("==")[1]
            if installed_version != required_version:
                print(f"\t{colors.WARNING}Warning: Installed version {installed_version} does not match required {required_version}{colors.RESET}")
    else:
        print(f"\tRequired: {state_of_package}")

    if extra_info:
        print(f"\tExtra info: {extra_info}")
        
def process_custom_entry(entry_name: str, values: List[Any]) -> None:
    """Process custom entries like git or extra-index-url."""
    print_section_header(entry_name)
    for value in values:
        directories = value[-1] if isinstance(value[-1], list) else [value[-1]]
        simplified_dirs = [simplify_package_name(dir) for dir in directories]
        print(f"\t{value[0]} in {simplified_dirs}")


@log_operation_time
@handle_error
def process_package(package_name: str, values: List[Dict], versions: List[str]) -> None:
    """Process a single package and display its status."""
    print_section_header(package_name)
    
    values_sorted = sorted(values, key=lambda x: x[2] if x[2] is not None else '')
    state_of_package = "any"
    installed_version = package_manager.get_installed_version(package_name)
    latest_version = package_manager.get_latest_version(package_name)
    extra_info = ""
    limiting_package = None

    # Filter available versions by all constraints
    allowed_versions = versions.copy()
    for value in values_sorted:
        if value[1]:  # Has operator
            try:
                if "<=" in value[1]:
                    allowed_versions = [v for v in allowed_versions if pkg_version.parse(v) <= pkg_version.parse(value[2])]
                elif "<" in value[1]:
                    allowed_versions = [v for v in allowed_versions if pkg_version.parse(v) < pkg_version.parse(value[2])]
                elif ">=" in value[1]:
                    allowed_versions = [v for v in allowed_versions if pkg_version.parse(v) >= pkg_version.parse(value[2])]
                elif "==" in value[1]:
                    allowed_versions = [v for v in allowed_versions if pkg_version.parse(v) == pkg_version.parse(value[2])]
                    state_of_package = f"{value[1]}{value[2]}"
                    limiting_package = simplify_package_name(value[-1][0] if isinstance(value[-1], list) else value[-1])
                elif "!=" in value[1]:
                    allowed_versions = [v for v in allowed_versions if pkg_version.parse(v) != pkg_version.parse(value[2])]
                    state_of_package = f"{value[1]}{value[2]}"
                    limiting_package = simplify_package_name(value[-1][0] if isinstance(value[-1], list) else value[-1])
                elif ">" in value[1]:
                    allowed_versions = [v for v in allowed_versions if pkg_version.parse(v) > pkg_version.parse(value[2])]
            except pkg_version.InvalidVersion as e:
                logger.warning(f"Invalid version '{value[2]}' for package '{package_name}': {str(e)}")
                print(f"\t{colors.WARNING}Warning: Invalid version '{value[2]}' skipped{colors.RESET}")

    # Determine the maximum allowed version
    max_allowed_version = max(allowed_versions, key=pkg_version.parse, default=None) if allowed_versions else None

    # Display required versions from requirements files
    for value in values_sorted:
        installable = f"{value[1] if value[1] else ''}{value[2] if value[2] else 'Any'}"
        directories = value[-1] if isinstance(value[-1], list) else [value[-1]]
        simplified_dirs = [simplify_package_name(dir) for dir in directories]
        print(f"\t{installable} in {simplified_dirs}")
        extra_info = value[0] if value[0] else ''
        if value[1] and not limiting_package:  # Set limiting_package only once
            limiting_package = simplified_dirs[0]
            state_of_package = f"{value[1]}{value[2]}"

    display_version_status(
        package_name,
        installed_version,
        latest_version,
        max_allowed_version,
        state_of_package,
        extra_info,
        limiting_package
    )
    
@log_operation_time
def main() -> None:
    try:
        logger.info("Starting requirements checker")
        
        check_dependencies()

        # Initialize configuration
        env_type = config_manager.get_value('env_type')
        if env_type == 'conda':
            config_manager.get_value('conda_path')
            config_manager.get_value('conda_env')
            config_manager.get_value('conda_env_folder')
        custom_nodes_dirs = get_custom_nodes_paths()
        if not custom_nodes_dirs:
            fallback = config_manager.get_value('custom_nodes_path')
            if fallback:
                custom_nodes_dirs = _dedupe_dirs([fallback])
                global _CUSTOM_NODES_PATHS_CACHE
                _CUSTOM_NODES_PATHS_CACHE = custom_nodes_dirs

        requirements_dict = OrderedDict()

        print_command_help()

        # Activate environment
        env_manager.activate_virtual_environment()

        # Add root requirements.txt
        parser = RequirementsParser()
        seen_root_reqs: set[str] = set()
        for custom_nodes_dir in custom_nodes_dirs:
            root_requirements_path = os.path.join(os.path.dirname(custom_nodes_dir), "requirements.txt")
            root_req_norm = _norm_real_path(root_requirements_path)
            if root_req_norm in seen_root_reqs:
                continue
            seen_root_reqs.add(root_req_norm)
            if os.path.exists(root_requirements_path):
                requirements = parser.get_active_requirements(Path(root_requirements_path))
                for req in requirements:
                    parsed = parser.parse_conditional_dependencies(req, os.path.dirname(root_requirements_path))
                    for key, value in parsed.items():
                        if key not in requirements_dict:
                            requirements_dict[key] = []
                        if isinstance(value, PackageRequirement):
                            requirements_dict[key].append([value.extras, value.operator, value.version, value.directory])
                        else:
                            requirements_dict[key].append([value.url, None, None, value.directory])

        # Process custom_nodes (top-level only)
        seen_nodes: set[str] = set()
        for custom_nodes_dir in custom_nodes_dirs:
            if not os.path.exists(custom_nodes_dir):
                continue
            try:
                entries = sorted(os.listdir(custom_nodes_dir))
            except OSError:
                entries = []
            for name in entries:
                if name == ".disabled" or name.endswith(".disable") or name.endswith(".disabled"):
                    continue
                node_dir = os.path.join(custom_nodes_dir, name)
                if not os.path.isdir(node_dir):
                    continue
                node_real = _norm_real_path(node_dir)
                if node_real in seen_nodes:
                    continue
                seen_nodes.add(node_real)
                file_path = os.path.join(node_dir, "requirements.txt")
                if not os.path.isfile(file_path):
                    continue
                requirements = parser.get_active_requirements(Path(file_path))
                for req in requirements:
                    parsed = parser.parse_conditional_dependencies(req, node_dir)
                    for key, value in parsed.items():
                        if key not in requirements_dict:
                            requirements_dict[key] = []
                        if isinstance(value, PackageRequirement):
                            requirements_dict[key].append([value.extras, value.operator, value.version, value.directory])
                        else:
                            requirements_dict[key].append([value.url, None, None, value.directory])

        # Further processing of results
        normalized_dict = RequirementsParser.process_requirements_dict(requirements_dict)
        sorted_dict = RequirementsParser.sort_ordered_dict(normalized_dict)
        result_dict = RequirementsParser.combine_names(sorted_dict)
        
        packages = sorted([i for i in result_dict], key=str.lower)
        for package_name in packages:
            if package_name in ["git", "--extra-index-url"]:
                process_custom_entry(package_name, result_dict[package_name])
            else:
                process_package(package_name, result_dict[package_name], package_manager.get_all_versions(package_name))

        logger.info("Requirements check completed successfully")
        
    except Exception as e:
        logger.error(f"An error occurred in main: {str(e)}")
        print(f"{colors.ERROR}An error occurred: {str(e)}{colors.RESET}")

    input("\nPress Enter to exit...")

if __name__ == '__main__':
    main()
