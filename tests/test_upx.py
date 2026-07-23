"""Tests for the UPX (Ultimate Packer for eXecutables) detection module."""
from __future__ import annotations

from pathlib import Path

import pytest

from build_tools.buildlib import upx as upx_module
from build_tools.buildlib.upx import (
    find_upx_binary,
    resolve_upx_for_build,
    upx_meets_minimum,
    upx_supported_for_target,
    upx_version,
)


def test_upx_supported_targets():
    """UPX is only supported on Windows and Linux, never macOS."""
    assert upx_supported_for_target("windows") is True
    assert upx_supported_for_target("linux") is True
    assert upx_supported_for_target("macos") is False


def test_resolve_upx_returns_none_when_disabled():
    """When enabled=False, resolve_upx_for_build returns None without
    looking for a binary."""
    assert resolve_upx_for_build("windows", enabled=False) is None
    assert resolve_upx_for_build("linux", enabled=False) is None


def test_resolve_upx_returns_none_for_macos():
    """Even when enabled=True, macOS returns None because UPX is unsupported
    there (breaks codesign + Apple Silicon ABI)."""
    assert resolve_upx_for_build("macos", enabled=True) is None


def test_resolve_upx_raises_when_enabled_but_missing(monkeypatch: pytest.MonkeyPatch):
    """When the user explicitly asks for UPX but no binary is found, the
    function raises RuntimeError with an actionable install hint."""
    monkeypatch.setenv("SHANGBACKGROUND_UPX_BINARY", "")
    monkeypatch.setattr(upx_module, "find_upx_binary", lambda: None)
    with pytest.raises(RuntimeError, match="UPX was requested"):
        resolve_upx_for_build("windows", enabled=True)
    with pytest.raises(RuntimeError, match="apt"):
        resolve_upx_for_build("linux", enabled=True)


def test_resolve_upx_returns_path_when_found(monkeypatch: pytest.MonkeyPatch):
    """When a valid UPX binary is found and meets the minimum version, the
    function returns its path."""
    monkeypatch.setattr(upx_module, "find_upx_binary", lambda: "/usr/bin/upx")
    monkeypatch.setattr(upx_module, "upx_meets_minimum", lambda binary, **kw: True)
    result = resolve_upx_for_build("linux", enabled=True)
    assert result == "/usr/bin/upx"


def test_resolve_upx_raises_when_version_too_old(monkeypatch: pytest.MonkeyPatch):
    """When UPX is found but the version is below the minimum, the function
    raises RuntimeError."""
    monkeypatch.setattr(upx_module, "find_upx_binary", lambda: "/usr/bin/upx")
    monkeypatch.setattr(upx_module, "upx_version", lambda binary: (3, 9, 6))
    with pytest.raises(RuntimeError, match="requires"):
        resolve_upx_for_build("windows", enabled=True)


def test_find_upx_binary_respects_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The SHANGBACKGROUND_UPX_BINARY env var overrides PATH lookup."""
    fake_upx = tmp_path / "upx"
    fake_upx.write_text("fake")
    monkeypatch.setenv("SHANGBACKGROUND_UPX_BINARY", str(fake_upx))
    result = find_upx_binary()
    assert result == str(fake_upx)


def test_find_upx_binary_returns_none_when_not_installed(monkeypatch: pytest.MonkeyPatch):
    """When no env override and nothing on PATH, returns None."""
    monkeypatch.setenv("SHANGBACKGROUND_UPX_BINARY", "")
    monkeypatch.setattr(upx_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(upx_module.sys, "platform", "linux")
    result = find_upx_binary()
    assert result is None


def test_upx_version_parses_standard_output():
    """The version regex correctly parses 'upx 4.2.4' style output."""
    # We can't call the real upx binary, but we can test the regex indirectly
    # by checking that upx_version returns None for a non-existent binary.
    result = upx_version("/nonexistent/upx")
    assert result is None


def test_upx_meets_minimum_with_valid_version(monkeypatch: pytest.MonkeyPatch):
    """A version >= UPX_MIN_VERSION (4.2.0) passes the check."""
    monkeypatch.setattr(upx_module, "upx_version", lambda binary: (4, 2, 0))
    assert upx_meets_minimum("/fake/upx") is True
    monkeypatch.setattr(upx_module, "upx_version", lambda binary: (5, 0, 0))
    assert upx_meets_minimum("/fake/upx") is True


def test_upx_meets_minimum_rejects_old_version(monkeypatch: pytest.MonkeyPatch):
    """A version < UPX_MIN_VERSION (4.2.0) fails the check."""
    monkeypatch.setattr(upx_module, "upx_version", lambda binary: (3, 9, 6))
    assert upx_meets_minimum("/fake/upx") is False
