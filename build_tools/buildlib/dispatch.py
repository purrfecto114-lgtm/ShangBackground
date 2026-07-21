from __future__ import annotations

import argparse

from .cli import BuildHelpFormatter, print_feature_list
from .constants import TOOLS
from .features import FEATURES


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False, formatter_class=BuildHelpFormatter)
    parser.add_argument("--tool", choices=TOOLS, default="pyinstaller")
    return parser


def main(argv: list[str] | None = None) -> int:
    values = list(argv or ())
    if "--list-features" in values:
        print_feature_list((item.key, item.label) for item in FEATURES)
        return 0
    known, remaining = _parser().parse_known_args(values)
    if known.tool == "nuitka":
        from .nuitka import main as backend
    else:
        from .pyinstaller import main as backend
    return int(backend(remaining))
