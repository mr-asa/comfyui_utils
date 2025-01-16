import os
import git
import json
from colorama import init, Fore, Style

# Initialize colorama
init()

# Function to check for a GitHub repository in the directory
def check_github_repo(directory):
    try:
        # Check if .git folder (repository) exists in the specified directory
        git_folder = os.path.join(directory, '.git')
        if os.path.exists(git_folder):
            # If .git exists, return the repository path
            return git_folder
        else:
            return None
    except Exception as e:
        print(Fore.RED + f"\tError checking GitHub repository: {e}" + Style.RESET_ALL)
        return None

# Function to get repository status information
def get_repository_status(directory):
    try:
        repo = git.Repo(directory)

        print("\t" + repo.remotes[0].url)

        try:
            current_branch = repo.active_branch
        except TypeError:
            branches = repo.branches

            if len(branches) == 1:
                branch_name = branches[0].name
                repo.heads[branch_name].checkout()
                current_branch = repo.active_branch
            else:
                print("Found several branches:")
                for branch in branches:
                    print(branch.name)

                # Wait for user input for the desired branch name
                desired_branch = input("Choose branch for update: ")

                # Switch to the selected branch
                repo.heads[desired_branch].checkout()
                current_branch = repo.active_branch

        tracking_branch = current_branch.tracking_branch()
        if tracking_branch is None:
            # Set the tracking branch for the current branch
            repo.git.branch('--set-upstream-to', f'origin/{current_branch.name}', current_branch.name)

        repo.remotes.origin.fetch()

        github_commits = list(repo.iter_commits(f'origin/{current_branch.name}'))
        github_commits.reverse()

        local_commits = {commit.hexsha for commit in repo.iter_commits(current_branch.name)}

        files_edited = False

        for commit in github_commits:
            if commit.hexsha not in local_commits:
                print(Fore.BLUE + f"--> {commit.authored_datetime}\n{commit.message}".rstrip('\n') + Style.RESET_ALL)
                files_edited = True

        if repo.is_dirty():
            try:
                # Stash local changes
                repo.git.stash()
            except git.exc.GitCommandError as e:
                print(f"Stash error: {e}")
                return

        if files_edited:
            print()
            result = repo.git.pull()
            print(result)

    except Exception as e:
        print(Fore.RED + f"\tError getting repository status: {e}" + Style.RESET_ALL)

# Main function to iterate through directories and perform checks
def main():
    try:
        # Specify the path to the directory containing the repositories
        root_directory = os.path.realpath(__file__).replace("\\", "/").rsplit("/", 1)[0]
        if os.path.exists(os.path.join(root_directory, "config.json")):
            with open(os.path.join(root_directory, "config.json"), "r") as config_file:
                config = json.load(config_file)
                if "custom_nodes_path" in config:
                    custom_nodes_directory = config["custom_nodes_path"]

        if not custom_nodes_directory:
            custom_nodes_directory = root_directory.rsplit("/", 2)[0] + "/custom_nodes"

        # Get a list of all folders in the base directory
        directories = [os.path.join(custom_nodes_directory, d) for d in os.listdir(custom_nodes_directory) if os.path.isdir(os.path.join(custom_nodes_directory, d))]
        directories = [custom_nodes_directory.replace("\\", "/").rsplit("/", 2)[0]] + directories
        
        # Iterate through each folder and check for a GitHub repository
        for directory in directories:
            github_repo_path = check_github_repo(directory)
            if github_repo_path:
                print()
                print(Fore.GREEN + os.path.basename(directory) + Style.RESET_ALL)
                get_repository_status(directory)
            else:
                pass
    except Exception as e:
        print(f"An error occurred: {e}")

# Call the main function
if __name__ == "__main__":
    main()

input("Press Enter to exit...")