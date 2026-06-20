"""Centralized source/bundle path resolution.

All UI decorations and language files are resolved from the branch ``src`` root.
PyInstaller 4.3+ provides an absolute ``__file__`` inside the bundle; ``_MEIPASS``
is retained only as a compatibility fallback.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
_SOURCE_ROOT = _PACKAGE_ROOT.parent


def _candidate_roots() -> list[Path]:
    roots = [_SOURCE_ROOT]
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        roots.append(Path(bundled))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    unique: list[Path] = []
    for root in roots:
        root = root.resolve()
        if root not in unique:
            unique.append(root)
    return unique


def _select_resource_root() -> Path:
    for root in _candidate_roots():
        if (root / "img").is_dir() and (root / "lang").is_dir():
            return root
    return _candidate_roots()[0]


RESOURCE_ROOT = _select_resource_root()
IMAGE_DIR = RESOURCE_ROOT / "img"
LANG_DIR = RESOURCE_ROOT / "lang"
TRANSLATIONS_DIR = RESOURCE_ROOT / "translations"
PROJECT_ROOT = _SOURCE_ROOT.parent


def resource_path(*parts: str | os.PathLike[str]) -> str:
    return os.fspath(RESOURCE_ROOT.joinpath(*map(os.fspath, parts)))


def image_path(name: str) -> str:
    return os.fspath(IMAGE_DIR / name)


def language_path(name: str) -> str:
    return os.fspath(LANG_DIR / name)


def font_directories() -> tuple[Path, ...]:
    candidates = [RESOURCE_ROOT / "fonts", PROJECT_ROOT / "fonts"]
    result: list[Path] = []
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate not in result:
            result.append(candidate)
    return tuple(result)


def entry_script_path() -> str:
    """Return the stable source entry script used by startup integrations."""
    return os.fspath(RESOURCE_ROOT / "main.py")
