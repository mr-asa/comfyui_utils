import os
import git
from colorama import init, Fore, Style
from requirements_checker.config_manager import config_manager
from pathlib import Path
from datetime import datetime

# Initialize colorama
init()

def get_default_branch(repo):
    """Определяет основную ветку репозитория"""
    try:
        # Сначала пробуем получить HEAD из remote
        try:
            remote_head = repo.remotes.origin.refs.HEAD.reference
            return remote_head.name.replace('origin/', '')
        except:
            pass
        
        # Если не получилось, ищем среди известных веток
        remote_branches = [ref.name.replace("origin/", "") for ref in repo.remotes.origin.refs if not ref.name.endswith('/HEAD')]
        
        for candidate in ["main", "master", "dev", "develop", "stable"]:
            if candidate in remote_branches:
                return candidate
        
        # Если ничего не найдено, берем первую доступную
        if remote_branches:
            return remote_branches[0]
        
        return None
    except Exception as e:
        print(f"\t❌ Error determining default branch: {e}")
        return None

def get_latest_commit_info(repo, branch_name):
    """Получает информацию о последнем коммите ветки"""
    try:
        if f'origin/{branch_name}' in [ref.name for ref in repo.remotes.origin.refs]:
            commit = repo.commit(f'origin/{branch_name}')
        else:
            commit = repo.commit(branch_name)
        return commit, commit.committed_datetime
    except:
        return None, None

def find_best_branch_to_update(repo, current_branch_name):
    """Определяет лучшую ветку для обновления"""
    default_branch = get_default_branch(repo)
    if not default_branch:
        return current_branch_name, "unknown", "Could not determine default branch"
    
    # Если мы уже на основной ветке
    if current_branch_name == default_branch:
        return current_branch_name, "default", None
    
    # Сравниваем даты коммитов
    current_commit, current_date = get_latest_commit_info(repo, current_branch_name)
    default_commit, default_date = get_latest_commit_info(repo, default_branch)
    
    if not current_date or not default_date:
        return current_branch_name, "unknown", "Could not compare branch dates"
    
    # Если основная ветка новее, переключаемся на неё
    if default_date > current_date:
        return default_branch, "switch", f"Switching to newer default branch ({default_branch})"
    else:
        return current_branch_name, "custom", f"Current branch ({current_branch_name}) is up to date"

def stash_local_changes(repo):
    """Сохраняет локальные изменения в stash"""
    if repo.is_dirty():
        try:
            repo.git.stash('push', '-m', f'Auto-stash {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
            return True, "📦 Local changes stashed"
        except git.exc.GitCommandError as e:
            return False, f"❌ Failed to stash changes: {e}"
    return False, None

def is_git_repository(directory):
    """Проверяет является ли директория git репозиторием"""
    return os.path.exists(os.path.join(directory, '.git'))

def should_skip_directory(directory):
    """Проверяет нужно ли пропустить директорию"""
    skip_patterns = ['__pycache__', '.git', 'node_modules', '.vscode', '.idea', 
                     '__MACOSX', '.DS_Store', 'Thumbs.db']
    dir_name = os.path.basename(directory)
    return any(pattern in dir_name for pattern in skip_patterns)

def initialize_git_repository(directory):
    """Инициализирует git репозиторий в директории с заданным remote URL"""
    try:
        repo_name = os.path.basename(directory)
        print(f"\t📁 Directory '{repo_name}' is not a git repository")
        
        # Запрашиваем URL репозитория
        repo_url = input(f"\tEnter git repository URL (or press Enter to skip): ").strip()
        
        if not repo_url:
            return False, "Skipped by user"
        
        # Валидация URL
        if not (repo_url.startswith('http') or repo_url.startswith('git@')):
            return False, "Invalid repository URL format"
        
        # Проверяем есть ли файлы в директории
        existing_files = os.listdir(directory)
        if existing_files:
            print(f"\t⚠️  Directory contains {len(existing_files)} files/folders")
            overwrite = input(f"\tReplace contents with git repository? (y/N): ").strip().lower()
            if overwrite != 'y':
                return False, "User chose not to overwrite existing content"
            
            # Создаем backup если нужно
            backup_dir = f"{directory}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            try:
                import shutil
                shutil.move(directory, backup_dir)
                os.makedirs(directory, exist_ok=True)
                print(f"\t📦 Existing content moved to: {os.path.basename(backup_dir)}")
            except Exception as e:
                return False, f"Failed to backup existing content: {e}"
        
        # Клонируем репозиторий
        print(f"\t🔄 Cloning repository...")
        try:
            git.Repo.clone_from(repo_url, directory)
            print(f"\t✅ Repository cloned successfully")
            return True, "Repository initialized and cloned"
        except git.exc.GitCommandError as e:
            return False, f"Failed to clone repository: {e}"
        except Exception as e:
            return False, f"Unexpected error during cloning: {e}"
            
    except KeyboardInterrupt:
        return False, "Interrupted by user"
    except Exception as e:
        return False, f"Error during repository initialization: {e}"

def update_repository(directory, is_disabled=False):
    """Обновляет конкретный репозиторий"""
    try:
        if not is_git_repository(directory):
            return False, "Not a git repository"
        
        repo = git.Repo(directory)
        
        # Получаем информацию о репозитории
        try:
            repo_url = repo.remotes.origin.url
            print(f"\t🔗 {repo_url}")
        except:
            print(f"\t🔗 No remote origin found")
        
        # Fetch обновления
        try:
            repo.remotes.origin.fetch()
        except git.exc.GitCommandError as e:
            return False, f"Failed to fetch: {e}"
        
        # Проверяем текущую ветку
        try:
            current_branch = repo.active_branch
            current_branch_name = current_branch.name
        except TypeError:
            return False, "Repository in detached HEAD state"
        
        # Определяем лучшую ветку для обновления
        target_branch, branch_type, switch_reason = find_best_branch_to_update(repo, current_branch_name)
        
        # Цветовая маркировка веток
        if target_branch != current_branch_name:
            branch_display = f"\t{Fore.RED}{current_branch_name} --> {target_branch}{Style.RESET_ALL}"
        elif branch_type == "custom":
            branch_display = f"\t{Fore.YELLOW}{current_branch_name} --> {target_branch}{Style.RESET_ALL}"
        else:  # default branch
            branch_display = f"\t{current_branch_name} --> {target_branch}"
        
        print(branch_display)
        
        # Показываем дополнительную информацию только если есть
        if switch_reason:
            print(f"\t💡 {switch_reason}")
        
        # Сохраняем локальные изменения если есть
        stashed, stash_msg = stash_local_changes(repo)
        if stashed:
            print(f"\t{stash_msg}")
        
        # Переключаемся на целевую ветку если нужно
        if target_branch != current_branch_name:
            try:
                # Проверяем существует ли локальная ветка
                local_branches = [branch.name for branch in repo.branches]
                if target_branch not in local_branches:
                    # Создаем локальную ветку из remote
                    repo.git.checkout('-b', target_branch, f'origin/{target_branch}')
                else:
                    repo.git.checkout(target_branch)
            except git.exc.GitCommandError as e:
                return False, f"Failed to switch branch: {e}"
        
        # Получаем коммиты до обновления
        try:
            # Проверяем существование remote ветки более надежно
            remote_refs = [ref.name for ref in repo.remotes.origin.refs]
            remote_branch_exists = f'origin/{target_branch}' in remote_refs
            
            if remote_branch_exists:
                remote_commits = list(repo.iter_commits(f'origin/{target_branch}', max_count=10))
                local_commits = {commit.hexsha for commit in repo.iter_commits(target_branch, max_count=50)}
            else:
                # Если remote ветки нет, но есть локальная - пробуем push
                try:
                    local_branches = [branch.name for branch in repo.branches]
                    if target_branch in local_branches:
                        # Пробуем создать remote ветку
                        repo.git.push('--set-upstream', 'origin', target_branch)
                        repo.remotes.origin.fetch()  # Обновляем информацию о remote
                        remote_commits = list(repo.iter_commits(f'origin/{target_branch}', max_count=10))
                        local_commits = {commit.hexsha for commit in repo.iter_commits(target_branch, max_count=50)}
                    else:
                        return False, f"Neither local nor remote branch '{target_branch}' found"
                except git.exc.GitCommandError:
                    # Если не можем создать remote ветку, работаем только с локальной
                    return True, f"✅ Local branch only (no remote tracking)"
        except Exception as e:
            return False, f"Failed to get commit information: {e}"
        
        # Проверяем есть ли новые коммиты
        new_commits = [commit for commit in remote_commits if commit.hexsha not in local_commits]
        
        if not new_commits:
            return True, "✅ No changes"
        
        # Выполняем pull
        try:
            current_branch = repo.active_branch
            
            # Проверяем есть ли tracking branch
            if current_branch.tracking_branch():
                result = repo.git.pull()
            else:
                # Пытаемся установить upstream
                try:
                    repo.git.branch('--set-upstream-to', f'origin/{target_branch}', target_branch)
                    result = repo.git.pull()
                except git.exc.GitCommandError:
                    # Если не получается установить upstream, пробуем pull с указанием remote и branch
                    try:
                        result = repo.git.pull('origin', target_branch)
                    except git.exc.GitCommandError as e:
                        # Если и это не работает, возможно это локальная ветка или форк
                        if "couldn't find remote ref" in str(e).lower():
                            return True, f"✅ Local branch (no remote updates available)"
                        else:
                            raise e
            
            # Формируем лог обновлений
            update_log = f"➡️ Updates ({len(new_commits)} commits):\n"
            for commit in reversed(new_commits):  # Показываем все коммиты
                commit_date = commit.committed_datetime.strftime('%Y-%m-%d %H:%M')
                commit_msg = commit.message.strip().split('\n')[0][:80]
                update_log += f"\t   • {commit_date}: {commit_msg}\n"
            
            # Получаем информацию о измененных файлах
            try:
                # Получаем статистику изменений
                diff_stats = repo.git.diff('--stat', f'HEAD~{len(new_commits)}', 'HEAD')
                if diff_stats.strip():
                    update_log += f"\t📁 Changed files:\n"
                    for line in diff_stats.split('\n'):
                        if line.strip() and '|' in line:
                            update_log += f"\t   {line.strip()}\n"
            except:
                pass  # Если не удалось получить статистику файлов, продолжаем без неё
            
            return True, update_log.rstrip()
            
        except git.exc.GitCommandError as e:
            return False, f"Pull failed: {e}"
    
    except Exception as e:
        return False, f"Unexpected error: {e}"

def main():
    """Основная функция"""
    try:
        print(f"{Fore.CYAN}🚀 Starting repository updates...{Style.RESET_ALL}\n")
        
        # Получаем конфигурацию
        env_type = config_manager.get_value('env_type')
        if env_type == 'conda':
            config_manager.get_value('conda_path')
            config_manager.get_value('conda_env')
            config_manager.get_value('conda_env_folder')

        custom_nodes_dir = config_manager.get_value('custom_nodes_path')
        parent_dir = Path(custom_nodes_dir).parent

        # Список директорий для обновления
        directories = []
        
        # Добавляем основной репозиторий ComfyUI
        directories.append(str(parent_dir))
        
        # Добавляем все репозитории из custom_nodes
        if os.path.exists(custom_nodes_dir):
            for item in os.listdir(custom_nodes_dir):
                item_path = os.path.join(custom_nodes_dir, item)
                if os.path.isdir(item_path) and not should_skip_directory(item_path):
                    if item == '.disabled':
                        # Обрабатываем disabled репозитории отдельно
                        disabled_repos = []
                        if os.path.exists(item_path):
                            for disabled_item in os.listdir(item_path):
                                disabled_item_path = os.path.join(item_path, disabled_item)
                                if os.path.isdir(disabled_item_path) and is_git_repository(disabled_item_path):
                                    disabled_repos.append(disabled_item_path)
                        directories.extend(disabled_repos)
                    else:
                        directories.append(item_path)

        print(f"Found {len(directories)} repositories to update\n")

        # Обновляем каждый репозиторий
        success_count = 0
        error_count = 0
        skipped_count = 0
        initialized_count = 0
        
        for directory in directories:
            repo_name = os.path.basename(directory)
            
            # Проверяем является ли это git репозиторием
            if not is_git_repository(directory):
                print(f"{Fore.YELLOW}{repo_name}{Style.RESET_ALL}")
                
                # Предлагаем инициализировать репозиторий
                success, message = initialize_git_repository(directory)
                
                if success:
                    initialized_count += 1
                    print(f"\t{message}")
                    # После успешной инициализации пробуем обновить
                    if is_git_repository(directory):
                        print(f"\t🔄 Now updating the newly cloned repository...")
                        is_disabled = '.disabled' in directory
                        success, update_message = update_repository(directory, is_disabled)
                        if success:
                            success_count += 1
                            print(f"\t{update_message}")
                        else:
                            error_count += 1
                            print(f"\t❌ Update error: {update_message}")
                else:
                    skipped_count += 1
                    print(f"\t⏭️  {message}")
                
                print()
                continue
            
            # Проверяем находится ли в disabled папке
            is_disabled = '.disabled' in directory
            disabled_suffix = " (disabled)" if is_disabled else ""
            
            print(f"{Fore.GREEN}{repo_name}{disabled_suffix}{Style.RESET_ALL}")
            
            success, message = update_repository(directory, is_disabled)
            
            if success:
                success_count += 1
                if "No changes" in message:
                    print(f"\t{message}")
                else:
                    print(f"\t{message}")
            else:
                error_count += 1
                print(f"\t❌ Error: {message}")
            
            print()  # Пустая строка между репозиториями

        # Итоговая статистика
        print(f"{Fore.CYAN}📊 Summary:{Style.RESET_ALL}")
        print(f"\t✅ Successfully processed: {success_count}")
        print(f"\t❌ Errors: {error_count}")
        print(f"\t🆕 Newly initialized: {initialized_count}")
        print(f"\t⏭️  Skipped: {skipped_count}")
        print(f"\t📁 Total directories checked: {len(directories) + skipped_count}")

    except Exception as e:
        print(f"{Fore.RED}💥 Fatal error: {e}{Style.RESET_ALL}")

if __name__ == "__main__":
    main()
    input("\nPress Enter to exit...")