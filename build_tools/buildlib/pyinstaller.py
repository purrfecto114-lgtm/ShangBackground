from __future__ import annotations

import argparse
import os
from pathlib import Path

from .bundle import data_directories, dynamic_modules, excluded_modules
from .cli import create_build_parser, print_plan, print_section
from .constants import APP_NAME, ENTRY_SCRIPT, PROJECT_ROOT, ensure_build_python_environment, python_executable
from .diagnostics import preflight, pyinstaller_executable, validate_frozen_runtime, validate_pyinstaller_output
from .features import requirement_files, resolve_features
from .locking import ExclusiveBuildLock
from .plan import (
    BuildPlan,
    create_plan,
    discard_staging_output,
    prepare_staging_output,
    publish_staging_output,
)
from .runner import install_requirements, run_build


def _asset_arg(flag: str, source: Path, destination: str) -> list[str]:
    return [flag, f"{os.fspath(source)}{os.pathsep}{destination}"]


def build_args(plan: BuildPlan, *, windows_console_mode: str, contents_directory: str) -> list[str]:
    command = [
        python_executable(),
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--onefile" if plan.mode == "onefile" else "--onedir",
        "--name",
        APP_NAME,
        "--distpath",
        os.fspath(plan.build_output_dir),
        "--workpath",
        os.fspath(plan.generated_dir / "work"),
        "--specpath",
        os.fspath(plan.generated_dir / "spec"),
        "--paths",
        os.fspath(PROJECT_ROOT / "src"),
        "--additional-hooks-dir",
        os.fspath(PROJECT_ROOT / "build_tools" / "pyinstaller_hooks"),
    ]
    if plan.mode == "standalone":
        command.extend(("--contents-directory", contents_directory))
    for source, destination in data_directories(plan):
        command += _asset_arg("--add-data", source, destination)
    command += _asset_arg("--add-data", plan.manifest_path, ".")

    source = plan.mpv.payload_dir
    if plan.mpv.mode == "bundled" and source is not None:
        for item in sorted(source.rglob("*")):
            if not item.is_file():
                continue
            destination = (Path("bin/mpv") / item.relative_to(source).parent).as_posix()
            suffix = item.suffix.lower()
            binary = suffix in {".dll", ".so", ".dylib", ".exe"} or ".so." in item.name
            # Native MPV files must be siblings in the final ``bin/mpv``
            # directory so Windows DLL search and the post-build verifier see
            # the same layout as the source runtime.
            if binary:
                destination = "bin/mpv"
            command += _asset_arg("--add-binary" if binary else "--add-data", item, destination)

    for module in dynamic_modules(plan):
        command.extend(("--hidden-import", module))
    for module in excluded_modules(plan):
        command.extend(("--exclude-module", module))

    if plan.target == "windows":
        command.append("--console" if windows_console_mode == "force" else "--noconsole")
        icon = PROJECT_ROOT / "src" / "img" / "LOGO.ico"
        if icon.is_file():
            command.extend(("--icon", os.fspath(icon)))
        version_file = PROJECT_ROOT / "src" / "main_version_info.txt"
        if version_file.is_file():
            command.extend(("--version-file", os.fspath(version_file)))
    elif plan.target == "macos":
        command.extend(("--windowed", "--osx-bundle-identifier", "studio.xxdz.shangbackground"))
    command.append(os.fspath(ENTRY_SCRIPT))
    return command


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return create_build_parser("pyinstaller", include_contents_directory=True).parse_args(argv)


def _execute(args: argparse.Namespace) -> int:
    features = resolve_features(args.profile, args.features, args.exclude_features)
    if args.skip_validate and not args.dry_run:
        raise RuntimeError("--skip-validate cannot be used for a publishable build")
    ensure_build_python_environment(dry_run=args.dry_run)
    plan = create_plan(
        tool="pyinstaller",
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
    print_plan(plan, extra=(("Contents", args.contents_directory), ("Staging", os.fspath(plan.build_output_dir))))
    if not args.skip_install:
        print_section("Resolve Python dependencies")
        install_requirements(
            [
                *requirement_files(PROJECT_ROOT, plan.target, plan.features),
                PROJECT_ROOT / "build_tools" / "requirements" / "build-pyinstaller.txt",
            ],
            verbose=args.verbose_install,
            dry_run=args.dry_run,
            report_path=plan.generated_dir / "pip-install-report.json",
        )
    warnings = preflight(plan, dry_run=args.dry_run)
    for warning in warnings:
        print(f"  WARNING: {warning}")
    print_section("Package with PyInstaller")
    command = build_args(
        plan, windows_console_mode=args.windows_console_mode, contents_directory=args.contents_directory
    )

    def _validate() -> tuple[str, ...]:
        return (
            *validate_pyinstaller_output(plan, args.contents_directory),
            *validate_frozen_runtime(plan, pyinstaller_executable(plan)),
        )

    environment = {
        "PYINSTALLER_CONFIG_DIR": os.fspath(plan.generated_dir / "pyinstaller-cache"),
    }
    return run_build(
        command,
        target=plan.target,
        dry_run=args.dry_run,
        env_updates=environment,
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
