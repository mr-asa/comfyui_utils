#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Local browser GUI for the ComfyUI utils (stdlib only, listens on 127.0.0.1).

  python utils_gui.py [--port 8765] [--no-browser]

Tabs: pip audit (filters, hold/pin, install selected). Long jobs run in threads and are polled by the page.
POST requests need the per-run token embedded in the served page, so other sites cannot call the API.
"""

from __future__ import annotations
import argparse
import json
import secrets
import shutil
import subprocess
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List

from packaging.utils import canonicalize_name

import config_schema
from comfyui_root import resolve_comfyui_root
from envtools import custom_nodes_paths, dedupe_dirs, install_prefix
from pip_audit_core import audit_rows, need_vcs_install, run_audit

HERE = Path(__file__).resolve().parent
CONFIG_PATH = str(HERE / "config.json")
TOKEN = secrets.token_urlsafe(16)
CONFIG_LOCK = threading.Lock()
JOBS: Dict[str, dict] = {}


def new_job(kind: str) -> dict:
    job = {"id": secrets.token_hex(4), "kind": kind, "status": "running", "progress": None, "log": [], "result": None, "error": ""}
    JOBS[job["id"]] = job
    return job


def run_in_thread(job: dict, fn, *args) -> None:
    def target() -> None:
        try:
            job["result"] = fn(job, *args)
            job["status"] = "done"
        except BaseException as e:  # SystemExit from the shared helpers must not kill the server
            job["error"] = f"{type(e).__name__}: {e}"
            job["log"].append(traceback.format_exc())
            job["status"] = "error"

    threading.Thread(target=target, daemon=True).start()


def venv_rows() -> dict:
    v2, _ = config_schema.load_v2(CONFIG_PATH, auto_migrate=False)
    return {
        "venvs": config_schema.list_venvs(v2),
        "comfyui_root": config_schema.get_value(v2, "comfyui_root"),
        "uv": bool(shutil.which("uv")),
    }


def venv_entry(name: str) -> dict:
    v2, _ = config_schema.load_v2(CONFIG_PATH, auto_migrate=False)
    row = next((r for r in config_schema.list_venvs(v2) if r["name"] == name), None)
    if not row or row["exists"] != "1":
        raise ValueError(f"venv '{name}' not found")
    return row


def audit_job(job: dict, name: str, include_repo: bool, force_pip: bool) -> dict:
    row = venv_entry(name)
    with CONFIG_LOCK:
        v2, _ = config_schema.load_v2(CONFIG_PATH)
        v2 = config_schema.set_selected_venv(v2, row["path"], name)
        config_schema.save_v2(CONFIG_PATH, v2)
        hold, pins = config_schema.get_pip_rules(v2, name)
        cfg, _ = config_schema.load_legacy_compat(CONFIG_PATH)
    hold_set = {canonicalize_name(h) for h in hold}
    pin_map = {canonicalize_name(k): v for k, v in pins.items()}
    root = str(resolve_comfyui_root(CONFIG_PATH))
    paths = custom_nodes_paths(cfg, root)
    repo = str(cfg.get("custom_nodes_repo_path") or "").strip()
    if include_repo and repo:
        paths = dedupe_dirs(paths + [repo])

    def progress(label: str, i: int, n: int, suffix: str = "") -> None:
        job["progress"] = {"label": label, "i": i, "n": n, "suffix": suffix}

    res = run_audit(cfg, root, paths, hold_set, pin_map, force_pip=force_pip, progress=progress)
    return {
        "rows": audit_rows(res, hold_set, pin_map),
        "vcs": [
            {"name": v.name or "", "repo": v.repo, "installed": str(v.installed or ""), "raw": v.raw, "ref": v.ref or "", "need_install": need_vcs_install(v)}
            for v in res.extra_reqs
        ],
        "comfy_env": [
            {"repo": r.repo, "isolations": [{"env": i.env_name, "built": i.built, "stale": i.stale} for i in r.isolations]}
            for r in res.comfy_env_reports
        ],
        "install_cmd": res.install_cmd,
        "venv": name,
        "hold": sorted(hold_set),
        "pins": pin_map,
    }


def install_job(job: dict, name: str, packages: List[str], force_pip: bool) -> dict:
    row = venv_entry(name)
    cfg = {"env_type": "venv", "venv_path": row["path"]}
    cmd = install_prefix(cfg, force_pip=force_pip) + ["--upgrade"] + packages
    job["log"].append("> " + " ".join(cmd))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    for line in p.stdout:
        job["log"].append(line.rstrip())
    code = p.wait()
    job["log"].append(f"exit code {code}")
    return {"exit_code": code}


def save_rules(name: str, hold: List[str], pins: Dict[str, str]) -> None:
    venv_entry(name)
    hold = [canonicalize_name(h) for h in hold if h.strip()]
    pins = {canonicalize_name(k): v.strip() for k, v in pins.items() if k.strip() and v.strip()}
    with CONFIG_LOCK:
        v2, _ = config_schema.load_v2(CONFIG_PATH)
        config_schema.save_v2(CONFIG_PATH, config_schema.set_pip_rules(v2, name, hold, pins))


class Handler(BaseHTTPRequestHandler):
    server_version = "ComfyUtilsGUI"

    def log_message(self, fmt, *args) -> None:  # keep the console quiet
        pass

    def send_json(self, data, code: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def host_ok(self) -> bool:
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_GET(self) -> None:
        if not self.host_ok():
            return self.send_json({"error": "bad host"}, 403)
        path, _, query = self.path.partition("?")
        if path == "/":
            html = (HERE / "gui" / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        elif path == "/api/state":
            self.send_json(venv_rows())
        elif path.startswith("/api/job/"):
            job = JOBS.get(path.rsplit("/", 1)[1])
            if not job:
                return self.send_json({"error": "unknown job"}, 404)
            since = int(dict(q.split("=", 1) for q in query.split("&") if "=" in q).get("since", 0))
            out = {k: job[k] for k in ("id", "kind", "status", "progress", "error")}
            out["log"] = job["log"][since:]
            out["next"] = len(job["log"])
            out["result"] = job["result"] if job["status"] == "done" else None
            self.send_json(out)
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if not self.host_ok() or self.headers.get("X-Token") != TOKEN:
            return self.send_json({"error": "forbidden"}, 403)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/audit":
                job = new_job("audit")
                run_in_thread(job, audit_job, body["venv"], bool(body.get("include_repo")), bool(body.get("force_pip")))
                self.send_json({"job": job["id"]})
            elif self.path == "/api/install":
                packages = [str(p) for p in body["packages"] if str(p).strip()]
                if not packages:
                    return self.send_json({"error": "no packages"}, 400)
                job = new_job("install")
                run_in_thread(job, install_job, body["venv"], packages, bool(body.get("force_pip")))
                self.send_json({"job": job["id"]})
            elif self.path == "/api/rules":
                save_rules(body["venv"], list(body.get("hold", [])), dict(body.get("pins", {})))
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "not found"}, 404)
        except (KeyError, ValueError, TypeError) as e:
            self.send_json({"error": f"{type(e).__name__}: {e}"}, 400)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"ComfyUI utils GUI: {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
