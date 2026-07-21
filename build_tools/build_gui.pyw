#!/usr/bin/env pythonw
from __future__ import annotations

from pathlib import Path
import importlib
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_run_gui():
    root = str(PROJECT_ROOT)
    if not sys.path or sys.path[0] != root:
        try:
            sys.path.remove(root)
        except ValueError:
            pass
        sys.path.insert(0, root)
    importlib.invalidate_caches()
    module = importlib.import_module("build_tools._entry")
    origin = Path(module.__file__).resolve() if module.__file__ else None
    expected = (PROJECT_ROOT / "build_tools" / "_entry.py").resolve()
    if origin != expected:
        raise ImportError(f"Loaded build_tools from unexpected location: {origin}; expected {expected}")
    return module.run_gui


if __name__ == "__main__":
    raise SystemExit(_load_run_gui()())
