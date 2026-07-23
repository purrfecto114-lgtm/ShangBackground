"""Windows ``setup.exe`` builder driven by Inno Setup.

This module is the single entry point for producing the user-facing Windows
installer. It deliberately stays out of the PyInstaller/Nuitka code path so
that:

- ``installer`` is a post-packaging step that consumes a *validated* native
  build, never a competing freezer;
- Linux/macOS hosts can dry-run the command (the .iss is generated and the
  Inno Setup compiler is located but not invoked);
- CI can call the same code path the developer runs locally.

Design notes
------------
- The Inno Setup script lives at ``packaging/windows/shangbackground.iss``
  with ``#ifndef``-guarded placeholder defaults. This keeps it openable in
  the Inno Setup IDE for visual inspection. The Python driver overrides the
  placeholders with ``ISCC.exe /D`` flags so we never rewrite the .iss.
- The license agreement is referenced as a stable relative path
  (``packaging/windows/license.rtf``) so the Inno Setup licence dialog is
  always shown after the welcome page. Acceptance is mandatory: the Next
  button stays disabled until the user explicitly accepts.
- The driver never silently downloads Inno Setup. On a non-Windows host we
  only emit the rendered command (``--dry-run``); on a Windows host we look
  for ``ISCC.exe`` in the well-known install paths and ``PATH``.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Iterable

from .cli import BuildHelpFormatter, print_banner, print_section
from .constants import (
    APP_NAME,
    COMPANY_NAME,
    PRODUCT_NAME,
    PROJECT_ROOT,
    ARCHES,
    PROFILES,
    normalize_arch,
    read_version,
    windows_numeric_version,
)
from .features import default_features, feature_summary
from .plan import BuildPlan, create_plan


INSTALLER_DIR = PROJECT_ROOT / "packaging" / "windows"
ISS_PATH = INSTALLER_DIR / "shangbackground.iss"
LICENSE_PATH = INSTALLER_DIR / "license.rtf"
INSTALLER_OUTPUT_DIR = PROJECT_ROOT / "dist-installer"


@dataclass(frozen=True, slots=True)
class InstallerPlan:
    """Inputs handed to Inno Setup Compiler (ISCC.exe)."""

    plan: BuildPlan
    source_root: Path
    output_dir: Path
    output_basename: str
    iss_path: Path
    license_path: Path

    @property
    def setup_executable(self) -> Path:
        return self.output_dir / f"{self.output_basename}.exe"

    @property
    def arch(self) -> str:
        return self.plan.arch


def _pyinstaller_publish_dir(plan: BuildPlan) -> Path:
    """Return the validated PyInstaller standalone output for ``plan``."""
    return plan.output_dir


def _validate_source_layout(source: Path) -> tuple[str, ...]:
    """Ensure the published PyInstaller output is ready for the installer.

    We deliberately do not call :func:`validate_pyinstaller_output` because
    that helper inspects ``plan.build_output_dir`` (the ephemeral staging
    directory). The installer consumes the *published* output under
    ``dist-pyinstaller/...`` instead, so we run a focused check here.
    """
    errors: list[str] = []
    if not source.is_dir():
        return (f"PyInstaller output is missing: {source}",)
    app_root = source / "ShangBackground"
    if not app_root.is_dir():
        return (f"ShangBackground/ bundle directory is missing under {source}",)
    executable = app_root / "ShangBackground.exe"
    if not executable.is_file():
        errors.append(f"ShangBackground.exe is missing: {executable}")
    internal = app_root / "_internal"
    if not internal.is_dir():
        errors.append(f"PyInstaller _internal directory is missing: {internal}")
    manifest = internal / "build-features.json"
    if not manifest.is_file():
        errors.append(f"build-features.json manifest is missing: {manifest}")
    return tuple(errors)


def create_installer_plan(
    *,
    target: str,
    profile: str,
    arch: str,
    features: Iterable[str],
    source: Path | None = None,
    output_dir: Path | None = None,
    dry_run: bool = False,
) -> InstallerPlan:
    """Resolve every path ISCC needs without invoking it.

    On a non-Windows host, or when ``dry_run`` is True, the function still
    succeeds: it just produces a plan + command preview rather than compiling.
    """
    if target != "windows":
        raise RuntimeError(
            f"Inno Setup installer is Windows-only; got target={target!r}. "
            "Use PyInstaller standalone archives for Linux and macOS."
        )
    if not ISS_PATH.is_file():
        raise RuntimeError(f"Inno Setup script is missing: {ISS_PATH}")
    if not LICENSE_PATH.is_file():
        raise RuntimeError(f"License agreement file is missing: {LICENSE_PATH}")

    arch = normalize_arch(arch)
    build_plan = create_plan(
        tool="pyinstaller",
        target=target,
        profile=profile,
        mode="standalone",
        jobs=2,
        features=features,
        mpv_runtime="system",
        mpv_version="auto",
        arch=arch,
        dry_run=True,  # planner is purely informational for the installer step
    )
    source_root = (source or _pyinstaller_publish_dir(build_plan)).resolve()
    out_dir = (output_dir or INSTALLER_OUTPUT_DIR / "windows" / build_plan.variant).resolve()
    version = read_version()
    tag = f"v{version}"
    output_basename = f"{APP_NAME}-{tag}-windows-{arch}-setup"
    return InstallerPlan(
        plan=build_plan,
        source_root=source_root,
        output_dir=out_dir,
        output_basename=output_basename,
        iss_path=ISS_PATH,
        license_path=LICENSE_PATH,
    )


def _find_iscc() -> str | None:
    """Locate ``ISCC.exe`` on a Windows host.

    Order:
    1. ``SHANGBACKGROUND_ISCC`` env var (explicit override).
    2. ``PATH`` lookup.
    3. Well-known install paths under ``%ProgramFiles%`` / ``%ProgramFiles(x86)%``.
    """
    if sys.platform != "win32" and os.name != "nt":
        return None
    override = os.environ.get("SHANGBACKGROUND_ISCC", "").strip()
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return os.fspath(candidate)
        raise RuntimeError(f"SHANGBACKGROUND_ISCC does not point to ISCC.exe: {override}")

    found = shutil.which("ISCC.exe") or shutil.which("ISCC")
    if found:
        return found

    candidate_roots = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
    ]
    for root in candidate_roots:
        if not root:
            continue
        for sub in ("Inno Setup 6", "Inno Setup 5"):
            candidate = Path(root) / sub / "ISCC.exe"
            if candidate.is_file():
                return os.fspath(candidate)
    return None


def render_iscc_command(plan: InstallerPlan) -> list[str]:
    """Render the full ``ISCC.exe`` command with ``/D`` placeholder overrides."""
    compiler = _find_iscc()
    binary = compiler or "ISCC.exe"
    version_pub = windows_numeric_version()
    return [
        binary,
        f"/DAPP_NAME={APP_NAME}",
        f"/DAPP_VERSION={read_version()}",
        f"/DAPP_VERSION_PUB={version_pub}",
        f"/DCOMPANY_NAME={COMPANY_NAME}",
        f"/DPRODUCT_NAME={PRODUCT_NAME}",
        f"/DARCH={plan.arch}",
        f"/DSOURCE_ROOT={os.fspath(plan.source_root)}",
        f"/DOUTPUT_DIR={os.fspath(plan.output_dir)}",
        f"/DOUTPUT_BASENAME={plan.output_basename}",
        f"/DPROJECT_ROOT={os.fspath(PROJECT_ROOT)}",
        "/Qp",  # Show progress but quiet success log
        os.fspath(plan.iss_path),
    ]


def _print_plan(plan: InstallerPlan) -> None:
    print_banner(
        "ShangBackground installer plan",
        f"windows · {plan.plan.profile} · {plan.arch}",
    )
    rows = [
        ("Source", os.fspath(plan.source_root)),
        ("Output", os.fspath(plan.setup_executable)),
        ("Installer script", os.fspath(plan.iss_path)),
        ("License", os.fspath(plan.license_path)),
        ("Features", feature_summary(plan.plan.features)),
        ("Variant", plan.plan.variant),
    ]
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"  {label:<{width}}  {value}")
    print()


def create_installer_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build.py installer",
        description="Build the Windows setup.exe from a validated PyInstaller standalone layout.",
        epilog=(
            "Examples:\n"
            "  python build_tools/build.py installer\n"
            "  python build_tools/build.py installer --profile lite --arch x86_64\n"
            "  python build_tools/build.py installer --dry-run\n"
        ),
        formatter_class=BuildHelpFormatter,
    )
    # The installer is Windows-only; we still accept the flag (so callers can
    # share a script with the PyInstaller/Nuitka entry points) but only
    # ``windows`` is a valid value. The argparse choices list makes misuse
    # fail-fast at parse time.
    parser.add_argument("--target", choices=("windows",), default="windows")
    parser.add_argument("--profile", choices=PROFILES, default="lite")
    parser.add_argument(
        "--arch",
        choices=("auto", *ARCHES),
        default="auto",
        help="Must match the PyInstaller build arch that produced --input.",
    )
    parser.add_argument(
        "--features",
        default=None,
        metavar="LIST",
        help="Optional: comma-separated feature keys. Defaults to the profile defaults.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Override the PyInstaller standalone root (default: dist-pyinstaller/windows/<variant>/standalone).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override the setup.exe output directory (default: dist-installer/windows/<variant>).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the plan and print the ISCC command without invoking the compiler.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip the source-layout pre-check; useful only when re-running on a known-good bundle.",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return create_installer_parser().parse_args(argv)


def _resolve_features(profile: str, raw: str | None) -> frozenset[str]:
    if raw is None:
        return default_features(profile)
    from .features import parse_feature_set

    explicit = parse_feature_set(raw)
    if explicit is None:
        return default_features(profile)
    return explicit


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    features = _resolve_features(args.profile, args.features)
    plan = create_installer_plan(
        target=args.target,
        profile=args.profile,
        arch=args.arch,
        features=features,
        source=args.input,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
    )
    _print_plan(plan)

    if not args.skip_validate and not args.dry_run:
        errors = _validate_source_layout(plan.source_root)
        if errors:
            print("Installer source layout is invalid:", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            return 1

    command = render_iscc_command(plan)
    print_section("Inno Setup command")
    print("  " + subprocess.list2cmdline(command) if os.name == "nt" else "  " + " ".join(command))

    if args.dry_run:
        print("\n  (dry-run: ISCC.exe not invoked)")
        return 0

    compiler = _find_iscc()
    if compiler is None:
        print(
            "\nISCC.exe was not found. Install Inno Setup 6 from https://jrsoftware.org/isdl.php "
            "or set SHANGBACKGROUND_ISCC to the absolute path of ISCC.exe.",
            file=sys.stderr,
        )
        return 1

    plan.output_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if completed.returncode != 0:
        return int(completed.returncode)
    if not plan.setup_executable.is_file():
        print(f"\nISCC reported success but {plan.setup_executable} was not produced.", file=sys.stderr)
        return 1
    print(f"\nInstaller ready: {plan.setup_executable}")
    return 0
