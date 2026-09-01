"""Local ComfyUI startup compatibility shims.

This file is loaded by Python through PYTHONPATH before ComfyUI imports custom
nodes. Keep repo-specific emergency fixes here instead of editing custom node
repositories, so ComfyUI-Manager and git updates can replace those repos cleanly.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import os
import sys


def _install_diffusers_controlnet_alias() -> None:
    """Restore the old diffusers.models.controlnet import path for older nodes."""
    try:
        controlnet = importlib.import_module(
            "diffusers.models.controlnets.controlnet"
        )
    except Exception:
        return

    sys.modules.setdefault("diffusers.models.controlnet", controlnet)


class _DisabledMtbFilmLoader(importlib.abc.Loader):
    def create_module(self, spec):  # noqa: D401 - importlib protocol
        return None

    def exec_module(self, module) -> None:
        raise ModuleNotFoundError(
            "Deprecated comfy_mtb FILM image_interpolation nodes are disabled "
            "to avoid TensorFlow startup hangs. Set MTB_ENABLE_DEPRECATED_FILM=1 "
            "to enable them."
        )


class _DisabledMtbFilmFinder(importlib.abc.MetaPathFinder):
    TARGET = "comfy_mtb.nodes.image_interpolation"

    def find_spec(self, fullname, path=None, target=None):
        if fullname != self.TARGET:
            return None

        enabled = os.environ.get("MTB_ENABLE_DEPRECATED_FILM", "").lower()
        if enabled in {"1", "true", "yes", "on"}:
            return None

        return importlib.machinery.ModuleSpec(fullname, _DisabledMtbFilmLoader())


_install_diffusers_controlnet_alias()
sys.meta_path.insert(0, _DisabledMtbFilmFinder())
