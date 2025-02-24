#!/usr/bin/env python3
"""
Launcher script for the requirements checker package.
Runs the main module of requirements_checker from the parent directory.
"""

import sys
import os

# Проверяем, что мы находимся в правильной директории
current_dir = os.path.abspath(os.path.dirname(__file__))
if not os.path.exists(os.path.join(current_dir, "requirements_checker")):
    print("Ошибка: директория 'requirements_checker' не найдена.")
    sys.exit(1)

# Запускаем main как модуль пакета
try:
    from requirements_checker.main import main
    main()
except ImportError as e:
    print(f"Ошибка импорта: {e}")
    print("Убедитесь, что все зависимости установлены и структура пакета корректна.")
    sys.exit(1)

if __name__ == "__main__":
    main()