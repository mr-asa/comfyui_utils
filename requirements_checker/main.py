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

def simplify_package_name(full_path: str) -> str:
    """Simplify package name based on its location relative to config paths."""
    custom_nodes_path = config_manager.get_value("custom_nodes_path")
    if not custom_nodes_path:
        return os.path.basename(full_path.rstrip(os.sep))  # Запасной вариант
    
    # Вычисляем корневой путь ComfyUI как родительскую директорию custom_nodes
    base_path = os.path.dirname(custom_nodes_path)
    
    # Проверяем, является ли путь корневым requirements.txt
    root_requirements = os.path.join(base_path, "requirements.txt")
    if full_path == root_requirements:
        return "ComfyUI"
    
    # Обрабатываем пути в custom_nodes
    if full_path.startswith(custom_nodes_path):
        # Убираем custom_nodes_path и берём первый сегмент после него
        relative_path = full_path[len(custom_nodes_path):].lstrip(os.sep)
        package_name = relative_path.split(os.sep)[0]
        return package_name
    
    # Для остальных случаев возвращаем последний сегмент пути
    return os.path.basename(full_path.rstrip(os.sep))

def display_version_status(package_name: str, installed_version: Optional[str], 
                         latest_version: Optional[str], max_allowed_version: Optional[str], 
                         state_of_package: str, extra_info: str, limiting_package: Optional[str] = None) -> None:
    """Display the version status of a package with colored output."""
    # Определяем цвет для Installed относительно max_allowed_version
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
    
    # Уточняем Required и проверяем соответствие
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

    # Фильтруем доступные версии по всем ограничениям
    allowed_versions = versions.copy()
    for value in values_sorted:
        if value[1]:  # Есть оператор
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

    # Определяем максимально допустимую версию
    max_allowed_version = max(allowed_versions, key=pkg_version.parse, default=None) if allowed_versions else None

    # Display required versions from requirements files
    for value in values_sorted:
        installable = f"{value[1] if value[1] else ''}{value[2] if value[2] else 'Any'}"
        directories = value[-1] if isinstance(value[-1], list) else [value[-1]]
        simplified_dirs = [simplify_package_name(dir) for dir in directories]
        print(f"\t{installable} in {simplified_dirs}")
        extra_info = value[0] if value[0] else ''
        if value[1] and not limiting_package:  # Устанавливаем limiting_package только если ещё не установлено
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

        # Инициализация конфигурации
        env_type = config_manager.get_value('env_type')
        if env_type == 'conda':
            config_manager.get_value('conda_path')
            config_manager.get_value('conda_env')
            config_manager.get_value('conda_env_folder')
        custom_nodes_dir = config_manager.get_value('custom_nodes_path')

        requirements_dict = OrderedDict()

        print_command_help()

        # Активация окружения
        env_manager.activate_virtual_environment()

        # Добавляем корневой requirements.txt
        root_requirements_path = os.path.join(os.path.dirname(custom_nodes_dir), "requirements.txt")
        parser = RequirementsParser()
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

        # Обрабатываем custom_nodes
        if os.path.exists(custom_nodes_dir):
            for root, dirs, files in os.walk(custom_nodes_dir):
                for file in files:
                    if file == 'requirements.txt':
                        file_path = os.path.join(root, file)
                        requirements = parser.get_active_requirements(Path(file_path))
                        for req in requirements:
                            parsed = parser.parse_conditional_dependencies(req, root)
                            for key, value in parsed.items():
                                if key not in requirements_dict:
                                    requirements_dict[key] = []
                                if isinstance(value, PackageRequirement):
                                    requirements_dict[key].append([value.extras, value.operator, value.version, value.directory])
                                else:
                                    requirements_dict[key].append([value.url, None, None, value.directory])

        # Дальнейшая обработка результатов
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
