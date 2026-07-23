from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess

import pytest

from app.version import APP_VERSION
from build_tools.buildlib import diagnostics


def test_linux_bundle_rejects_missing_cursor_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    plugin = tmp_path / "PySide6" / "Qt" / "plugins" / "platforms" / "libqxcb.so"
    plugin.parent.mkdir(parents=True)
    plugin.write_bytes(b"ELF")
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/usr/bin/ldd" if name == "ldd" else None)
    monkeypatch.setattr(
        diagnostics.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(
            args[0],
            0,
            stdout="libxcb-cursor.so.0 => not found\n",
            stderr="",
        ),
    )

    errors: list[str] = []
    diagnostics._validate_linux_shared_dependencies(tmp_path, errors)

    assert any("not self-contained" in error for error in errors)
    assert any("libxcb-cursor.so.0" in error and "unresolved" in error for error in errors)


def test_linux_bundle_accepts_collected_cursor_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    plugin = tmp_path / "PySide6" / "Qt" / "plugins" / "platforms" / "libqxcb.so"
    plugin.parent.mkdir(parents=True)
    plugin.write_bytes(b"ELF")
    (tmp_path / "libxcb-cursor.so.0").write_bytes(b"ELF")
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/usr/bin/ldd" if name == "ldd" else None)
    monkeypatch.setattr(
        diagnostics.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(
            args[0],
            0,
            stdout="libxcb-cursor.so.0 => /bundle/libxcb-cursor.so.0\n",
            stderr="",
        ),
    )

    errors: list[str] = []
    diagnostics._validate_linux_shared_dependencies(tmp_path, errors)

    assert errors == []


def test_linux_preflight_rejects_unresolved_qt_plugin_before_compilation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    plugin = tmp_path / "libqxcb.so"
    plugin.write_bytes(b"ELF")
    monkeypatch.setattr(
        diagnostics.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"ldd", "xvfb-run", "Xvfb", "xauth"} else None,
    )
    monkeypatch.setattr(diagnostics, "_linux_qt_plugin_path", lambda: plugin)
    monkeypatch.setattr(
        diagnostics.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(
            args[0], 0, stdout="libxcb-cursor.so.0 => not found\n", stderr=""
        ),
    )

    with pytest.raises(RuntimeError, match="libxcb-cursor0"):
        diagnostics._validate_linux_build_host()


def test_pyinstaller_linux_preflight_requires_binutils(monkeypatch: pytest.MonkeyPatch):
    available = {"ldd", "xvfb-run", "Xvfb", "xauth", "objdump"}
    monkeypatch.setattr(
        diagnostics.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in available else None,
    )

    with pytest.raises(RuntimeError, match="objcopy.*binutils"):
        diagnostics._validate_linux_build_host("pyinstaller")


def test_nuitka_linux_preflight_requires_compiler(monkeypatch: pytest.MonkeyPatch):
    available = {"ldd", "xvfb-run", "Xvfb", "xauth"}
    monkeypatch.setattr(
        diagnostics.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in available else None,
    )

    with pytest.raises(RuntimeError, match="C11-capable compiler"):
        diagnostics._validate_linux_build_host("nuitka")


def test_frozen_linux_runtime_forces_xcb_and_rejects_offscreen_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import json

    from build_tools.buildlib.mpv_runtime import MpvBuildSelection
    from build_tools.buildlib.plan import BuildPlan

    executable = tmp_path / "bundle" / "ShangBackground"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"ELF")
    manifest = tmp_path / "generated" / "build-features.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema": 3,
                "tool": "pyinstaller",
                "target": "linux",
                "arch": "x86_64",
                "profile": "lite",
                "enabled": {
                    key: False
                    for key in ("video", "html", "bing", "hotkeys", "updates", "fonts")
                },
                "html_runtime": "disabled",
                "video_runtime": {"mode": "disabled"},
            }
        ),
        encoding="utf-8",
    )
    plan = BuildPlan(
        tool="pyinstaller",
        target="linux",
        profile="lite",
        mode="standalone",
        jobs=2,
        arch="x86_64",
        features=frozenset(),
        mpv=MpvBuildSelection("disabled", "disabled", "linux", "x86_64", "", None, {}),
        variant="unit",
        generated_dir=manifest.parent,
        manifest_path=manifest,
        staged_mpv_dir=None,
    )
    captured_environment: dict[str, str] = {}

    def fake_run(command, **kwargs):
        captured_environment.update(kwargs["env"])
        report = Path(command[-1])
        report.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "app_version": APP_VERSION,
                    "platform": "linux",
                    "architecture": "x86_64",
                    "packaged": True,
                    "resource_root": str(executable.parent),
                    "enabled_features": [],
                    "html_runtime": "disabled",
                    "video_runtime": {"mode": "disabled"},
                    "qt_smoke": {"ok": True, "platform_plugin": "offscreen"},
                    "diagnostics": {"healthy": True},
                }
            ),
            encoding="utf-8",
        )
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("QT_PLUGIN_PATH", "/host/qt/plugins")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/host/libs")
    monkeypatch.setattr(
        diagnostics.shutil,
        "which",
        lambda name: "/usr/bin/xvfb-run" if name == "xvfb-run" else None,
    )
    monkeypatch.setattr(diagnostics.subprocess, "run", fake_run)

    errors = diagnostics.validate_frozen_runtime(plan, executable)

    assert captured_environment["QT_QPA_PLATFORM"] == "xcb"
    assert "QT_PLUGIN_PATH" not in captured_environment
    assert "LD_LIBRARY_PATH" not in captured_environment
    assert any("did not load the XCB" in error for error in errors)
