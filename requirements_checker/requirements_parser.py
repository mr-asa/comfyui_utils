"""
Requirements Parser Module

This module provides functionality for parsing and processing Python package requirements files.
It includes functions for reading requirements files, normalizing package names, and handling
different requirement formats.
"""

import re
import json
import requests
from typing import Dict, List, Any, Union, Optional, OrderedDict
from dataclasses import dataclass
from packaging import version as pkg_version
from pathlib import Path


@dataclass
class PackageRequirement:
    """Structure for holding package requirement information."""
    name: str
    extras: Optional[str] = None
    operator: Optional[str] = None
    version: Optional[str] = None
    directory: Optional[str] = None

@dataclass
class GitRequirement:
    """Structure for holding git requirement information."""
    url: str
    directory: str

@dataclass
class IndexRequirement:
    """Structure for holding extra index URL information."""
    url: str
    directory: str

@dataclass
class DependencyInfo:
    """Structure for holding dependency information from pipdeptree."""
    package_name: str
    installed_version: str
    required_version: str = ''


class RequirementsParser:
    """Class for parsing and processing requirements files."""
    
    @staticmethod
    # def get_active_requirements(file_path: Path) -> List[str]:
    #     """
    #     Read and parse requirements from a file, returning only active (non-commented) requirements.
        
    #     Args:
    #         file_path: Path to the requirements file
            
    #     Returns:
    #         List of active requirements
    #     """
    #     active_requirements = []
    #     with open(file_path, 'r') as file:
    #         for line in file:
    #             line = line.strip()
    #             if line and not line.startswith("#"):
    #                 pairs = line.split(',')
    #                 gpackage = ""
    #                 for pair in pairs:
    #                     pair = pair.strip()
    #                     match = re.match(r'([\w-]+)(?:\[(.*?)\])?(?:([!><=]+)(\d+(?:\.\d+)*))?', pair)
    #                     if match:
    #                         package, dopPack, operator, version = match.groups()
    #                         if package == "git":
    #                             active_requirements.append(f"{package}+{pair[4:]}")
    #                         elif package == "--extra-index-url":
    #                             active_requirements.append(f"{package} {pair.rsplit(' ')[1]}")
    #                         else:
    #                             gpackage = package
    #                             active_requirements.append("".join([
    #                                 package.strip(),
    #                                 f"[{dopPack}]" if dopPack else "",
    #                                 operator if operator else "",
    #                                 version if version else ""
    #                             ]))
    #                     else:
    #                         active_requirements.append("".join([gpackage, pair]))
    #     return active_requirements
    def get_active_requirements(file_path: Path) -> List[str]:
        """
        Read and parse requirements from a file, returning only active (non-commented) requirements.
        
        Args:
            file_path: Path to the requirements file
            
        Returns:
            List of active requirements
        """
        active_requirements = []
        with open(file_path, 'r') as file:
            for line in file:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                # Обработка специальных случаев: git и --extra-index-url
                if line.startswith("git+"):
                    active_requirements.append(line)
                    continue
                elif line.startswith("--extra-index-url"):
                    active_requirements.append(line)
                    continue

                # Парсинг стандартных требований с поддержкой сложных спецификаторов
                # Пример: package[extra1,extra2]>=1.0,<2.0,!=1.5
                pattern = r'^([\w-]+)(?:\[([^\]]*)\])?(.*)$'
                match = re.match(pattern, line)
                if match:
                    package, extras, version_specs = match.groups()
                    requirement = package
                    if extras:
                        requirement += f"[{extras}]"

                    # Парсим все спецификаторы версий
                    if version_specs:
                        spec_pattern = r'([><=!~]+)(\d+(?:\.\d+)*)'
                        specs = re.findall(spec_pattern, version_specs)
                        if specs:
                            requirement += ",".join(f"{op}{ver}" for op, ver in specs)
                        else:
                            # Если спецификаторы не распознаны, добавляем как есть
                            requirement += version_specs.strip()

                    active_requirements.append(requirement)
                else:
                    # Если строка не соответствует шаблону, добавляем как есть
                    active_requirements.append(line)

        return active_requirements

    @staticmethod
    def normalize_package_name(package_name: str) -> str:
        """
        Normalize package name to handle different writing styles.
        
        Args:
            package_name: Name of the package to normalize
            
        Returns:
            Normalized package name
        """
        return package_name.lower().replace('-', '_')

    @staticmethod
    def get_canonical_package_name(package_name: str) -> str:
        """
        Get the canonical name of a package from PyPI.
        
        Args:
            package_name: Name of the package to look up
            
        Returns:
            Canonical package name from PyPI or original name if lookup fails
        """
        try:
            response = requests.get(f"https://pypi.org/pypi/{package_name}/json")
            if response.status_code == 200:
                return response.json()["info"]["name"]
            return package_name
        except:
            return package_name

    @classmethod
    def process_requirements_dict(cls, requirements_dict: Dict[str, Any]) -> OrderedDict:
        """
        Process and combine requirements with different name formats.
        
        Args:
            requirements_dict: Dictionary of requirements to process
            
        Returns:
            Processed and normalized requirements dictionary
        """
        normalized_dict = OrderedDict()
        
        # First pass: collect all variations of package names
        name_variations = {}
        for package_name in requirements_dict:
            if package_name not in ["git", "--extra-index-url"]:
                normalized_name = cls.normalize_package_name(package_name)
                if normalized_name not in name_variations:
                    name_variations[normalized_name] = []
                name_variations[normalized_name].append(package_name)
        
        # Second pass: combine requirements under canonical names
        for normalized_name, variations in name_variations.items():
            if len(variations) > 1:
                canonical_name = cls.get_canonical_package_name(variations[0])
                combined_requirements = []
                for variant in variations:
                    combined_requirements.extend(requirements_dict[variant])
                normalized_dict[canonical_name] = combined_requirements
            else:
                normalized_dict[variations[0]] = requirements_dict[variations[0]]
        
        # Add back non-package entries
        for key in ["git", "--extra-index-url"]:
            if key in requirements_dict:
                normalized_dict[key] = requirements_dict[key]
                
        return normalized_dict

    @staticmethod
    def parse_conditional_dependencies(dependency: str, directory: str) -> Dict[str, Any]:
        """
        Parse dependency string with conditions and version requirements.
        
        Args:
            dependency: Dependency string to parse
            directory: Directory containing the requirement
            
        Returns:
            Dictionary containing parsed dependency information
        """
        pattern = re.compile(r'^([\w-]+)(?:\[(.*?)\])?(?:([!><=]+)(\d+(?:\.\d+)*))?')
        match = pattern.match(dependency)
        if match:
            package_name = match.group(1)
            conditions = match.group(2)
            comparison = match.group(3)
            version = match.group(4)
            
            if package_name == "git":
                return {package_name: GitRequirement(url=dependency[4:], directory=directory)}
            elif package_name == "--extra-index-url":
                return {package_name: IndexRequirement(url=dependency.rsplit(" ")[1], directory=directory)}
            else:
                return {package_name: PackageRequirement(
                    name=package_name,
                    extras=conditions,
                    operator=comparison,
                    version=version,
                    directory=directory
                )}
        else:
            return {dependency: PackageRequirement(name=dependency, directory=directory)}

    @staticmethod
    def parse_pipdeptree_text_output(output: str) -> List[DependencyInfo]:
        """
        Parse pipdeptree text output when JSON parsing fails.
        
        Args:
            output: Text output from pipdeptree
            
        Returns:
            List of DependencyInfo objects containing parsed dependency information
        """
        dependencies = []
        lines = output.split('\n')
        current_package = None
        
        for line in lines:
            if not line.startswith(' '):
                if '=>' in line:
                    parts = line.split('=>')
                    current_package = DependencyInfo(
                        package_name=parts[0].strip(),
                        installed_version=parts[1].strip() if len(parts) > 1 else ''
                    )
                    dependencies.append(current_package)
            elif current_package and '=>' in line:
                parts = line.split('=>')
                dependencies.append(DependencyInfo(
                    package_name=parts[0].strip(),
                    installed_version=parts[1].strip() if len(parts) > 1 else ''
                ))
        
        return dependencies

    @staticmethod
    def parse_version(version_str: Union[str, None]) -> pkg_version.Version:
        """
        Enhanced version parser that handles non-standard formats.
        
        Args:
            version_str: Version string to parse
            
        Returns:
            Parsed version object
        """
        if not version_str:
            return pkg_version.parse('0')

        version_str = str(version_str)
        version_str = re.sub(r'^[vV]?er?\.?\s*', '', version_str)
        version_str = re.sub(r'[^0-9a-zA-Z\.\-]', '.', version_str)
        
        if re.match(r'^\d+[a-zA-Z]$', version_str):
            match = re.match(r'(\d+)([a-zA-Z])$', version_str)
            if match:
                num, letter = match.groups()
                version_str = f"{num}.0.{ord(letter.lower()) - ord('a') + 1}"
        
        try:
            return pkg_version.parse(version_str)
        except pkg_version.InvalidVersion:
            print(f"Warning: Could not parse version '{version_str}'. Using default version.")
            return pkg_version.parse('0')

    @staticmethod
    def sort_ordered_dict(input_ordered_dict: OrderedDict) -> OrderedDict:
        """
        Sort an ordered dictionary by its values.
        
        Args:
            input_ordered_dict: OrderedDict to sort
            
        Returns:
            Sorted OrderedDict
        """
        sorted_ordered_dict = OrderedDict()
        for key, values in input_ordered_dict.items():
            sorted_values = sorted(values, key=lambda x: x[-1] if x[-1] is not None else float('inf'))
            sorted_ordered_dict[key] = sorted_values
        return sorted_ordered_dict

    @staticmethod
    def combine_names(input_ordered_dict: OrderedDict) -> Dict[str, List[List[Any]]]:
        """
        Combine package names and their associated values.
        
        Args:
            input_ordered_dict: OrderedDict to process
            
        Returns:
            Dictionary with combined names and values
        """
        combined_values_dict = {}
        for key, values in input_ordered_dict.items():
            combined_values_dict[key] = []
            temp_dict = {}
            for sublist in values:
                sublist_key = tuple(sublist[:-1])
                if sublist_key not in temp_dict:
                    temp_dict[sublist_key] = []
                if sublist[-1] not in temp_dict[sublist_key]:
                    temp_dict[sublist_key].append(sublist[-1])

            for sublist_key, names in temp_dict.items():
                combined_values_dict[key].append(list(sublist_key) + [names])
                
        return combined_values_dict
