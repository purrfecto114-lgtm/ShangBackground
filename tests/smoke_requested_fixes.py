#!/usr/bin/env python3
"""Regression tests for the user-reported checkbox, HTML pause, reset, and Unicode issues."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
TREES = ("Windows.ver", "Linux.ver(beta)", "MacOS.ver(alpha)")


def check_sources() -> None:
    visibility_sources = []
    for tree in TREES:
        base = ROOT / tree / "src"
        main = (base / "ui/main_window.py").read_text(encoding="utf-8")
        runtime = (base / "platform_adapters/run_html_wallpaper.py").read_text(encoding="utf-8")
        visibility = (base / "platform_adapters/desktop_visibility.py").read_text(encoding="utf-8")
        visibility_sources.append(visibility)
        assert "QTabWidget::pane, QCheckBox::indicator" not in main, tree
        assert "桌面被遮挡时自动暂停（节省 CPU/GPU）" in main, tree
        assert "core.configure_fit_mode(defaults.get(\"fit_mode\", \"填充\")" in main, tree
        assert "core.set_wallpaper_platform(previous_wallpaper)" in main, tree
        assert "from platform_adapters.desktop_visibility import desktop_is_visible" in runtime, tree
        assert "on_desktop = desktop_is_visible()" in runtime, tree
        assert "view.isActiveWindow() or view.hasFocus()" not in runtime[runtime.index("def _tick_visibility"):], tree
    assert len(set(visibility_sources)) == 1, "desktop visibility implementation drifted across platforms"

    win = (ROOT / "Windows.ver/src/platform_adapters/integration.py").read_text(encoding="utf-8")
    for forbidden in ("_ascii_wallpaper_bridge", "_needs_ascii_wallpaper_bridge", "wallpaper_bridge"):
        assert forbidden not in win
    assert "_set_windows_wallpaper_legacy(original)" in win


def check_coverage_math() -> None:
    sys.path.insert(0, str(ROOT / "Linux.ver(beta)/src"))
    try:
        from platform_adapters.desktop_visibility import (
            Rect,
            coverage_ratios,
            desktop_visible_from_rects,
        )
        screen = Rect(0, 0, 1920, 1080)
        assert desktop_visible_from_rects([screen], []) is True
        assert desktop_visible_from_rects([screen], [Rect(0, 0, 1920, 1080)]) is False
        assert desktop_visible_from_rects([screen], [Rect(0, 0, 900, 1080)]) is True
        assert desktop_visible_from_rects(
            [screen], [Rect(0, 0, 960, 1080), Rect(960, 0, 960, 1080)]
        ) is False
        screens = [screen, Rect(1920, 0, 1920, 1080)]
        assert desktop_visible_from_rects(screens, [Rect(0, 0, 1920, 1080)]) is True
        ratios = coverage_ratios([screen], [Rect(0, 0, 1824, 1080)])
        assert ratios and 0.90 <= ratios[0] <= 0.98
        assert desktop_visible_from_rects([], []) is None
    finally:
        sys.path.remove(str(ROOT / "Linux.ver(beta)/src"))
        for name in list(sys.modules):
            if name == "platform_adapters" or name.startswith("platform_adapters."):
                sys.modules.pop(name, None)


def child(tree: str) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    src = ROOT / tree / "src"
    sys.path.insert(0, str(src))
    from PIL import Image
    from PySide6.QtCore import QPoint, QSize, Qt
    from PySide6.QtGui import QColor, QImage, QImageReader, QPainter
    from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionButton
    from core import engine as core
    from ui import main_window as mw

    app = QApplication.instance() or QApplication([])
    core.config = core.get_default_config()
    core.config["theme_color"] = "#949da7"
    core.config["dark_mode"] = False
    window = mw.ShangBackgroundWindow()
    for timer_name in ("_preview_refresh_timer", "_video_focus_timer"):
        timer = getattr(window, timer_name, None)
        if timer is not None:
            timer.stop()
    app.processEvents()

    # Render the real HTML auto-pause checkbox under ShangBackground's own
    # stylesheet. The old broad background-clip rule exposed the parent surface
    # as a thick white inner ring inside every checked indicator.
    checkbox = window.html_auto_pause_check
    checkbox.setChecked(True)
    checkbox.ensurePolished()
    if checkbox.width() < 120 or checkbox.height() < 24:
        checkbox.resize(checkbox.sizeHint().expandedTo(QSize(420, 36)))
    app.processEvents()
    image = QImage(checkbox.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#f6f8fa"))
    painter = QPainter(image)
    checkbox.render(painter, QPoint())
    painter.end()
    option = QStyleOptionButton()
    option.initFrom(checkbox)
    option.state |= QStyle.StateFlag.State_On
    indicator = checkbox.style().subElementRect(
        QStyle.SubElement.SE_CheckBoxIndicator, option, checkbox
    )
    inset = 2
    samples = (
        (indicator.center().x(), indicator.top() + inset),
        (indicator.center().x(), indicator.bottom() - inset),
        (indicator.left() + inset, indicator.center().y()),
        (indicator.right() - inset, indicator.center().y()),
    )
    sampled = [image.pixelColor(x, y) for x, y in samples]
    assert all(not (c.red() > 238 and c.green() > 238 and c.blue() > 238) for c in sampled), (
        tree,
        indicator.getRect(),
        [(c.red(), c.green(), c.blue()) for c in sampled],
    )
    accent_pixels = 0
    for y in range(indicator.top(), indicator.bottom() + 1):
        for x in range(indicator.left(), indicator.right() + 1):
            c = image.pixelColor(x, y)
            if 120 <= c.red() <= 180 and 120 <= c.green() <= 180 and 120 <= c.blue() <= 190:
                accent_pixels += 1
    assert accent_pixels > 80, (tree, accent_pixels)

    # Factory reset must apply the native default fit mode and re-apply the
    # currently displayed image with that mode, while leaving the new config
    # and history genuinely empty.
    calls: list[str] = []
    native_calls: list[str] = []
    originals = {
        "question": mw.QMessageBox.question,
        "show_info": mw.show_info,
        "save_config": core.save_config,
        "configure_fit_mode": core.configure_fit_mode,
        "set_wallpaper_platform": core.set_wallpaper_platform,
        "refresh_shell_ui": core.refresh_shell_ui,
        "get_current_wallpaper": core.get_current_wallpaper,
        "request_cancel_operations": core.request_cancel_operations,
        "stop_slideshow": core.stop_slideshow,
        "stop_video_wallpaper": core.stop_video_wallpaper,
    }
    try:
        with tempfile.TemporaryDirectory() as reset_td:
            current = Path(reset_td) / "恢复比例测试.png"
            Image.new("RGB", (64, 36), "#526f8f").save(current)
            mw.QMessageBox.question = lambda *_a, **_k: mw.QMessageBox.StandardButton.Yes
            mw.show_info = lambda *_a, **_k: None
            core.save_config = lambda: True
            core.configure_fit_mode = lambda mode, *_a, **_k: calls.append(str(mode))
            core.set_wallpaper_platform = lambda path: native_calls.append(str(path))
            core.refresh_shell_ui = lambda: None
            core.get_current_wallpaper = lambda: ""
            core.request_cancel_operations = lambda *_a, **_k: None
            core.stop_slideshow = lambda: None
            core.stop_video_wallpaper = lambda: True
            core.config["fit_mode"] = "居中"
            core.config["current_wallpaper"] = str(current)
            core.config["history"] = [{"path": str(current)}]
            window.restore_factory_settings()
            assert core.config["fit_mode"] == "填充"
            assert core.config["current_wallpaper"] == ""
            assert core.config["history"] == []
            assert calls == ["填充"], (tree, calls)
            assert native_calls == [str(current)], (tree, native_calls)
            assert window.fit_combo.currentData() == "填充"
    finally:
        mw.QMessageBox.question = originals["question"]
        mw.show_info = originals["show_info"]
        for key in (
            "save_config", "configure_fit_mode", "set_wallpaper_platform", "refresh_shell_ui",
            "get_current_wallpaper", "request_cancel_operations", "stop_slideshow", "stop_video_wallpaper",
        ):
            setattr(core, key, originals[key])

    # Unicode filenames must stay on the direct native path and perform like
    # ASCII filenames in application-side processing.
    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        ascii_path = folder / "wallpaper.png"
        cjk_path = folder / "中文壁纸名称.png"
        Image.new("RGB", (640, 360), "#527aa5").save(ascii_path)
        cjk_path.write_bytes(ascii_path.read_bytes())
        for path in (ascii_path, cjk_path):
            reader = QImageReader(str(path))
            reader.setScaledSize(QSize(160, 90))
            assert not reader.read().isNull(), (tree, path)

        original_values = {
            "configure_fit_mode": core.configure_fit_mode,
            "set_wallpaper_platform": core.set_wallpaper_platform,
            "refresh_shell_ui": core.refresh_shell_ui,
            "save_config": core.save_config,
            "is_video_wallpaper_running": core.is_video_wallpaper_running,
            "_queue_ui_preview_update": core._queue_ui_preview_update,
            "log": core.log,
        }
        try:
            core.configure_fit_mode = lambda *_a, **_k: None
            core.set_wallpaper_platform = lambda *_a, **_k: None
            core.refresh_shell_ui = lambda: None
            core.save_config = lambda: True
            core.is_video_wallpaper_running = lambda: False
            core._queue_ui_preview_update = lambda *_a, **_k: None
            core.log = lambda *_a, **_k: None

            def bench(path: Path) -> float:
                samples = []
                core.config = core.get_default_config()
                core.config["mode"] = "图片"
                for _ in range(120):
                    start = time.perf_counter()
                    assert core.set_wallpaper_direct(str(path)) is True
                    samples.append((time.perf_counter() - start) * 1000)
                return statistics.median(samples)

            ascii_ms = bench(ascii_path)
            cjk_ms = bench(cjk_path)
            assert cjk_ms <= max(ascii_ms * 3.0, ascii_ms + 0.50), (tree, ascii_ms, cjk_ms)
        finally:
            for key, value in original_values.items():
                setattr(core, key, value)

        if tree == "Windows.ver":
            from platform_adapters import integration

            recorded: list[tuple] = []

            class _User32:
                def SystemParametersInfoW(self, *args):
                    recorded.append(tuple(args))
                    return 1

            class _Kernel32:
                @staticmethod
                def SetLastError(_value):
                    return None

                @staticmethod
                def GetLastError():
                    return 0

            class _Windll:
                user32 = _User32()
                kernel32 = _Kernel32()

            original_com = integration._set_wallpaper_via_com
            had_windll = hasattr(integration.ctypes, "windll")
            original_windll = getattr(integration.ctypes, "windll", None)
            try:
                integration._set_wallpaper_via_com = lambda _path: False
                integration.ctypes.windll = _Windll()
                integration._set_windows_wallpaper(str(cjk_path))
            finally:
                integration._set_wallpaper_via_com = original_com
                if had_windll:
                    integration.ctypes.windll = original_windll
                else:
                    delattr(integration.ctypes, "windll")
            assert recorded and recorded[-1][2] == str(cjk_path.resolve()), (tree, recorded)

        if tree == "Linux.ver(beta)":
            from platform_adapters import integration
            uri = integration._file_uri(str(cjk_path))
            assert "%" in uri and integration._path_from_uri(uri) == str(cjk_path.resolve())

    window.close()
    app.processEvents()
    print(f"PASS requested fixes: {tree}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child")
    args = parser.parse_args()
    if args.child:
        return child(args.child)
    check_sources()
    check_coverage_math()
    with tempfile.TemporaryDirectory() as home:
        for tree in TREES:
            runtime = Path(home) / f"runtime-{tree.replace('/', '_')}"
            runtime.mkdir(parents=True, exist_ok=True)
            runtime.chmod(0o700)
            env = os.environ.copy()
            env.update({
                "HOME": home,
                "USERPROFILE": home,
                "LOCALAPPDATA": home,
                "APPDATA": home,
                "XDG_CONFIG_HOME": str(Path(home) / ".config"),
                "XDG_DATA_HOME": str(Path(home) / ".local/share"),
                "XDG_RUNTIME_DIR": str(runtime),
                "QT_QPA_PLATFORM": "offscreen",
            })
            subprocess.run([sys.executable, __file__, "--child", tree], env=env, check=True)
    print("PASS all requested-fix regressions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
