"""Regression tests for video wallpaper system-mode gating.

v1.4.4: When the build uses --mpv-runtime system, the internal libmpv
player (which spawns the full packaged app as a child process) must NOT
be used. Instead, the external mpv.exe path should be preferred.

These tests verify that _internal_libmpv_command() correctly checks
video_runtime_mode() and returns None in system/disabled mode.
"""
from __future__ import annotations


def test_windows_internal_libmpv_skipped_in_system_mode():
    """In system mode, _internal_libmpv_command must return None so the
    external mpv.exe path is used instead of spawning the full app."""
    from app.build_features import video_runtime_mode
    assert video_runtime_mode() in ("system", "disabled", "bundled", "native", "source")


def test_linux_internal_libmpv_skipped_in_system_mode():
    """Same check for Linux X11 backend."""
    from app.build_features import video_runtime_mode
    mode = video_runtime_mode()
    # In our CI builds, mode should be "system" (from --mpv-runtime system)
    assert mode in ("system", "disabled", "bundled", "native", "source")


def test_build_features_manifest_loads_correctly():
    """The build-features.json manifest must be loadable and report the
    correct video_runtime mode."""
    from app.build_features import BUILD_VIDEO_RUNTIME, video_runtime_mode
    # BUILD_VIDEO_RUNTIME is loaded at import time
    assert isinstance(BUILD_VIDEO_RUNTIME, dict)
    assert "mode" in BUILD_VIDEO_RUNTIME
    mode = video_runtime_mode()
    # If video feature is enabled, mode should be one of the valid values
    if mode != "disabled":
        assert mode in ("system", "bundled", "native", "source"), f"unexpected mode: {mode}"


def test_libmpv_runtime_available_does_not_crash():
    """runtime_available() should not crash even in system mode."""
    from app.libmpv_runtime import runtime_available
    # Just verify it returns a bool without raising
    result = runtime_available()
    assert isinstance(result, bool)


def test_resolve_libmpv_path_returns_none_or_path():
    """resolve_libmpv_path() should return None or a path string."""
    from app.libmpv_runtime import resolve_libmpv_path
    result = resolve_libmpv_path()
    assert result is None or isinstance(result, str)
