from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from config_schema import (
    emit_env_lines,
    get_value,
    load_v2,
    save_v2,
    set_selected_venv,
    set_value,
)


def _default_config_path() -> str:
    return str(Path(__file__).resolve().parent / "config.json")


def _print_if_value(value: str) -> int:
    if value:
        print(value)
    return 0


def _cmd_ensure(args: argparse.Namespace) -> int:
    _cfg, _changed = load_v2(args.config, auto_migrate=True)
    return 0


def _cmd_get(args: argparse.Namespace) -> int:
    cfg, _ = load_v2(args.config, auto_migrate=True)
    return _print_if_value(get_value(cfg, args.key, venv_name=args.venv_name))


def _cmd_set(args: argparse.Namespace) -> int:
    cfg, _ = load_v2(args.config, auto_migrate=True)
    cfg = set_value(cfg, args.key, args.value)
    save_v2(args.config, cfg)
    return 0


def _cmd_set_selected_venv(args: argparse.Namespace) -> int:
    cfg, _ = load_v2(args.config, auto_migrate=True)
    cfg = set_selected_venv(cfg, venv_path=args.path, venv_name=args.name)
    save_v2(args.config, cfg)
    return 0


def _cmd_emit_env(args: argparse.Namespace) -> int:
    cfg, _ = load_v2(args.config, auto_migrate=True)
    for line in emit_env_lines(cfg, venv_name=args.venv_name):
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ComfyUI config.json migration and query helper")
    parser.add_argument("--config", default=_default_config_path(), help="Path to config.json")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ensure = sub.add_parser("ensure", help="Migrate config.json to current schema")
    p_ensure.set_defaults(func=_cmd_ensure)

    p_get = sub.add_parser("get", help="Read a config value")
    p_get.add_argument("--key", required=True)
    p_get.add_argument("--venv-name", default="")
    p_get.set_defaults(func=_cmd_get)

    p_set = sub.add_parser("set", help="Set a config value")
    p_set.add_argument("--key", required=True)
    p_set.add_argument("--value", required=True)
    p_set.set_defaults(func=_cmd_set)

    p_sel = sub.add_parser("set-selected-venv", help="Set selected venv")
    p_sel.add_argument("--path", required=True, help="Absolute path to venv folder")
    p_sel.add_argument("--name", default="", help="Optional stable venv name")
    p_sel.set_defaults(func=_cmd_set_selected_venv)

    p_emit = sub.add_parser("emit-env", help="Emit env vars as KEY=VALUE lines")
    p_emit.add_argument("--venv-name", default="")
    p_emit.set_defaults(func=_cmd_emit_env)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.config = os.path.abspath(args.config)
    try:
        return int(args.func(args))
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
