[![English](https://img.shields.io/badge/lang-English-blue)](README.md) [![Russian](https://img.shields.io/badge/lang-Russian-red)](README_RU.md)

# ComfyUI Utils

Небольшие утилиты для обслуживания ComfyUI и связанных репозиториев. Разместите этот репозиторий
внутри папки ComfyUI (например `...\ComfyUI\self_write\comfyui_utils`).

## Инструменты и возможности

- `update_comfy_repos.py` обновляет основной репозиторий ComfyUI и все репозитории в `custom_nodes`
  (пропускает отключенные папки), пишет детальный лог с коммитами и измененными файлами.
- `update_workflow_repos.py` обновляет все Git-репозитории в `../user/default/workflows/github`
  и сообщает о пропущенных папках без Git.
- `comfyui_pip_update_audit.py` сканирует `requirements.txt` в корне ComfyUI и в верхнем уровне
  `custom_nodes`, сравнивает установленные версии с последними и печатает безопасные/рискованные
  команды обновления.
- `requirements_checker/` дает расширенную проверку требований с выбором окружения (venv/conda),
  пользовательскими путями и статусами по каждому пакету.
- `clone-workflow_repos.py` клонирует репозитории воркфлоу из `clone-workflow_repos.txt`
  в папку `github` (предлагает выбрать путь).
- `make_tmp_custom_nodes.py` создает `tmp_custom_nodes.json` со списком загруженных/отключенных
  нод и их URL репозиториев.
- `png_to_json.py` извлекает метаданные воркфлоу ComfyUI из `.png`/`.jpeg` в `.json`.

## Hold / Risk (comfyui_pip_update_audit.py)

- Hold: пакеты из config.json исключаются из обновлений для текущего окружения.
- Pin: пакеты из config.json фиксируются на конкретной версии.
- Risky: `pip --dry-run` сообщает о конфликте зависимостей (ResolutionImpossible, incompatible и т.п.).
- Unknown: `pip --dry-run` упал из-за сети/таймаута/прочих ошибок, не связанных с зависимостями.
