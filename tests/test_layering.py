"""Layering guard tests: shared code must not import platform backends.

Architecture rule (docs/ARCHITECTURE.md): shared layers (``core/``,
``app/``) must not import concrete platform backends
(``platform_adapters.backends.<platform>.*``) directly.  The only legal
entry points are the ``platform_adapters`` facades (which dynamically
dispatch to the current platform) and ``app.bootstrap`` (whose job is
backend assembly).

v1.6.1 fixed three violations of this rule:

- ``core/engine.py``   -> ``platform_adapters.backends.windows.integration``
- ``app/diagnostics.py`` -> ``platform_adapters.backends.linux.session``
- ``app/config.py``      -> ``platform_adapters.backends.linux.session``

These tests pin the rule so a regression fails CI instead of silently
re-coupling shared code to one platform's backend.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"

# bootstrap is the designated assembly layer: it is allowed (and expected)
# to import platform backends behind its importer indirection.
SHARED_LAYER_ALLOWLIST = {"bootstrap.py"}


def _shared_layer_files() -> list[Path]:
    files: list[Path] = []
    for layer in ("core", "app"):
        directory = SRC_ROOT / layer
        if not directory.is_dir():
            continue
        files.extend(sorted(directory.glob("*.py")))
    return files


def _backend_imports(path: Path) -> list[str]:
    """Return backend module names imported by ``path`` (AST-based, so
    docstrings and comments mentioning backends are not false positives)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("platform_adapters.backends"):
                    offenders.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("platform_adapters.backends"):
                offenders.append(module)
    return offenders


def test_shared_layers_do_not_import_platform_backends():
    """core/ and app/ (except bootstrap) must not import platform backends."""
    violations: list[str] = []
    for path in _shared_layer_files():
        if path.name in SHARED_LAYER_ALLOWLIST:
            continue
        for module in _backend_imports(path):
            violations.append(f"{path.relative_to(SRC_ROOT)} imports {module}")
    assert not violations, (
        "Architecture dependency violation — shared layers must go through "
        "platform_adapters facades or app.bootstrap:\n  " + "\n  ".join(violations)
    )


def test_every_backend_integration_exposes_prime_desktop_wallpaper_host():
    """All three platform integration backends must keep the public facade
    name so ``from platform_adapters.integration import
    prime_desktop_wallpaper_host`` resolves on every platform."""
    for platform_name in ("windows", "linux", "macos"):
        backend = (
            SRC_ROOT / "platform_adapters" / "backends" / platform_name / "integration.py"
        )
        assert backend.is_file(), f"missing backend file: {backend}"
        functions = {
            node.name
            for node in ast.walk(ast.parse(backend.read_text(encoding="utf-8")))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert "prime_desktop_wallpaper_host" in functions, (
            f"{platform_name} integration lost the public facade entry "
            "prime_desktop_wallpaper_host"
        )


# ---------------------------------------------------------------------------
# v1.6.1: session facade behavior (dispatch + inert defaults)
# ---------------------------------------------------------------------------


def test_session_facade_is_import_safe_and_dispatches_on_linux():
    """The session facade must import on any platform and, on Linux hosts,
    delegate to the canonical backend fallback chain."""
    import sys

    from platform_adapters import session as session_facade

    if sys.platform.startswith("linux"):
        assert session_facade.detect_session_type({"XDG_SESSION_TYPE": "wayland"}) == "wayland"
        assert session_facade.detect_session_type({"XDG_SESSION_TYPE": "x11"}) == "x11"
        # Desktop launchers that drop XDG_SESSION_TYPE still get a verdict.
        assert session_facade.detect_session_type({"WAYLAND_DISPLAY": "wayland-0"}) == "wayland"
        assert session_facade.detect_session_type({"DISPLAY": ":0"}) == "x11"
        assert session_facade.detect_session_type({}) == "unknown"
        assert session_facade.is_wayland_session({"XDG_SESSION_TYPE": "wayland"}) is True
        assert session_facade.is_wayland_session({"XDG_SESSION_TYPE": "x11"}) is False
    else:
        # Inert defaults: never raise, never claim a session exists.
        assert session_facade.detect_session_type() == "unknown"
        assert session_facade.is_wayland_session() is False
        assert session_facade.session_bus_available() is False


def test_prime_desktop_wallpaper_host_is_a_noop_on_linux():
    """On non-Windows hosts the Explorer warm-up facade must be a harmless
    no-op (returns False) instead of raising."""
    import sys

    import pytest as _pytest

    if not sys.platform.startswith("linux"):
        raise _pytest.skip("linux-host no-op check runs on Linux CI runners")

    from platform_adapters.integration import prime_desktop_wallpaper_host

    assert prime_desktop_wallpaper_host() is False


def test_session_facade_does_not_import_app_config():
    """The session facade must stay dependency-free of ``app.config`` so
    ``app.config`` can import it at module scope (circular-import guard)."""
    source = (SRC_ROOT / "platform_adapters" / "session.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("app."), (
                    f"session facade must not import app.* (found {alias.name})"
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("app."), (
                f"session facade must not import app.* (found {module})"
            )


@pytest.mark.parametrize("offender_source", [
    "from platform_adapters.backends.linux.session import is_wayland_session\n",
    "import platform_adapters.backends.windows.integration\n",
    "from platform_adapters.backends.macos.integration import anything\n",
])
def test_backend_import_detector_catches_violations(tmp_path: Path, offender_source: str):
    """Sanity check for the detector itself: planted violations must be
    found (prevents a silently broken guard from false-passing)."""
    planted = tmp_path / "planted.py"
    planted.write_text(offender_source, encoding="utf-8")
    assert _backend_imports(planted), "detector failed to catch a planted violation"
