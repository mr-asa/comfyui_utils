from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SCHEMA_VERSION = 2


def _read_json(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: str, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def _norm(path: str) -> str:
    return os.path.normpath(path.strip())


def _norm_cmp(path: str) -> str:
    return os.path.normcase(os.path.normpath(path.strip()))


def _is_path_like(value: str) -> bool:
    v = value.strip()
    return bool(v) and ("\\" in v or "/" in v or ":" in v or v.startswith("."))


def _venv_name_from_path(path: str) -> str:
    p = _norm(path)
    name = os.path.basename(p.rstrip("\\/"))
    return name or p


def _empty_v2() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "paths": {
            "comfyui_root": "",
            "custom_nodes_path": "",
            "custom_nodes_repo_path": "",
            "custom_nodes_paths": [],
        },
        "runtime": {
            "env_type": "venv",
            "selected": {
                "kind": "venv",
                "name": "",
            },
        },
        "defaults": {
            "env_vars": {},
        },
        "environments": {
            "venvs": {},
            "conda": {
                "path": "",
                "env": "",
                "env_folder": "",
                "cuda_path": "",
                "env_vars": {},
                "pip": {
                    "hold_packages": [],
                    "pin_packages": {},
                },
            },
        },
        "extras": {},
    }


def _ensure_dict(v: Any) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _ensure_list(v: Any) -> List[Any]:
    return v if isinstance(v, list) else []


def _sanitize_v2(cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = _empty_v2()
    out["schema_version"] = SCHEMA_VERSION

    src_paths = _ensure_dict(cfg.get("paths"))
    out["paths"]["comfyui_root"] = str(src_paths.get("comfyui_root") or "").strip()
    out["paths"]["custom_nodes_path"] = str(src_paths.get("custom_nodes_path") or "").strip()
    out["paths"]["custom_nodes_repo_path"] = str(src_paths.get("custom_nodes_repo_path") or "").strip()
    out["paths"]["custom_nodes_paths"] = [
        str(p).strip() for p in _ensure_list(src_paths.get("custom_nodes_paths")) if isinstance(p, str) and str(p).strip()
    ]

    src_runtime = _ensure_dict(cfg.get("runtime"))
    out["runtime"]["env_type"] = str(src_runtime.get("env_type") or "venv").strip().lower() or "venv"
    src_selected = _ensure_dict(src_runtime.get("selected"))
    out["runtime"]["selected"]["kind"] = str(src_selected.get("kind") or "venv").strip().lower() or "venv"
    out["runtime"]["selected"]["name"] = str(src_selected.get("name") or "").strip()

    src_defaults = _ensure_dict(cfg.get("defaults"))
    raw_default_env = _ensure_dict(src_defaults.get("env_vars"))
    out["defaults"]["env_vars"] = {str(k): str(v) for k, v in raw_default_env.items() if str(k).strip()}

    src_envs = _ensure_dict(cfg.get("environments"))
    src_venvs = _ensure_dict(src_envs.get("venvs"))
    clean_venvs: Dict[str, Any] = {}
    for raw_name, raw_entry in src_venvs.items():
        name = str(raw_name).strip()
        if not name:
            continue
        entry = _ensure_dict(raw_entry)
        venv_path = str(entry.get("path") or "").strip()
        env_vars = _ensure_dict(entry.get("env_vars"))
        pip = _ensure_dict(entry.get("pip"))
        hold_list = [str(p).strip() for p in _ensure_list(pip.get("hold_packages")) if str(p).strip()]
        pin_map = {str(k).strip(): str(v).strip() for k, v in _ensure_dict(pip.get("pin_packages")).items() if str(k).strip()}
        clean_venvs[name] = {
            "path": venv_path,
            "cuda_path": str(entry.get("cuda_path") or "").strip(),
            "env_vars": {str(k): str(v) for k, v in env_vars.items() if str(k).strip()},
            "pip": {
                "hold_packages": hold_list,
                "pin_packages": pin_map,
            },
        }
    out["environments"]["venvs"] = clean_venvs

    src_conda = _ensure_dict(src_envs.get("conda"))
    c_env_vars = _ensure_dict(src_conda.get("env_vars"))
    c_pip = _ensure_dict(src_conda.get("pip"))
    out["environments"]["conda"] = {
        "path": str(src_conda.get("path") or "").strip(),
        "env": str(src_conda.get("env") or "").strip(),
        "env_folder": str(src_conda.get("env_folder") or "").strip(),
        "cuda_path": str(src_conda.get("cuda_path") or "").strip(),
        "env_vars": {str(k): str(v) for k, v in c_env_vars.items() if str(k).strip()},
        "pip": {
            "hold_packages": [str(p).strip() for p in _ensure_list(c_pip.get("hold_packages")) if str(p).strip()],
            "pin_packages": {
                str(k).strip(): str(v).strip()
                for k, v in _ensure_dict(c_pip.get("pin_packages")).items()
                if str(k).strip()
            },
        },
    }

    out["extras"] = _ensure_dict(cfg.get("extras"))
    return out


def _resolve_or_create_venv_name(
    venvs: Dict[str, Dict[str, Any]],
    path_to_name: Dict[str, str],
    token: str,
) -> str:
    raw = token.strip()
    if not raw:
        return ""
    if raw in venvs:
        return raw
    if _is_path_like(raw):
        cmp_path = _norm_cmp(raw)
        existing = path_to_name.get(cmp_path)
        if existing:
            return existing
        base = _venv_name_from_path(raw)
    else:
        base = raw
    name = base
    idx = 2
    while name in venvs:
        name = f"{base}_{idx}"
        idx += 1
    venvs[name] = {
        "path": _norm(raw) if _is_path_like(raw) else "",
        "cuda_path": "",
        "env_vars": {},
        "pip": {"hold_packages": [], "pin_packages": {}},
    }
    if _is_path_like(raw):
        path_to_name[_norm_cmp(raw)] = name
    return name


def _legacy_to_v2(cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = _empty_v2()
    out["schema_version"] = SCHEMA_VERSION

    legacy_known = {
        "schema_version",
        "Comfyui_root",
        "comfyui_root",
        "COMFYUI_ROOT",
        "custom_nodes_path",
        "custom_nodes_paths",
        "custom_nodes_repo_path",
        "env_type",
        "venv_path",
        "venv_paths",
        "conda_path",
        "conda_env",
        "conda_env_folder",
        "holds",
        "hold_packages",
        "pin_packages",
        "env_by_venv",
        "cuda_path_by_venv",
        "cuda_path",
    }
    out["extras"] = {k: v for k, v in cfg.items() if k not in legacy_known}

    comfy_root = (
        str(cfg.get("Comfyui_root") or "").strip()
        or str(cfg.get("comfyui_root") or "").strip()
        or str(cfg.get("COMFYUI_ROOT") or "").strip()
    )
    out["paths"]["comfyui_root"] = comfy_root
    out["paths"]["custom_nodes_path"] = str(cfg.get("custom_nodes_path") or "").strip()
    out["paths"]["custom_nodes_repo_path"] = str(cfg.get("custom_nodes_repo_path") or "").strip()
    out["paths"]["custom_nodes_paths"] = [
        str(p).strip() for p in _ensure_list(cfg.get("custom_nodes_paths")) if isinstance(p, str) and str(p).strip()
    ]

    env_type = str(cfg.get("env_type") or "venv").strip().lower() or "venv"
    out["runtime"]["env_type"] = env_type

    venvs: Dict[str, Dict[str, Any]] = {}
    path_to_name: Dict[str, str] = {}
    for p in _ensure_list(cfg.get("venv_paths")):
        if isinstance(p, str) and p.strip():
            name = _resolve_or_create_venv_name(venvs, path_to_name, p.strip())
            if name and not venvs[name]["path"]:
                venvs[name]["path"] = _norm(p.strip())
    venv_path = str(cfg.get("venv_path") or "").strip()
    selected_name = ""
    if venv_path:
        selected_name = _resolve_or_create_venv_name(venvs, path_to_name, venv_path)
        if selected_name and not venvs[selected_name]["path"]:
            venvs[selected_name]["path"] = _norm(venv_path)

    env_by_venv = _ensure_dict(cfg.get("env_by_venv"))
    for raw_key, raw_entry in env_by_venv.items():
        key = str(raw_key).strip()
        if not key:
            continue
        if key == "__all__":
            out["defaults"]["env_vars"] = {
                str(k): str(v)
                for k, v in _ensure_dict(raw_entry).items()
                if str(k).strip()
            }
            continue
        name = _resolve_or_create_venv_name(venvs, path_to_name, key)
        if not name:
            continue
        venvs[name]["env_vars"] = {
            str(k): str(v) for k, v in _ensure_dict(raw_entry).items() if str(k).strip()
        }

    cuda_map = _ensure_dict(cfg.get("cuda_path_by_venv"))
    for raw_key, raw_cuda in cuda_map.items():
        key = str(raw_key).strip()
        if not key:
            continue
        name = _resolve_or_create_venv_name(venvs, path_to_name, key)
        if not name:
            continue
        venvs[name]["cuda_path"] = str(raw_cuda or "").strip()

    holds = _ensure_dict(cfg.get("holds"))
    for raw_env_key, raw_entry in holds.items():
        env_key = str(raw_env_key).strip()
        entry = _ensure_dict(raw_entry)
        hold_list = [str(p).strip() for p in _ensure_list(entry.get("hold_packages")) if str(p).strip()]
        pin_map = {str(k).strip(): str(v).strip() for k, v in _ensure_dict(entry.get("pin_packages")).items() if str(k).strip()}
        if env_key.startswith("conda:"):
            out["environments"]["conda"]["pip"]["hold_packages"] = hold_list
            out["environments"]["conda"]["pip"]["pin_packages"] = pin_map
            continue
        if _is_path_like(env_key):
            name = _resolve_or_create_venv_name(venvs, path_to_name, env_key)
        else:
            name = _resolve_or_create_venv_name(venvs, path_to_name, env_key)
        if name:
            venvs[name]["pip"]["hold_packages"] = hold_list
            venvs[name]["pip"]["pin_packages"] = pin_map

    if not holds:
        # Legacy flat hold/pin fallback.
        hold_list = [str(p).strip() for p in _ensure_list(cfg.get("hold_packages")) if str(p).strip()]
        pin_map = {str(k).strip(): str(v).strip() for k, v in _ensure_dict(cfg.get("pin_packages")).items() if str(k).strip()}
        if selected_name:
            venvs[selected_name]["pip"]["hold_packages"] = hold_list
            venvs[selected_name]["pip"]["pin_packages"] = pin_map

    out["environments"]["venvs"] = venvs
    if not selected_name and venvs:
        selected_name = next(iter(venvs.keys()))
    out["runtime"]["selected"] = {"kind": "venv", "name": selected_name}

    out["environments"]["conda"]["path"] = str(cfg.get("conda_path") or "").strip()
    out["environments"]["conda"]["env"] = str(cfg.get("conda_env") or "").strip()
    out["environments"]["conda"]["env_folder"] = str(cfg.get("conda_env_folder") or "").strip()
    out["environments"]["conda"]["cuda_path"] = str(cfg.get("cuda_path") or "").strip()
    return _sanitize_v2(out)


def _v2_to_legacy(v2: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _sanitize_v2(v2)
    out: Dict[str, Any] = copy.deepcopy(_ensure_dict(cfg.get("extras")))

    paths = _ensure_dict(cfg.get("paths"))
    runtime = _ensure_dict(cfg.get("runtime"))
    envs = _ensure_dict(cfg.get("environments"))
    venvs = _ensure_dict(envs.get("venvs"))
    conda = _ensure_dict(envs.get("conda"))

    comfy_root = str(paths.get("comfyui_root") or "").strip()
    if comfy_root:
        out["Comfyui_root"] = comfy_root
    custom_nodes_path = str(paths.get("custom_nodes_path") or "").strip()
    if custom_nodes_path:
        out["custom_nodes_path"] = custom_nodes_path
    custom_nodes_repo_path = str(paths.get("custom_nodes_repo_path") or "").strip()
    if custom_nodes_repo_path:
        out["custom_nodes_repo_path"] = custom_nodes_repo_path

    custom_nodes_paths = [str(p).strip() for p in _ensure_list(paths.get("custom_nodes_paths")) if str(p).strip()]
    if custom_nodes_paths:
        out["custom_nodes_paths"] = custom_nodes_paths

    env_type = str(runtime.get("env_type") or "venv").strip().lower() or "venv"
    out["env_type"] = env_type

    venv_paths: List[str] = []
    name_to_path: Dict[str, str] = {}
    for name, raw_entry in venvs.items():
        entry = _ensure_dict(raw_entry)
        p = str(entry.get("path") or "").strip()
        if p:
            venv_paths.append(_norm(p))
            name_to_path[str(name)] = _norm(p)
    if venv_paths:
        out["venv_paths"] = venv_paths

    selected = _ensure_dict(runtime.get("selected"))
    selected_name = str(selected.get("name") or "").strip()
    if selected_name and selected_name in name_to_path:
        out["venv_path"] = name_to_path[selected_name]
    elif venv_paths:
        out["venv_path"] = venv_paths[0]

    env_by_venv: Dict[str, Any] = {}
    defaults = _ensure_dict(cfg.get("defaults"))
    default_env = _ensure_dict(defaults.get("env_vars"))
    if default_env:
        env_by_venv["__all__"] = default_env
    for name, raw_entry in venvs.items():
        env_vars = _ensure_dict(_ensure_dict(raw_entry).get("env_vars"))
        if env_vars:
            env_by_venv[str(name)] = env_vars
    if env_by_venv:
        out["env_by_venv"] = env_by_venv

    cuda_by_venv: Dict[str, str] = {}
    holds: Dict[str, Any] = {}
    for name, raw_entry in venvs.items():
        entry = _ensure_dict(raw_entry)
        p = str(entry.get("path") or "").strip()
        if not p:
            continue
        cuda = str(entry.get("cuda_path") or "").strip()
        if cuda:
            cuda_by_venv[str(name)] = cuda
        pip = _ensure_dict(entry.get("pip"))
        hold_packages = [str(x).strip() for x in _ensure_list(pip.get("hold_packages")) if str(x).strip()]
        pin_packages = {str(k).strip(): str(v).strip() for k, v in _ensure_dict(pip.get("pin_packages")).items() if str(k).strip()}
        if hold_packages or pin_packages:
            holds[_norm_cmp(p)] = {
                "hold_packages": hold_packages,
                "pin_packages": pin_packages,
            }
    if cuda_by_venv:
        out["cuda_path_by_venv"] = cuda_by_venv

    if conda:
        cpath = str(conda.get("path") or "").strip()
        cenv = str(conda.get("env") or "").strip()
        cfolder = str(conda.get("env_folder") or "").strip()
        if cpath:
            out["conda_path"] = cpath
        if cenv:
            out["conda_env"] = cenv
        if cfolder:
            out["conda_env_folder"] = cfolder
        ccuda = str(conda.get("cuda_path") or "").strip()
        if ccuda:
            out["cuda_path"] = ccuda
        cpip = _ensure_dict(conda.get("pip"))
        c_hold = [str(x).strip() for x in _ensure_list(cpip.get("hold_packages")) if str(x).strip()]
        c_pin = {str(k).strip(): str(v).strip() for k, v in _ensure_dict(cpip.get("pin_packages")).items() if str(k).strip()}
        if c_hold or c_pin:
            c_key = f"conda:{cenv}" if cenv else (cfolder or "conda:default")
            holds[c_key] = {
                "hold_packages": c_hold,
                "pin_packages": c_pin,
            }

    if holds:
        out["holds"] = holds
    return out


def migrate_data(cfg: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(cfg, dict):
        return _empty_v2()
    version = cfg.get("schema_version")
    if isinstance(version, int) and version >= SCHEMA_VERSION:
        return _sanitize_v2(cfg)
    return _legacy_to_v2(cfg)


def load_v2(path: str, auto_migrate: bool = True) -> Tuple[Dict[str, Any], bool]:
    raw = _read_json(path)
    migrated = migrate_data(raw)
    changed = raw != migrated
    if auto_migrate and changed:
        _write_json(path, migrated)
    return migrated, changed


def save_v2(path: str, cfg_v2: Dict[str, Any]) -> None:
    _write_json(path, _sanitize_v2(cfg_v2))


def load_legacy_compat(path: str, auto_migrate: bool = True) -> Tuple[Dict[str, Any], bool]:
    v2, changed = load_v2(path, auto_migrate=auto_migrate)
    return _v2_to_legacy(v2), changed


def save_legacy_compat(path: str, legacy_cfg: Dict[str, Any]) -> None:
    v2 = migrate_data(legacy_cfg)
    save_v2(path, v2)


def _get_selected_venv_name(cfg_v2: Dict[str, Any]) -> str:
    runtime = _ensure_dict(cfg_v2.get("runtime"))
    selected = _ensure_dict(runtime.get("selected"))
    if str(selected.get("kind") or "venv").lower() != "venv":
        return ""
    return str(selected.get("name") or "").strip()


def _get_selected_venv_entry(cfg_v2: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    envs = _ensure_dict(cfg_v2.get("environments"))
    venvs = _ensure_dict(envs.get("venvs"))
    name = _get_selected_venv_name(cfg_v2)
    entry = _ensure_dict(venvs.get(name))
    return name, entry


def get_value(cfg_v2: Dict[str, Any], key: str, venv_name: Optional[str] = None) -> str:
    cfg = _sanitize_v2(cfg_v2)
    paths = _ensure_dict(cfg.get("paths"))
    envs = _ensure_dict(cfg.get("environments"))
    venvs = _ensure_dict(envs.get("venvs"))
    runtime = _ensure_dict(cfg.get("runtime"))
    conda = _ensure_dict(envs.get("conda"))

    if key == "comfyui_root":
        return str(paths.get("comfyui_root") or "").strip()
    if key == "custom_nodes_path":
        return str(paths.get("custom_nodes_path") or "").strip()
    if key == "custom_nodes_repo_path":
        return str(paths.get("custom_nodes_repo_path") or "").strip()
    if key == "env_type":
        return str(runtime.get("env_type") or "venv").strip().lower()
    if key == "selected_venv_name":
        return _get_selected_venv_name(cfg)
    if key == "selected_venv_path":
        _, entry = _get_selected_venv_entry(cfg)
        return str(entry.get("path") or "").strip()
    if key == "selected_python":
        _, entry = _get_selected_venv_entry(cfg)
        p = str(entry.get("path") or "").strip()
        return os.path.join(p, "Scripts", "python.exe") if p else ""
    if key == "python_for_active_env":
        env_type = str(runtime.get("env_type") or "venv").strip().lower()
        if env_type == "venv":
            return get_value(cfg, "selected_python")
        cfolder = str(conda.get("env_folder") or "").strip()
        return os.path.join(cfolder, "python.exe") if cfolder else ""
    if key == "venv_cuda_path":
        target_name = (venv_name or "").strip() or _get_selected_venv_name(cfg)
        entry = _ensure_dict(venvs.get(target_name))
        cuda = str(entry.get("cuda_path") or "").strip()
        if cuda:
            return cuda
        return str(conda.get("cuda_path") or "").strip()
    return ""


def set_value(cfg_v2: Dict[str, Any], key: str, value: str) -> Dict[str, Any]:
    cfg = _sanitize_v2(cfg_v2)
    paths = _ensure_dict(cfg.get("paths"))
    envs = _ensure_dict(cfg.get("environments"))
    conda = _ensure_dict(envs.get("conda"))
    if key == "comfyui_root":
        paths["comfyui_root"] = value.strip()
    elif key == "custom_nodes_path":
        paths["custom_nodes_path"] = value.strip()
    elif key == "custom_nodes_repo_path":
        paths["custom_nodes_repo_path"] = value.strip()
    elif key == "env_type":
        cfg["runtime"]["env_type"] = (value.strip().lower() or "venv")
    elif key == "conda_path":
        conda["path"] = value.strip()
    elif key == "conda_env":
        conda["env"] = value.strip()
    elif key == "conda_env_folder":
        conda["env_folder"] = value.strip()
    return cfg


def set_selected_venv(cfg_v2: Dict[str, Any], venv_path: str, venv_name: Optional[str] = None) -> Dict[str, Any]:
    cfg = _sanitize_v2(cfg_v2)
    envs = _ensure_dict(cfg.get("environments"))
    venvs = _ensure_dict(envs.get("venvs"))
    norm_path = _norm(venv_path)
    name = (venv_name or "").strip() or _venv_name_from_path(norm_path)
    if not name:
        name = norm_path
    if name not in venvs:
        venvs[name] = {
            "path": norm_path,
            "cuda_path": "",
            "env_vars": {},
            "pip": {"hold_packages": [], "pin_packages": {}},
        }
    else:
        entry = _ensure_dict(venvs.get(name))
        if not str(entry.get("path") or "").strip():
            entry["path"] = norm_path
        venvs[name] = entry
    cfg["runtime"]["env_type"] = "venv"
    cfg["runtime"]["selected"] = {"kind": "venv", "name": name}
    return cfg


def emit_env_lines(cfg_v2: Dict[str, Any], venv_name: Optional[str] = None) -> List[str]:
    cfg = _sanitize_v2(cfg_v2)
    defaults = _ensure_dict(_ensure_dict(cfg.get("defaults")).get("env_vars"))
    envs = _ensure_dict(cfg.get("environments"))
    venvs = _ensure_dict(envs.get("venvs"))
    selected = (venv_name or "").strip() or _get_selected_venv_name(cfg)
    current = _ensure_dict(venvs.get(selected)).get("env_vars")
    env_vars = {str(k): str(v) for k, v in defaults.items() if str(k).strip()}
    env_vars.update({str(k): str(v) for k, v in _ensure_dict(current).items() if str(k).strip()})
    return [f"{k}={v}" for k, v in env_vars.items()]
