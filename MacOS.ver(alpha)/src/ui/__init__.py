"""Branch-local UI package.

PartC keeps Windows/Linux/macOS as independent branches.  UI classes are exposed
lazily so old tools can resolve them without forcing PySide6 import during CLI
or environment checks.
"""
from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "PreviewCanvas": ("ui.preview_canvas", "PreviewCanvas"),
    "QtRootShim": ("ui.qt_root_shim", "QtRootShim"),
    "ShangBackgroundWindow": ("ui.main_window", "ShangBackgroundWindow"),
    "WallpaperSidebar": ("ui.sidebar", "WallpaperSidebar"),
}
__all__ = list(_EXPORTS)


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
