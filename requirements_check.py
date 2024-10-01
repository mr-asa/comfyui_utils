import os
import re
import sys
import json
import pathlib
import requests
import subprocess
from colorama import Fore, Style
from collections import OrderedDict


def is_windows():
    return os.name == 'nt'

def is_linux():
    return os.name == 'posix'

def choose_environment_type():
    while True:
        choice = input("Choose environment type (1 for venv, 2 for conda): ").strip()
        if choice == '1':
            return 'venv'
        elif choice == '2':
            return 'conda'
        elif choice.upper() == 'NO':
            sys.exit("Exiting script as requested.")
        print("Invalid choice. Please enter 1 for venv, 2 for conda, or 'NO' to exit.")

def read_from_config(key, check=False):

    # config_file = 'config.json'
    if not os.path.exists(config_file):
        # Create an empty config file if it does not exist
        with open(config_file, 'w') as f:
            json.dump({}, f, indent=4)
        print(f"\tConfig file '{config_file}' created.")

    with open(config_file, 'r') as f:
        config = json.load(f)
            
    value = config.get(key)

    if check:
        return value
    
    else:
        if key == 'env_type':
            if key not in config:
                value = choose_environment_type()
                config[key] = value
                with open(config_file, 'w') as f:
                    json.dump(config, f, indent=4)
                return value
            else:
                env_type = config.get(key)
            
            return env_type

        env_type = config.get('env_type')
        # print("\tenv_type = ",env_type)

        while True:
            # print("\tcollect other", env_type)
            if env_type == 'venv':
                if key == "venv_path":
                    if not value or not os.path.exists(value):
                        value = input("Enter venv path (or 'NO' to exit): ")
                        if value.upper() == 'NO':
                            sys.exit("Exiting script as requested.")
                        if os.path.exists(value):
                            value = os.path.join(value, 'Scripts', 'activate_this.py')
                        else:
                            print("\tInvalid venv path. Please try again.")
                            continue

            elif env_type == 'conda':
                # print("\tIt Is Conda!!!", key)
                if key in ["conda_path", "conda_env"]:
                    if not value:
                        if key == "conda_path":
                            value = get_conda_path()
                        elif key == "conda_env":
                            value = get_conda_env()
                        elif value.upper() == 'NO':
                            sys.exit("Exiting script as requested.")
                    # elif key == "conda_path" and not os.path.exists(value):
                    #     print("\tConda path not found. Please enter it again.")
                    #     value = get_conda_path()
                    # else:
                    #     return config.get(key)

            with open(config_file, 'r') as f:
                config = json.load(f)

            # print("\tkey in config.keys()",key in config.keys())
            if key not in config.keys():
                choice = input(f"Enter {key}: ")
                config[key] = choice

            # if value and (key not in ["project_path", "custom_nodes_path"] or os.path.exists(value)):
            #     config[key] = value
                with open(config_file, 'w') as f:
                    json.dump(config, f, indent=4)
                return choice
            # else:
            #     print(f"\tInvalid {key}. Please provide a valid value.")
            #     value = None  # Reset value to force re-entry
            else:
                return config.get(key)


        return value

def get_conda_path():
    # print("__get_conda_path__")
    choice = read_from_config("conda_path",check=True)
    print("choice",choice)

    with open(config_file, 'r') as f:
        config = json.load(f)

    if choice:
        return choice
    else:
        username = os.environ['USERNAME'] if is_windows() else os.environ['USER']
        
        default_paths = [
            fr"C:\ProgramData\Anaconda3\Scripts\conda.exe",
            fr"C:\ProgramData\miniconda3\Scripts\conda.exe",
            fr"C:\Users\{username}\Anaconda3\Scripts\conda.exe",
            fr"C:\Users\{username}\miniconda3\Scripts\conda.exe",
        ] if is_windows() else [
            fr"/home/{username}/anaconda3/bin/conda",
            fr"/home/{username}/miniconda3/bin/conda"
    ]
        existing_paths = [path for path in default_paths if os.path.exists(path)]
        
        if existing_paths:
            print("Choose conda.exe path:")
            for i, path in enumerate(existing_paths, 1):
                print(f"{i}. {path}")
            print(f"{len(existing_paths) + 1}. Enter custom path")
            choice = input("Enter your choice (or 'NO' to exit): ")
            
            if choice.upper() == 'NO':
                return 'NO'
            
            if choice.isdigit() and 1 <= int(choice) <= len(existing_paths):
                selected_path = existing_paths[int(choice) - 1]
                # Save selected path to config
                config['conda_path'] = selected_path
                with open(config_file, 'w') as f:
                    json.dump(config, f, indent=4)
                return selected_path
            elif choice == str(len(existing_paths) + 1):
                custom_path = input("Enter custom conda.exe path: ")
                # Save custom path to config
                config['conda_path'] = custom_path
                with open(config_file, 'w') as f:
                    json.dump(config, f, indent=4)
                return custom_path
            else:
                print("Invalid choice. Please try again.")
                # return get_conda_path()
        else:
            return input("Enter conda.exe path (or 'NO' to exit): ")

def get_conda_env():
    # print(">>> get_conda_env >>>")
    with open(config_file, 'r') as f:
        config = json.load(f)
    
    try:
        env_path = read_from_config("conda_env",check=True)
    
        if env_path:  
            print(f"Using existing conda environment: {env_path}")
            return env_path
        else:
            conda_path = get_conda_path()
            result = subprocess.run([conda_path, 'env', 'list'], capture_output=True, text=True)
            # print("result - ",result)
            env_list = [line.split()[0] for line in result.stdout.splitlines() if line.strip() and not line.startswith('#')]
            # print("env_list - ",env_list)

            if not env_list:
                print("No conda environments found.")
                return None
            
            print("Choose Conda environment:")
            for i, env in enumerate(env_list, 1):
                print(f"{i}. {env}")
            print(f"{len(env_list) + 1}. Enter custom environment name")
            choice = input("Enter your choice (or 'NO' to exit): ")
            
            if choice.upper() == 'NO':
                return 'NO'
            
            elif choice.isdigit() and 1 <= int(choice) <= len(env_list):
                # return env_list[int(choice) - 1]
                
                selected_env = env_list[int(choice) - 1]
                # Save selected path to config
                config['conda_env'] = selected_env
                config['conda_env_folder'] = os.path.join(os.path.dirname(os.path.dirname(conda_path)),
                    "envs",
                    selected_env
                    )
                
                with open(config_file, 'w') as f:
                    json.dump(config, f, indent=4)
                return selected_env

            
            elif choice == str(len(env_list) + 1):
                custom_path = input("Enter the path for the custom environment: ")
                env_list.append(custom_path)
                
                config['conda_env'] = custom_path
                config['conda_env_folder'] = custom_path
                with open(config_file, 'w') as f:
                    json.dump(config, f, indent=4)

                return custom_path
                
            else:
                print("Invalid choice. Please try again.")
                return get_conda_env()
    except subprocess.CalledProcessError:
        return input("Unable to list Conda environments. Enter environment name (or 'NO' to exit): ")

def get_active_requirements(file_path):
    # print("__get_active_requirements__",file_path)
    active_requirements = []
    with open(file_path, 'r') as file:
        for line in file:
            line = line.strip()
            # print("\t",line)
            if line and not line.startswith("#"):
                pairs = line.split(',')
                gpackage = ""
                for pair in pairs:
                    pair = pair.strip()
                    match = re.match(r'([\w-]+)(?:\[(.*?)\])?(?:([!><=]+)(\d+(?:\.\d+)*))?', pair)
                    if match:
                        package, dopPack, operator, version = match.groups()
                        if package == "git":
                            active_requirements.append(f"{package}+{pair[4:]}")
                        elif package == "--extra-index-url":
                            active_requirements.append(f"{package} {pair.rsplit(' ')[1]}")
                        else:
                            gpackage = package
                            active_requirements.append("".join([
                                package.strip(),
                                f"[{dopPack}]" if dopPack else "",
                                operator if operator else "",
                                version if version else ""
                            ]))
                    else:
                        active_requirements.append("".join([gpackage, pair]))
    return active_requirements

def get_installed_version(package_name):
    
    with open(config_file, 'r') as f:
        config = json.load(f)

    env_type = config.get('env_type')
    try:
        if env_type == "conda":
            env_name = config.get('conda_env_folder')

            if os.name == 'nt':  # Windows
                python_executable = f"{env_name}python.exe"  # Use backslash for Windows paths
            else:  # Linux or other OS
                python_executable = f"{env_name}/bin/python"  # Use forward slash for Linux paths            
            result = subprocess.run(
                [python_executable, '-m', 'pip', 'show', package_name], 
                capture_output=True,
                text=True,
                check=True
                )
        else:
            result = subprocess.run(
                ['pip', 'show', package_name], 
                capture_output=True,
                text=True,
                check=True
                )

        if result.returncode == 0:
            # print("__get_installed_version__",package_name, result.stdout)
            for line in result.stdout.split('\n'):
                if line.startswith('Version:'):
                    return line.split(':', 1)[1].strip()
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None

def get_latest_version(package_name):
    try:
        response = requests.get(f"https://pypi.org/pypi/{package_name}/json")
        response.raise_for_status()
        package_info = response.json()
        latest_version = package_info["info"]["version"]
        return latest_version
    except requests.RequestException as e:
        print(f"Error while retrieving data from PyPI: {e}")
        return None
    
def get_all_versions(package_name):
    try:
        result = subprocess.run(
            ['pip', 'index', 'versions', package_name], 
            # capture_output=True,
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            text=True,
            check=True,
            )
        
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if line.startswith('Available versions:'):
                    return line.split(':', 1)[1].strip()
        
        # return result
    except requests.RequestException as e:
        print(f"Error while retrieving data from PyPI: {e}")
        return None

def activate_virtual_environment():
    # config_file = 'config.json'
    with open(config_file, 'r') as f:
        config = json.load(f)

    env_type = config.get('env_type')

    if env_type == 'venv':
        venv_activate = read_from_config("venv_path")
        if os.path.exists(venv_activate):

            if is_windows():
                activate_script = os.path.join(venv_activate, 'Scripts', 'activate_this.py')
            else:
                activate_script = os.path.join(venv_activate, 'bin', 'activate_this.py')

            with open(activate_script) as f:
                code = compile(f.read(), activate_script, 'exec')
                exec(code, dict(__file__=activate_script))
            # Update sys.path accordingly
            venv_paths = os.environ['VIRTUAL_ENV'].split(os.pathsep)
            sys.path[:0] = [str(pathlib.Path(venv_activate).parent.parent)] + venv_paths
            path_to_cd = os.path.dirname(os.path.dirname(venv_activate))
            print(f"--> Strings to cmd if you use venv<--\n" + 
                Fore.BLUE + f"cd /d {path_to_cd}\ncall Scripts\\activate.bat\n\n" + Style.RESET_ALL)
        else:
            print("No valid venv path found.")

    elif env_type == 'conda':
        if activate_conda_environment():
            pass
        else:
            print("Failed to activate Conda environment.")
    else:
        print("No valid virtual environment type found.")

def activate_conda_environment():
    conda_path = read_from_config("conda_path")
    env_folder = read_from_config("conda_env_folder", check=True)
    env_name = read_from_config("conda_env")
    if not env_folder:
        env_folder = env_name
    
    

    if is_windows():
        activate_command = f'call "{conda_path}" && conda activate {env_name}'
        activate_commands_in_cmd = [
            f"set PATH=%PATH%;{conda_path}",
            f"call {conda_path} && conda activate {env_name} && cd /d {env_name}"
            ]
    else:
        activate_command = f'source "{os.path.dirname(conda_path)}/activate" {env_folder}'
        activate_commands_in_cmd = [
            f"export PATH=$PATH:{conda_path}",
            f"source {os.path.dirname(conda_path)}/activate {env_folder}",
            f"cd {env_folder}"
        ]

    activation_script = "\n".join(activate_commands_in_cmd)

    print(f"--> Commands to activate Conda environment <--\n" + 
          Fore.BLUE + activation_script + "\n" + Style.RESET_ALL)

    process = subprocess.Popen(activate_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ)
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        print(f"Error activating Conda environment: {stderr.decode('windows-1252')}")
    else:
        os.environ['PATH'] = os.pathsep.join([env_folder, os.environ['PATH']])
        print(Fore.GREEN + "\nConda environment activated successfully." + Style.RESET_ALL)

    return process.returncode == 0


def parse_conditional_dependencies(dependency, directory):
    pattern = re.compile(r'^([\w-]+)(?:\[(.*?)\])?(?:([!><=]+)(\d+(?:\.\d+)*))?')
    match = pattern.match(dependency)
    if match:
        package_name = match.group(1)
        conditions = match.group(2)
        comparison = match.group(3)
        version = match.group(4)
        if package_name == "git":
            packages = {package_name: [None, "+", dependency[4:], directory]}
        elif package_name == "--extra-index-url":
            packages = {package_name: [None, " ", dependency.rsplit(" ")[1], directory]}
        else:
            packages = {package_name: [f"[{conditions}]" if conditions else conditions, comparison, version, directory]}
        return packages
    else:
        return [dependency, directory]

def sort_ordered_dict(input_ordered_dict):
    sorted_ordered_dict = OrderedDict()
    for key, values in input_ordered_dict.items():
        sorted_values = sorted(values, key=lambda x: x[-1] if x[-1] is not None else float('inf'))
        sorted_ordered_dict[key] = sorted_values
    return sorted_ordered_dict

def combine_names(input_ordered_dict):

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

def parse_version(version_str):
    # Разделение на части: основная версия и любые постфиксы
    parts = re.split(r'[\.\-]', version_str)
    version_tuple = []
    for part in parts:
        if part.isdigit():
            version_tuple.append(int(part))
        else:
            # Для буквенных частей добавим кортеж с числом и самой строкой
            version_tuple.append((part,))
    return tuple(version_tuple)

def main():
    global config_file
    config_file = 'config.json'


    try:
        # print("read_from_config")
        read_from_config('env_type')  # This will ensure env_type is set
        requirements_dict = OrderedDict()
        # print("activate_virtual_environment")

        print("--> Some useful commands <--\n" +
            "check package info - " + Fore.BLUE + 
                "pip show <PACKAGE_NAME>\n" + Style.RESET_ALL + 
            "get all available versions of package - " + Fore.BLUE + 
                "pip index versions <PACKAGE_NAME>\n" + Style.RESET_ALL +
            "remove all items from the cache - " + Fore.BLUE + 
                "pip cache purge\n" + Style.RESET_ALL
            
            )
        
        activate_virtual_environment()
        directory = read_from_config("custom_nodes_path")
        # print("directory",directory)
        root, dirs, files = next(os.walk(directory))
        # print(root, dirs, files)
        root = os.path.abspath(root)
        dirs.append(os.path.dirname(root))

        for dir in dirs:
            # print("dir",dir)
            subroot, subdirs, subfiles = next(os.walk(os.path.join(root, dir)))
            # print("\t",subroot, subdirs, subfiles)
            for file in subfiles:
                if file == 'requirements.txt':
                    file_path = os.path.join(root, dir, file)
                    # folder = file_path.split(os.path.abspath(directory))[-1].split("\\")[1]
                    folder = os.path.basename(os.path.abspath(directory))
                    active_requirements = get_active_requirements(file_path)
                    for requirement in active_requirements:
                        packages = parse_conditional_dependencies(requirement, folder)
                        for package in packages:
                            if package not in requirements_dict:
                                requirements_dict[package] = [packages[package]]
                            else:
                                requirements_dict[package].append(packages[package])

        sorted_ordered_dict = sort_ordered_dict(requirements_dict)
        # print("sorted_ordered_dict",sorted_ordered_dict)
        result_ordered_dict = combine_names(sorted_ordered_dict)
        # print("result_ordered_dict",result_ordered_dict)
        packages = sorted([i for i in result_ordered_dict], key=str.lower)

        for package_name in packages:
            if package_name in ["git", "--extra-index-url"]:
                print(Fore.GREEN + "\nCustom " + Style.RESET_ALL)
                values = result_ordered_dict[package_name]
                for i in values:
                    print(Fore.BLUE + f"\t{package_name}{i[1]}{i[2]}" + Style.RESET_ALL + f" in {i[3]}")
            else:
                # if package_name in ['numpy']:
                print(Fore.GREEN + "\n" + package_name + Style.RESET_ALL)
                values = result_ordered_dict[package_name]
                values_sorted = sorted(values, key=lambda x: x[2] if x[2] is not None else '')

                state_of_package = "any"
                versions = []
                installed_version = get_installed_version(package_name)
                latest_version = get_latest_version(package_name)


                for i in values_sorted:
                    installable = f"{i[1] if i[1] else ''}{i[2] if i[2] else 'Any'}" 
                    print("\t" + installable + f" in {i[-1]}")
                    
                    if i[1]:
                        if "<" in i[1]:
                            if not versions:
                                versions = get_all_versions(package_name).split(", ")
                                # print(versions)

                            versions = [v for v in versions if parse_version(v) <= parse_version(i[2])]
                            if i[1] == "<":
                                if i[2] in versions:
                                    versions.remove(i[2])
                            state_of_package = f"{i[1]}{i[2]}"
                        elif "==" in i[1]:
                            state_of_package = f"{i[1]}{i[2]}"
                            versions = [i[2]]
                        # else:
                        #     versions = [latest_version]
                        #     pass
                            
                    
                    # print(Fore.BLUE + "\t" + installable + Style.RESET_ALL + 
                    #     f" in {i[-1]} - {Fore.YELLOW}{installed_version}{Style.RESET_ALL} installed - {Fore.RED}{latest_version}{Style.RESET_ALL} last version")
                    # print("installed_version,latest_version",installed_version,latest_version,installed_version==latest_version)



                if not installed_version:
                    print(Fore.RED + "\tNone" + 
                        Fore.CYAN + f" pip install {package_name}{i[0] if i[0] else ''}=={latest_version}" +
                        Style.RESET_ALL )
                    # values = result_ordered_dict[package_name]
                elif installed_version == latest_version or (versions and installed_version == versions[0]):
                    print(f"\tYou have a latest {installed_version} version")
                else:
                    if not versions:
                        versions = [latest_version]

                    print(
                        Fore.YELLOW + f"\tCan updated from {installed_version} to {versions[0]}" + 
                        Fore.CYAN + f" pip install {package_name}=={versions[0]}" + Style.RESET_ALL
                        )
                    if state_of_package == "any":
                        print(
                            Fore.YELLOW + "\tOr update to the latest by command " +
                            Fore.CYAN + f"pip install --upgrade {package_name}" + Style.RESET_ALL
                            )
                    # print(f"installed_version")
                    
                    # break






    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == '__main__':
    main()

input("\nPress Enter to exit...")