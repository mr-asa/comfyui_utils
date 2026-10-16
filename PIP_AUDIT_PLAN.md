# Pip audit: improvement plan

Mark items `[x]` when done, delete or edit freely. Files: `pip_audit_core.py` (core), `utils_gui.py` (API), `gui/index.html` (UI), `wheels.py` (wheel catalog).

## Problems found

| # | Symptom | Root cause |
|---|---------|------------|
| P1 | llama-cpp-python `0.3.40 [wheel]` shows target `0.3.36` | Target column = PyPI max, meaningless for wheel installs; explanation hidden in Issue column |
| P2 | All "risky" look the same (albumentationsx vs pandas) | `risky_update` merges "blocked by dependents" and "resolvable with side effects"; `side_changes` is kept only for safe |
| P3 | Versions dialog says "satisfies constraints" but install fails | Verdict checks only requirements.txt + installed dependents, never the package's own dependencies |
| P4 | Protection error is a dead end | Generic text; "allow" buttons only for held/pinned names; no way to know which mode works |
| P5 | torchvision list lacks the installed `0.26.0+cu130` | `version == current` ignores local tag; PyPI versions are not the CUDA build; no torch pairing check; false "downgrade" |
| P6 | pillow cannot be inspected/held, no "why this version" | `openVersions` and `rule()` need a table row; no cap-source view |
| P7 | Wheel sources: unclear what is searched, no feedback | See block E |

## Other gaps

- `fetch_pypi` swallows all errors (network == "no releases"), runs sequentially.
- Fallback safe-version search stops after 12 dry runs without saying so.
- Held/pinned packages are never classified (no "update available, held").
- Requirement markers evaluated with the GUI's Python, not the venv's; `-r`/`-c`/index lines ignored.
- Reverse constraints drop requirements that apply through extras; yanked releases can become `max_allowed`.
- `classify_dry_run` treats any output containing "requires"/"depends on" as risky.
- Audit classifies with no protection, install dialog defaults to `held`, `all` fails on any side change.
- `downgrade`/capped rows do not say who caps them.

## A. Core (`pip_audit_core.py`)

- [x] A1. `risky_kind` (`blocked` | `side-effects` | `unresolvable`) + structured `risky_why`; keep `side_changes` for risky.
- [x] A2. Compare by `Version.public` for installed checks (local tags, false downgrade).
- [x] A3. Torch family: flag PyPI versions as wrong index when installed has `+cuXXX`; check pairing with installed torch. (Decided: query the PyTorch index, acceptable for this tool.)
- [x] A4. Forward dependency check per release (`requires_dist` from PyPI per-version JSON vs installed set) -> `forward_conflicts`.
- [x] A5. `fetch_pypi` returns error reason (network / 404); thread pool.
- [x] A6. Classify held/pinned for info ("update available, held"); report when the 12-check limit was hit.
- [x] A7. Markers evaluated for the target venv (extras of installed packages cannot be known from their metadata, so not handled); skip yanked in `max_allowed`; tighten `classify_dry_run` markers.

## B. Versions dialog (`versions_info` + HTML)

- [x] B1. Work for any installed package, not only table rows; make package names in preview/Issue column clickable; allow hold/pin from there.
- [x] B2. "Why this version" header: every cap (requirements.txt, installed dependent + spec, hold, pin, protected), strictest upper bound.
- [x] B3. Verdict: `satisfies` | `needs: albucore 0.0.24 -> 0.2.21` | `blocked by X<...`; lazy per-row "check" (uv dry run).
- [x] B4. Installed marker for local tags; wheel installs show wheel source and no PyPI target.

## C. Main table

- [x] C1. Badges `risky: blocked` / `risky: +deps`; split filter and "Select all non-risky".
- [x] C2. Side-effect rows: list the changes + "resolve..." button opening the install dialog with the minimal release set.
- [x] C3. `[wheel]` rows: "installed from wheel; PyPI max X (older)" tooltip; "newer wheel" only when real.
- [x] C4. `downgrade`/capped rows show the cap source ("capped by SAM-2 <12").

## D. Install dialog

- [x] D1. Dry run under current protection plus a `none` run; show diff "to install this release: albucore 0.0.24 -> 0.2.21".
- [x] D2. "Let it change for this install only" for every package named in the error/diff; one-click "release all".
- [x] D3. Structured reason instead of the generic error; raw uv output behind a toggle.
- [x] D4. Mark which protection modes would succeed / disable known-failing ones.

## E. Wheel sources (`wheels.py`, settings, audit)

Today: `Packages` = name filter; `Sources` = GitHub releases (100 max) or HF tree (optional folder); remote scan only with "check wheel sources online" (off by default); match by Python/torch/CUDA/win_amd64, update only if quality >= 2 and newer.

- [x] E1. Settings panel: per-source "Test" (reachable/error, wheel count, packages found, how many fit this venv); per-package list of catalog wheels with fit verdict (fits / other CUDA / other torch / other Python / unparsed); help text for Packages, Folder in repo, and how to add another author's repo. (one Test button for all sources, not per source)
- [x] E2. Audit status line: "7 sources OK, 2 failed, 41 wheels, 3 fit", failed sources named.
- [x] E3. Wheel section in the versions dialog: all catalog wheels for the package with verdict + use/install; newer-but-incompatible greyed with reason.
- [x] E4. Suggest adding a package when a source has wheels for something installed. (in the Test results, for installed packages)
- [x] E5. Remote lookup on by default or remembered per venv; ~1 h cache.
- [x] E6. Count/list wheel filenames the regex rejects instead of dropping silently. (counted in Test results and the audit; filenames listed in Test)

## Order

1. B1, B2, E1, E2 (visibility; answers pillow/rembg and wheel-source questions)
2. A2, B4 (torchvision list)
3. A1, C1, C2, D1-D3 (risky handling)
4. A4, B3, E3 (forward check)
5. Rest

## Decisions

- Query the PyTorch index for torch-family versions: yes.
- Forward check: PyPI JSON for the list, uv dry run on demand.
- E4 (suggest packages from sources): keep.
