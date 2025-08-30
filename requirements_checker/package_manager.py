"""
Package Manager Module

Handles package management operations including version checking, dependency resolution,
and package information retrieval from PyPI and local environment.
"""

import json
import os
import subprocess
import requests
from typing import Dict, List, Tuple, Optional, Any
from packaging import version as pkg_version
from colorama import Fore, Style
from dataclasses import dataclass

from .environment_manager import env_manager
from .config_manager import config_manager

@dataclass
class PackageInfo:
    """Data structure for package information."""
    name: str
    installed_version: Optional[str]
    latest_version: Optional[str]
    available_versions: List[str]
    dependencies: Dict[str, str]
    reverse_dependencies: List[Dict[str, str]]

class PackageManager:
    """Manages package-related operations and version checking."""

    def check_pipdeptree_installed(self) -> bool:
        """
        Check if pipdeptree is installed and install if missing.
        
        Returns:
            bool: True if pipdeptree is available, False otherwise
        """
        try:
            env_manager.run_in_environment(
                ['pipdeptree', '--version'],
                capture_output=True,
                check=True
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            print(Fore.YELLOW + "\tInstalling pipdeptree for dependency checking..." + Style.RESET_ALL)
            try:
                env_manager.run_in_environment(
                    ['pip', 'install', 'pipdeptree'],
                    check=True
                )
                return True
            except subprocess.CalledProcessError:
                print(Fore.RED + "\tFailed to install pipdeptree. Dependency checking will be limited." + Style.RESET_ALL)
                return False

    def get_installed_version(self, package_name: str) -> Optional[str]:
        """
        Get the installed version of a package.
        
        Args:
            package_name: Name of the package
            
        Returns:
            Optional[str]: Installed version or None if not installed
        """
        config = config_manager.read_config()
        env_type = config.get('env_type')
        
        try:
            if env_type == "conda":
                # env_name = config.get('conda_env_folder')
                env_folder = config.get('conda_env_folder')
                if env_folder:
                    python_executable = (
                        os.path.join(env_folder, 'python.exe') if env_manager.is_windows()
                        else os.path.join(env_folder, 'bin', 'python')
                    )
                    result = subprocess.run(
                        [python_executable, '-m', 'pip', 'show', package_name],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                else:
                    raise ValueError("Conda environment folder not specified")
                # python_executable = (
                #     f"{env_name}python.exe" if env_manager.is_windows()
                #     else f"{env_name}/bin/python"
                # )
            else:
                result = subprocess.run(
                    ['pip', 'show', package_name],
                    capture_output=True,
                    text=True,
                    check=True
                )

            for line in result.stdout.split('\n'):
                if line.startswith('Version:'):
                    return line.split(':', 1)[1].strip()
            return None
            
        except subprocess.CalledProcessError:
            print(Fore.YELLOW + f"\tWarning: Package '{package_name}' is not installed." + Style.RESET_ALL)
            return None
        except Exception as e:
            print(f"An error occurred: {e}")
            return None

    def get_latest_version(self, package_name: str) -> Optional[str]:
        """
        Get the latest version of a package from PyPI.
        
        Args:
            package_name: Name of the package
            
        Returns:
            Optional[str]: Latest version or None if not found
        """
        try:
            response = requests.get(f"https://pypi.org/pypi/{package_name}/json")
            response.raise_for_status()
            package_info = response.json()
            return package_info["info"]["version"]
        except requests.RequestException as e:
            print(f"Error while retrieving data from PyPI: {e}")
            return None

    def get_all_versions(self, package_name: str) -> List[str]:
        """
        Get all available versions of a package from PyPI.
        
        Args:
            package_name: Name of the package
            
        Returns:
            List[str]: List of available versions
        """
        try:
            response = requests.get(f"https://pypi.org/pypi/{package_name}/json")
            response.raise_for_status()
            package_info = response.json()
            versions = list(package_info["releases"].keys())
            # Filter valid versions only
            valid_versions = []
            for v in versions:
                try:
                    pkg_version.parse(v)
                    valid_versions.append(v)
                except pkg_version.InvalidVersion:
                    print(f"{Fore.YELLOW}\tWarning: Invalid version '{v}' for {package_name} skipped{Style.RESET_ALL}")
            return sorted(valid_versions, key=pkg_version.parse, reverse=True)
        except requests.RequestException as e:
            print(f"Error while retrieving versions for {package_name} from PyPI: {e}")
            return []
        
    def get_package_dependencies(self, package_name: str) -> Dict[str, str]:
        """
        Get all dependencies and their version constraints for a package.
        
        Args:
            package_name: Name of the package
            
        Returns:
            Dict[str, str]: Dictionary of dependencies and their versions
        """
        if not self.check_pipdeptree_installed():
            return {}
            
        try:
            result = env_manager.run_in_environment(
                ['pipdeptree', '-p', package_name, '--json-tree'],
                capture_output=True,
                text=True,
                check=True
            )
            
            try:
                dependencies = json.loads(result.stdout)
                if dependencies and isinstance(dependencies, list):
                    all_deps = {}
                    for pkg in dependencies:
                        if isinstance(pkg, dict):
                            for dep in pkg.get('dependencies', []):
                                name = dep.get('package_name')
                                version = dep.get('installed_version')
                                if name and version:
                                    all_deps[name] = version
                    return all_deps
                return {}
                
            except json.JSONDecodeError as e:
                print(Fore.YELLOW + f"\tWarning: Could not parse pipdeptree output: {str(e)}" + Style.RESET_ALL)
                return {}
                
        except subprocess.CalledProcessError as e:
            print(Fore.YELLOW + f"\tWarning: pipdeptree failed: {str(e)}" + Style.RESET_ALL)
            return {}

    def get_reverse_dependencies(self, package_name: str) -> List[Dict[str, str]]:
        """
        Get all packages that depend on the given package.
        
        Args:
            package_name: Name of the package
            
        Returns:
            List[Dict[str, str]]: List of reverse dependencies
        """
        if not self.check_pipdeptree_installed():
            print("Pipdeptree not installed!")
            return []
            
        try:
            result = env_manager.run_in_environment(
                ['pipdeptree', '--reverse', '--packages', package_name, '--json'],
                capture_output=True,
                text=True,
                check=True
            )
            
            try:
                dependencies = json.loads(result.stdout)
                reverse_deps = []
                for pkg in dependencies:
                    if isinstance(pkg, dict):
                        pkg_info = pkg.get('package', {})
                        if pkg_info.get('key') == package_name:
                            reverse_deps.append({
                                'package_name': pkg.get('dependencies')[0].get('package_name'),
                                'installed_version': pkg_info.get('installed_version'),
                                'required_version': pkg_info.get('required_version', '')
                            })
                return reverse_deps
                
            except json.JSONDecodeError:
                print("JSON decoding failed, falling back to text parsing")
                result = env_manager.run_in_environment(
                    ['pipdeptree', '--reverse', '--packages', package_name],
                    capture_output=True,
                    text=True,
                    check=True
                )
                return self._parse_pipdeptree_text_output(result.stdout)
                
        except subprocess.CalledProcessError as e:
            print(Fore.YELLOW + f"\tWarning: Could not get reverse dependencies for {package_name}: {str(e)}" + Style.RESET_ALL)
            return []

    def find_max_allowed_version(self, package_name: str, available_versions: List[str]) -> Tuple[Optional[str], Optional[str]]:
        """
        Find the maximum allowed version based on dependency constraints.
        
        Args:
            package_name: Name of the package
            available_versions: List of available versions
            
        Returns:
            Tuple[Optional[str], Optional[str]]: (max allowed version, limiting package)
        """
        if not available_versions:
            return None, None

        try:
            reverse_deps = self.get_reverse_dependencies(package_name)
            if not reverse_deps:
                return available_versions[0], None

            parsed_versions = [
                (pkg_version.parse(v), v) for v in available_versions
            ]
            parsed_versions.sort(key=lambda x: x[0], reverse=True)

            min_allowed = None
            max_allowed = parsed_versions[0][1]
            limiting_package = None

            for dep in reverse_deps:
                dep_name = dep.get('package_name', '')
                version_spec = dep.get('required_version', '')

                if version_spec and version_spec != "Any":
                    import re
                    constraints = re.findall(r'([><=!]+)\s*([\d.]+)', version_spec)

                    for op, ver in constraints:
                        parsed_ver = pkg_version.parse(ver)

                        if op in ('>', '>='):
                            if min_allowed is None or parsed_ver > min_allowed:
                                min_allowed = parsed_ver
                        elif op in ('<', '<='):
                            current_max = pkg_version.parse(max_allowed)
                            limiting_package = dep_name
                            if parsed_ver < current_max:
                                max_allowed = ver

            filtered_versions = [
                v for parsed_v, v in parsed_versions
                if (min_allowed is None or parsed_v >= min_allowed) and 
                   (pkg_version.parse(max_allowed) is None or parsed_v <= pkg_version.parse(max_allowed))
            ]

            if filtered_versions:
                return filtered_versions[0], limiting_package
            return None, limiting_package

        except Exception as e:
            print(Fore.YELLOW + f"\tWarning: Error checking version constraints: {str(e)}" + Style.RESET_ALL)
            return None, None

    def _parse_pipdeptree_text_output(self, output: str) -> List[Dict[str, str]]:
        """
        Parse pipdeptree text output when JSON parsing fails.
        
        Args:
            output: Text output from pipdeptree
            
        Returns:
            List[Dict[str, str]]: Parsed dependencies
        """
        dependencies = []
        lines = output.split('\n')
        current_package = None
        
        for line in lines:
            if not line.startswith(' '):
                if '=>' in line:
                    parts = line.split('=>')
                    current_package = {
                        'package_name': parts[0].strip(),
                        'installed_version': parts[1].strip() if len(parts) > 1 else '',
                        'required_version': ''
                    }
                    dependencies.append(current_package)
            elif current_package and '=>' in line:
                parts = line.split('=>')
                dependencies.append({
                    'package_name': parts[0].strip(),
                    'installed_version': parts[1].strip() if len(parts) > 1 else '',
                    'required_version': ''
                })
        
        return dependencies

    def get_package_info(self, package_name: str) -> PackageInfo:
        """
        Get comprehensive information about a package.
        
        Args:
            package_name: Name of the package
            
        Returns:
            PackageInfo: Complete package information
        """
        installed_version = self.get_installed_version(package_name)
        latest_version = self.get_latest_version(package_name)
        available_versions = self.get_all_versions(package_name)
        dependencies = self.get_package_dependencies(package_name)
        reverse_dependencies = self.get_reverse_dependencies(package_name)
        
        return PackageInfo(
            name=package_name,
            installed_version=installed_version,
            latest_version=latest_version,
            available_versions=available_versions,
            dependencies=dependencies,
            reverse_dependencies=reverse_dependencies
        )

# Global package manager instance
package_manager = PackageManager()
