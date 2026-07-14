#!/usr/bin/env python3
"""Functional matrix for shared wallpaper, persistence, and adapter contracts."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TREES = ("Windows.ver", "Linux.ver(beta)", "MacOS.ver(alpha)")


def _child(tree: str) -> int:
    sys.path.insert(0, str(ROOT / tree / "src"))
    from PIL import Image
    from core import engine as core

    with tempfile.TemporaryDirectory() as temp:
        td = Path(temp)
        core.DATA_DIR = str(td / "data")
        core.CONFIG_PATH = str(td / "data/settings.json")
        core.BUNDLED_CONFIG_PATH = str(td / "missing.json")
        core.LEGACY_CONFIG_PATH = str(td / "missing-legacy.json")
        core.LEGACY_BUNDLED_CONFIG_PATH = str(td / "missing-legacy-bundled.json")
        Path(core.DATA_DIR).mkdir(parents=True, exist_ok=True)

        image_path = td / "wallpaper.png"
        Image.new("RGB", (32, 20), "#336699").save(image_path)
        progress: list[tuple[str, float]] = []
        calls: list[object] = []

        originals = {
            "configure_fit_mode": core.configure_fit_mode,
            "set_wallpaper_platform": core.set_wallpaper_platform,
            "refresh_shell_ui": core.refresh_shell_ui,
            "save_config": core.save_config,
            "is_video_wallpaper_running": core.is_video_wallpaper_running,
            "_queue_ui_preview_update": core._queue_ui_preview_update,
        }
        try:
            core.configure_fit_mode = lambda mode, *_a, **_k: calls.append(("fit", mode))
            core.set_wallpaper_platform = lambda path: calls.append(("set", Path(path).name))
            core.refresh_shell_ui = lambda: calls.append("refresh")
            core.save_config = lambda: calls.append("save") or True
            core.is_video_wallpaper_running = lambda: False
            core._queue_ui_preview_update = lambda path: calls.append(("preview", Path(path).name))
            core.config = core.get_default_config()
            core.config["mode"] = "图片"
            ok = core.set_wallpaper_direct(
                str(image_path),
                operation_name="functional-test",
                progress_cb=lambda status, value: progress.append((status, value)),
            )
        finally:
            for name, value in originals.items():
                setattr(core, name, value)
        assert ok is True
        assert core.config["current_wallpaper"] == str(image_path.resolve())
        assert calls[0] == ("fit", "填充")
        assert ("set", "wallpaper.png") in calls
        assert calls[-1] == ("preview", "wallpaper.png")
        assert progress[0][1] == 0.2 and progress[-1][1] == 1.0

        # Slideshow enumeration and history navigation should remain shared.
        folder = td / "slides"
        folder.mkdir()
        slides = []
        for idx in range(3):
            path = folder / f"{idx}.png"
            Image.new("RGB", (8, 8), (idx * 30, 20, 40)).save(path)
            slides.append(str(path))
        core.config = core.get_default_config()
        core.config.update({"slide_folder": str(folder), "current_wallpaper": slides[0], "shuffle": False})
        discovered = core.random_copy.get_original_image_paths(str(folder))
        assert {str(Path(path).resolve()) for path in discovered} == {str(Path(path).resolve()) for path in slides}
        core.slide_images = [str(Path(path).resolve()) for path in discovered]
        core._invalidate_slideshow_index_cache()
        nxt = core.get_next_wallpaper()
        assert nxt in slides and nxt != slides[0]
        core.config["history"] = slides[:2]
        original_set_wallpaper = core.set_wallpaper
        selected: list[str] = []
        try:
            core.set_wallpaper = lambda path, *_a, **_k: selected.append(path) or True
            core.previous_wallpaper()
        finally:
            core.set_wallpaper = original_set_wallpaper
        assert selected and selected[-1] in slides

        # Solid and gradient generation must produce valid images without NumPy.
        original_get_screen = core.get_screen_size
        try:
            core.get_screen_size = lambda _root=None: (64, 40)
            core.config = core.get_default_config()
            gradient = core.create_gradient_wallpaper_optimized("#000000", "#ffffff", 45)
            assert gradient and Path(gradient).is_file()
            with Image.open(gradient) as img:
                assert img.size == (64, 40)
        finally:
            core.get_screen_size = original_get_screen

        # HTML runtime option files are atomic and preserve multiple options.
        from platform_adapters import html_wallpaper
        html_wallpaper._OPTIONS_FILE = str(td / "html-options.json")
        assert html_wallpaper.runtime_set_option("auto_pause", False)
        assert html_wallpaper.runtime_set_option("mouse_through", True)
        options = html_wallpaper._read_options_all()
        assert options["auto_pause"] is False and options["mouse_through"] is True

        local_html = td / "index.html"
        local_html.write_text("<!doctype html><title>matrix</title>", encoding="utf-8")
        assert html_wallpaper.validate_html_path(str(local_html))
        assert html_wallpaper.validate_html_path(local_html.as_uri())
        assert html_wallpaper.validate_html_path("https://example.com/wallpaper")
        assert not html_wallpaper.validate_html_path("javascript:alert(1)")

        # Single-instance lock can be acquired, released, and reacquired.
        # The Windows source tree is executed on Linux in CI, so replace only
        # its msvcrt/Win32 primitives with deterministic successful adapters.
        from core import single_instance
        single_instance.release()
        restored = {}
        if tree == "Windows.ver" and os.name != "nt":
            for name in ("_try_windows_mutex", "_lock_file_region", "_unlock_file_region", "_try_loopback_lock"):
                restored[name] = getattr(single_instance, name)
            single_instance._try_windows_mutex = lambda: None
            single_instance._lock_file_region = lambda _fh: True
            single_instance._unlock_file_region = lambda _fh: None
            single_instance._try_loopback_lock = lambda: True
        try:
            assert single_instance.acquire() is True
            single_instance.release()
            assert single_instance.acquire() is True
            single_instance.release()
        finally:
            single_instance.release()
            for name, value in restored.items():
                setattr(single_instance, name, value)

    print(f"PASS functional matrix: {tree}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child")
    args = parser.parse_args()
    if args.child:
        return _child(args.child)

    with tempfile.TemporaryDirectory() as home:
        for tree in TREES:
            env = os.environ.copy()
            runtime = Path(home) / f"runtime-{tree.replace('/', '_')}"
            runtime.mkdir(parents=True, exist_ok=True)
            env.update(
                {
                    "HOME": home,
                    "USERPROFILE": home,
                    "LOCALAPPDATA": home,
                    "APPDATA": home,
                    "XDG_CONFIG_HOME": str(Path(home) / ".config"),
                    "XDG_DATA_HOME": str(Path(home) / ".local/share"),
                    "XDG_RUNTIME_DIR": str(runtime),
                    "QT_QPA_PLATFORM": "offscreen",
                }
            )
            subprocess.run([sys.executable, __file__, "--child", tree], check=True, env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
