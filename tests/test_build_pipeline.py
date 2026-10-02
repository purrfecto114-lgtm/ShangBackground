from __future__ import annotations

import json
import os
from pathlib import Path
import pytest

from build_tools.buildlib import diagnostics
from build_tools.buildlib.constants import python_executable
from build_tools.buildlib.mpv_runtime import MpvBuildSelection
from build_tools.buildlib.plan import (
    BuildPlan,
    discard_staging_output,
    prepare_staging_output,
    publish_staging_output,
    recover_published_output,
)


def _plan(tmp_path: Path) -> BuildPlan:
    manifest = tmp_path / "generated" / "build-features.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema": 3,
                "tool": "pyinstaller",
                "target": "linux",
                "profile": "lite",
                "enabled": {key: False for key in ("video", "html", "bing", "hotkeys", "updates", "fonts")},
                "html_runtime": "disabled",
                "video_runtime": {"mode": "disabled"},
            }
        ),
        encoding="utf-8",
    )
    mpv = MpvBuildSelection(
        requested_mode="disabled",
        mode="disabled",
        target="linux",
        arch="x86_64",
        runtime_id="",
        payload_dir=None,
        metadata={},
    )
    return BuildPlan(
        tool="pyinstaller",
        target="linux",
        profile="lite",
        mode="standalone",
        jobs=2,
        arch="x86_64",
        features=frozenset(),
        mpv=mpv,
        variant="unit",
        generated_dir=tmp_path / "generated",
        manifest_path=manifest,
        staged_mpv_dir=None,
    )


def test_prepare_staging_removes_stale_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    plan = _plan(tmp_path)
    monkeypatch.setattr("build_tools.buildlib.plan.PROJECT_ROOT", tmp_path)
    stale = plan.build_output_dir / "stale.exe"
    stale.parent.mkdir(parents=True)
    stale.write_text("old", encoding="utf-8")

    prepare_staging_output(plan)

    assert not plan.build_output_dir.exists()


def test_publish_replaces_release_only_after_staging_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("build_tools.buildlib.plan.PROJECT_ROOT", tmp_path)
    plan = _plan(tmp_path)
    final = plan.output_dir
    final.mkdir(parents=True)
    (final / "marker.txt").write_text("old", encoding="utf-8")
    plan.build_output_dir.mkdir(parents=True)
    (plan.build_output_dir / "marker.txt").write_text("new", encoding="utf-8")

    publish_staging_output(plan)

    assert (final / "marker.txt").read_text(encoding="utf-8") == "new"
    assert not plan.build_output_dir.exists()


def test_failed_build_cleanup_does_not_delete_published_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("build_tools.buildlib.plan.PROJECT_ROOT", tmp_path)
    plan = _plan(tmp_path)
    final = plan.output_dir
    final.mkdir(parents=True)
    (final / "marker.txt").write_text("good", encoding="utf-8")
    plan.build_output_dir.mkdir(parents=True)
    (plan.build_output_dir / "marker.txt").write_text("broken", encoding="utf-8")

    discard_staging_output(plan)

    assert (final / "marker.txt").read_text(encoding="utf-8") == "good"
    assert not plan.build_output_dir.exists()


def test_python_executable_points_to_an_existing_console_interpreter():
    selected = Path(python_executable())
    assert selected.is_file()
    if os.name == "nt":
        assert selected.name.lower() == "python.exe"


def test_pyinstaller_macos_executable_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("build_tools.buildlib.plan.PROJECT_ROOT", tmp_path)
    plan = _plan(tmp_path)
    object.__setattr__(plan, "target", "macos")
    executable = plan.build_output_dir / "ShangBackground.app" / "Contents" / "MacOS" / "ShangBackground"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"binary")

    assert diagnostics.pyinstaller_executable(plan) == executable


def test_staging_and_published_outputs_are_separate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("build_tools.buildlib.plan.PROJECT_ROOT", tmp_path)
    plan = _plan(tmp_path)

    assert plan.build_output_dir != plan.output_dir
    assert plan.build_output_dir.is_relative_to(plan.generated_dir)
    assert plan.output_dir.is_relative_to(tmp_path / "dist-pyinstaller")


def test_publish_rolls_back_when_exchange_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import build_tools.buildlib.plan as plan_module

    monkeypatch.setattr(plan_module, "PROJECT_ROOT", tmp_path)
    plan = _plan(tmp_path)
    final = plan.output_dir
    final.mkdir(parents=True)
    (final / "marker.txt").write_text("old", encoding="utf-8")
    plan.build_output_dir.mkdir(parents=True)
    (plan.build_output_dir / "marker.txt").write_text("new", encoding="utf-8")
    real_replace = plan_module.os.replace
    calls = 0

    def failing_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated publish failure")
        return real_replace(source, destination)

    monkeypatch.setattr(plan_module.os, "replace", failing_replace)
    with pytest.raises(OSError, match="simulated publish failure"):
        publish_staging_output(plan)

    assert (final / "marker.txt").read_text(encoding="utf-8") == "old"
    assert (plan.build_output_dir / "marker.txt").read_text(encoding="utf-8") == "new"


def test_recover_restores_release_left_in_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("build_tools.buildlib.plan.PROJECT_ROOT", tmp_path)
    plan = _plan(tmp_path)
    backup = plan.output_dir.parent / f".{plan.output_dir.name}.previous-crash"
    backup.mkdir(parents=True)
    (backup / "marker.txt").write_text("known-good", encoding="utf-8")

    recover_published_output(plan)

    assert (plan.output_dir / "marker.txt").read_text(encoding="utf-8") == "known-good"
    assert not backup.exists()


def test_dry_run_plan_does_not_create_or_rewrite_generated_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import build_tools.buildlib.plan as plan_module

    monkeypatch.setattr(plan_module, "PROJECT_ROOT", tmp_path)
    plan = plan_module.create_plan(
        tool="pyinstaller",
        target="linux",
        profile="lite",
        mode="standalone",
        jobs=2,
        features=(),
        mpv_runtime="system",
        mpv_version="auto",
        arch="x86_64",
        dry_run=True,
    )

    assert not plan.generated_dir.exists()
    assert not plan.manifest_path.exists()


def test_dry_run_requirement_install_does_not_create_report_parent(tmp_path: Path):
    from build_tools.buildlib.runner import install_requirements

    requirement = tmp_path / "requirements.txt"
    requirement.write_text("", encoding="utf-8")
    report = tmp_path / "generated" / "pip-report.json"

    install_requirements((requirement,), verbose=False, dry_run=True, report_path=report)

    assert not report.parent.exists()


def test_pyinstaller_flattens_windows_mpv_native_dependencies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from build_tools.buildlib import bundle, pyinstaller

    monkeypatch.setattr(pyinstaller, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(bundle, "PROJECT_ROOT", tmp_path)
    (tmp_path / "src" / "img").mkdir(parents=True)
    (tmp_path / "src" / "lang").mkdir(parents=True)
    payload = tmp_path / "runtime" / "nested"
    payload.mkdir(parents=True)
    dependency = payload / "avcodec-61.dll"
    dependency.write_bytes(b"dll")
    (payload / "mpv.exe").write_bytes(b"exe")
    plan = _plan(tmp_path)
    object.__setattr__(plan, "target", "windows")
    object.__setattr__(plan, "features", frozenset({"video"}))
    object.__setattr__(
        plan,
        "mpv",
        MpvBuildSelection(
            requested_mode="bundled",
            mode="bundled",
            target="windows",
            arch="x86_64",
            runtime_id="fixture",
            payload_dir=tmp_path / "runtime",
            metadata={"version": "fixture"},
        ),
    )

    command = pyinstaller.build_args(plan, windows_console_mode="disable", contents_directory="_internal")

    assert f"{dependency}{os.pathsep}bin/mpv" in command


def test_nuitka_mpv_payload_ships_as_data_files_never_dlls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """v1.6.2 contract: the Nuitka mpv package config must reference the
    payload via data-files only. Nuitka 4.1.3's UPX plugin compresses every
    dll/exe entry point copied through the ``dlls`` mechanism
    (UpxPlugin.onCopiedDLL has no per-file opt-out), and UPX-packed
    mpv.exe / codec DLLs are a well-documented antivirus false-positive
    magnet. The release smoke test only runs ``--version``, so a
    UPX-broken mpv.exe would ship unnoticed. Data files are copied
    verbatim and land flat in ``bin/mpv`` — the layout
    verify_bundled_runtime_output() enforces (mpv.exe and runtime.json as
    direct children of bin/mpv)."""
    from build_tools.buildlib import nuitka, plan as plan_module

    monkeypatch.setattr(plan_module, "PROJECT_ROOT", tmp_path)
    payload = tmp_path / "runtime"
    payload.mkdir()
    (payload / "mpv.exe").write_bytes(b"exe")
    (payload / "vulkan-1.dll").write_bytes(b"dll")
    (payload / "runtime.json").write_text("{}", encoding="utf-8")
    (payload / "licenses").mkdir()
    (payload / "licenses" / "mpv.txt").write_text("GPL", encoding="utf-8")
    plan = _plan(tmp_path)
    object.__setattr__(plan, "tool", "nuitka")
    object.__setattr__(plan, "target", "windows")
    object.__setattr__(
        plan,
        "mpv",
        MpvBuildSelection(
            requested_mode="bundled",
            mode="bundled",
            target="windows",
            arch="x86_64",
            runtime_id="fixture",
            payload_dir=payload,
            metadata={"version": "fixture"},
        ),
    )
    # create_plan stages the payload directly into the package directory
    # (flat, no payload/ child) — reproduce that staging here.
    staged = plan.generated_dir / "python" / "shangbackground_native_runtime"
    plan_module._copy_verified_payload(payload, staged)

    package_root, config = nuitka._runtime_package(plan, materialize=True)

    assert package_root is not None and config is not None
    # Flat staging: payload files are direct children of the package dir
    # alongside the materialized anchor module.
    assert (staged / "__init__.py").is_file()
    assert (staged / "mpv.exe").is_file()
    assert (staged / "runtime.json").is_file()
    text = config.read_text(encoding="utf-8")
    # The UPX-exposed ``dlls`` mechanism must stay gone — a regression here
    # silently re-exposes the bundled mpv.exe to UPX compression.
    assert "dlls:" not in text
    assert "executable:" not in text
    # Flat patterns (no payload/ prefix) land flat in bin/mpv. The ``when``
    # value must stay 'win32' — Nuitka's evaluation context only defines
    # win32/linux/macos, and an unknown name (e.g. 'windows') passes the
    # yaml schema but aborts the build at condition evaluation (36-V
    # mutation finding: no test pinned this).
    for needed in ("'*.dll'", "'*.exe'", "'*.json'", "'*.txt'", "dest_path: 'bin/mpv'", "when: 'win32'"):
        assert needed in text


def test_nuitka_bundled_plan_stages_payload_into_package_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """v1.6.2: create_plan must stage the mpv payload directly into the
    shangbackground_native_runtime package directory (flat, no payload/
    child). Nuitka data-file patterns preserve the path relative to the
    package folder, so only a flat staging layout lands flat in the
    bundle's bin/mpv — a nested staging breaks
    verify_bundled_runtime_output() (fail-closed: blocks the release CI
    rather than silently shipping a broken payload, but still an outage).
    Acceptance finding #3: this contract previously had no pinning test."""
    import struct

    from build_tools.buildlib import plan as plan_module

    monkeypatch.setattr(plan_module, "PROJECT_ROOT", tmp_path)
    # The host guard rejects real (non-dry-run) builds for a foreign target —
    # it exists to stop accidental cross-compiles, not to make the staging
    # contract untestable. The staging logic below is host-independent, so
    # pretend this host is Windows (same pattern the CLI tests use for
    # foreign-target dry-runs, here for the real staging path).
    monkeypatch.setattr(plan_module, "host_target", lambda: "windows")

    def fake_pe(machine: int) -> bytes:
        header = bytearray(0x40)
        header[0:2] = b"MZ"
        header[0x3C:0x40] = struct.pack("<I", 0x40)
        return bytes(header) + b"PE\0\0" + struct.pack("<H", machine)

    # A structurally valid Windows x86_64 payload (PE machine=0x8664).
    payload = tmp_path / "src" / "bin" / "mpv" / "windows" / "x86_64" / "fixture"
    payload.mkdir(parents=True)
    (payload / "mpv.exe").write_bytes(fake_pe(0x8664))
    (payload / "vulkan-1.dll").write_bytes(fake_pe(0x8664))
    (payload / "runtime.json").write_text("{}", encoding="utf-8")

    built = plan_module.create_plan(
        tool="nuitka",
        target="windows",
        profile="full",
        mode="standalone",
        jobs=2,
        features=frozenset({"video"}),
        mpv_runtime="bundled",
        mpv_version="auto",
        arch="x86_64",
        dry_run=False,
    )

    staged = built.staged_mpv_dir
    assert staged is not None
    assert staged.name == "shangbackground_native_runtime"
    assert staged.parent.name == "python"
    # Flat: payload children are direct children of the staged package dir.
    assert (staged / "mpv.exe").is_file()
    assert (staged / "runtime.json").is_file()
    assert not (staged / "payload").exists()


def test_publish_retries_on_transient_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Regression: on Windows, antivirus / file watchers can briefly lock
    freshly-built files, causing ``os.replace`` to fail with ``PermissionError``
    (WinError 5). The publish step must retry a few times before giving up
    so transient locks do not fail the whole build."""
    import build_tools.buildlib.plan as plan_module

    monkeypatch.setattr(plan_module, "PROJECT_ROOT", tmp_path)
    # Speed up the test: do not actually sleep between retries.
    monkeypatch.setattr(plan_module, "_PUBLISH_RETRY_DELAY_SECONDS", 0.0)
    plan = _plan(tmp_path)
    final = plan.output_dir
    final.mkdir(parents=True)
    (final / "marker.txt").write_text("old", encoding="utf-8")
    plan.build_output_dir.mkdir(parents=True)
    (plan.build_output_dir / "marker.txt").write_text("new", encoding="utf-8")

    real_replace = plan_module.os.replace
    attempts = {"count": 0}

    def transiently_failing_replace(source, destination):
        attempts["count"] += 1
        # Fail the first two attempts of the staging->final move with
        # PermissionError, then succeed.
        if source == plan.build_output_dir and attempts["count"] <= 2:
            raise PermissionError(5, "Access is denied (simulated AV lock)")
        return real_replace(source, destination)

    monkeypatch.setattr(plan_module.os, "replace", transiently_failing_replace)

    publish_staging_output(plan)

    # The publish must have succeeded after retries.
    assert (final / "marker.txt").read_text(encoding="utf-8") == "new"
    assert not plan.build_output_dir.exists()
    # At least 3 attempts: 2 failures + 1 success on the staging->final move,
    # plus 1 successful final->backup move before that.
    assert attempts["count"] >= 3


def test_publish_falls_back_to_copytree_when_replace_permanently_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """If every ``os.replace`` retry fails, the publish step must fall back to
    ``shutil.copytree`` + ``shutil.rmtree`` so a stray locked file does not
    block the build permanently."""
    import build_tools.buildlib.plan as plan_module

    monkeypatch.setattr(plan_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(plan_module, "_PUBLISH_RETRY_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(plan_module, "_PUBLISH_RETRY_ATTEMPTS", 2)
    plan = _plan(tmp_path)
    final = plan.output_dir
    final.mkdir(parents=True)
    (final / "marker.txt").write_text("old", encoding="utf-8")
    plan.build_output_dir.mkdir(parents=True)
    (plan.build_output_dir / "marker.txt").write_text("new", encoding="utf-8")

    def always_failing_replace(source, destination):
        raise PermissionError(5, "Access is denied (permanent lock)")

    monkeypatch.setattr(plan_module.os, "replace", always_failing_replace)

    # Mock shutil.rmtree so the rollback path in publish_staging_output
    # can also use the copytree fallback (it calls _replace_directory_with_retry
    # to move backup back to final).
    publish_staging_output(plan)

    # The publish must have succeeded via the copytree fallback.
    assert (final / "marker.txt").read_text(encoding="utf-8") == "new"
    # The staging directory must have been removed by the fallback.
    assert not plan.build_output_dir.exists()
