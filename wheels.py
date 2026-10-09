"""Catalog of prebuilt third-party wheels (sageattention, flash-attn, ...): local folder plus the GitHub / Hugging Face
sources listed in wheel_sources.json, and the rules for which wheel fits a Python / torch / CUDA combination.
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote

import requests
from packaging.utils import canonicalize_name
from packaging.version import Version

HERE = Path(__file__).resolve().parent
SOURCES_PATH = HERE / "wheel_sources.json"


def tup(v: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))

WHEEL_RE = re.compile(r"^(?P<name>[^-]+)-(?P<ver>[^-]+)(?:-\d[^-]*)?-(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^-]+)\.whl$")


@dataclass
class Wheel:
    filename: str
    name: str
    version: str
    py: str
    abi: str
    plat: str
    cuda: Optional[Tuple[int, int]]
    torch: Optional[Tuple[int, ...]]
    torch_min: bool
    path: Optional[Path] = None
    url: Optional[str] = None

    def pep_version(self) -> Version:
        try:
            return Version(self.version.split("+")[0])
        except Exception:
            return Version("0")


def parse_wheel(filename: str, path: Optional[Path] = None, url: Optional[str] = None) -> Optional[Wheel]:
    fn = unquote(filename)
    if fn.endswith(".whl.whl"):
        fn = fn[:-4]
    m = WHEEL_RE.match(fn)
    if not m:
        return None
    ver = m["ver"]
    cu = re.search(r"cu(\d{2})(\d)", ver)
    tt = re.search(r"torch(\d+(?:\.\d+)*)", ver)
    torch = None
    if tt:
        s = tt[1] if "." in tt[1] else ".".join(tt[1]) if len(tt[1]) <= 3 else tt[1][0] + "." + tt[1][1:]
        torch = tup(s)
    return Wheel(fn, canonicalize_name(m["name"]), ver, m["py"], m["abi"], m["plat"],
                 (int(cu[1]), int(cu[2])) if cu else None, torch, "andhigher" in ver, path, url)


def scan_local(wheel_dir: Path) -> List[Wheel]:
    out = []
    for p in sorted(wheel_dir.glob("*.whl*")) if wheel_dir.is_dir() else []:
        w = parse_wheel(p.name, path=p)
        if w:
            out.append(w)
    return out


def scan_remote(sources: List[dict]) -> List[Wheel]:
    out = []
    for src in sources:
        try:
            if src["type"] == "github":
                r = requests.get(f"https://api.github.com/repos/{src['repo']}/releases?per_page=100", timeout=30)
                r.raise_for_status()
                items = [(a["name"], a["browser_download_url"]) for rel in r.json() for a in rel.get("assets", [])]
            elif src["type"] == "hf":
                r = requests.get(f"https://huggingface.co/api/models/{src['repo']}/tree/main/{src.get('path', '')}?recursive=true", timeout=30)
                r.raise_for_status()
                items = [(Path(f["path"]).name, f"https://huggingface.co/{src['repo']}/resolve/main/{f['path']}") for f in r.json() if f["path"].endswith(".whl")]
            else:
                continue
        except (requests.RequestException, KeyError, ValueError) as e:
            print(f"  ! source {src.get('repo')} failed: {e}")
            continue
        for name, url in items:
            w = parse_wheel(name, url=url)
            if w:
                out.append(w)
    return out


def load_catalog(remote: bool) -> Tuple[List[Wheel], Path, List[str]]:
    cfg = json.loads(SOURCES_PATH.read_text(encoding="utf-8")) if SOURCES_PATH.exists() else {}
    wheel_dir = Path(cfg.get("wheel_dir", HERE / "whl"))
    wanted = [canonicalize_name(p) for p in cfg.get("packages", [])]
    catalog = scan_local(wheel_dir)
    if remote:
        have = {w.filename for w in catalog}
        catalog += [w for w in scan_remote(cfg.get("sources", [])) if w.filename not in have]
    return [w for w in catalog if w.name in wanted], wheel_dir, wanted


def match_quality(w: Wheel, py: str, torch_mm: Tuple[int, int], cuda: Tuple[int, int]) -> int:
    """0 = incompatible, 3 = exact tags, 2 = same CUDA major, 1 = untagged (unverified)."""
    if w.plat not in ("win_amd64", "any"):
        return 0
    pyv = py.replace(".", "")
    if w.py.startswith("cp"):
        if w.abi == "abi3":
            if int(pyv) < int(w.py[2:]):
                return 0
        elif w.py != f"cp{pyv}":
            return 0
    if w.torch:
        if w.torch_min:
            if torch_mm < w.torch[:2]:
                return 0
        elif w.torch[:2] != torch_mm:
            return 0
    if w.cuda:
        if w.cuda == cuda:
            return 3
        return 2 if w.cuda[0] == cuda[0] and w.torch else 0
    return 3 if w.torch else 1


def best_wheels(catalog: List[Wheel], wanted: List[str], py: str, torch_mm: Tuple[int, int], cuda: Tuple[int, int]) -> Dict[str, Tuple[Wheel, int]]:
    best: Dict[str, Tuple[Wheel, int]] = {}
    for w in catalog:
        q = match_quality(w, py, torch_mm, cuda)
        if not q:
            continue
        key = (q, w.pep_version(), w.path is not None)
        cur = best.get(w.name)
        if cur is None or key > (cur[1], cur[0].pep_version(), cur[0].path is not None):
            best[w.name] = (w, q)
    return best
