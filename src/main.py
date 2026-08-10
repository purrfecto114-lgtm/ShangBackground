# ShangBackground PySide6 thin entry point.
from __future__ import annotations

from importlib import import_module
import sys


def _dispatch_internal_mode() -> int | None:
    video_flag = "--internal-video-player"
    if video_flag in sys.argv:
        index = sys.argv.index(video_flag)
        try:
            video_path = sys.argv[index + 1]
        except IndexError:
            return 2
        from platform_adapters.video import run_player

        # Parse optional --muted and --volume flags from the remainder of argv.
        # Both flags are accepted on every platform for symmetry; on Windows and
        # Linux the internal-video-player mode is currently unused, but the
        # argument-parsing contract must stay identical across the three trees
        # to avoid CLI drift.
        rest = sys.argv[index + 2:]
        muted = "--muted" in rest
        volume = 100
        if "--volume" in rest:
            vol_idx = rest.index("--volume")
            try:
                volume = int(rest[vol_idx + 1])
            except (IndexError, ValueError):
                volume = 100
        run_player(video_path, muted=muted, volume=volume)
        return 0

    libmpv_flag = "--internal-libmpv-player"
    if libmpv_flag in sys.argv:
        import argparse

        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument(libmpv_flag, dest="video_path")
        parser.add_argument("--wid", required=True)
        parser.add_argument("--ipc-path", default="")
        parser.add_argument("--muted", action="store_true")
        parser.add_argument("--volume", type=int, default=100)
        args, _unknown = parser.parse_known_args(sys.argv[1:])
        from app.libmpv_runtime import run_libmpv_player

        run_libmpv_player(
            args.video_path,
            wid=args.wid,
            ipc_path=args.ipc_path,
            muted=args.muted,
            volume=args.volume,
        )
        return 0

    native_html_flag = "--internal-native-html-wallpaper-runner"
    if native_html_flag in sys.argv:
        sys.argv = [arg for arg in sys.argv if arg != native_html_flag]
        from platform_adapters.native_html_runner import main as run_native_html_main

        return int(run_native_html_main())


    return None


def main() -> int:
    # Desktop context-menu actions have an Explorer-facing latency budget.  Do
    # this before importing app.entry/core/PySide6: forward to the already
    # running message-only window when possible, otherwise detach a normal
    # cold-start child and let Explorer return immediately.
    from app.context_menu_fastpath import handle_context_menu_fastpath

    context_result = handle_context_menu_fastpath()
    if context_result is not None:
        return context_result

    # Keep version and diagnostics available even when Qt is not installed, and
    # avoid importing the large runtime engine for these headless operations.
    if "--version" in sys.argv:
        from app.version import APP_VERSION

        print(APP_VERSION)
        return 0
    if "--doctor" in sys.argv or "--doctor-json" in sys.argv:
        from app.diagnostics import main as diagnostics_main

        return diagnostics_main(json_output="--doctor-json" in sys.argv)
    verification_flag = "--build-verify-file"
    if verification_flag in sys.argv:
        index = sys.argv.index(verification_flag)
        try:
            report_path = sys.argv[index + 1]
        except IndexError:
            return 2
        from app.build_verification import write_build_verification

        return write_build_verification(report_path)

    internal_result = _dispatch_internal_mode()
    if internal_result is not None:
        return internal_result

    from app.entry import main as application_main

    return application_main()


# Backward-compatible lazy exports for tools/plugins that imported classes from
# the historical god-file main.py.  Keeping these lazy avoids loading PySide6
# until the attribute is actually requested.
_LEGACY_EXPORTS = {
    "PreviewCanvas": ("ui.preview_canvas", "PreviewCanvas"),
    "QtRootShim": ("ui.qt_root_shim", "QtRootShim"),
    "BingSyncWorker": ("services.bing_sync", "BingSyncWorker"),
    "ShangBackgroundWindow": ("ui.main_window", "ShangBackgroundWindow"),
}

__all__ = ["main", *_LEGACY_EXPORTS]


def __getattr__(name: str):
    try:
        module_name, attr_name = _LEGACY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


if __name__ == "__main__":
    raise SystemExit(main())
