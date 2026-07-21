from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import shutil
import sys
from typing import TYPE_CHECKING, Iterable

from .constants import (
    MODES, MPV_MODES, PROFILES, PYINSTALLER_CONTENTS_DIRECTORY, TARGETS,
    WINDOWS_CONSOLE_MODES, host_target,
)
from .features import FEATURE_KEYS, feature_summary

if TYPE_CHECKING:
    from .plan import BuildPlan


class BuildHelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog: str) -> None:
        width = max(78, min(118, shutil.get_terminal_size((100, 24)).columns))
        super().__init__(prog, max_help_position=30, width=width)


def _contents_directory(value: str) -> str:
    name = str(value).strip()
    if not name or name == ".":
        raise argparse.ArgumentTypeError("flat PyInstaller onedir layout is unsupported; use _internal")
    if name in {"..", "/", "\\"} or "/" in name or "\\" in name:
        raise argparse.ArgumentTypeError("contents directory must be one relative directory name")
    return name


def create_build_parser(tool: str, *, include_contents_directory: bool = False) -> argparse.ArgumentParser:
    display = "Nuitka" if tool == "nuitka" else "PyInstaller"
    parser = argparse.ArgumentParser(
        prog=f"build.py --tool {tool}",
        description=f"Build ShangBackground with {display}.",
        epilog=(
            "Examples:\n"
            f"  python build_tools/build.py --tool {tool} --profile full --mode standalone\n"
            f"  python build_tools/build.py --tool {tool} --features video,html,bing --dry-run\n"
            "  python build_tools/build.py --gui"
        ),
        formatter_class=BuildHelpFormatter,
    )
    build = parser.add_argument_group("Build selection")
    build.add_argument("--target", choices=TARGETS, default=host_target())
    build.add_argument("--profile", choices=PROFILES, default="full")
    build.add_argument("--mode", choices=MODES, default="standalone")
    build.add_argument("--jobs", type=int, choices=(1, 2, 4), default=2)

    feature_group = parser.add_argument_group("Feature selection")
    feature_group.add_argument("--features", default=None, metavar="LIST", help=f"Comma-separated: {', '.join(FEATURE_KEYS)}; accepts all or none")
    feature_group.add_argument("--exclude-features", default=None, metavar="LIST")

    runtime = parser.add_argument_group("Native runtime")
    runtime.add_argument("--mpv-runtime", choices=MPV_MODES, default="auto")
    runtime.add_argument("--mpv-version", default="auto", metavar="VERSION")
    runtime.add_argument("--mpv-arch", default="auto", metavar="ARCH")

    platform_group = parser.add_argument_group("Platform packaging")
    platform_group.add_argument("--windows-console-mode", choices=WINDOWS_CONSOLE_MODES, default="disable")
    if include_contents_directory:
        platform_group.add_argument(
            "--contents-directory", type=_contents_directory, default=PYINSTALLER_CONTENTS_DIRECTORY,
            metavar="NAME", help="PyInstaller onedir support directory; _internal is the release layout",
        )

    execution = parser.add_argument_group("Execution")
    execution.add_argument("--skip-install", action="store_true")
    execution.add_argument("--verbose-install", action="store_true")
    execution.add_argument("--dry-run", action="store_true")
    execution.add_argument("--skip-validate", action="store_true", help="Skip post-build bundle structure validation")
    return parser


@dataclass(frozen=True, slots=True)
class ConsoleTheme:
    rule: str = "─"
    marker: str = "◆"


_THEME = ConsoleTheme()


def _supports_unicode() -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        _THEME.marker.encode(encoding)
        return True
    except (LookupError, UnicodeEncodeError):
        return False


def _glyph(value: str, fallback: str) -> str:
    return value if _supports_unicode() else fallback


def print_banner(title: str, subtitle: str = "") -> None:
    width = max(58, min(92, shutil.get_terminal_size((80, 24)).columns))
    print(_glyph(_THEME.rule, "-") * width)
    print(f"{_glyph(_THEME.marker, '>')} {title}")
    if subtitle:
        print(f"  {subtitle}")
    print(_glyph(_THEME.rule, "-") * width)


def print_plan(plan: "BuildPlan", *, extra: Iterable[tuple[str, str]] = ()) -> None:
    print_banner("ShangBackground build plan", f"{plan.tool} · {plan.target} · {plan.mode}")
    rows = [
        ("Profile", plan.profile),
        ("Features", feature_summary(plan.features)),
        ("MPV", plan.mpv.description),
        ("Variant", plan.variant),
        ("Output", os.fspath(plan.output_dir)),
        *extra,
    ]
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"  {label:<{width}}  {value}")
    print()


def print_section(title: str) -> None:
    print(f"\n{_glyph(_THEME.marker, '>')} {title}", flush=True)


def print_feature_list(items: Iterable[tuple[str, str]]) -> None:
    print_banner("Available build features")
    for key, label in items:
        print(f"  {key:<10} {label}")
