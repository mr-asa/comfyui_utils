import os
import re
import glob
import requests
import subprocess
import importlib.metadata
from colorama import init, Fore, Style
from collections import OrderedDict


# Функция для получения списка активных требований из файла requirements.txt
def get_active_requirements(file_path):
    active_requirements = []
    with open(file_path, 'r') as file:
        for line in file:
            line = line.strip()
            # print("\tget_active_requirements line",line)
            if line and not line.startswith("#"):  # Игнорировать пустые строки и закомментированные строки
                # Разделение строки на отдельные пары "пакет-оператор-версия"
                pairs = line.split(',')
                gpackage = ""
                for pair in pairs:
                    pair = pair.strip()
                    # Поиск паттернов операторов сравнения и разделение строки на имя пакета и версию
                    match = re.match(r'([\w-]+)(?:\[(.*?)\])?(?:([!><=]+)(\d+(?:\.\d+)*))?', pair)
                    # print("\tget_active_requirements ","pair",pair, match)
                    if match:  
                        # print("\tget_active_requirements match.groups() ",match.groups())
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
                        active_requirements.append("".join([gpackage,pair]))
    return active_requirements


def get_installed_version(package_name):
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def get_latest_version(package_name):
    try:
        # print("\tget_latest_version(package_name)",package_name)
        response = requests.get(f"https://pypi.org/pypi/{package_name}/json")
        response.raise_for_status()  # Проверка наличия ошибок HTTP
        package_info = response.json()
        latest_version = package_info["info"]["version"]
        return latest_version
    except requests.RequestException as e:
        print(f"Ошибка при получении данных с PyPI: {e}")
        return None


# Функция для активации виртуального окружения
def activate_virtual_environment():
    activate_script = r'F:\AUTOMATIC18\venv\Scripts\activate.bat'
    subprocess.run([activate_script], shell=True)

# Функция для разбора условных зависимостей и получения всех пакетов
def parse_conditional_dependencies(dependency,directory):
    # print("\t\tparse_conditional_dependencies(dependency)",dependency)
    pattern = re.compile(r'^([\w-]+)(?:\[(.*?)\])?(?:([!><=]+)(\d+(?:\.\d+)*))?')
    match = pattern.match(dependency)
    if match:  
        package_name = match.group(1)
        # print("\t\tpackage_name",package_name)
        conditions = match.group(2)
        # print("\t\tconditions",conditions)
        comparison = match.group(3)
        # print("\t\tcomparison",comparison)
        version = match.group(4)
        # print("\t\tversion",version)
        # if comparison == "==":
        #     packages = [f"{package_name}[{conditions}]=={version}"]
        # else:
        #     packages = [f"{package_name}[{conditions}]{comparison}{version}"]
        # packages = [f"{package_name}[{conditions}]{comparison}{version}"]
        if package_name == "git":
            packages = {package_name:[None,"+",dependency[4:],directory]}
        elif package_name == "--extra-index-url":
            packages = {package_name:[None," ",dependency.rsplit(" ")[1],directory]}
        else:
            packages = {package_name:[f"[{conditions}]" if conditions else conditions,comparison,version,directory]}
        return packages
    else:
        return [dependency,directory]

def sort_ordered_dict(input_ordered_dict):
    sorted_ordered_dict = OrderedDict()
    
    for key, values in input_ordered_dict.items():
        sorted_values = sorted(values, key=lambda x: x[-1] if x[-1] is not None else float('inf'))
        sorted_ordered_dict[key] = sorted_values
    
    return sorted_ordered_dict

def sort_nested_lists(lst):
    for i, item in enumerate(lst):
        # print("sort_nested_lists i, item",i, item)

        if isinstance(item[-1], list):
            item[-1] = sorted(item[-1])
    return(lst)

def combine_names(input_ordered_dict):
    # Создаем словарь для хранения уникальных имен для каждого ключа
    unique_names_dict = {}

    # Находим все уникальные имена и объединяем их в словарь
    for key, values in input_ordered_dict.items():
        unique_names_dict[key] = set()  # Создаем пустое множество для каждого ключа
        for sublist in values:
            name = sublist[-1]  # Получаем имя из последнего элемента вложенного списка
            if name is not None:  # Проверяем, что имя не равно None
                unique_names_dict[key].add(name)

    # Создаем словарь для хранения объединенных значений
    combined_values_dict = {}

    # Обновляем вложенные списки, объединяя строки с одинаковыми первыми тремя элементами
    for key, values in input_ordered_dict.items():
        combined_values_dict[key] = []
        temp_dict = {}
        for sublist in values:
            sublist_key = tuple(sublist[:-1])  # Преобразуем список в кортеж для использования в качестве ключа
            if sublist_key not in temp_dict:
                temp_dict[sublist_key] = []
            temp_dict[sublist_key].append(sublist[-1])

        for sublist_key, names in temp_dict.items():
            combined_values_dict[key].append(list(sublist_key) + [names])

    return combined_values_dict


def main():
    try:

        # Словарь для хранения требований
        requirements_dict = OrderedDict()

        # Активируем виртуальное окружение
        activate_virtual_environment()

        # Обход всех каталогов и поиск файлов requirements.txt
        directory = r'f:/ComfyUI/custom_nodes'
        root, dirs, files = next(os.walk(directory))
        for dir in dirs:
            # print(dir)
            # Получаем информацию о подкаталоге
            subroot, subdirs, subfiles = next(os.walk(os.path.join(root, dir)))
        # for root, dirs, files in os.walk(directory):
            for file in subfiles:
                if file == 'requirements.txt':
                    file_path = os.path.join(root, dir, file)
                    # print("-->file_path",file_path)
                    folder = file_path.split(directory)[-1].split("\\")[1]
                    active_requirements = get_active_requirements(file_path)
                    # print("-->active_requirements",active_requirements)
                    for requirement in active_requirements:
                        packages = parse_conditional_dependencies(requirement,folder)
                        # print("\t\t-->packages:",packages)
                        for package in packages:
                            # print("\t\t\t-->package",package)

                        #     package_name, *_ = package.split('==')
                            if package not in requirements_dict:
                                requirements_dict[package] = [packages[package]]
                            else:
                                requirements_dict[package].append(packages[package])



        # for key, value in requirements_dict.items():
        #     requirements_dict[key] = [sublist for sublist in value if sublist != [None, None, None]]

        # print("-->requirements_dict\n",requirements_dict)

        sorted_ordered_dict = sort_ordered_dict(requirements_dict)

        result_ordered_dict = combine_names(sorted_ordered_dict)

        # result_ordered_dict = sort_ordered_dict(sorted_ordered_dict)


        # print("--> result_ordered_dict ",result_ordered_dict)

        # # Удаление дубликатов и вывод требований
        # requirements_list = list(requirements_dict.values())
        # print("Список требований:")

        packages = sorted([i for i in result_ordered_dict], key=str.lower)
        for package_name in packages:
            if package_name in ["git","--extra-index-url"]:
                print(Fore.GREEN + "\nCustom " + Style.RESET_ALL)
                values = (result_ordered_dict[package_name])
                for i in values:
                    print(Fore.BLUE + f"\t{package_name}{i[1]}{i[2]}" + Style.RESET_ALL + f" in {i[3]}")
                # print("GIT")
                # pass
            # elif package_name == "--extra-index-url":
            #     print("EXTRA")
            #     values = (result_ordered_dict[package_name])
            #     for i in values:
            #         print(Fore.BLUE + f"\t{package_name}{i[1]}{i[2]}" + Style.RESET_ALL + f" in {i[3]}")
            #     pass
            else:
                print(Fore.GREEN + "\n" + package_name + Style.RESET_ALL)

                values = (result_ordered_dict[package_name])
                # for i in values:
                #     print(f"\t{i[0]+' ' if i[0] else ''}{i[1]+i[2]+' ' if i[1] else 'any version '}in {i[3]}")

                # def custom_sort_key(item):
                #     print("custom_sort_key",item)
                #     version = item[2]
                #     if not version:
                #         return float('inf')  # Поместить строки, начинающиеся с "any" в конец
                #     return version

                # values_sorted = sorted(values, key=custom_sort_key)

                values_sorted = sorted(values, key=lambda x: x[2] if x[2] is not None else '')


                for i in values_sorted:
                    print(f"\t{i[0] if i[0] else ''}{i[1]+i[2]+' ' if i[1] else 'any version '}in {i[3]}")


                installed_version = get_installed_version(package_name)
                latest_version = get_latest_version(package_name)
                # print("installed_version,latest_version",installed_version,latest_version,installed_version==latest_version)

                if not installed_version:
                    print(Fore.RED + "\tNone" + 
                        Fore.CYAN + f" pip install {package_name}{i[0] if i[0] else ''}=={latest_version}" +
                        Style.RESET_ALL )
                    values = result_ordered_dict[package_name]

                elif installed_version == latest_version:
                    print(f"\tYou hav a latest {installed_version} version")
                else:
                    print(Fore.YELLOW + f"\tCan updated from {installed_version} to {latest_version}" + 
                        Fore.CYAN + f" pip install {package_name}=={latest_version}" +
                        Style.RESET_ALL + " or update " +
                        Fore.CYAN + f"pip install --upgrade {package_name}" + 
                        Style.RESET_ALL
                        )
                    # print(f"installed_version")



        # # Проверка установленных и актуальных версий
        # print("\nПроверка установленных и актуальных версий:")
        # for requirement in requirements_list:
        #     package_name, package_version = requirement.split('==')
        #     installed_version = get_installed_version(package_name)
        #     if installed_version:
        #         print(f"{package_name}: Установленная версия - {installed_version}")
        #     latest_version = get_latest_version(package_name)
        #     if latest_version:
        #         print(f"{package_name}: Актуальная версия - {latest_version}")

    except Exception as e:
        print(f"An error occurred: {e}")



# Вызываем основную функцию
if __name__ == "__main__":
    main()


input("\nPress Enter to exit...")