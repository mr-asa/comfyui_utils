@echo off
REM Change to the directory where your script is located
cd /d "%~dp0"
REM Run the Python script
python requirements_check.py
pause