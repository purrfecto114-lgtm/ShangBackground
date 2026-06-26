"""Centralized source/bundle path resolution.

All UI decorations and language files are resolved from the branch ``src`` root.
PyInstaller 4.3+ provides an absolute ``__file__`` inside the bundle; ``_MEIPASS``
is retained only as a compatibility fallback.  Nuitka does not set ``sys.frozen``;
its compiled runtime is detected through ``__compiled__`` so standalone builds do
not accidentally fall back to source-mode launch paths.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
_SOURCE_ROOT = _PACKAGE_ROOT.parent


def _compiled_value():
    """Return Nuitka's ``__compiled__`` marker when this module is compiled."""
    try:
        return globals().get("__compiled__")
    except Exception:
        return None


def is_nuitka_compiled() -> bool:
    """Return True when running from a Nuitka-compiled module/program."""
    if _compiled_value() is not None:
        return True
    main_module = sys.modules.get("__main__")
    return bool(getattr(main_module, "__compiled__", None))


def is_packaged_runtime() -> bool:
    """Return True for PyInstaller/cx_Freeze-style or Nuitka packaged runs."""
    return bool(getattr(sys, "frozen", False) or is_nuitka_compiled())


def compiled_containing_dir() -> Path | None:
    """Return the packaged application/dist folder when Nuitka exposes it."""
    compiled = _compiled_value() or getattr(sys.modules.get("__main__"), "__compiled__", None)
    containing_dir = getattr(compiled, "containing_dir", None)
    if containing_dir:
        try:
            return Path(containing_dir).resolve()
        except Exception:
            return Path(os.fspath(containing_dir))
    return None


def executable_dir() -> Path:
    """Return the directory containing the active executable/interpreter."""
    try:
        return Path(sys.executable).resolve().parent
    except Exception:
        return Path(sys.argv[0] or ".").resolve().parent


def _candidate_roots() -> list[Path]:
    roots = [_SOURCE_ROOT]
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        roots.append(Path(bundled))
    compiled_dir = compiled_containing_dir()
    if compiled_dir is not None:
        roots.append(compiled_dir)
    if is_packaged_runtime():
        roots.append(executable_dir())
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
    """Return the runtime entry target used by startup/source launchers."""
    if is_packaged_runtime():
        return os.fspath(Path(sys.executable).resolve())
    return os.fspath(RESOURCE_ROOT / "main.py")
