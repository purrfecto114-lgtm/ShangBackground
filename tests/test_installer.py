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
        "BUNDLE_SUBDIR",
        "MANIFEST_RELATIVE",
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
    # Source: current Inno Setup 7 help, [Setup] section directives list.
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
        "SetupArchitecture=x64",
        "MinVersion=10.0.17763",
        "SetupLogging=yes",
    ):
        assert required in text, f".iss is missing required [Setup] directive: {required}"


def test_installer_prepare_to_install_does_not_perform_post_copy_sanity_checks():
    """Regression: ``PrepareToInstall`` runs before [Files] copies the new payload.

    It may inspect an *existing* installed executable for upgrade coordination,
    but missing destination files must never be treated as an installation error
    here. New-payload sanity checks belong in ``ssPostInstall``.
    """
    text = installer_module.ISS_PATH.read_text(encoding="utf-8")
    prepare = text.split("function PrepareToInstall", 1)[1].split(
        "function InitializeUninstall", 1
    )[0]
    assert "RaiseException" not in prepare
    assert "打包产物缺失" not in prepare
    assert "build-features.json" not in prepare
    assert "procedure CurStepChanged" in text
    assert "ssPostInstall" in text


def test_installer_attempts_graceful_upgrade_shutdown_before_restart_manager():
    text = installer_module.ISS_PATH.read_text(encoding="utf-8")
    prepare = text.split("function PrepareToInstall", 1)[1].split(
        "function InitializeUninstall", 1
    )[0]
    assert "ExecAsOriginalUser" in prepare
    assert "--quit --wait-for-exit" in prepare
    assert "GetPackedVersion" in prepare
    assert "PackVersionComponents(1, 4, 2, 0)" in prepare
    assert "ComparePackedVersion" in prepare
    assert "Result := '';" in prepare
    assert "CloseApplications=yes" in text
    assert "AppMutex=" not in text


def test_installer_code_section_has_no_line_starting_with_bracket():
    """Regression: Inno Setup 7's parser treats a line starting with ``[``
    (after whitespace) as a potential section header. Inside the ``[Code]``
    section, Pascal array literals like ``[ResultCode, SysErrorMessage(...)]``
    must NOT appear at the start of a continuation line — otherwise ISCC
    aborts with "Invalid section tag".

    See: https://github.com/purrfecto114-lgtm/ShangBackground commit bf124a0
    broke the Windows setup.exe build because a multi-line Format() call had
    its array argument on a separate line starting with ``[``.
    """
    text = installer_module.ISS_PATH.read_text(encoding="utf-8")
    code_section = text.split("[Code]", 1)[1] if "[Code]" in text else ""
    # Find lines that start with [ (after optional whitespace) inside [Code].
    # These are NOT valid Pascal statements — they're parser traps.
    bad_lines = []
    for i, line in enumerate(code_section.split("\n"), start=1):
        stripped = line.lstrip()
        if stripped.startswith("[") and not stripped.startswith("[Code]"):
            # Allow comments that happen to start with [
            if not stripped.startswith("[//") and not stripped.startswith("[;"):
                bad_lines.append((i, line.rstrip()))
    assert not bad_lines, (
        f"Lines starting with '[' found inside [Code] section — "
        f"Inno Setup 7 will abort with 'Invalid section tag': {bad_lines}"
    )


def test_uninstaller_does_not_create_setup_wizard_pages():
    text = installer_module.ISS_PATH.read_text(encoding="utf-8")
    assert "CreateOutputMsgPage" not in text
    assert "--quit --wait-for-exit" in text
    assert "SuppressibleMsgBox" in text
    assert "Check: ShouldDeleteConfig" in text


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
        "/DBUNDLE_SUBDIR=",
        "/DMANIFEST_RELATIVE=",
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
    (internal / "build-features.json").write_text(
        '{"tool":"pyinstaller","target":"windows","arch":"x86_64"}', encoding="utf-8"
    )
    errors = _validate_source_layout(source)
    assert errors == ()


def test_validate_source_layout_accepts_full_bundle_nuitka(tmp_path: Path):
    source = tmp_path / "standalone"
    dist = source / "ShangBackground.dist"
    dist.mkdir(parents=True)
    (dist / "ShangBackground.exe").write_bytes(b"MZ")
    (dist / "build-features.json").write_text(
        '{"tool":"nuitka","target":"windows","arch":"x86_64"}', encoding="utf-8"
    )
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


def test_installer_script_uses_one_fail_fast_bundle_source():
    text = installer_module.ISS_PATH.read_text(encoding="utf-8")
    files_section = text.split("[Files]", 1)[1].split("[Icons]", 1)[0]
    assert "{#BUNDLE_SUBDIR}" in files_section
    source_lines = [line for line in files_section.splitlines() if line.lstrip().startswith("Source:")]
    assert all("skipifsourcedoesntexist" not in line for line in source_lines)
    assert files_section.count("Source:") == 1
    assert 'Type: filesandordirs; Name: "{app}\\_internal"' in text
    assert 'Type: filesandordirs; Name: "{app}\\bin\\mpv"' in text
    assert 'Name: "{app}\\*"' not in text


def test_uninstaller_can_continue_when_installed_executable_is_broken():
    text = installer_module.ISS_PATH.read_text(encoding="utf-8")
    uninstall_init = text.split("function InitializeUninstall(): Boolean;", 1)[1].split("var\n  DeleteConfigSelected", 1)[0]
    assert "MB_YESNO" in uninstall_init
    assert "IDYES" in uninstall_init
    assert "Result := False" not in uninstall_init


def test_validate_source_layout_rejects_invalid_manifest(tmp_path: Path):
    source = tmp_path / "standalone"
    dist = source / "ShangBackground.dist"
    dist.mkdir(parents=True)
    (dist / "ShangBackground.exe").write_bytes(b"MZ")
    (dist / "build-features.json").write_text("not-json", encoding="utf-8")
    errors = _validate_source_layout(source)
    assert any("manifest is invalid" in error for error in errors)


def test_validate_source_layout_rejects_wrong_freezer_manifest(tmp_path: Path):
    source = tmp_path / "standalone"
    dist = source / "ShangBackground.dist"
    dist.mkdir(parents=True)
    (dist / "ShangBackground.exe").write_bytes(b"MZ")
    (dist / "build-features.json").write_text(
        '{"tool":"pyinstaller","target":"windows","arch":"x86_64"}', encoding="utf-8"
    )
    errors = _validate_source_layout(source)
    assert any("tool mismatch" in error for error in errors)


def test_validate_source_layout_rejects_selected_tool_mismatch(tmp_path: Path):
    source = tmp_path / "standalone"
    dist = source / "ShangBackground.dist"
    dist.mkdir(parents=True)
    (dist / "ShangBackground.exe").write_bytes(b"MZ")
    (dist / "build-features.json").write_text(
        '{"tool":"nuitka","target":"windows","arch":"x86_64"}', encoding="utf-8"
    )
    errors = _validate_source_layout(source, expected_tool="pyinstaller")
    assert any("installer tool is pyinstaller" in error for error in errors)


def test_real_installer_cannot_skip_validation(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(installer_module, "_print_plan", lambda _plan: None)
    code = installer_module.main([
        "--target", "windows", "--tool", "nuitka", "--profile", "full",
        "--arch", "x86_64", "--input", str(tmp_path / "missing"), "--skip-validate",
    ])
    assert code == 2
