"""Fast, platform-independent checks for the build command contract."""

from __future__ import annotations

import argparse
import compileall
import subprocess

from .bundle import WEBVIEW_MODULES
from .constants import PROJECT_ROOT, PYINSTALLER_CONTENTS_DIRECTORY, TARGETS, python_executable
from .diagnostics import preflight
from .features import default_features
from .installer import ISS_PATH, LICENSE_PATH
from .nuitka import build_args as nuitka_args
from .plan import create_plan
from .pyinstaller import build_args as pyinstaller_args


def _check(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _option_values(arguments: list[str], flag: str) -> tuple[str, ...]:
    return tuple(arguments[index + 1] for index, value in enumerate(arguments[:-1]) if value == flag)


def _plan(tool: str, target: str, profile: str):
    return create_plan(
        tool=tool,
        target=target,
        profile=profile,
        mode="standalone",
        jobs=2,
        features=default_features(profile),
        mpv_runtime="system",
        mpv_version="auto",
        arch="auto",
        dry_run=True,
    )


def run_checks() -> tuple[str, ...]:
    errors: list[str] = []
    _check(
        compileall.compile_dir(PROJECT_ROOT / "build_tools", quiet=1, force=True),
        "build_tools contains a Python syntax error",
        errors,
    )
    _check(
        compileall.compile_dir(PROJECT_ROOT / "src", quiet=1, force=True),
        "src contains a Python syntax error",
        errors,
    )
    hooks = PROJECT_ROOT / "build_tools" / "pyinstaller_hooks"
    for forbidden in ("hook-PySide6.QtCore.py", "hook-PySide6.QtGui.py", "hook-PySide6.QtNetwork.py"):
        _check(not (hooks / forbidden).exists(), f"obsolete Qt hook override still exists: {forbidden}", errors)

    # Installer invariants: the .iss and license.rtf must always be present,
    # and the .iss must reference the license so acceptance is mandatory.
    _check(ISS_PATH.is_file(), f"Inno Setup script is missing: {ISS_PATH}", errors)
    _check(LICENSE_PATH.is_file(), f"Installer license file is missing: {LICENSE_PATH}", errors)
    if ISS_PATH.is_file():
        iss_text = ISS_PATH.read_text(encoding="utf-8")
        _check(
            "LicenseFile=" in iss_text and "license.rtf" in iss_text,
            "Inno Setup script must reference LicenseFile pointing to license.rtf",
            errors,
        )
        _check(
            "ArchitecturesInstallIn64BitMode=x64compatible" in iss_text,
            "Inno Setup script must lock to 64-bit install mode",
            errors,
        )

    for target in TARGETS:
        full_pyi = _plan("pyinstaller", target, "full")
        preflight(full_pyi, dry_run=True)
        pyi = pyinstaller_args(
            full_pyi,
            windows_console_mode="disable",
            contents_directory=PYINSTALLER_CONTENTS_DIRECTORY,
        )
        hidden = _option_values(pyi, "--hidden-import")
        excluded = _option_values(pyi, "--exclude-module")
        contents = _option_values(pyi, "--contents-directory")
        _check(contents == (PYINSTALLER_CONTENTS_DIRECTORY,), f"{target}: PyInstaller _internal layout missing", errors)
        for module in WEBVIEW_MODULES[target]:
            _check(module in hidden, f"{target}: PyInstaller hidden import missing: {module}", errors)
        for forbidden in ("PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore"):
            _check(forbidden in excluded, f"{target}: PyInstaller exclusion missing: {forbidden}", errors)

        full_nui = _plan("nuitka", target, "full")
        preflight(full_nui, dry_run=True)
        nui, _env = nuitka_args(full_nui, windows_console_mode="disable")
        _check("--disable-plugin=pywebview" in nui, f"{target}: Nuitka pywebview workaround missing", errors)
        _check("--include-package-data=webview" in nui, f"{target}: Nuitka pywebview data collection missing", errors)
        for module in WEBVIEW_MODULES[target]:
            _check(f"--include-module={module}" in nui, f"{target}: Nuitka module missing: {module}", errors)

        lite_pyi = _plan("pyinstaller", target, "lite")
        lite_args = pyinstaller_args(
            lite_pyi,
            windows_console_mode="disable",
            contents_directory=PYINSTALLER_CONTENTS_DIRECTORY,
        )
        lite_hidden = _option_values(lite_args, "--hidden-import")
        lite_excluded = _option_values(lite_args, "--exclude-module")
        _check("webview" not in lite_hidden, f"{target}: lite PyInstaller build still imports webview", errors)
        _check("webview" in lite_excluded, f"{target}: lite PyInstaller build does not exclude webview", errors)
        lite_nui, _env = nuitka_args(_plan("nuitka", target, "lite"), windows_console_mode="disable")
        _check(
            "--disable-plugin=pywebview" not in lite_nui, f"{target}: lite Nuitka build enables HTML workaround", errors
        )
        _check(
            "--nofollow-import-to=webview" in lite_nui, f"{target}: lite Nuitka build does not exclude webview", errors
        )

    return tuple(errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate ShangBackground build-tool invariants.")
    parser.add_argument(
        "--dynamic", action="store_true", help="Perform a real core-only frozen build and runtime verification"
    )
    parser.add_argument("--dynamic-tool", choices=("pyinstaller", "nuitka"), default="pyinstaller")
    args = parser.parse_args(argv)
    errors = run_checks()
    if errors:
        print("Build-tool self-test failed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("Build-tool self-test passed.", flush=True)
    print(
        "Checked syntax, staging output, native HTML backend chains, Qt WebEngine "
        "exclusion, lite build plans, and Windows installer invariants.",
        flush=True,
    )
    if not args.dynamic:
        return 0
    command = [
        python_executable(),
        str(PROJECT_ROOT / "build_tools" / "build.py"),
        "--tool",
        args.dynamic_tool,
        "--profile",
        "lite",
        "--mode",
        "standalone",
        "--features",
        "none",
        "--mpv-runtime",
        "system",
    ]
    print("Running dynamic frozen-build verification:", flush=True)
    print("  " + " ".join(command), flush=True)
    return subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode
