"""Tests for the Inno Setup installer driver.

These tests never invoke ``ISCC.exe``. They only verify the plan resolution,
placeholder rendering, source-layout validation, and CLI contract so the
installer path stays healthy on every platform (CI runs on Linux/macOS
without Inno Setup).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from build_tools.buildlib import installer as installer_module
from build_tools.buildlib.installer import (
    InstallerPlan,
    create_installer_plan,
    create_installer_parser,
    render_iscc_command,
    _validate_source_layout,
)


def test_installer_script_and_license_are_present():
    """The .iss and license.rtf must ship with the repo at all times."""
    assert installer_module.ISS_PATH.is_file(), installer_module.ISS_PATH
    assert installer_module.LICENSE_PATH.is_file(), installer_module.LICENSE_PATH


def test_installer_script_references_license_file():
    """``LicenseFile=`` must point to ``license.rtf`` for mandatory acceptance."""
    text = installer_module.ISS_PATH.read_text(encoding="utf-8")
    assert "LicenseFile=" in text
    assert "license.rtf" in text
    # 64-bit-only install is required (PyInstaller standalone is x86_64).
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in text
    assert "ArchitecturesAllowed=x64compatible" in text


def test_installer_script_uses_python_rendered_placeholders():
    """The .iss must use ``#ifndef``-guarded placeholders so ISCC.exe can
    override them with ``/D`` flags without rewriting the .iss on disk."""
    text = installer_module.ISS_PATH.read_text(encoding="utf-8")
    for placeholder in (
        "APP_NAME",
        "APP_VERSION",
        "APP_VERSION_PUB",
        "COMPANY_NAME",
        "PRODUCT_NAME",
        "ARCH",
        "SOURCE_ROOT",
        "OUTPUT_DIR",
        "OUTPUT_BASENAME",
        "PROJECT_ROOT",
    ):
        assert f"#ifndef {placeholder}" in text, placeholder
        assert f"#define {placeholder}" in text, placeholder


def test_installer_plan_resolves_for_windows_x86_64():
    plan = create_installer_plan(
        target="windows",
        profile="lite",
        arch="x86_64",
        features=frozenset({"bing", "hotkeys", "updates", "fonts"}),
        dry_run=True,
    )
    assert isinstance(plan, InstallerPlan)
    assert plan.arch == "x86_64"
    assert plan.output_basename.startswith("ShangBackground-v")
    assert plan.output_basename.endswith("-windows-x86_64-setup")
    assert plan.iss_path == installer_module.ISS_PATH
    assert plan.license_path == installer_module.LICENSE_PATH
    assert plan.setup_executable.name == f"{plan.output_basename}.exe"
    # Variant mirrors the PyInstaller variant naming so dist-pyinstaller/windows/<variant>/standalone aligns.
    assert plan.plan.variant == "lite-x86_64", plan.plan.variant


def test_installer_plan_rejects_non_windows_targets():
    for target in ("linux", "macos"):
        with pytest.raises(RuntimeError, match="Windows-only"):
            create_installer_plan(target=target, profile="lite", arch="x86_64", features=frozenset(), dry_run=True)


def test_installer_command_carries_all_placeholders():
    plan = create_installer_plan(
        target="windows",
        profile="lite",
        arch="x86_64",
        features=frozenset(),
        dry_run=True,
    )
    command = render_iscc_command(plan)
    # The command must mention every /D override expected by the .iss.
    for flag in (
        "/DAPP_NAME=",
        "/DAPP_VERSION=",
        "/DAPP_VERSION_PUB=",
        "/DCOMPANY_NAME=",
        "/DPRODUCT_NAME=",
        "/DARCH=x86_64",
        "/DSOURCE_ROOT=",
        "/DOUTPUT_DIR=",
        "/DOUTPUT_BASENAME=",
        "/DPROJECT_ROOT=",
    ):
        assert any(arg.startswith(flag) for arg in command), flag
    # The .iss path is always the last positional argument.
    assert command[-1] == str(installer_module.ISS_PATH)


def test_installer_command_output_basename_includes_version():
    plan = create_installer_plan(
        target="windows",
        profile="lite",
        arch="x86_64",
        features=frozenset(),
        dry_run=True,
    )
    command = render_iscc_command(plan)
    output_arg = next(arg for arg in command if arg.startswith("/DOUTPUT_BASENAME="))
    from app.version import APP_VERSION

    assert f"ShangBackground-v{APP_VERSION}-windows-x86_64-setup" in output_arg


def test_validate_source_layout_reports_missing_root(tmp_path: Path):
    errors = _validate_source_layout(tmp_path / "does-not-exist")
    assert errors, "expected at least one error for missing root"
    assert any("missing" in error for error in errors)


def test_validate_source_layout_reports_missing_executable(tmp_path: Path):
    source = tmp_path / "standalone"
    bundle = source / "ShangBackground"
    bundle.mkdir(parents=True)
    # No ShangBackground.exe, no _internal/, no build-features.json.
    errors = _validate_source_layout(source)
    assert any("ShangBackground.exe" in error for error in errors)
    assert any("_internal" in error for error in errors)


def test_validate_source_layout_accepts_full_bundle(tmp_path: Path):
    source = tmp_path / "standalone"
    bundle = source / "ShangBackground"
    internal = bundle / "_internal"
    internal.mkdir(parents=True)
    (bundle / "ShangBackground.exe").write_bytes(b"MZ")
    (internal / "build-features.json").write_text("{}", encoding="utf-8")
    errors = _validate_source_layout(source)
    assert errors == ()


def test_installer_cli_parser_accepts_expected_flags():
    parser = create_installer_parser()
    args = parser.parse_args(["--profile", "lite", "--arch", "x86_64", "--dry-run"])
    assert args.target == "windows"
    assert args.profile == "lite"
    assert args.arch == "x86_64"
    assert args.dry_run is True
    assert args.skip_validate is False


def test_installer_cli_parser_defaults_to_lite_windows_x86_64():
    parser = create_installer_parser()
    args = parser.parse_args([])
    assert args.target == "windows"
    assert args.profile == "lite"
    assert args.arch == "auto"


def test_installer_cli_parser_rejects_non_windows_targets():
    parser = create_installer_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--target", "linux"])


def test_installer_main_dry_run_returns_zero(capsys: pytest.CaptureFixture[str]):
    rc = installer_module.main(["--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Inno Setup command" in out
    assert "dry-run: ISCC.exe not invoked" in out
