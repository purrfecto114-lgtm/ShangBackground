from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from build_tools.buildlib import diagnostics
from build_tools.buildlib.constants import PYSIDE6_ESSENTIALS_VERSION, PYWEBVIEW_VERSION
from build_tools.buildlib.plan import BuildPlan
from build_tools.buildlib.mpv_runtime import MpvBuildSelection
from build_tools.buildlib.pyinstaller import _execute as execute_pyinstaller


def _plan(tmp_path: Path, *, arch: str = "x86_64") -> BuildPlan:
    manifest = tmp_path / "generated" / "build-features.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema": 3,
                "tool": "pyinstaller",
                "target": "linux",
                "arch": arch,
                "profile": "lite",
                "enabled": {key: False for key in ("video", "html", "bing", "hotkeys", "updates", "fonts")},
                "html_runtime": "disabled",
                "video_runtime": {"mode": "disabled"},
            }
        ),
        encoding="utf-8",
    )
    mpv = MpvBuildSelection("disabled", "disabled", "linux", arch, "", None, {})
    return BuildPlan(
        tool="pyinstaller",
        target="linux",
        profile="lite",
        mode="standalone",
        jobs=2,
        arch=arch,
        features=frozenset(),
        mpv=mpv,
        variant=f"unit-{arch}",
        generated_dir=tmp_path / "generated",
        manifest_path=manifest,
        staged_mpv_dir=None,
    )


def test_publishable_build_cannot_disable_validation(monkeypatch: pytest.MonkeyPatch):
    args = argparse.Namespace(
        profile="lite",
        features="none",
        exclude_features=None,
        skip_validate=True,
        dry_run=False,
    )
    with pytest.raises(RuntimeError, match="cannot be used"):
        execute_pyinstaller(args)


def test_architecture_mismatch_fails_preflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    plan = _plan(tmp_path, arch="arm64")
    monkeypatch.setattr(diagnostics, "ensure_project_layout", lambda: None)
    monkeypatch.setattr(diagnostics, "host_target", lambda: "linux")
    monkeypatch.setattr(diagnostics, "_selected_python_architecture", lambda: "x86_64")
    monkeypatch.setattr(diagnostics, "_python_probe", lambda *args: subprocess_result("3.13.5\n"))

    with pytest.raises(RuntimeError, match="architecture mismatch"):
        diagnostics.preflight(plan, dry_run=False)


def subprocess_result(stdout: str):
    from subprocess import CompletedProcess

    return CompletedProcess([], 0, stdout=stdout, stderr="")


def test_python_probe_is_bounded_and_reports_timeout(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 0))

    monkeypatch.setattr(diagnostics.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="timed out"):
        diagnostics._python_probe("print('probe')")
    assert isinstance(seen.get("timeout"), (int, float))
    assert 0 < float(seen["timeout"]) <= 30


def test_pinned_runtime_versions_match_requirement_files():
    root = Path(__file__).resolve().parents[1]
    base = (root / "requirements" / "base.txt").read_text(encoding="utf-8")
    html = (root / "requirements" / "html-native.txt").read_text(encoding="utf-8")
    assert f"PySide6-Essentials=={PYSIDE6_ESSENTIALS_VERSION}" in base
    assert f"pywebview=={PYWEBVIEW_VERSION}" in html


def test_nuitka_dry_plan_does_not_materialize_runtime_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from build_tools.buildlib import nuitka

    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "libmpv.so.2").write_bytes(b"ELF")
    plan = _plan(tmp_path)
    bundled = MpvBuildSelection("bundled", "bundled", "linux", "x86_64", "unit", payload, {})
    object.__setattr__(plan, "tool", "nuitka")
    object.__setattr__(plan, "mpv", bundled)
    object.__setattr__(plan, "staged_mpv_dir", plan.generated_dir / "python" / "shangbackground_native_runtime" / "payload")
    monkeypatch.setattr(nuitka, "python_executable", lambda: "/usr/bin/python3")

    command, environment = nuitka.build_args(plan, windows_console_mode="disable")

    assert any("shangbackground_native_runtime" in argument for argument in command)
    assert "PYTHONPATH" in environment
    assert not (plan.generated_dir / "python").exists()
    assert not (plan.generated_dir / "shangbackground-mpv.nuitka-package.config.yml").exists()
