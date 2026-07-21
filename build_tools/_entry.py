"""Public entry-point helpers.

All command-line wrappers delegate here so there is only one parser and one
backend implementation for each freezer.
"""
from __future__ import annotations

from collections.abc import Sequence


def run_build(argv: Sequence[str] | None = None, *, forced_tool: str | None = None) -> int:
    from build_tools.buildlib.dispatch import main

    values = list(argv or ())
    if forced_tool is not None:
        values = ["--tool", forced_tool, *values]
    return int(main(values))


def run_gui() -> int:
    from build_tools.buildlib.gui import main

    return int(main())


def run_unified(argv: Sequence[str] | None = None) -> int:
    values = list(argv or ())
    if values and values[0] == "mpv":
        from build_tools.buildlib.mpv_runtime import main

        return int(main(values[1:]))
    if values and values[0] in {"self-test", "selftest"}:
        from build_tools.buildlib.selftest import main

        return int(main(values[1:]))
    if "--gui" in values:
        values.remove("--gui")
        if values:
            raise SystemExit("--gui cannot be combined with build options")
        return run_gui()
    return run_build(values)
