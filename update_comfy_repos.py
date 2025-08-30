#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ComfyUI Repo Updater
====================

Обновляет сначала сам ComfyUI, затем все плагины в каталоге custom_nodes
(папки с признаком disabled пропускаются).

Формат лога для каждого репозитория:

--- <Название репозитория> ---
<web ссылка на репозиторий>
ветка: <название ветки или DETACHED>
<лог обновления>
  - если есть изменения: два блока
      * Сообщения коммитов (между старым и новым HEAD)
      * Изменённые файлы в формате: +<added> -<deleted> <path>
  - если изменений нет: "Правок не было."

Примеры запуска:
    python update_comfy_repos.py --root "F:/ComfyUI/ComfyUI"
    python update_comfy_repos.py --root "/opt/ComfyUI/ComfyUI" --plugins-dir custom_nodes

Политики и переопределения:
  - POLICIES: настраиваемое поведение при локальных правках и способ pull.
  - REMOTE_OVERRIDES: задать URL origin для репо, где он не найден.
  - BRANCH_OVERRIDES: задать ветку, если HEAD detached или нужна нестандартная ветка.

Никаких сторонних библиотек. Требуется установленный Git в PATH.
"""

from __future__ import annotations
import argparse
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple


# Цвета ANSI
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"
    GRAY = "\033[90m"


@dataclass
class UpdateResult:
    name: str
    path: str
    web_url: str
    branch: str
    changed: bool
    commit_messages: List[str]
    numstat: List[Tuple[int, int, str]]
    notes: List[str]
    error: Optional[str] = None

IGNORED_DIRS = {"__pycache__", ".idea", ".vscode", "venv", "env", ".disabled"}

# ===================== Пользовательская конфигурация =====================

# Дефолтная политика. Возможные значения:
#   on_local_changes: "stash" | "commit" | "reset" | "skip" | "abort"
#   pull_method:      "merge" | "rebase"
#   pull_from:        "origin" | "upstream"
#   set_remote_if_missing: True/False — добавлять origin из REMOTE_OVERRIDES, если отсутствует
#   auto_stash_pop:   True/False — автоматически применять stash pop после удачного pull
POLICIES: Dict[str, Dict[str, object]] = {
    "default": {
        "on_local_changes": "stash",
        "pull_method": "rebase",
        "pull_from": "origin",
        "set_remote_if_missing": True,
        "auto_stash_pop": True,
    },
    # Примеры точечных переопределений по имени папки/пути (regex):
    # r"ComfyUI$": {"pull_method": "rebase"},
    # r"MyForkedNode$": {"on_local_changes": "skip", "pull_from": "origin"},
}

# Если у репозитория нет origin.url, можно указать его здесь.
# Ключ — имя папки репозитория или абсолютный путь; значение — URL.
REMOTE_OVERRIDES: Dict[str, str] = {
    # "FooNode": "https://github.com/user/FooNode.git",
    # r".*BarNode$": "git@github.com:user/BarNode.git",
}

# Переопределение веток. Полезно для detached HEAD или нестандартных веток.
BRANCH_OVERRIDES: Dict[str, str] = {
    # "ComfyUI": "master",
}

# Если True, репозитории без .git будут помечены как ошибка с подсказкой.
# Безопаснее оставить False. Если выставить True, можно попробовать автоинициализацию
# (git init + remote add), но по умолчанию этот функционал отключён как рискованный.
AUTO_INIT_MISSING_GIT = False

# ========================================================================

@dataclass
class UpdateResult:
    name: str
    path: str
    web_url: str
    branch: str
    changed: bool
    commit_messages: List[str]
    numstat: List[Tuple[int, int, str]]  # (added, deleted, path)
    notes: List[str]
    error: Optional[str] = None


def main() -> int:
    parser = argparse.ArgumentParser(description="Update ComfyUI and its plugins.")
    parser.add_argument("--root", required=True, help="Путь к корню репозитория ComfyUI")
    parser.add_argument("--plugins-dir", default="custom_nodes", help="Каталог с плагинами относительно root")
    parser.add_argument("--include-disabled", action="store_true", help="Не игнорировать папки с признаком disabled")
    parser.add_argument("--only", nargs="*", default=None, help="Обновлять только репозитории, содержащие эти подстроки/regex")
    parser.add_argument("--skip", nargs="*", default=None, help="Пропускать репозитории, соответствующие подстрокам/regex")
    parser.add_argument("--dry-run", action="store_true", help="Показывать, что будет сделано, без git изменений")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    plugins_dir = os.path.join(root, args.plugins_dir)

    repos: List[str] = []

    # 1) Сначала сам ComfyUI (root)
    if os.path.isdir(os.path.join(root, ".git")):
        repos.append(root)
    else:
        print("ВНИМАНИЕ: Папка root не является git-репозиторием:", root)

    # 2) Затем плагины из plugins_dir (только директории верхнего уровня)
    if os.path.isdir(plugins_dir):
        for name in sorted(os.listdir(plugins_dir)):
            if name in IGNORED_DIRS:
                continue
            path = os.path.join(plugins_dir, name)
            if not os.path.isdir(path):
                continue
            repos.append(path)

    # Фильтры only/skip
    # repos = apply_filters(repos, only=args.only, skip=args.skip)

    # Обновляем по очереди
    any_errors = False
    for repo_path in repos:
        res = update_repo(repo_path, dry_run=args.dry_run)
        print_report(res)
        if res.error:
            any_errors = True

    return 1 if any_errors else 0


def apply_filters(paths: List[str], only: Optional[List[str]], skip: Optional[List[str]]) -> List[str]:
    def match_any(patterns: List[str], text: str) -> bool:
        for p in patterns:
            # Поддержка как подстроки, так и regex
            if p.startswith("r/") and p.endswith("/"):
                if re.search(p[2:-1], text):
                    return True
            elif p in text:
                return True
        return False

    out = []
    for p in paths:
        if only and not match_any(only, p):
            continue
        if skip and match_any(skip, p):
            continue
        out.append(p)
    return out


def is_disabled_dir(name: str, path: str) -> bool:
    """Определяем, помечена ли папка как disabled различными популярными способами."""
    low = name.lower()
    if low.startswith("disabled") or low.endswith(".disabled"):
        return True
    markers = [".disabled", "DISABLED", "disabled", "_disabled"]
    for m in markers:
        if os.path.exists(os.path.join(path, m)):
            return True
    # Специальная папка, куда складывают отключённые плагины
    if os.path.basename(os.path.dirname(path)).lower() in ("disabled", "custom_nodes_disabled"):
        return True
    return False


def update_repo(path: str, dry_run: bool = False) -> UpdateResult:
    name = os.path.basename(path.rstrip(os.sep))

    # Собираем политику (regex по ключам POLICIES, fallback на default)
    policy = dict(POLICIES.get("default", {}))
    for pat, cfg in POLICIES.items():
        if pat == "default":
            continue
        try:
            if re.search(pat, path) or re.search(pat, name):
                policy.update(cfg)
        except re.error:
            # Воспринимать как простое совпадение по концу имени
            if pat in path or pat in name:
                policy.update(cfg)

    # Получаем origin url (или из overrides), ветку и т.п.
    web_url = ""
    branch = ""
    notes: List[str] = []

    if not os.path.isdir(os.path.join(path, ".git")):
        err = (
            "Папка не является git-репозиторием. "
            "Либо инициализируйте git в этой папке, либо укажите URL в REMOTE_OVERRIDES."
        )
        # Подсказки
        notes.extend(remedy_not_git_repo(name, path))
        return UpdateResult(name, path, web_url, branch or "", False, [], [], notes, error=err)

    # Cчитываем текущие параметры репозитория
    remote = (policy.get("pull_from") or "origin").strip()
    origin_url = get_remote_url(path, remote)

    # Применяем REMOTE_OVERRIDES, если origin отсутствует и политика разрешает
    override_url = resolve_remote_override(name, path)
    if not origin_url and override_url and policy.get("set_remote_if_missing", True):
        if dry_run:
            notes.append(f"[dry-run] Добавил бы удалённый '{remote}': {override_url}")
        else:
            ok, out, err = run_git(["remote", "add", remote, override_url], cwd=path)
            if ok:
                origin_url = override_url
                notes.append(f"Добавлен удалённый '{remote}': {override_url}")
            else:
                return UpdateResult(name, path, web_url, branch or "", False, [], [], notes,
                                    error=f"Не удалось добавить remote '{remote}': {err or out}")

    if not origin_url:
        err = (
            f"У репозитория нет удалённого '{remote}'. Укажите URL в REMOTE_OVERRIDES или добавьте remote вручную."
        )
        notes.extend(remedy_no_remote(name, path))
        return UpdateResult(name, path, web_url, branch or "", False, [], [], notes, error=err)

    web_url = to_web_url(origin_url)

    # Определяем ветку
    branch = get_current_branch(path)
    if (not branch) or branch == "HEAD":
        # попробуем overrides
        forced = resolve_branch_override(name, path)
        if forced:
            branch = forced
            notes.append(f"HEAD detached — использую ветку из BRANCH_OVERRIDES: {branch}")
        else:
            notes.append("HEAD detached — попытка пулла без указания ветки может быть небезопасной")

    # Сохраняем старый HEAD
    old_head = get_head_commit(path) or ""

    # Обработка локальных изменений
    if working_tree_dirty(path):
        action = str(policy.get("on_local_changes", "stash"))
        if action == "skip":
            return UpdateResult(name, path, web_url, branch or "", False, [], [],
                                notes + ["Пропускаю: есть локальные изменения (policy: skip)"], error=None)
        elif action == "abort":
            return UpdateResult(name, path, web_url, branch or "", False, [], [],
                                notes + ["Остановлено: есть локальные изменения (policy: abort)"], error=
                                "Локальные изменения. Измените политику или очистите рабочее дерево.")
        elif action == "reset":
            if dry_run:
                notes.append("[dry-run] Выполнил бы: git reset --hard")
            else:
                ok, out, err = run_git(["reset", "--hard"], cwd=path)
                if not ok:
                    return UpdateResult(name, path, web_url, branch or "", False, [], [], notes,
                                        error=f"Не удалось выполнить reset --hard: {err or out}")
                notes.append("Выполнен reset --hard (локальные изменения отброшены)")
        elif action == "commit":
            if dry_run:
                notes.append("[dry-run] Выполнил бы auto-commit локальных изменений")
            else:
                run_git(["add", "-A"], cwd=path)
                msg = f"chore: auto-commit before update ({datetime.now().isoformat(timespec='seconds')})"
                ok, out, err = run_git(["commit", "-m", msg], cwd=path)
                if ok:
                    notes.append("Сделан auto-commit локальных изменений")
                else:
                    # могло быть нечего коммитить
                    if "nothing to commit" in (out + err).lower():
                        pass
                    else:
                        return UpdateResult(name, path, web_url, branch or "", False, [], [], notes,
                                            error=f"Ошибка auto-commit: {err or out}")
        else:  # stash (по умолчанию)
            if dry_run:
                notes.append("[dry-run] Выполнил бы: git stash push -u")
            else:
                msg = f"auto-stash: update script {datetime.now().isoformat(timespec='seconds')}"
                ok, out, err = run_git(["stash", "push", "-u", "-m", msg], cwd=path)
                if ok:
                    notes.append("Сделан stash локальных изменений")
                else:
                    return UpdateResult(name, path, web_url, branch or "", False, [], [], notes,
                                        error=f"Не удалось выполнить stash: {err or out}")

    # Выполняем pull
    pull_args = ["pull"]
    method = str(policy.get("pull_method", "merge"))
    if method == "rebase":
        pull_args.append("--rebase")
    pull_args.append(str(policy.get("pull_from", "origin")))
    if branch:
        pull_args.append(branch)

    if dry_run:
        notes.append("[dry-run] Выполнил бы: git " + " ".join(shlex.quote(a) for a in pull_args))
        new_head = old_head
        changed = False
        commit_msgs: List[str] = []
        numstat: List[Tuple[int, int, str]] = []
    else:
        ok, out, err = run_git(pull_args, cwd=path)
        if not ok:
            # типичные подсказки при ошибках pull
            hints = remedy_pull_failure(out, err)
            notes.extend(hints)
            return UpdateResult(name, path, web_url, branch or "", False, [], [], notes,
                                error=f"Не удалось выполнить git pull: {(err or out).strip()}")

        # Новый HEAD
        new_head = get_head_commit(path) or ""
        changed = old_head != new_head and bool(old_head)

        if changed:
            commit_msgs = get_commit_messages(path, old_head, new_head)
            numstat = get_numstat(path, old_head, new_head)
        else:
            commit_msgs = []
            numstat = []

        # Авто-возврат stash
        if policy.get("auto_stash_pop", True) and stash_has_items(path):
            ok, out, err = run_git(["stash", "pop"], cwd=path)
            if ok:
                notes.append("stash pop выполнен")
            else:
                notes.append("Не удалось применить stash pop — возможно, конфликты. Оставлен в stash.")

    return UpdateResult(name, path, web_url, branch or "", changed, commit_msgs, numstat, notes, error=None)


# ------------------------- Git helpers -------------------------

def run_git(args: List[str], cwd: str) -> Tuple[bool, str, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        out = proc.stdout.decode("utf-8", errors="replace")
        err = proc.stderr.decode("utf-8", errors="replace")
        return proc.returncode == 0, out, err
    except FileNotFoundError:
        return False, "", "Git не найден в PATH. Установите Git."


def get_remote_url(path: str, remote: str = "origin") -> str:
    ok, out, err = run_git(["config", f"--get", f"remote.{remote}.url"], cwd=path)
    return out.strip() if ok else ""


def to_web_url(remote_url: str) -> str:
    u = remote_url.strip()
    if u.startswith("git@github.com:"):
        u = u.replace("git@github.com:", "https://github.com/")
    if u.endswith(".git"):
        u = u[:-4]
    return u


def get_current_branch(path: str) -> str:
    ok, out, err = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    if ok:
        return out.strip()
    return ""


def get_head_commit(path: str) -> str:
    ok, out, err = run_git(["rev-parse", "HEAD"], cwd=path)
    return out.strip() if ok else ""


def working_tree_dirty(path: str) -> bool:
    ok, out, err = run_git(["status", "--porcelain"], cwd=path)
    return bool(out.strip()) if ok else False


def stash_has_items(path: str) -> bool:
    ok, out, err = run_git(["stash", "list"], cwd=path)
    if not ok:
        return False
    return bool(out.strip())


def get_commit_messages(path: str, old: str, new: str) -> List[str]:
    if not old or not new:
        return []
    ok, out, err = run_git(["log", "--pretty=format:%s", f"{old}..{new}"], cwd=path)
    if not ok:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def get_numstat(path: str, old: str, new: str) -> List[Tuple[int, int, str]]:
    if not old or not new:
        return []
    ok, out, err = run_git(["diff", "--numstat", f"{old}..{new}"], cwd=path)
    if not ok:
        return []
    items: List[Tuple[int, int, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            a, d, p = parts
            try:
                added = int(a) if a.isdigit() else 0
                deleted = int(d) if d.isdigit() else 0
            except ValueError:
                added, deleted = 0, 0
            items.append((added, deleted, p))
    return items


# ------------------------- Overrides & Remedies -------------------------

def resolve_remote_override(name: str, path: str) -> Optional[str]:
    # 1) точное совпадение по имени или пути
    if name in REMOTE_OVERRIDES:
        return REMOTE_OVERRIDES[name]
    if path in REMOTE_OVERRIDES:
        return REMOTE_OVERRIDES[path]
    # 2) regex-ключи
    for k, v in REMOTE_OVERRIDES.items():
        try:
            if re.search(k, name) or re.search(k, path):
                return v
        except re.error:
            if k in name or k in path:
                return v
    return None


def resolve_branch_override(name: str, path: str) -> Optional[str]:
    if name in BRANCH_OVERRIDES:
        return BRANCH_OVERRIDES[name]
    if path in BRANCH_OVERRIDES:
        return BRANCH_OVERRIDES[path]
    for k, v in BRANCH_OVERRIDES.items():
        try:
            if re.search(k, name) or re.search(k, path):
                return v
        except re.error:
            if k in name or k in path:
                return v
    return None


def remedy_not_git_repo(name: str, path: str) -> List[str]:
    return [
        "Варианты решения:",
        "  • Инициализировать репозиторий: git init; git remote add origin <URL>; git fetch; git checkout <ветка>",
        "  • Либо в скрипте указать REMOTE_OVERRIDES для этого пути/имени и вручную выполнить clone/init.",
        "  • Если папка — просто архив без git, проще удалить её и заново установить через git clone.",
        'Подсказка по скрипту: REMOTE_OVERRIDES["%s"] = "https://github.com/user/%s.git"' % (name, name),
    ]


def remedy_no_remote(name: str, path: str) -> List[str]:
    return [
        "Варианты решения:",
        "  • Добавить удалённый вручную: git remote add origin <URL>",
        "  • Или прописать REMOTE_OVERRIDES вверху скрипта и запустить снова.",
        'Подсказка по скрипту: REMOTE_OVERRIDES[r"%s"] = "https://github.com/user/%s.git"' % (re.escape(name), name),
    ]


def remedy_pull_failure(out: str, err: str) -> List[str]:
    text = (out + "\n" + err).lower()
    tips = ["Что можно сделать:"]
    if "would be overwritten by merge" in text or "local changes" in text:
        tips += [
            "  • Есть локальные правки. Установите политику on_local_changes: 'stash' | 'commit' | 'reset' | 'skip' | 'abort'",
            "  • Пример: POLICIES[r'YourRepo'] = {'on_local_changes': 'stash'}",
        ]
    if "divergent branches" in text or "rebase" in text:
        tips += [
            "  • Ветки разошлись. Попробуйте pull_method='rebase' или решите конфликты вручную.",
            "  • Пример: POLICIES[r'YourRepo'] = {'pull_method': 'rebase'}",
        ]
    if "couldn't find remote ref" in text or "repository not found" in text:
        tips += [
            "  • Проверьте существование ветки/URL. Задайте BRANCH_OVERRIDES или REMOTE_OVERRIDES.",
        ]
    if "permission denied" in text or "authenticat" in text:
        tips += [
            "  • Проблемы авторизации. Проверьте SSH-ключи/токены или используйте https URL.",
        ]
    tips += [
        "  • Если это форк, можно использовать pull_from='upstream' для получения обновлений с апстрима.",
        "  • Или пропустить проблемный репозиторий с on_local_changes='skip' и вернуться к нему позже.",
    ]
    return tips


# ------------------------- Report printing -------------------------

def print_report(res: UpdateResult) -> None:
    title = f"{C.BOLD}{C.CYAN}{res.name}{C.RESET}"
    print(title)

    if res.web_url:
        print(f"\t🔗 {C.MAGENTA}{res.web_url}{C.RESET}")
    else:
        print(f"\t(локальный путь: {res.path})")

    if res.branch:
        print(f"\t➡️  ветка: {C.YELLOW}{res.branch}{C.RESET}")

    if res.error:
        print(f"\t❌ {C.RED}ОШИБКА: {res.error}{C.RESET}")
        for n in res.notes:
            print(f"\t   {C.GRAY}{n}{C.RESET}")
        print()
        return

    if res.changed:
        if res.commit_messages:
            print(f"\t📌 Сообщения коммитов:")
            for msg in res.commit_messages:
                print(f"\t   - {msg}")
        if res.numstat:
            print(f"\n\t⚠️  Изменённые файлы:")
            for added, deleted, path in res.numstat:
                print(f"\t   +{added} -{deleted}  {path}")
        print(f"\t✅ Обновлено")
    else:
        print(f"\t✅ Нет изменений")

    for n in res.notes:
        print(f"\t{C.GRAY}{n}{C.RESET}")

    print()

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nПрервано пользователем.")
        sys.exit(130)
