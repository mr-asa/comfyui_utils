[![English](https://img.shields.io/badge/lang-English-blue)](README.md) [![Russian](https://img.shields.io/badge/lang-Russian-red)](README_RU.md)

<p align="center"><img src="docs/banner.png" alt="ComfyUI Utils: venvs, pip packages, repos, workflows, custom nodes"></p>

# ComfyUI Utils

Локальный набор инструментов для ухода за установкой ComfyUI: виртуальные окружения, pip-пакеты, репозитории, воркфлоу и ссылки на custom nodes —
в одном браузерном интерфейсе. Прежние консольные утилиты продолжают работать (см. свёрнутый раздел внизу).

## Быстрый старт

1. Запустите GUI: `utils_gui_run.bat` (Linux: `utils_gui_run.sh`) или ярлык **Utils GUI**. Откроется `http://127.0.0.1:8765/`
   (`python utils_gui.py --port N --no-browser`, чтобы поменять порт). Сервер слушает только `127.0.0.1` и работает на стандартной библиотеке Python.
2. Нажмите **шестерёнку** в шапке и задайте папки (ComfyUI, `custom_nodes`, репозиторий junction-ссылок, воркфлоу, wheel) и окружения. Править JSON вручную не нужно.
3. Идите по вкладкам слева направо.

Нужно: Python с `requests` и `packaging` (есть в любом venv ComfyUI) и [`uv`](https://docs.astral.sh/uv/) (`pip install uv`) для установки пакетов и сборки venv.

## Скриншоты

<p align="center"><img src="docs/screenshot_audit.png" alt="Pip audit" width="900"><br>*Pip audit: что можно обновить безопасно, что тянет за собой другие пакеты (`safe +deps`) и что сломает требование (`risky`), с причиной.*</p>

<p align="center"><img src="docs/screenshot_install_preview.png" alt="Install preview" width="900"><br>*Предпросмотр установки: все пакеты, которые изменит обновление, включая зависимости, с выбором, что оставить нетронутым.*</p>

## Вкладки (в порядке работы)

| Вкладка | Для чего |
|---|---|
| <img src="ico/build_venv_run.ico" width="16" height="16" alt=""> **Build venv** | Планирует и собирает свежий venv: подбирает связку Python / CUDA / torch, при которой разрешаются зависимости всех нод и находятся готовые wheel, и ставит её через `uv`. |
| <img src="ico/update_comfy_repos_run.ico" width="16" height="16" alt=""> **Custom nodes** | Два вида одних и тех же нод. **Git:** сканирует ComfyUI и все custom nodes (fetch, отставание / опережение, локальные правки), показывает входящие коммиты, обновляет с выбранной политикой для локальных правок, добавляет ноду по URL (сразу после этого запускается Pip audit), отвязывает или удаляет; фильтр по тегу. **Links:** junction-ссылки между репозиторием нод и `custom_nodes`, пресеты, теги, выбор по тегу. Выделение переходит между видами. |
| <img src="ico/comfyui_pip_update_audit_run.ico" width="16" height="16" alt=""> **Pip audit** | Сравнивает установленные пакеты с требованиями всех нод. Статусы safe / safe +deps / risky, предпросмотр установки с изменениями зависимостей, список версий, hold / pin, обновления wheel. Можно поставить любой пакет вручную (имя или версия). **Check deps** запускает `pip check` и добавляет нарушенные требования в таблицу (с пометкой update / downgrade / install); **Purge cache** очищает кэш пакетов и показывает освобождённое место. Hold или pin можно снять на одну установку, если он мешает зависимости. |
| <img src="ico/update_workflow_repos_run.ico" width="16" height="16" alt=""> **Workflows** | То же сканирование / обновление / добавление / удаление для источников воркфлоу, включая частичные (только папка). |
| <img src="ico/compare_venv_versions_run.ico" width="16" height="16" alt=""> **Compare venvs** | Версии пакетов рядом, с цветами; избранные (ключевые) модули редактируются с возможностью отмены. |

Общее для вкладок: панель лога внизу (меняется по высоте, очищается, с цветами), долгие задачи можно отменить, перед опасными действиями есть подтверждение. Идущая задача показывает прогресс на своей вкладке (полоска и `готово/всего`); если задача закончилась, пока открыта другая вкладка, на ней появляется зелёная точка.

## Переход на интерфейс

Что изменилось и зачем:

- **Один GUI на всё.** Таблицы, фильтры, предпросмотр и живой лог вместо запросов в консоли. Консольные скрипты не менялись и работают.
- **Pip audit оценивает побочные эффекты.** «safe» теперь значит, что меняется только этот пакет; «safe +deps» перечисляет изменения зависимостей; всё, что сломает требование, понизит версию или заденет torch / numpy, помечается «risky». Зачем: один безобидный апдейт раньше молча двигал numpy и десяток пакетов.
- **Предпросмотр установки и защита.** Dry-run показывает каждый пакет, который изменится, и даёт выбрать: держать hold / pin или все остальные пакеты. Каждая реальная установка сохраняет снимок `_before.txt` и лог в `install_logs/` для отката.
- **Больше нет `--upgrade`.** С uv он обновляет ещё и все зависимости. Теперь ставятся только точные версии.
- **Список версий пакета.** Все релизы PyPI с вердиктом по requirements и по установленным зависимым пакетам; можно выбрать или поставить любую версию (ответ на «есть ли что-то новее, что всё ещё подходит?»).
- **Пакеты, поставленные из wheel, распознаются** (sageattention, flash-attn, llama-cpp...). Они сверяются с `wheel_sources.json`, а не с PyPI; более новые подходящие wheel можно скачать и поставить из GUI.
- **Build venv.** `build_venv.py` (он же консольная утилита): разрешает все требования нод через uv, ослабляет конфликтующие пины, переносит пакеты из ваших других venv, ставит подходящие wheel, сохраняет lock в `locks/`.
- **Custom nodes / Workflows.** Fetch и отставание / опережение сразу видны, входящие коммиты видны до обновления, локальные правки можно stash / пропустить / отбросить. Частичные источники воркфлоу теперь помнят синхронизированный коммит, поэтому показывают up-to-date или behind и дают ссылку на оригинал. Источники добавляются и удаляются из интерфейса; удаление не теряет локальную работу без вашего подтверждения.
- **Вид Links.** Junction-ссылки, пресеты и теги (прежний link manager) со ссылками на GitHub у каждой ноды, теперь это вид вкладки Custom nodes.
- **Шестерёнка настроек.** Все папки, окружения (комментарий, CUDA path, переменные окружения), общие переменные окружения, источники wheel и опции апдейтера в одном окне.
- **Общее ядро.** `envtools.py`, `pip_audit_core.py`, `wheels.py` содержат логику, общую для консольных утилит и GUI. Новые флаги консоли: `comfyui_pip_update_audit.py --hide-unchanged`, `--pip`. `run_comfyui.bat` теперь обновляет frontend-пакеты через uv, если он есть в PATH.

### Апдейты

<details>
<summary><b>2026-10-16</b></summary>

- **Pip audit:** «risky» разделён на `blocked` и `+deps`, видно, что сдерживает новую версию.
- **Окно версий** открывается для любого пакета: ограничения, `check` версии, hold / pin.
- **torch / torchvision / torchaudio** обновляются с индекса PyTorch, CUDA-сборка не подменяется.
- **Предпросмотр установки** объясняет отказ и предлагает освободить мешающие пакеты.
- **Wheel sources:** кнопка Test sources, итог поиска в аудите, wheel в окне версий.
- **Отозванная (yanked) версия** больше не считается downgrade.
- **Custom nodes:** добавление нескольких нод сразу, колонка Added с сортировкой по дате.

</details>

## Ярлыки и запуск

- Windows: файлы `*.bat`; `powershell -ExecutionPolicy Bypass -File create_windows_links.ps1` создает ярлыки `.lnk` (с иконками из репозитория) в `run_windows`.
- Linux: эквиваленты `*.sh`; `bash create_linux_links.sh` создает `.desktop` в `run_linux`.

## Какие файлы создаются

| Файл / папка | Содержимое |
|---|---|
| `config.json` | папки, окружения (с правилами hold / pin), переменные окружения, теги нод. Редактируется в **Settings**. |
| `wheel_sources.json` | папка wheel, имена пакетов и источники GitHub / Hugging Face. |
| `update_comfy_repos_config.json` | опции апдейтера (параллельные задачи, шаблоны auto-merge). |
| `run_comfyui_presets_config.json` | пресеты нод (управляются на вкладке Custom nodes, вид Links). |
| `compare_venv_versions_config.json` | избранные модули для Compare venvs (предыдущее состояние хранится в `.bak`). |
| `install_logs/` | логи установки и сборки, снимки `_before.txt`, использованные constraints. |
| `locks/` | списки пакетов venv, собранных `build_venv.py`. |

<details>
<summary><b>Консольные утилиты (старое, поддерживается)</b></summary>

Исходные скрипты и их `.bat` / `.sh` запуск не менялись. У большинства теперь есть вкладка в GUI; разделы ниже остаются полезными для скриптов,
запуска без интерфейса или если вы предпочитаете терминал. Часть шаблона config.json ниже — старый плоский формат; актуальный формат записывает Settings.

### Консольные утилиты

- <img src="ico/update_comfy_repos_run.ico" width="16" height="16" alt=""> `update_comfy_repos.py` обновляет основной репозиторий ComfyUI и все репозитории в `custom_nodes`
  (пропускает отключенные папки), пишет детальный лог с коммитами и измененными файлами.
- <img src="ico/update_workflow_repos_run.ico" width="16" height="16" alt=""> `update_workflow_repos.py` обновляет workflow-источники в `user/default/workflows/github`:
  полные Git-репозитории и частичные выгрузки с `.workflow_source.json`.
- <img src="ico/comfyui_pip_update_audit_run.ico" width="16" height="16" alt=""> [`comfyui_pip_update_audit.py`](#обновление-виртуального-окружения-comfyui_pip_update_auditpy) сканирует `requirements.txt` в корне ComfyUI и в верхнем уровне
  `custom_nodes`, отмечает `comfy-env` isolated env, сравнивает установленные версии с последними и печатает команды обновления.
- <img src="ico/run_comfyui.ico" width="16" height="16" alt=""> [`run_comfyui.bat`](#загрузчик-comfyui-run_comfyui-bat) запускает ComfyUI с выбором окружения и пресетами custom nodes через junction.
- <img src="ico/custom_nodes_link_manager_run.ico" width="16" height="16" alt=""> [`custom_nodes_link_manager.py`](#менеджер-custom-nodes-custom_nodes_link_managerpy) управляет junction-ссылками custom nodes (сравнение repo и custom_nodes, добавление/удаление).
- <img src="ico/partial_repo_sync_run.ico" width="16" height="16" alt=""> [`partial_repo_sync.py`](#частичная-синхронизация-репозиториев-partial_repo_syncpy) синхронизирует выбранные файлы/папки из git-репозитория в целевой каталог
  вне основного workflow-сценария.
- `requirements_checker/` дает расширенную проверку требований с выбором окружения (venv/conda),
  пользовательскими путями и статусами по каждому пакету.
- <img src="ico/clone_workflow_repos_run.ico" width="16" height="16" alt=""> `clone-workflow_repos.py` добавляет workflow-источники по ссылкам из консоли:
  полный репозиторий клонирует целиком, ссылку на папку синхронизирует частично.
- `rename_to_english.py` переименовывает файлы и папки с не-ASCII символами в английские эквиваленты (перевод/транслит) — удобно, чтобы разобраться с пачкой воркфлоу с иероглифами в названии.
- `make_tmp_custom_nodes.py` создает `tmp_custom_nodes.json` со списком загруженных/отключенных
  нод и их URL репозиториев, полезно для сбора плагинов в единый список/репозиторий.
- <img src="ico/png_to_json_run.ico" width="16" height="16" alt=""> `png_to_json.py` сканирует папку с изображениями и видео (`.png/.jpeg/.webp/.bmp/.tif/.mp4/.mkv/.webm/.mov/.gif`), читает метаданные ComfyUI `workflow`
  и пишет `.json` рядом с каждым файлом, где есть workflow.
- `comfyui_root.py` определяет корневую папку ComfyUI через config, валидацию и поиск вверх.

### Пути и расположение

- Скрипты не завязаны на фиксированную структуру каталогов.
- `comfyui_root.py` валидирует корень по наличию `custom_nodes`, `models`, `main.py`
  и `extra_model_paths.yaml.example`.
- Если `Comfyui_root` отсутствует или невалиден, скрипты ищут корень вверх и сохраняют его в `config.json`.

### config.json (шаблон)

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

### Менеджер custom nodes (custom_nodes_link_manager.py)

#### Концепция переноса нод

Основная идея: все реальные ноды хранятся в одном каталоге `custom_nodes_repo`,
а папка `custom_nodes` содержит только junction-ссылки. Это дает единый источник
ноды, упрощает обслуживание и позволяет быстро включать/выключать наборы нод
без перемещения файлов.

#### Утилита

- Показывает компактный список в две колонки; активные ссылки отмечены `=>`.
- Нумерация идет сверху вниз в каждом столбике (сначала левый, потом правый).
- Команды: `a` добавить (все), `r` убрать (все), `i` инвертировать (все), `s` синхронизация, `p` выбор пресета, `w` сохранить пресет, `q` выход.
- Для выборочных действий используйте `a <n>`, `r <n>`, `i <n>`.
- Фильтры списка: `?+` только включенные (linked), `?-` только выключенные (unlinked), `?*` все.
- При фильтре команды `a <n>`, `r <n>`, `i <n>` работают по номерам текущего отфильтрованного списка.
- Выбор номеров поддерживает одиночные, диапазоны и списки (`3`, `2-6`, `1,4,9`, `1 3-5`).
- Синхронизация: добавляет отсутствующие ссылки и удаляет лишние (junction).
- Путь к `custom_nodes_repo` берется из `config.json` (`custom_nodes_repo_path`) или спрашивается.
- Путь к `custom_nodes` берется из `custom_nodes_path`/`custom_nodes_paths`.
- Пресеты читаются/сохраняются в `run_comfyui_presets_config.json`. При сохранении режим (`blacklist`/`whitelist`) выбирается по меньшему количеству отключенных/включенных нод.

### Частичная синхронизация репозиториев (partial_repo_sync.py)

- Для workflow теперь обычно используйте `clone-workflow_repos.py` и `update_workflow_repos.py`.
- Старые workflow-задания из `partial_repo_sync_config.json` автоматически мигрируют в `.workflow_source.json` при запуске `update_workflow_repos.py`.
- Синхронизирует только выбранные файлы/каталоги из git-репозитория (не весь репозиторий).
- Использует локальный кэш и git sparse-checkout, затем копирует выбранные пути в `target`.
- Задания: `partial_repo_sync_config.json` (repo, branch, target, paths).
- В `paths` можно указать регулярки: `re:^styles/.*\\.json$` или `regex:^styles/` (пути в git с `/`).
- Запуск через `partial_repo_sync_run.bat`.

### Загрузчик ComfyUI (run_comfyui.bat)

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

### Обновление виртуального окружения (comfyui_pip_update_audit.py)

#### Особенности

- Сканирует только `requirements.txt` (корень и верхний уровень custom nodes).
- Объединяет дубликаты ограничений и считает max allowed версию.
- Фильтрует prerelease/dev версии из предлагаемых апдейтов.
- Классифицирует обновления как safe/risky/unknown и показывает причины для risky.
- Проверяет обратные зависимости установленных пакетов, чтобы ловить конфликты до установки.
- Корректно обрабатывает inline-комментарии в `requirements.txt` (например `pkg  # comment`).
- Обнаруживает `comfy-env-root.toml` и вложенные `comfy-env.toml` в custom nodes.
  - `comfy-env-root.toml` без `comfy-env.toml` помечается как host/runtime helper: зависимости из `requirements.txt` всё ещё относятся к основному pip-окружению.
  - Вложенный `comfy-env.toml` помечается как isolated pixi env; если env отсутствует, в финальных командах печатается запуск `install.py`.
  - Скрипт только аудирует `comfy-env` и печатает команды, но не запускает `install.py` автоматически.

#### Hold / pin 

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

</details>

---

> [!WARNING]
> Я работаю сейчас на Windows и venv-окружениях. Эта связка тестируется.\
> PS. Утилиты пишу для себя, стараюсь обновлять не ломая функционал. Дополняю по мере появления идей
