[![English](https://img.shields.io/badge/lang-English-blue)](README.md) [![Russian](https://img.shields.io/badge/lang-Russian-red)](README_RU.md)

# ComfyUI Utils

Утилиты для обслуживания ComfyUI и связанных репозиториев. Скрипты могут лежать где угодно,
но удобнее держать этот репозиторий рядом с папкой ComfyUI.

## Что включено

- `update_comfy_repos.py` обновляет основной репозиторий ComfyUI и все репозитории в `custom_nodes`
  (пропускает отключенные папки), пишет детальный лог с коммитами и измененными файлами.
- `update_workflow_repos.py` обновляет все Git-репозитории в `user/default/workflows/github` и
  сообщает о пропущенных папках без Git.
- `comfyui_pip_update_audit.py` сканирует `requirements.txt` в корне ComfyUI и в верхнем уровне
  `custom_nodes`, сравнивает установленные версии с последними и печатает команды обновления.
- `requirements_checker/` дает расширенную проверку требований с выбором окружения (venv/conda),
  пользовательскими путями и статусами по каждому пакету.
- `clone-workflow_repos.py` клонирует репозитории воркфлоу из `clone-workflow_repos.txt`
  в папку `github` (предлагает выбрать путь).
- `make_tmp_custom_nodes.py` создает `tmp_custom_nodes.json` со списком загруженных/отключенных
  нод и их URL репозиториев, полезно для сбора плагинов в единый список/репозиторий.
- `png_to_json.py` сканирует папку с `.png/.jpeg`, читает метаданные ComfyUI `workflow`
  и пишет `.json` рядом с каждым изображением, где есть workflow.
- `comfyui_root.py` определяет корневую папку ComfyUI через config, валидацию и поиск вверх.

## Пути и расположение

- Скрипты не завязаны на фиксированную структуру каталогов.
- `comfyui_root.py` валидирует корень по наличию `custom_nodes`, `models`, `main.py`
  и `extra_model_paths.yaml.example`.
- Если `Comfyui_root` отсутствует или невалиден, скрипты ищут корень вверх и сохраняют его в `config.json`.

## Лаунчеры

- Windows: файлы `*.bat`.
- Linux: аналоги `*.sh` (запуск через `bash` или `./file.sh`).

## Особенности comfyui_pip_update_audit.py

- Сканирует только `requirements.txt` (корень и верхний уровень custom nodes).
- Объединяет дубликаты ограничений и считает max allowed версию.
- Фильтрует prerelease/dev версии из предлагаемых апдейтов.
- Классифицирует обновления как safe/risky/unknown и показывает причины для risky.
- Проверяет обратные зависимости установленных пакетов, чтобы ловить конфликты до установки.
- Корректно обрабатывает inline-комментарии в `requirements.txt` (например `pkg  # comment`).

## Hold / pin (comfyui_pip_update_audit.py)

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
> Проверено на Windows и venv-окружениях.
