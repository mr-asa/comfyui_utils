import os
import git
from colorama import init, Fore, Style

# Инициализация colorama
init()

# Функция для проверки наличия репозитория GitHub в каталоге
def check_github_repo(directory):
    try:
        # Проверяем, есть ли папка .git (репозиторий) в указанном каталоге
        git_folder = os.path.join(directory, '.git')
        if os.path.exists(git_folder):
            # Если .git существует, возвращаем путь к репозиторию
            return git_folder
        else:
            return None
    except Exception as e:
        print(f"Error checking GitHub repository: {e}")
        return None

# Функция для получения информации о состоянии репозитория
def get_repository_status(directory):
    try:
        repo = git.Repo(directory)

        print("\t"+repo.remotes[0].url)

        current_branch = repo.active_branch

        tracking_branch = current_branch.tracking_branch()
        if tracking_branch is None:
            # Устанавливаем отслеживаемую ветку для текущей ветки
            repo.git.branch('--set-upstream-to', f'origin/{current_branch.name}', current_branch.name)


        repo.remotes.origin.fetch()

        github_commits = list(repo.iter_commits(f'origin/{current_branch.name}'))
        github_commits.reverse()
        # print(github_commits)

        local_commits = {commit.hexsha for commit in repo.iter_commits(current_branch.name)}
        # local_commits = sorted(repo.iter_commits(current_branch.name), key=lambda x: x.authored_datetime, reverse=True)

        files_edited = False

        for commit in github_commits:
            # print(commit.authored_datetime)
            if commit.hexsha not in local_commits:
                print(Fore.BLUE + f"--> {commit.authored_datetime}\n{commit.message}".rstrip('\n') + Style.RESET_ALL)
                files_edited = True

        if files_edited:
            print()
            result = repo.git.pull()
            print(result)

    except Exception as e:
        print(f"Error getting repository status: {e}")

# Основная функция для обхода каталогов и выполнения проверок
def main():
    try:
    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # 
        # Указываем путь к каталогу, в котором находятся каталоги с репозиториями
        # У меня лежит в каталоге "f:\ComfyUI\self_write\comfyui_utils\" поэтому можно брать путь такой
        base_directory = os.path.realpath(__file__).rsplit("\\",3)[0]+"\\custom_nodes"
        # base_directory = "f:\\temp\\git_tests" # Можно указать конкретный путь к каталогу плагинов
        # print("base_directory",base_directory)
    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # 

        # Получаем список всех папок в базовой директории
        directories = [os.path.join(base_directory, d) for d in os.listdir(base_directory) if os.path.isdir(os.path.join(base_directory, d))]
        directories = [base_directory.rsplit("\\",1)[0]]+directories
        # print("directories",directories)
        
        # Проходимся по каждой папке и проверяем наличие репозитория GitHub
        for directory in directories:
            print()
            print(Fore.GREEN + os.path.basename(directory) + Style.RESET_ALL)
            github_repo_path = check_github_repo(directory)
            if github_repo_path:
                # print(f"\t{github_repo_path}")
                get_repository_status(directory)
            else:
                pass
            # print("-" * 50)
    except Exception as e:
        print(f"An error occurred: {e}")

# Вызываем основную функцию
if __name__ == "__main__":
    main()

input("Нажмите Enter для завершения работы...")