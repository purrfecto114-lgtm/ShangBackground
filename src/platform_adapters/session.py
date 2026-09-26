"""Shared-layer-safe desktop session detection facade.

Architecture rule: shared layers (``core/``, ``app/``) must not import
platform backends directly.  This facade is the single legal entry point for
desktop-session queries: it dispatches to the Linux backend's canonical
fallback chain and answers inert defaults on platforms that have no Linux
session semantics.

Callers already guard these helpers behind Linux platform checks, but the
facade must stay import-safe on every platform (a stray import on Windows
must not raise), so non-Linux hosts get inert defaults instead of a backend
load attempt.

Unlike ``platform_adapters.integration`` this facade cannot use the
whole-module replacement pattern: only the Linux backend ships a ``session``
module.  It also intentionally avoids importing ``app.config`` so that
``app.config`` itself can import this facade at module scope without a
circular import.
"""
from __future__ import annotations

import importlib
from collections.abc import Mapping
from pathlib import Path
import sys

# Support direct source execution (python src/platform_adapters/<tool>.py).
_source_root = str(Path(__file__).resolve().parents[1])
if _source_root not in sys.path:
    sys.path.insert(0, _source_root)

if sys.platform.startswith("linux"):
    _backend = importlib.import_module("platform_adapters.backends.linux.session")
    detect_session_type = _backend.detect_session_type
    is_wayland_session = _backend.is_wayland_session
    session_bus_available = _backend.session_bus_available
else:  # pragma: no cover - exercised only on non-Linux hosts
    _backend = None

    def detect_session_type(env: Mapping[str, str] | None = None) -> str:
        """Desktop-session type is a Linux-only concept; other hosts report unknown."""
        del env
        return "unknown"

    def is_wayland_session(env: Mapping[str, str] | None = None) -> bool:
        del env
        return False

    def session_bus_available(env: Mapping[str, str] | None = None) -> bool:
        del env
        return False

__all__ = ["detect_session_type", "is_wayland_session", "session_bus_available"]
