"""Public entry-point helpers.

All command-line wrappers delegate here so there is only one parser and one
backend implementation for each freezer.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
import os
import subprocess
import sys


def _guard_cli(action: Callable[[], int]) -> int:
    """Render expected build failures without burying the actionable cause.

    Set ``SHANGBACKGROUND_BUILD_TRACEBACK=1`` when a full traceback is useful
    for build-tool development.
    """
    try:
        return int(action())
    except KeyboardInterrupt:
        sys.stdout.flush()
        print("ERROR: build interrupted by user", file=sys.stderr)
        return 130
    except subprocess.CalledProcessError as exc:
        sys.stdout.flush()
        if os.environ.get("SHANGBACKGROUND_BUILD_TRACEBACK") == "1":
            raise
        print(f"ERROR: build command exited with status {exc.returncode}", file=sys.stderr)
        return int(exc.returncode or 1)
    except (RuntimeError, ValueError, OSError) as exc:
        sys.stdout.flush()
        if os.environ.get("SHANGBACKGROUND_BUILD_TRACEBACK") == "1":
            raise
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def run_build(argv: Sequence[str] | None = None, *, forced_tool: str | None = None) -> int:
    from build_tools.buildlib.dispatch import main

    values = list(argv or ())
    if forced_tool is not None:
        values = ["--tool", forced_tool, *values]
    return _guard_cli(lambda: int(main(values)))


def run_gui() -> int:
    from build_tools.buildlib.gui import main

    return int(main())


def run_unified(argv: Sequence[str] | None = None) -> int:
    values = list(argv or ())
    if values and values[0] == "mpv":
        from build_tools.buildlib.mpv_runtime import main

        return _guard_cli(lambda: int(main(values[1:])))
    if values and values[0] == "installer":
        from build_tools.buildlib.installer import main

        return _guard_cli(lambda: int(main(values[1:])))
    if values and values[0] in {"self-test", "selftest"}:
        from build_tools.buildlib.selftest import main

        return _guard_cli(lambda: int(main(values[1:])))
    if "--gui" in values:
        values.remove("--gui")
        if values:
            raise SystemExit("--gui cannot be combined with build options")
        return run_gui()
    return run_build(values)
