"""
Utilities Module

Provides helper functions, constants, and formatting utilities for the requirements checker.
Includes color formatting, output formatting, and common utility functions.
"""

from typing import Any, Optional, List
from colorama import Fore, Style
from dataclasses import dataclass
from datetime import datetime
import logging
import subprocess
import sys


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('requirements_checker.log'),
        # logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

@dataclass
class OutputColors:
    """Color constants for terminal output."""
    SUCCESS: str = Fore.GREEN
    WARNING: str = Fore.YELLOW
    ERROR: str = Fore.RED
    INFO: str = Fore.BLUE
    COMMAND: str = Fore.CYAN
    RESET: str = Style.RESET_ALL

colors = OutputColors()

def format_command(command: str) -> str:
    """Format a command for display in the terminal."""
    return f"{colors.COMMAND}{command}{colors.RESET}"

def format_version(version: str, is_latest: bool = False) -> str:
    """Format a version number for display."""
    color = colors.SUCCESS if is_latest else colors.INFO
    return f"{color}{version}{colors.RESET}"

def format_package_info(package: str, version: Optional[str] = None) -> str:
    """Format package information for display."""
    if version:
        return f"{colors.SUCCESS}{package}{colors.RESET} ({version})"
    return f"{colors.SUCCESS}{package}{colors.RESET}"

def print_section_header(title: str) -> None:
    """Print a formatted section header."""
    print(f"\n{colors.INFO}{'='*20} {title} {'='*20}{colors.RESET}")

def print_command_help() -> None:
    """Print helpful commands for package management."""
    commands = [
        ("Check package info", "pip show <PACKAGE_NAME>"),
        ("Get all available versions", "pip index versions <PACKAGE_NAME>"),
        ("Install need version", "pip install <PACKAGE_NAME>==<VERSION>"),
        ("Install latest version", "pip install --upgrade <PACKAGE_NAME>"),
        ("Uninstall package", "pip uninstall <PACKAGE_NAME>"),
        ("Get all dependencies", "pipdeptree -p <PACKAGE_NAME>"),
        ("Get reverse dependencies", "pipdeptree --reverse --packages <PACKAGE_NAME>"),
        ("Clear pip cache", "pip cache purge")
    ]
    
    print_section_header("Useful Commands")
    for description, command in commands:
        print(f"{description}: {format_command(command)}")

def display_version_status(package_name: str, installed_version: Optional[str], 
                         latest_version: Optional[str], available_versions: List[str], 
                         state_of_package: str, extra_info: str) -> None:
    """Display the version status of a package."""
    print(f"\tInstalled: {installed_version if installed_version else 'Not installed'}")
    print(f"\tLatest: {latest_version if latest_version else 'Unknown'}")
    print(f"\tRequired: {state_of_package}")
    if extra_info:
        print(f"\tExtra info: {extra_info}")

def process_custom_entry(entry_name: str, values: List[Any]) -> None:
    """Process custom entries like git or extra-index-url."""
    print_section_header(entry_name)
    for value in values:
        print(f"\t{value[0]} in {value[-1]}")

def log_operation_time(func: Any) -> Any:
    """Decorator to log operation execution time."""
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start_time = datetime.now()
        result = func(*args, **kwargs)
        end_time = datetime.now()
        duration = end_time - start_time
        logger.info(f"Operation '{func.__name__}' completed in {duration}")
        return result
    return wrapper

def handle_error(func: Any) -> Any:
    """Decorator for consistent error handling."""
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {str(e)}")
            print(f"{colors.ERROR}Error: {str(e)}{colors.RESET}")
            return None
    return wrapper

def check_dependencies() -> None:
    """
    Check for required external dependencies and install them if missing.
    """
    required = ['colorama', 'requests', 'packaging']
    missing = []

    # Проверяем наличие каждой библиотеки
    for module in required:
        try:
            __import__(module)
        except ImportError:
            missing.append(module)

    # Устанавливаем отсутствующие библиотеки
    if missing:
        print(f"{colors.WARNING}The following required packages are missing: {', '.join(missing)}{colors.RESET}")
        print(f"{colors.INFO}Installing missing dependencies...{colors.RESET}")
        try:
            subprocess.run(
                [sys.executable, '-m', 'pip', 'install', *missing],
                check=True,
                capture_output=True,
                text=True
            )
            print(f"{colors.SUCCESS}Successfully installed: {', '.join(missing)}{colors.RESET}")
        except subprocess.CalledProcessError as e:
            print(f"{colors.ERROR}Failed to install dependencies: {e.stderr}{colors.RESET}")
            sys.exit(1)