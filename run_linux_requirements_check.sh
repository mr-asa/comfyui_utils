#!/bin/bash

# Get the full path of the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Launch a new terminal window and execute the Python script
xfce4-terminal --hold -e "bash -c '
cd \"$SCRIPT_DIR\"
python3 requirements_check.py
echo \"Requirements check completed. Press any key to close this window.\"
read -n 1
'"