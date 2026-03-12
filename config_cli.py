from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from config_schema import (
    add_or_update_venv,
    emit_env_lines,
    get_value,
    list_venvs,
    load_v2,
    prune_missing_venvs,
    remove_venv,
    save_v2,
    set_venv_comment,
    set_selected_venv,
    set_value,
    venv_exists,
    venv_python_path,
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


def _print_venv_rows(cfg: dict) -> None:
    rows = list_venvs(cfg)
    for row in rows:
        print(
            "\t".join(
                [
                    row["name"],
                    row["path"],
                    row["comment"],
                    row["exists"],
                    row["selected"],
                ]
            )
        )


def _cmd_list_venvs(args: argparse.Namespace) -> int:
    cfg, _ = load_v2(args.config, auto_migrate=True)
    _print_venv_rows(cfg)
    return 0


def _cmd_add_venv(args: argparse.Namespace) -> int:
    cfg, _ = load_v2(args.config, auto_migrate=True)
    cfg = add_or_update_venv(cfg, venv_path=args.path, venv_name=args.name or None, comment=args.comment)
    save_v2(args.config, cfg)
    return 0


def _cmd_remove_venv(args: argparse.Namespace) -> int:
    cfg, _ = load_v2(args.config, auto_migrate=True)
    cfg = remove_venv(cfg, args.name)
    save_v2(args.config, cfg)
    return 0


def _cmd_set_venv_comment(args: argparse.Namespace) -> int:
    cfg, _ = load_v2(args.config, auto_migrate=True)
    cfg = set_venv_comment(cfg, args.name, args.comment)
    save_v2(args.config, cfg)
    return 0


def _cmd_prune_missing_venvs(args: argparse.Namespace) -> int:
    cfg, _ = load_v2(args.config, auto_migrate=True)
    cfg, removed = prune_missing_venvs(cfg)
    save_v2(args.config, cfg)
    if removed:
        for name in removed:
            print(name)
    return 0


def _prompt(msg: str) -> str:
    return input(msg).strip()


def _choose_default_idx(rows: list[dict]) -> int:
    for i, row in enumerate(rows, 1):
        if row.get("selected") == "1":
            return i
    return 1 if rows else 0


def _cmd_select_venv(args: argparse.Namespace) -> int:
    cfg, _ = load_v2(args.config, auto_migrate=True)
    while True:
        rows = list_venvs(cfg)
        missing = [r for r in rows if r["exists"] != "1"]
        if missing:
            print("Missing venv entries found:")
            for r in missing:
                print(f" - {r['name']}: {r['path']}")
            ans = _prompt("Remove missing entries from config now? [Y/n]: ").lower()
            if ans in ("", "y", "yes"):
                cfg, _removed = prune_missing_venvs(cfg)
                save_v2(args.config, cfg)
                rows = list_venvs(cfg)

        if rows:
            print("\n--> Select venv <--")
            idx_w = max(1, len(str(len(rows))))
            path_w = max(1, max((len(r["path"]) for r in rows), default=0))
            for i, r in enumerate(rows, 1):
                mark = "*" if r["selected"] == "1" else " "
                comment = r["comment"] or "-"
                if r["exists"] != "1":
                    comment = f"{comment} [missing]"
                print(f" {i:>{idx_w}}){mark} {r['path']:<{path_w}} | {comment}")
            print("\nA=add new, D=delete, C=comment")
            default_idx = _choose_default_idx(rows)
            raw = _prompt(f"Choice [{default_idx}]: ")
            if not raw:
                raw = str(default_idx)
            low = raw.lower()
            if low in ("a", "add"):
                new_path = _prompt("New venv path: ")
                if not new_path:
                    continue
                if not venv_exists(new_path):
                    print("Python executable not found in this venv path.")
                    continue
                new_comment = _prompt("Comment (optional): ")
                cfg = add_or_update_venv(cfg, venv_path=new_path, venv_name=None, comment=new_comment)
                save_v2(args.config, cfg)
                continue
            if low in ("d", "del", "delete"):
                which = _prompt("Index to delete: ")
                if which.isdigit():
                    idx = int(which)
                    if 1 <= idx <= len(rows):
                        cfg = remove_venv(cfg, rows[idx - 1]["name"])
                        save_v2(args.config, cfg)
                continue
            if low in ("c", "comment"):
                which = _prompt("Index for comment: ")
                if which.isdigit():
                    idx = int(which)
                    if 1 <= idx <= len(rows):
                        new_comment = _prompt("New comment (empty to clear): ")
                        cfg = set_venv_comment(cfg, rows[idx - 1]["name"], new_comment)
                        save_v2(args.config, cfg)
                continue
            if raw.isdigit():
                idx = int(raw)
                if 1 <= idx <= len(rows):
                    selected = rows[idx - 1]
                    if selected["exists"] != "1":
                        print("Selected venv is missing on disk.")
                        continue
                    cfg = set_selected_venv(cfg, selected["path"], selected["name"])
                    save_v2(args.config, cfg)
                    lines = [
                        f"SELECTED_NAME={selected['name']}",
                        f"SELECTED_PATH={selected['path']}",
                        f"SELECTED_PYTHON={venv_python_path(selected['path'])}",
                    ]
                    if args.out:
                        with open(args.out, "w", encoding="utf-8") as f:
                            f.write("\n".join(lines) + "\n")
                    else:
                        for ln in lines:
                            print(ln)
                    return 0
            print("Invalid choice.")
            continue

        print("No venv entries in config.")
        new_path = _prompt("Add new venv path (or empty to cancel): ")
        if not new_path:
            return 1
        if not venv_exists(new_path):
            print("Python executable not found in this venv path.")
            continue
        new_comment = _prompt("Comment (optional): ")
        cfg = add_or_update_venv(cfg, venv_path=new_path, venv_name=None, comment=new_comment)
        save_v2(args.config, cfg)


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

    p_list = sub.add_parser("list-venvs", help="List venv records from config DB")
    p_list.set_defaults(func=_cmd_list_venvs)

    p_add = sub.add_parser("add-venv", help="Add/update venv in config DB")
    p_add.add_argument("--path", required=True)
    p_add.add_argument("--name", default="")
    p_add.add_argument("--comment", default="")
    p_add.set_defaults(func=_cmd_add_venv)

    p_rm = sub.add_parser("remove-venv", help="Remove venv from config DB by name")
    p_rm.add_argument("--name", required=True)
    p_rm.set_defaults(func=_cmd_remove_venv)

    p_cmt = sub.add_parser("set-venv-comment", help="Set venv comment by name")
    p_cmt.add_argument("--name", required=True)
    p_cmt.add_argument("--comment", required=True)
    p_cmt.set_defaults(func=_cmd_set_venv_comment)

    p_prune = sub.add_parser("prune-missing-venvs", help="Remove missing venv paths from config DB")
    p_prune.set_defaults(func=_cmd_prune_missing_venvs)

    p_select = sub.add_parser("select-venv", help="Interactive venv selection from config DB")
    p_select.add_argument("--out", default="", help="Optional output file for SELECTED_* lines")
    p_select.set_defaults(func=_cmd_select_venv)
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
