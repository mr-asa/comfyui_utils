[![English](https://img.shields.io/badge/lang-English-blue)](README.md) [![Russian](https://img.shields.io/badge/lang-Russian-red)](README_RU.md)

# ComfyUI Utils

Утилиты для обслуживания ComfyUI и связанных репозиториев. Скрипты могут лежать где угодно,
но удобнее держать этот репозиторий рядом с папкой ComfyUI.

## Что включено

- <img src="ico/update_comfy_repos_run.ico" width="16" height="16" alt=""> `update_comfy_repos.py` обновляет основной репозиторий ComfyUI и все репозитории в `custom_nodes`
  (пропускает отключенные папки), пишет детальный лог с коммитами и измененными файлами.
- <img src="ico/update_workflow_repos_run.ico" width="16" height="16" alt=""> `update_workflow_repos.py` обновляет все Git-репозитории в `user/default/workflows/github` и
  сообщает о пропущенных папках без Git.
- <img src="ico/comfyui_pip_update_audit_run.ico" width="16" height="16" alt=""> [`comfyui_pip_update_audit.py`](#обновление-виртуального-окружения-comfyui_pip_update_auditpy) сканирует `requirements.txt` в корне ComfyUI и в верхнем уровне
  `custom_nodes`, сравнивает установленные версии с последними и печатает команды обновления.
- <img src="ico/run_comfyui.ico" width="16" height="16" alt=""> [`run_comfyui.bat`](#загрузчик-comfyui-run_comfyui-bat) запускает ComfyUI с выбором окружения и пресетами custom nodes через junction.
- <img src="ico/custom_nodes_link_manager_run.ico" width="16" height="16" alt=""> [`custom_nodes_link_manager.py`](#менеджер-custom-nodes-custom_nodes_link_managerpy) управляет junction-ссылками custom nodes (сравнение repo и custom_nodes, добавление/удаление).
- <img src="ico/partial_repo_sync_run.ico" width="16" height="16" alt=""> [`partial_repo_sync.py`](#частичная-синхронизация-репозиториев-partial_repo_syncpy) синхронизирует выбранные файлы/папки из git-репозитория в целевой каталог.
- `requirements_checker/` дает расширенную проверку требований с выбором окружения (venv/conda),
  пользовательскими путями и статусами по каждому пакету.
- <img src="ico/clone_workflow_repos_run.ico" width="16" height="16" alt=""> `clone-workflow_repos.py` клонирует репозитории воркфлоу из `clone-workflow_repos.txt`
  в папку `github` (предлагает выбрать путь).
- `rename_to_english.py` переименовывает файлы и папки с не-ASCII символами в английские эквиваленты (перевод/транслит) — удобно, чтобы разобраться с пачкой воркфлоу с иероглифами в названии.
- `make_tmp_custom_nodes.py` создает `tmp_custom_nodes.json` со списком загруженных/отключенных
  нод и их URL репозиториев, полезно для сбора плагинов в единый список/репозиторий.
- <img src="ico/png_to_json_run.ico" width="16" height="16" alt=""> `png_to_json.py` сканирует папку с `.png/.jpeg`, читает метаданные ComfyUI `workflow`
  и пишет `.json` рядом с каждым изображением, где есть workflow.
- `comfyui_root.py` определяет корневую папку ComfyUI через config, валидацию и поиск вверх.

## Пути и расположение

- Скрипты не завязаны на фиксированную структуру каталогов.
- `comfyui_root.py` валидирует корень по наличию `custom_nodes`, `models`, `main.py`
  и `extra_model_paths.yaml.example`.
- Если `Comfyui_root` отсутствует или невалиден, скрипты ищут корень вверх и сохраняют его в `config.json`.

## config.json (шаблон)

Ниже пример с комментариями (JSONC). В реальном `config.json` комментарии удалить.

```jsonc
{
  // Корень ComfyUI (автоопределяется, но можно зафиксировать)
  "Comfyui_root": "C:/ComfyUI/ComfyUI",
  // Альтернативные ключи на тот же смысл
  "comfyui_root": "C:/ComfyUI/ComfyUI",
  "COMFYUI_ROOT": "C:/ComfyUI/ComfyUI",

  // Один путь к custom_nodes (legacy, поддерживается везде)
  "custom_nodes_path": "C:/ComfyUI/ComfyUI/custom_nodes",
  // Несколько путей к custom_nodes (новое). Повторы/Junction-дубликаты отфильтруются.
  "custom_nodes_paths": [
    "D:/ComfyUI/custom_nodes",
    "E:/ComfyUI_nodes"
  ],

  // Тип окружения: "venv" или "conda"
  "env_type": "venv",

  // Venv: текущий путь + список известных
  "venv_path": "C:/ComfyUI/venv",
  "venv_paths": [
    "C:/ComfyUI/venv",
    "D:/ComfyUI_envs/venv"
  ],

  // Conda: путь к conda.exe и имя/путь окружения
  "conda_path": "C:/Users/USER/miniconda3/Scripts/conda.exe",
  "conda_env": "comfyui",
  "conda_env_folder": "C:/Users/USER/miniconda3/envs/comfyui",

  // Необязательный путь к проекту (используется requirements_checker)
  "project_path": "C:/ComfyUI",

  // Путь к каталогу-репозиторию custom nodes для run_comfyui.bat
  "custom_nodes_repo_path": "D:/ComfyUI/custom_nodes_repo",

  // Hold/Pin: пер-окружение (env_key = venv_path | conda_env_folder | conda_env | "default")
  "holds": {
    "C:/ComfyUI/venv": {
      "hold_packages": ["torch", "torchvision"],
      "pin_packages": {
        "numpy": "1.26.4"
      }
    },
    "conda:comfyui": {
      "hold_packages": ["xformers"],
      "pin_packages": {}
    }
  },

  // Legacy: без привязки к окружению (поддерживается, но лучше "holds")
  "hold_packages": ["pkg1", "pkg2"],
  "pin_packages": {
    "pkg3": "1.2.3"
  }
}
```

## Лаунчеры

- Windows: файлы `*.bat`.
- Linux: аналоги `*.sh` (запуск через `bash` или `./file.sh`).

## Ярлыки

- Windows: `powershell -ExecutionPolicy Bypass -File create_windows_links.ps1` (создает `.lnk` в `run_windows`).
- Linux: `bash create_linux_links.sh` (создает `.desktop` в `run_linux`).

## Менеджер custom nodes (custom_nodes_link_manager.py)

### Концепция переноса нод

Основная идея: все реальные ноды хранятся в одном каталоге `custom_nodes_repo`,
а папка `custom_nodes` содержит только junction-ссылки. Это дает единый источник
ноды, упрощает обслуживание и позволяет быстро включать/выключать наборы нод
без перемещения файлов.

### Утилита

- Показывает компактный список в две колонки; активные ссылки отмечены `=>`.
- Нумерация идет сверху вниз в каждом столбике (сначала левый, потом правый).
- Команды: `a` добавить (все), `r` убрать (все), `i` инвертировать (все), `s` синхронизация, `p` выбор пресета, `w` сохранить пресет, `q` выход.
- Для выборочных действий используйте `a <n>`, `r <n>`, `i <n>`.
- Выбор номеров поддерживает одиночные, диапазоны и списки (`3`, `2-6`, `1,4,9`, `1 3-5`).
- Синхронизация: добавляет отсутствующие ссылки и удаляет лишние (junction).
- Путь к `custom_nodes_repo` берется из `config.json` (`custom_nodes_repo_path`) или спрашивается.
- Путь к `custom_nodes` берется из `custom_nodes_path`/`custom_nodes_paths`.
- Пресеты читаются/сохраняются в `run_comfyui_presets_config.json`. При сохранении режим (`blacklist`/`whitelist`) выбирается по меньшему количеству отключенных/включенных нод.

## Частичная синхронизация репозиториев (partial_repo_sync.py)

- Синхронизирует только выбранные файлы/каталоги из git-репозитория (не весь репозиторий).
- Использует локальный кэш и git sparse-checkout, затем копирует выбранные пути в `target`.
- Задания: `partial_repo_sync_config.json` (repo, branch, target, paths).
- В `paths` можно указать регулярки: `re:^styles/.*\\.json$` или `regex:^styles/` (пути в git с `/`).
- Запуск через `partial_repo_sync_run.bat`.

## Загрузчик ComfyUI (run_comfyui.bat)

- Берет корень ComfyUI из `config.json` (ключ `Comfyui_root`/`comfyui_root`/`COMFYUI_ROOT`) или ищет вверх.
- Показывает список venv `.venv*` и запускает выбранный `python.exe`.
- Поддерживает пресеты custom nodes через junction в `custom_nodes` из папки `custom_nodes_repo`.
  - Пресет `current` ничего не меняет.
  - Пресеты задаются в `run_comfyui_presets_config.json` (режимы `whitelist`/`blacklist`, список `nodes`).
  - Чистит только junction-папки, реальные каталоги не трогает.
- При первом запуске создается дефолтный `run_comfyui_presets_config.json`, если его нет.
- Флаги запуска задаются в `run_comfyui_flags_config.json` списком объектов (`name`, `keys`, `comment`).
  - `current` хранит список активных имен. Нажмите Enter для текущих или введите номера через пробел.
  - Перед запуском обновляет frontend-пакеты ComfyUI.
  - Используйте `@no_update` в пресете, чтобы пропустить обновление frontend-пакетов.

## Обновление виртуального окружения (comfyui_pip_update_audit.py)

### Особенности

- Сканирует только `requirements.txt` (корень и верхний уровень custom nodes).
- Объединяет дубликаты ограничений и считает max allowed версию.
- Фильтрует prerelease/dev версии из предлагаемых апдейтов.
- Классифицирует обновления как safe/risky/unknown и показывает причины для risky.
- Проверяет обратные зависимости установленных пакетов, чтобы ловить конфликты до установки.
- Корректно обрабатывает inline-комментарии в `requirements.txt` (например `pkg  # comment`).

### Hold / pin 

- Hold: исключает пакеты из обновления для текущего окружения.
- Pin: фиксирует пакет на конкретной версии для текущего окружения.
- Risky: конфликт зависимостей, найденный reverse-check или `pip --dry-run`.
- Unknown: ошибки сети/таймаута и другие не-зависимости при dry-run.

Команды:

```bash
python comfyui_pip_update_audit.py --hold pkg1 pkg2
python comfyui_pip_update_audit.py --unhold pkg1 pkg2
python comfyui_pip_update_audit.py --pin pkg1==1.2.3 pkg2
python comfyui_pip_update_audit.py --unpin pkg1 pkg2
```

---

> [!WARNING]
> Я работаю сейчас на Windows и venv-окружениях. Эта связка тестируется.\
> PS. Утилиты пишу для себя, стараюсь обновлять не ломая функционал. Дополняю по мере появления идей
