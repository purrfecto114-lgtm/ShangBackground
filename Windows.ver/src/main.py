# ShangBackground PySide6 thin entry point.
from __future__ import annotations

from importlib import import_module
import sys

from app.entry import main as _application_main


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

    html_flag = "--internal-html-wallpaper-runner"
    if html_flag in sys.argv:
        # The bundled EXE uses this internal flag to enter the HTML wallpaper
        # child runner.  Remove it before handing argv to that runner's parser.
        sys.argv = [arg for arg in sys.argv if arg != html_flag]
        from platform_adapters.run_html_wallpaper import main as run_html_wallpaper_main
        return int(run_html_wallpaper_main())

    return None


def main() -> int:
    internal_result = _dispatch_internal_mode()
    if internal_result is not None:
        return internal_result
    return _application_main()


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
