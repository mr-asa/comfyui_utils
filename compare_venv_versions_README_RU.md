# compare_venv_versions: новый формат настроек

## Главное
Теперь есть 2 уровня настройки:

1. Простой: `modules`  
   Просто список модулей строками.

2. Точечный (сложные случаи): `custom_checks`  
   Для пакетов, где pip-имя и import-имя отличаются, или нужна особая логика.

Версия Python и CUDA включаются отдельными флагами:
- `include_python`
- `include_cuda_from_torch`

## Как это работает

1. `include_python=true` добавляет строку `Python`.
2. `include_cuda_from_torch=true` добавляет строку `CUDA`.
3. Каждый элемент `modules` добавляет строку-проверку типа `module` с `candidates=[<имя>]`.
4. `custom_checks` добавляются последними и позволяют вручную задать:
   - `name` - как назвать строку в таблице,
   - `kind` - тип проверки,
   - `candidates` - варианты имени пакета/модуля.

## Что такое `kind`

Поддерживаются:
- `python` - версия Python из venv,
- `cuda_from_torch` - `torch.version.cuda`,
- `module` - проверка версии модуля.

Для `module` скрипт делает:
1. `importlib.metadata.version(candidate)` (pip-имя),
2. если не найдено: `import candidate` и `__version__`,
3. иначе `-`.

## Как добавить `transformers`

Самый простой вариант:

```json
"modules": ["torch", "xformers", "triton", "transformers"]
```

## Как понять, нужен ли `custom_checks`

Используйте `custom_checks`, если:
- имя в pip и в import разные,
- хотите красивое имя строки в таблице,
- нужно несколько candidate-имен.

Быстрая проверка в shell для одного venv:

```powershell
$py = "C:\ComfyUI\ComfyUI\.venv\Scripts\python.exe"
& $py -m pip show flash-attn
& $py -c "import flash_attn; print(flash_attn.__version__)"
```

Если имена отличаются, добавляйте `custom_checks` с двумя вариантами в `candidates`.
