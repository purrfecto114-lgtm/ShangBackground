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


def test_chinese_simplified_language_file_is_bundled():
    """ChineseSimplified.isl must be bundled in the repo because it ships
    with Inno Setup 6.5.0+ only. The Chocolatey innosetup package currently
    installs 6.4.x, so referencing ``compiler:Languages\\ChineseSimplified.isl``
    fails at compile time. We bundle the official language file from the
    Inno Setup source repo and reference it via a relative path instead.
    """
    isl_path = installer_module.INSTALLER_DIR / "ChineseSimplified.isl"
    assert isl_path.is_file(), isl_path
    text = isl_path.read_text(encoding="utf-8")
    # Sanity-check it's the real Inno Setup language file, not an empty stub.
    assert "[LangOptions]" in text or "[Messages]" in text, text[:200]
    assert "chinesesimp" in text.lower() or "simplified" in text.lower(), text[:200]


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


def test_installer_script_uses_only_valid_inno_setup_directives():
    """Guard against typos in [Setup] section directives. Inno Setup aborts
    compilation with 'Unrecognized [Setup] section directive' if a directive
    name is misspelled (e.g. ``VersionInfoFileVersion`` which does not exist;
    the correct name is just ``VersionInfoVersion``).
    """
    text = installer_module.ISS_PATH.read_text(encoding="utf-8")
    # Source: Inno Setup 6 help file, [Setup] section directives list.
    # We only assert the directives we actually use plus a denylist of
    # common misspellings that look right but cause compile failures.
    forbidden_directives = (
        "VersionInfoFileVersion",  # NOT a real Inno Setup directive; the correct name is VersionInfoVersion
        "VersionInfoProductText",  # misspelling of VersionInfoProductVersion
    )
    for forbidden in forbidden_directives:
        assert forbidden not in text, f".iss uses non-existent Inno Setup directive: {forbidden}"

    # Spot-check that the directives we DO use are the canonical names.
    for required in (
        "VersionInfoVersion=",
        "VersionInfoCompany=",
        "VersionInfoProductName=",
        "VersionInfoProductVersion=",
        "AppId=",
        "AppName=",
        "AppVersion=",
        "LicenseFile=",
        "OutputDir=",
        "OutputBaseFilename=",
        "ArchitecturesInstallIn64BitMode=",
    ):
        assert required in text, f".iss is missing required [Setup] directive: {required}"


def test_installer_script_does_not_check_destination_in_prepare_to_install():
    """Regression: ``PrepareToInstall`` runs BEFORE [Files] copies anything,
    so checking ``{app}\\ShangBackground.exe`` at that point always fails and
    blocks every install with '打包产物缺失'. The .iss must NOT contain a
    PrepareToInstall function that calls FileExists on a destination path.

    The post-install sanity check belongs in ``CurStepChanged(ssPostInstall)``
    instead, which fires AFTER all [Files] entries have been copied.
    """
    text = installer_module.ISS_PATH.read_text(encoding="utf-8")
    # The .iss must NOT define a PrepareToInstall function.
    assert "function PrepareToInstall" not in text, (
        ".iss defines PrepareToInstall - this hook runs BEFORE file copy, so "
        "any FileExists check on {app} will always fail and block installation. "
        "Move the check to CurStepChanged(ssPostInstall) instead."
    )
    # The .iss MUST define a CurStepChanged post-install guard.
    assert "procedure CurStepChanged" in text, (
        ".iss must define CurStepChanged for post-install sanity check"
    )
    assert "ssPostInstall" in text, ".iss CurStepChanged must check ssPostInstall step"


def test_uninstaller_does_not_create_setup_wizard_pages():
    text = installer_module.ISS_PATH.read_text(encoding="utf-8")
    assert "CreateOutputMsgPage" not in text
    assert "--quit --wait-for-exit" in text
    assert "SuppressibleMsgBox" in text
    assert "Check: ShouldDeleteConfig" in text


def test_installer_plan_resolves_for_windows_x86_64():
    plan = create_installer_plan(
        target="windows",
        profile="lite",
        arch="x86_64",
        features=frozenset({"bing", "hotkeys", "updates", "fonts"}),
        dry_run=True,
        tool="pyinstaller",
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


def test_installer_auto_arch_is_windows_x86_64(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(installer_module, "normalize_arch", lambda _arch: "arm64")
    plan = create_installer_plan(
        target="windows",
        profile="full",
        arch="auto",
        features=frozenset(),
        dry_run=True,
    )
    assert plan.arch == "x86_64"


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


def test_validate_source_layout_reports_missing_executable_pyinstaller(tmp_path: Path):
    """PyInstaller layout: source/ShangBackground/ exists but ShangBackground.exe
    and _internal/ are missing."""
    source = tmp_path / "standalone"
    bundle = source / "ShangBackground"
    bundle.mkdir(parents=True)
    # No ShangBackground.exe, no _internal/, no build-features.json.
    errors = _validate_source_layout(source)
    assert any("ShangBackground.exe" in error for error in errors)
    assert any("_internal" in error for error in errors)


def test_validate_source_layout_reports_missing_executable_nuitka(tmp_path: Path):
    """Nuitka layout: source/ShangBackground.dist/ exists but ShangBackground.exe
    and build-features.json are missing."""
    source = tmp_path / "standalone"
    dist = source / "ShangBackground.dist"
    dist.mkdir(parents=True)
    errors = _validate_source_layout(source)
    assert any("ShangBackground.exe" in error for error in errors)
    assert any("build-features.json" in error for error in errors)


def test_validate_source_layout_accepts_full_bundle_pyinstaller(tmp_path: Path):
    source = tmp_path / "standalone"
    bundle = source / "ShangBackground"
    internal = bundle / "_internal"
    internal.mkdir(parents=True)
    (bundle / "ShangBackground.exe").write_bytes(b"MZ")
    (internal / "build-features.json").write_text("{}", encoding="utf-8")
    errors = _validate_source_layout(source)
    assert errors == ()


def test_validate_source_layout_accepts_full_bundle_nuitka(tmp_path: Path):
    source = tmp_path / "standalone"
    dist = source / "ShangBackground.dist"
    dist.mkdir(parents=True)
    (dist / "ShangBackground.exe").write_bytes(b"MZ")
    (dist / "build-features.json").write_text("{}", encoding="utf-8")
    errors = _validate_source_layout(source)
    assert errors == ()


def test_validate_source_layout_rejects_ambiguous_bundles(tmp_path: Path):
    source = tmp_path / "standalone"
    (source / "ShangBackground").mkdir(parents=True)
    (source / "ShangBackground.dist").mkdir()
    errors = _validate_source_layout(source)
    assert any("Ambiguous" in error for error in errors)


def test_installer_cli_parser_accepts_expected_flags():
    parser = create_installer_parser()
    args = parser.parse_args(["--profile", "lite", "--arch", "x86_64", "--dry-run"])
    assert args.target == "windows"
    assert args.profile == "lite"
    assert args.arch == "x86_64"
    assert args.dry_run is True
    assert args.skip_validate is False


def test_installer_cli_parser_defaults_to_full_nuitka_windows():
    parser = create_installer_parser()
    args = parser.parse_args([])
    assert args.target == "windows"
    # The default is now full + nuitka to match the release pipeline.
    assert args.profile == "full"
    assert args.tool == "nuitka"
    assert args.arch == "auto"


def test_installer_cli_parser_rejects_non_windows_targets():
    parser = create_installer_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--target", "linux"])


def test_installer_main_dry_run_returns_zero(capsys: pytest.CaptureFixture[str]):
    rc = installer_module.main([
        "--dry-run", "--skip-validate", "--tool", "pyinstaller", "--profile", "lite"
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Inno Setup command" in out
    assert "dry-run: ISCC.exe not invoked" in out
