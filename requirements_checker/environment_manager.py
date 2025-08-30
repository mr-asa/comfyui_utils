"""
Environment Manager Module

Handles all virtual environment operations including detection, activation,
and management of both venv and conda environments.
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import Optional, List, Tuple, Any
from dataclasses import dataclass

from .config_manager import config_manager
from colorama import Fore, Style

@dataclass
class EnvironmentInfo:
    """Data structure for environment information."""
    env_type: str
    python_path: str
    env_path: str
    activation_commands: List[str]

class EnvironmentManager:
    """Manages virtual environment operations."""
    
    @staticmethod
    def is_windows() -> bool:
        """Check if the current system is Windows."""
        return os.name == 'nt'

    @staticmethod
    def is_linux() -> bool:
        """Check if the current system is Linux/Unix."""
        return os.name == 'posix'

    def get_python_executable(self) -> str:
        """
        Get the correct Python executable based on the environment.
        
        Returns:
            str: Path to the Python executable
        """
        config = config_manager.read_config()
        env_type = config.get('env_type')
        
        if env_type == "conda":
            env_folder = config.get('conda_env_folder')
            if env_folder:
                if self.is_windows():
                    return os.path.join(env_folder, 'python.exe')
                else:
                    return os.path.join(env_folder, 'bin', 'python')
        
        return sys.executable

    def run_in_environment(self, cmd: List[str], **kwargs: Any) -> subprocess.CompletedProcess:
        """
        Run a command in the correct environment.
        
        Args:
            cmd: Command to run as list of strings
            **kwargs: Additional arguments for subprocess.run
            
        Returns:
            subprocess.CompletedProcess: Result of the command execution
        """
        python_exe = self.get_python_executable()
        if python_exe != sys.executable:
            if cmd[0] == '-m':
                cmd = cmd[1:]
            cmd = [python_exe, '-m'] + cmd
        else:
            cmd = [python_exe] + cmd
        
        return subprocess.run(cmd, **kwargs)

    def get_conda_path(self) -> Optional[str]:
        """
        Get the path to conda executable.
        
        Returns:
            Optional[str]: Path to conda executable or None if not found
        """
        choice = config_manager.get_value("conda_path", check_only=True)
        
        if choice:
            return choice
            
        username = os.environ['USERNAME'] if self.is_windows() else os.environ['USER']
        
        default_paths = [
            fr"C:\ProgramData\Anaconda3\Scripts\conda.exe",
            fr"C:\ProgramData\miniconda3\Scripts\conda.exe",
            fr"C:\Users\{username}\Anaconda3\Scripts\conda.exe",
            fr"C:\Users\{username}\miniconda3\Scripts\conda.exe",
        ] if self.is_windows() else [
            fr"/home/{username}/anaconda3/bin/conda",
            fr"/home/{username}/miniconda3/bin/conda"
        ]
        
        existing_paths = [path for path in default_paths if os.path.exists(path)]
        
        if existing_paths:
            print("Choose conda.exe path:")
            for i, path in enumerate(existing_paths, 1):
                print(f"{i}. {path}")
            print(f"{len(existing_paths) + 1}. Enter custom path")
            
            while True:
                choice = input("Enter your choice (or 'NO' to exit): ")
                
                if choice.upper() == 'NO':
                    return None
                
                if choice.isdigit():
                    choice_num = int(choice)
                    if 1 <= choice_num <= len(existing_paths):
                        selected_path = existing_paths[choice_num - 1]
                        config_manager.set_value('conda_path', selected_path)
                        return selected_path
                    elif choice_num == len(existing_paths) + 1:
                        custom_path = input("Enter custom conda.exe path: ")
                        config_manager.set_value('conda_path', custom_path)
                        return custom_path
                
                print("Invalid choice. Please try again.")
        else:
            custom_path = input("Enter conda.exe path (or 'NO' to exit): ")
            if custom_path.upper() == 'NO':
                return None
            config_manager.set_value('conda_path', custom_path)
            return custom_path

    def get_conda_env(self) -> Optional[str]:
        """
        Get the conda environment name.
        
        Returns:
            Optional[str]: Name of the conda environment or None if not found
        """
        env_path = config_manager.get_value("conda_env", check_only=True)
    
        if env_path:  
            print(f"Using existing conda environment: {env_path}")
            return env_path
            
        conda_path = self.get_conda_path()
        if not conda_path:
            return None
            
        try:
            result = subprocess.run(
                [conda_path, 'env', 'list'], 
                capture_output=True, 
                text=True, 
                check=True
            )
            
            env_list = [
                line.split()[0] for line in result.stdout.splitlines() 
                if line.strip() and not line.startswith('#')
            ]
            
            if not env_list:
                print("No conda environments found.")
                return None
            
            print("Choose Conda environment:")
            for i, env in enumerate(env_list, 1):
                print(f"{i}. {env}")
            print(f"{len(env_list) + 1}. Enter custom environment name")
            
            while True:
                choice = input("Enter your choice (or 'NO' to exit): ")
                
                if choice.upper() == 'NO':
                    return None
                
                if choice.isdigit():
                    choice_num = int(choice)
                    if 1 <= choice_num <= len(env_list):
                        selected_env = env_list[choice_num - 1]
                        env_folder = os.path.join(
                            os.path.dirname(os.path.dirname(conda_path)),
                            "envs",
                            selected_env
                        )
                        
                        config_manager.set_value('conda_env', selected_env)
                        config_manager.set_value('conda_env_folder', env_folder)
                        return selected_env
                    elif choice_num == len(env_list) + 1:
                        custom_env = input("Enter custom environment name: ")
                        config_manager.set_value('conda_env', custom_env)
                        config_manager.set_value('conda_env_folder', custom_env)
                        return custom_env
                
                print("Invalid choice. Please try again.")
                
        except subprocess.CalledProcessError:
            custom_env = input("Unable to list Conda environments. Enter environment name (or 'NO' to exit): ")
            if custom_env.upper() == 'NO':
                return None
            config_manager.set_value('conda_env', custom_env)
            return custom_env

    # def activate_conda_environment(self) -> bool:
    #     """
    #     Activate the conda environment.
        
    #     Returns:
    #         bool: True if activation was successful, False otherwise
    #     """
    #     conda_path = config_manager.get_value("conda_path")
    #     env_folder = config_manager.get_value("conda_env_folder", check_only=True)
    #     env_name = config_manager.get_value("conda_env")
        
    #     if not env_folder:
    #         env_folder = env_name
        
    #     if self.is_windows():
    #         activate_command = f'call "{conda_path}" && conda activate {env_name}'
    #         activate_commands = [
    #             f"set PATH=%PATH%;{conda_path}",
    #             f"call {conda_path} && conda activate {env_name} && cd /d {env_name}"
    #         ]
    #     else:
    #         activate_command = f'source "{os.path.dirname(conda_path)}/activate" {env_folder}'
    #         activate_commands = [
    #             f"export PATH=$PATH:{conda_path}",
    #             f"source {os.path.dirname(conda_path)}/activate {env_folder}",
    #             f"cd {env_folder}"
    #         ]

    #     print(f"--> Commands to activate Conda environment <--\n" + 
    #           Fore.BLUE + "\n".join(activate_commands) + "\n" + Style.RESET_ALL)

    #     process = subprocess.Popen(
    #         activate_command,
    #         shell=True,
    #         stdout=subprocess.PIPE,
    #         stderr=subprocess.PIPE,
    #         env=os.environ
    #     )
    #     stdout, stderr = process.communicate()
        
    #     if process.returncode != 0:
    #         print(f"Error activating Conda environment: {stderr.decode('utf-8', errors='replace')}")
    #         return False
            
    #     os.environ['PATH'] = os.pathsep.join([env_folder, os.environ['PATH']])
    #     print(Fore.GREEN + "\nConda environment activated successfully." + Style.RESET_ALL)
    #     return True

    def activate_conda_environment(self) -> bool:
        """
        Activate the conda environment.
        
        Returns:
            bool: True if activation was successful, False otherwise
        """
        from .utils import colors, print_section_header, format_command  # Add required imports

        conda_path = config_manager.get_value("conda_path")
        env_folder = config_manager.get_value("conda_env_folder", check_only=True)
        env_name = config_manager.get_value("conda_env")
        
        if not env_folder:
            env_folder = env_name
        
        if not os.path.exists(conda_path):
            print(f"Error: Conda executable not found at {conda_path}")
            return False
        
        if not os.path.exists(env_folder):
            print(f"Error: Conda environment folder not found at {env_folder}")
            return False

        if self.is_windows():
            activate_commands = [
                f"set PATH=%PATH%;{os.path.dirname(conda_path)}",
                f"call \"{conda_path}\" && conda activate \"{env_name}\" && cd /d \"{env_name}\""
            ]
        else:
            activate_commands = [
                f"export PATH=$PATH:{os.path.dirname(conda_path)}",
                f"source \"{os.path.dirname(conda_path)}/activate\" \"{env_folder}\"",
                f"cd \"{env_folder}\""
            ]

        # Print commands in a unified style
        print_section_header("Commands to activate Conda environment")
        for cmd in activate_commands:
            print(f"{format_command(cmd)}")

        # Build path to Python in the environment
        python_path = os.path.join(env_folder, 'python.exe' if self.is_windows() else 'bin/python')
        if not os.path.exists(python_path):
            print(f"Error: Python executable not found at {python_path}")
            return False

        # Update PATH
        conda_bin = os.path.dirname(conda_path)
        os.environ['PATH'] = os.pathsep.join([conda_bin, env_folder, os.environ['PATH']])
        
        # Update sys.executable
        sys.executable = python_path
        
        print(Fore.GREEN + f"\nConda environment activated successfully: {env_folder}" + Style.RESET_ALL)
        return True

    def activate_virtual_environment(self) -> None:
        """Activate the appropriate virtual environment based on configuration."""
        config = config_manager.read_config()
        env_type = config.get('env_type')

        if env_type == 'venv':
            venv_path = config_manager.get_value("venv_path")
            if os.path.exists(venv_path):
                # if self.is_windows():
                #     activate_script = os.path.join(venv_path, 'Scripts', 'activate_this.py')
                # else:
                #     activate_script = os.path.join(venv_path, 'bin', 'activate_this.py')

                if self.is_windows():
                    activate_command = os.path.join(venv_path, 'Scripts', 'activate.bat')
                    print(f"Run in terminal: {activate_command}")
                else:
                    activate_command = f"source {os.path.join(venv_path, 'bin', 'activate')}"
                    print(f"Run in terminal: {activate_command}")

                with open(activate_command) as f:
                    code = compile(f.read(), activate_command, 'exec')
                    exec(code, dict(__file__=activate_command))
                
                venv_paths = os.environ['VIRTUAL_ENV'].split(os.pathsep)
                sys.path[:0] = [str(Path(venv_path).parent.parent)] + venv_paths
                path_to_cd = os.path.dirname(os.path.dirname(venv_path))
                
                print(f"--> Commands for venv activation <--\n" + 
                    Fore.BLUE + f"cd /d {path_to_cd}\ncall Scripts\\activate.bat\n\n" + Style.RESET_ALL)
            else:
                print("No valid venv path found.")

        elif env_type == 'conda':
            if not self.activate_conda_environment():
                print("Failed to activate Conda environment.")
        else:
            print("No valid virtual environment type found.")

# Global environment manager instance
env_manager = EnvironmentManager()
