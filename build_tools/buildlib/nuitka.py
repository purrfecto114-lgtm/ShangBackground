from __future__ import annotations

import argparse
import os
from pathlib import Path

from .bundle import data_directories, dynamic_modules, excluded_modules
from .cli import create_build_parser, print_plan, print_section
from .constants import (
    APP_NAME,
    COMPANY_NAME,
    DESCRIPTION,
    ENTRY_SCRIPT,
    PROJECT_ROOT,
    ensure_build_python_environment,
    python_executable,
    read_version,
    windows_numeric_version,
)
from .diagnostics import nuitka_executable, preflight, validate_frozen_runtime, validate_nuitka_output
from .features import requirement_files, resolve_features
from .locking import ExclusiveBuildLock
from .plan import (
    BuildPlan,
    create_plan,
    discard_staging_output,
    prepare_staging_output,
    publish_staging_output,
    relative,
)
from .runner import install_requirements, run_build
from .upx import resolve_upx_for_build, upx_supported_for_target


def _runtime_package(plan: BuildPlan, *, materialize: bool) -> tuple[Path | None, Path | None]:
    source = plan.staged_mpv_dir or plan.mpv.payload_dir
    if plan.mpv.mode != "bundled" or source is None:
        return None, None
    package_root = plan.generated_dir / "python"
    package = package_root / "shangbackground_native_runtime"
    if materialize:
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text(
            '"""Build-only native runtime anchor."""\n', encoding="utf-8"
        )
    # During dry-runs the potentially large payload is intentionally not
    # copied, but the generated command must still show the complete package
    # configuration contract. A real build always stages the verified payload.
    config = plan.generated_dir / "shangbackground-mpv.nuitka-package.config.yml"
    when = {"windows": "win32", "linux": "linux", "macos": "macos"}[plan.target]
    lines = ["---", "- module-name: 'shangbackground_native_runtime'", "  dlls:"]
    if plan.target == "windows":
        lines.extend(
            (
                "    - from_filenames:",
                "        relative_path: 'payload'",
                "        prefixes: ['']",
                "        suffixes: ['dll']",
                "      dest_path: 'bin/mpv'",
                f"      when: '{when}'",
                "    - from_filenames:",
                "        relative_path: 'payload'",
                "        prefixes: ['mpv']",
                "        suffixes: ['exe']",
                "      dest_path: 'bin/mpv'",
                "      executable: 'yes'",
                f"      when: '{when}'",
            )
        )
    elif plan.target == "linux":
        lines.extend(
            (
                "    - from_filenames:",
                "        relative_path: 'payload'",
                "        prefixes: ['lib']",
                "      dest_path: 'bin/mpv'",
                f"      when: '{when}'",
                "    - from_filenames:",
                "        relative_path: 'payload'",
                "        prefixes: ['mpv']",
                "      dest_path: 'bin/mpv'",
                "      executable: 'yes'",
                f"      when: '{when}'",
            )
        )
    lines.extend(
        (
            "  data-files:",
            "    patterns:",
            "      - 'payload/*.txt'",
            "      - 'payload/*.json'",
            "      - 'payload/*.conf'",
            "      - 'payload/licenses/*'",
            "    dest_path: 'bin/mpv'",
            f"    when: '{when}'",
        )
    )
    if materialize:
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return package_root, config


def build_args(
    plan: BuildPlan,
    *,
    windows_console_mode: str,
    materialize_runtime: bool = False,
    upx_binary: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    report = plan.build_output_dir / "compilation-report.xml"
    # Nuitka 4.1.3 introduced --mode= as the unified way to select standalone /
    # onefile. The --macos-create-app-bundle flag is the macOS-specific way to
    # produce a .app bundle; it does NOT conflict with --mode=standalone
    # (only with --mode=app). For macOS standalone, use --mode=standalone +
    # --macos-create-app-bundle to get a .app bundle with the correct
    # Contents/MacOS/ShangBackground layout that our validator expects.
    command = [
        python_executable(),
        "-m",
        "nuitka",
        f"--mode={plan.mode}",
        "--assume-yes-for-downloads",
        f"--output-dir={relative(plan.build_output_dir)}",
        f"--output-filename={APP_NAME}{'.exe' if plan.target == 'windows' else ''}",
        "--enable-plugin=pyside6",
        f"--jobs={plan.jobs}",
        "--lto=no",
        "--remove-output",
        f"--report={relative(report)}",
        "--noinclude-setuptools-mode=nofollow",
        "--noinclude-pytest-mode=nofollow",
    ]
    # UPX post-build compression. In Nuitka 4.1.3, UPX is a standard plugin
    # activated via --enable-plugin=upx. The optional --upx-binary=PATH
    # sub-option only becomes a recognized flag AFTER the plugin is enabled,
    # so we must pass --enable-plugin=upx first, then optionally --upx-binary.
    # Nuitka auto-detects `upx` on PATH if --upx-binary is omitted; we pass
    # the explicit path when we have one so CI reproducibility is guaranteed.
    # On macOS we never enable UPX: compressed Mach-O breaks codesign and
    # the Apple Silicon ABI.
    if upx_binary is not None:
        if not upx_supported_for_target(plan.target):
            raise RuntimeError(
                f"UPX was requested for target {plan.target!r} but UPX is only "
                "supported on Windows and Linux. Drop --upx for macOS builds."
            )
        command.append("--enable-plugin=upx")
        command.append(f"--upx-binary={upx_binary}")
    for source, destination in data_directories(plan):
        command.append(f"--include-data-dir={relative(source)}={destination}")
    command.append(f"--include-data-files={relative(plan.manifest_path)}=build-features.json")

    if "html" in plan.features:
        # Nuitka 4.1.3's always-on pywebview plugin omits
        # webview.platforms.win32 although winforms imports it. Disable that
        # plugin and include the exact native backend chain ourselves. Standard
        # Nuitka package configuration still collects webview/lib DLLs/data.
        command.extend(("--disable-plugin=pywebview", "--include-package-data=webview"))
    for module in dynamic_modules(plan):
        command.append(f"--include-module={module}")
    for module in excluded_modules(plan):
        command.append(f"--nofollow-import-to={module}")

    env: dict[str, str] = {}
    package_root, config = _runtime_package(plan, materialize=materialize_runtime)
    if package_root is not None and config is not None:
        command.extend(
            (
                "--include-package=shangbackground_native_runtime",
                f"--user-package-configuration-file={relative(config)}",
            )
        )
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (os.fspath(package_root), os.environ.get("PYTHONPATH", ""))))

    if plan.target == "windows":
        command.append(f"--windows-console-mode={windows_console_mode}")
        icon = PROJECT_ROOT / "src" / "img" / "LOGO.ico"
        if icon.is_file():
            command.append(f"--windows-icon-from-ico={relative(icon)}")
        command.extend(
            (
                f"--file-version={windows_numeric_version()}",
                f"--product-version={windows_numeric_version()}",
                f"--company-name={COMPANY_NAME}",
                f"--file-description={DESCRIPTION}",
                f"--product-name={DESCRIPTION}",
            )
        )
    elif plan.target == "macos":
        # --macos-create-app-bundle produces a .app bundle with the correct
        # Contents/MacOS/ShangBackground layout. It works with --mode=standalone
        # (only conflicts with --mode=app, which we don't use).
        command.extend(
            (
                "--macos-create-app-bundle",
                f"--macos-app-name={APP_NAME}",
                f"--macos-app-version={read_version()}",
            )
        )
    command.append(relative(ENTRY_SCRIPT))
    return command, env


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return create_build_parser("nuitka").parse_args(argv)


def _execute(args: argparse.Namespace) -> int:
    features = resolve_features(args.profile, args.features, args.exclude_features)
    if args.skip_validate and not args.dry_run:
        raise RuntimeError("--skip-validate cannot be used for a publishable build")
    ensure_build_python_environment(dry_run=args.dry_run)
    plan = create_plan(
        tool="nuitka",
        target=args.target,
        profile=args.profile,
        mode=args.mode,
        jobs=args.jobs,
        features=features,
        mpv_runtime=args.mpv_runtime,
        mpv_version=args.mpv_version,
        arch=args.arch,
        dry_run=args.dry_run,
    )
    print_plan(plan, extra=(("Staging", os.fspath(plan.build_output_dir)),))
    if not args.skip_install:
        print_section("Resolve Python dependencies")
        install_requirements(
            [
                *requirement_files(PROJECT_ROOT, plan.target, plan.features),
                PROJECT_ROOT / "build_tools" / "requirements" / "build-nuitka.txt",
            ],
            verbose=args.verbose_install,
            dry_run=args.dry_run,
            report_path=plan.generated_dir / "pip-install-report.json",
        )
    warnings = preflight(plan, dry_run=args.dry_run)
    for warning in warnings:
        print(f"  WARNING: {warning}")

    # Resolve UPX. ``--upx`` enables it (errors out if UPX is missing on a
    # supported target); ``--no-upx`` disables it; the default (None) auto-
    # enables UPX when a compatible binary is present on Windows/Linux and
    # silently skips on macOS. During dry-run, UPX is never actually invoked,
    # so we skip the resolution entirely to avoid false failures on hosts
    # that don't have UPX installed.
    upx_enabled_requested = args.upx if args.upx is not None else True
    upx_binary: str | None = None
    if upx_enabled_requested and upx_supported_for_target(plan.target) and not args.dry_run:
        try:
            upx_binary = resolve_upx_for_build(plan.target, enabled=True)
        except RuntimeError as exc:
            if args.upx is True:
                # User explicitly asked for --upx; surface the error.
                raise
            # Default auto-mode: UPX is optional, just warn.
            print(f"  WARNING: UPX not used: {exc}")
            upx_binary = None
    elif upx_enabled_requested and upx_supported_for_target(plan.target) and args.dry_run:
        # During dry-run, report what would happen but don't fail.
        from .upx import find_upx_binary
        found = find_upx_binary()
        if found:
            print(f"  INFO: UPX would use: {found}")
        else:
            print("  INFO: UPX not found locally; CI installs it automatically.")
    if upx_binary is not None:
        print_section("UPX compression enabled")
        print(f"  binary: {upx_binary}")

    print_section("Compile with Nuitka")
    command, env = build_args(
        plan,
        windows_console_mode=args.windows_console_mode,
        materialize_runtime=not args.dry_run,
        upx_binary=upx_binary,
    )

    def _validate() -> tuple[str, ...]:
        return (
            *validate_nuitka_output(plan),
            *validate_frozen_runtime(plan, nuitka_executable(plan)),
        )

    return run_build(
        command,
        target=plan.target,
        dry_run=args.dry_run,
        env_updates=env,
        prepare=lambda: prepare_staging_output(plan),
        validator=_validate,
        publisher=lambda: publish_staging_output(plan),
        cleanup_failed=lambda: discard_staging_output(plan),
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run:
        return _execute(args)
    with ExclusiveBuildLock():
        return _execute(args)
