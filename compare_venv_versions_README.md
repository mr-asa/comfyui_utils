# compare_venv_versions: config format

[Русский](compare_venv_versions_README_RU.md)

## Overview
There are now 2 config layers:

1. Simple: `modules`  
   A plain list of module names.

2. Targeted (advanced cases): `custom_checks`  
   For packages where pip name and import name differ, or when custom logic is needed.

Python and CUDA versions are controlled by separate flags:
- `include_python`
- `include_cuda_from_torch`

## How It Works

1. `include_python=true` adds the `Python` row.
2. `include_cuda_from_torch=true` adds the `CUDA` row.
3. Each `modules` item adds a `module` check row with `candidates=[<name>]`.
4. `custom_checks` are appended last and let you define:
   - `name` - row label in the table,
   - `kind` - check type,
   - `candidates` - package/module name options.

## What `kind` Means

Supported values:
- `python` - Python version from the venv,
- `cuda_from_torch` - `torch.version.cuda`,
- `module` - module version check.

For `module`, the script does:
1. `importlib.metadata.version(candidate)` (pip name),
2. if not found: `import candidate` and read `__version__`,
3. otherwise `-`.

## How to add `transformers`

Simplest option:

```json
"modules": ["torch", "xformers", "triton", "transformers"]
```

## When to use `custom_checks`

Use `custom_checks` if:
- pip and import names differ,
- you want a cleaner row name in the table,
- you need multiple candidate names.

Quick shell check for one venv:

```powershell
$py = "C:\ComfyUI\ComfyUI\.venv\Scripts\python.exe"
& $py -m pip show flash-attn
& $py -c "import flash_attn; print(flash_attn.__version__)"
```

If names differ, add `custom_checks` with both variants in `candidates`.
