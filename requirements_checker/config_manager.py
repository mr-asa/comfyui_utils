"""
Configuration Manager Module

Handles all configuration-related operations including reading and writing config files,
managing environment settings, and providing configuration defaults.
"""

import json
import sys
import os
from pathlib import Path
from typing import Any, Dict, Optional, Union
from dataclasses import dataclass

@dataclass
class EnvironmentConfig:
    """Configuration data structure for environment settings."""
    env_type: str
    venv_path: Optional[str] = None
    conda_path: Optional[str] = None
    conda_env: Optional[str] = None
    conda_env_folder: Optional[str] = None
    project_path: Optional[str] = None
    custom_nodes_path: Optional[str] = None

class ConfigManager:
    """Manages configuration operations for the requirements checker."""
    
    def __init__(self, config_path: Union[str, Path] = "config.json"):
        """
        Initialize the configuration manager.
        
        Args:
            config_path: Path to the configuration file
        """
        self.config_path = Path(config_path)
        self._ensure_config_exists()
        
    def _ensure_config_exists(self) -> None:
        """Ensure the configuration file exists, create if it doesn't."""
        if not self.config_path.exists():
            self.config_path.write_text(json.dumps({}, indent=4))
            print(f"\tConfig file '{self.config_path}' created.")

    def read_config(self) -> Dict[str, Any]:
        """
        Read the entire configuration file.
        
        Returns:
            Dict containing configuration data
        """
        return json.loads(self.config_path.read_text())

    def write_config(self, config: Dict[str, Any]) -> None:
        """
        Write configuration to file.
        
        Args:
            config: Configuration dictionary to write
        """
        self.config_path.write_text(json.dumps(config, indent=4))

    def get_value(self, key: str, check_only: bool = False) -> Optional[Any]:
        """
        Get a configuration value, prompting for missing values if not check_only.
        
        Args:
            key: Configuration key to retrieve
            check_only: If True, only check for existence without prompting
            
        Returns:
            Configuration value or None if not found and check_only is True
        """
        config = self.read_config()
        value = config.get(key)
        
        if check_only or value is not None:
            return value
            
        if key == 'env_type':
            value = self._choose_environment_type()
        elif key == 'conda_path':
            value = self._prompt_conda_path()
        elif key == 'conda_env':
            value = self._prompt_conda_env()
        elif key == 'conda_env_folder':
            value = self._prompt_conda_env_folder()
        elif key == 'custom_nodes_path':
            value = self._prompt_custom_nodes_path()
        else:
            return None
        
        config[key] = value
        self.write_config(config)
        return value

    def set_value(self, key: str, value: Any) -> None:
        """
        Set a configuration value.
        
        Args:
            key: Configuration key to set
            value: Value to set
        """
        config = self.read_config()
        config[key] = value
        self.write_config(config)

    def _choose_environment_type(self) -> str:
        """
        Prompt user to choose environment type.
        
        Returns:
            Selected environment type ('venv' or 'conda')
        """
        while True:
            choice = input("Choose environment type (1 for venv, 2 for conda): ").strip()
            if choice == '1':
                return 'venv'
            elif choice == '2':
                return 'conda'
            elif choice.upper() == 'NO':
                sys.exit("Exiting script as requested.")
            print("Invalid choice. Please enter 1 for venv, 2 for conda, or 'NO' to exit.")

    def _prompt_conda_path(self) -> str:
        """Prompt user for conda executable path, checking default paths first."""
        def is_windows() -> bool:
            return os.name == 'nt'

        username = os.environ.get('USERNAME') if is_windows() else os.environ.get('USER')
        if not username:
            username = "user"

        default_paths = [
            fr"C:\ProgramData\Anaconda3\Scripts\conda.exe",
            fr"C:\ProgramData\miniconda3\Scripts\conda.exe",
            fr"C:\Users\{username}\Anaconda3\Scripts\conda.exe",
            fr"C:\Users\{username}\miniconda3\Scripts\conda.exe",
        ] if is_windows() else [
            fr"/home/{username}/anaconda3/bin/conda",
            fr"/home/{username}/miniconda3/bin/conda"
        ]
        default_paths = [path.format(username=username) for path in default_paths]
        existing_paths = [path for path in default_paths if os.path.exists(path)]
        
        if existing_paths:
            found_path = existing_paths[0]
            while True:
                choice = input(
                    f"Found conda at '{found_path}'. Use it? (Y/N, or enter new path): "
                ).strip().upper()
                if choice == 'Y' or choice == '':
                    return found_path
                elif choice == 'N':
                    break
                elif choice == 'NO':
                    sys.exit("Exiting script as requested.")
                else:
                    if os.path.exists(choice):
                        return choice
                    print("Invalid path. Please enter a valid path or choose Y/N.")
        
        default_example = default_paths[0]
        while True:
            path = input(f"Enter path to conda (default: {default_example}, or 'NO' to exit): ").strip().upper()
            if path == 'Y' or path == '':
                return default_example
            elif path == 'NO':
                sys.exit("Exiting script as requested.")
            elif os.path.exists(path):
                return path
            print("Invalid path. Please enter a valid path to conda executable.")

    def _prompt_conda_env(self) -> str:
        """Prompt user for conda environment name or path with easy default selection."""
        default_env = r"f:\ComfyUI\env"
        while True:
            choice = input(
                f"Conda environment: '{default_env}'. Use it? (Y/N, or enter new path): "
            ).strip().upper()
            if choice == 'Y' or choice == '':
                return default_env
            elif choice == 'N':
                break
            elif choice == 'NO':
                sys.exit("Exiting script as requested.")
            else:
                if os.path.exists(choice) or not os.path.isabs(choice):  # Allow environment names as non-absolute
                    return choice
                print("Invalid path or environment name. Please enter a valid value or choose Y/N.")
        
        while True:
            env = input(f"Enter conda environment name or path (default: {default_env}, or 'NO' to exit): ").strip().upper()
            if env == 'Y' or env == '':
                return default_env
            elif env == 'NO':
                sys.exit("Exiting script as requested.")
            elif os.path.exists(env) or not os.path.isabs(env):  # Allow environment names as non-absolute
                return env
            print("Invalid path or environment name. Please enter a valid value.")

    def _prompt_conda_env_folder(self) -> str:
        """Prompt user for conda environment folder with easy default selection."""
        default_folder = r"f:\ComfyUI\env"
        while True:
            choice = input(
                f"Conda environment folder: '{default_folder}'. Use it? (Y/N, or enter new path): "
            ).strip().upper()
            if choice == 'Y' or choice == '':
                return default_folder
            elif choice == 'N':
                break
            elif choice == 'NO':
                sys.exit("Exiting script as requested.")
            else:
                if os.path.exists(choice):
                    return choice
                print("Invalid path. Please enter a valid path or choose Y/N.")
        
        while True:
            folder = input(f"Enter conda environment folder (default: {default_folder}, or 'NO' to exit): ").strip().upper()
            if folder == 'Y' or folder == '':
                return default_folder
            elif folder == 'NO':
                sys.exit("Exiting script as requested.")
            elif os.path.exists(folder):
                return folder
            print("Invalid path. Please enter a valid directory path.")

    def _prompt_custom_nodes_path(self) -> str:
        """Prompt user for custom nodes path with easy default selection."""
        default_path = r"f:\ComfyUI\ComfyUI\custom_nodes"
        while True:
            choice = input(
                f"Custom nodes path: '{default_path}'. Use it? (Y/N, or enter new path): "
            ).strip().upper()
            if choice == 'Y' or choice == '':
                return default_path
            elif choice == 'N':
                break
            elif choice == 'NO':
                sys.exit("Exiting script as requested.")
            else:
                if os.path.exists(choice):
                    return choice
                print("Invalid path. Please enter a valid path or choose Y/N.")
        
        while True:
            path = input(f"Enter custom nodes path (default: {default_path}, or 'NO' to exit): ").strip().upper()
            if path == 'Y' or path == '':
                return default_path
            elif path == 'NO':
                sys.exit("Exiting script as requested.")
            elif os.path.exists(path):
                return path
            print("Invalid path. Please enter a valid directory path.")

    def get_environment_config(self) -> EnvironmentConfig:
        """
        Get complete environment configuration.
        
        Returns:
            EnvironmentConfig object containing all environment settings
        """
        config = self.read_config()
        return EnvironmentConfig(
            env_type=config.get('env_type', ''),
            venv_path=config.get('venv_path'),
            conda_path=config.get('conda_path'),
            conda_env=config.get('conda_env'),
            conda_env_folder=config.get('conda_env_folder'),
            project_path=config.get('project_path'),
            custom_nodes_path=config.get('custom_nodes_path')
        )

# Global configuration manager instance
config_manager = ConfigManager()
