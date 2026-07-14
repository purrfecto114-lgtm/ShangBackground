#!/usr/bin/env python3
"""Dynamic Linux/X11 UI workflow smoke test.

This test runs the real PySide6 main window under Xvfb/Openbox, drives the
visible controls, and exercises the UI-to-core orchestration for every shared
wallpaper mode. Native wallpaper/video backends are replaced at their narrow
boundary because the CI desktop intentionally has no wallpaper daemon, mpv,
or system tray host. Real backend contracts and real QtWebEngine lifecycle are
covered by the other smoke suites.

KDE-specific implementation is deliberately out of scope for this test. It
only verifies that the generic Linux UI does not expose or require KDE-only
services in a non-KDE X11 session.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "Linux.ver(beta)" / "src"


def _terminate(proc: subprocess.Popen[Any] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()
        proc.wait(timeout=3)


def _wait_for(predicate: Callable[[], bool], timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError(f"timeout waiting for {label}")


def _parent() -> int:
    required = ("Xvfb", "openbox", "xdpyinfo")
    missing = [name for name in required if not shutil.which(name)]
    if missing:
        print("SKIP dynamic Linux UI test; missing: " + ", ".join(missing))
        return 0

    display = ":97"
    with tempfile.TemporaryDirectory(prefix="shang-dynamic-ui-") as td:
        home = Path(td)
        runtime = home / "runtime"
        runtime.mkdir(mode=0o700)
        env = os.environ.copy()
        env.update(
            {
                "DISPLAY": display,
                "XDG_SESSION_TYPE": "x11",
                "XDG_CURRENT_DESKTOP": "Openbox",
                "HOME": str(home),
                "USERPROFILE": str(home),
                "LOCALAPPDATA": str(home),
                "APPDATA": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_DATA_HOME": str(home / ".local/share"),
                "XDG_RUNTIME_DIR": str(runtime),
                "QT_QPA_PLATFORM": "xcb",
                "QTWEBENGINE_DISABLE_SANDBOX": "1",
                "QTWEBENGINE_CHROMIUM_FLAGS": "--no-sandbox --disable-gpu",
                "PYTHONPATH": str(SRC),
            }
        )
        stub = os.environ.get("SHANG_XCB_STUB_DIR", "")
        if stub:
            env["LD_LIBRARY_PATH"] = stub + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")

        xvfb = openbox = None
        try:
            xvfb = subprocess.Popen(
                ["Xvfb", display, "-screen", "0", "1440x900x24", "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            _wait_for(
                lambda: subprocess.run(
                    ["xdpyinfo", "-display", display],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                ).returncode
                == 0,
                8,
                "Xvfb",
            )
            openbox = subprocess.Popen(
                ["openbox"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
            )
            time.sleep(1.2)
            proc = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--child"],
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=150,
            )
            if proc.stdout:
                print(proc.stdout, end="")
            if proc.stderr:
                print(proc.stderr, file=sys.stderr, end="")
            if proc.returncode != 0:
                raise AssertionError(f"dynamic UI child failed with code {proc.returncode}")
            return 0
        finally:
            _terminate(openbox)
            _terminate(xvfb)


def _child() -> int:
    sys.path.insert(0, str(SRC))

    from PIL import Image
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QColorDialog

    from core import engine as core
    import ui.main_window as mw

    home = Path.home()
    assets = home / "dynamic-assets"
    slide_dir = assets / "幻灯片"
    slide_dir.mkdir(parents=True, exist_ok=True)
    img1 = slide_dir / "中文壁纸一.png"
    img2 = slide_dir / "wallpaper_two.jpg"
    Image.new("RGB", (640, 360), (50, 100, 160)).save(img1)
    Image.new("RGB", (640, 360), (160, 80, 50)).save(img2)
    video = assets / "中文视频.mp4"
    video.write_bytes(b"dynamic-test-video-placeholder")
    html = assets / "中文动态壁纸.html"
    html.write_text("<!doctype html><meta charset='utf-8'><title>动态测试</title><h1>OK</h1>", encoding="utf-8")

    events: list[dict[str, Any]] = []
    state = {"video": False, "html": False, "html_last": str(html)}

    def record(name: str, result: Any = True, state_update: Callable[[], None] | None = None):
        def _fn(*args: Any, **kwargs: Any) -> Any:
            events.append({"name": name, "args": [str(a) for a in args], "kwargs": {k: str(v) for k, v in kwargs.items()}})
            if state_update is not None:
                state_update()
            return result

        _fn.__name__ = name
        return _fn

    def start_video(path: str | None = None) -> bool:
        events.append({"name": "start_video_wallpaper", "args": [str(path or "")], "kwargs": {}})
        state["video"] = True
        return True

    def stop_video() -> bool:
        events.append({"name": "stop_video_wallpaper", "args": [], "kwargs": {}})
        state["video"] = False
        return True

    def start_html(path: str | None = None) -> bool:
        events.append({"name": "start_html_wallpaper", "args": [str(path or "")], "kwargs": {}})
        state["html"] = True
        state["html_last"] = str(path or state["html_last"])
        return True

    def stop_html() -> bool:
        events.append({"name": "stop_html_wallpaper", "args": [], "kwargs": {}})
        state["html"] = False
        return True

    def restart_html(path: str | None = None) -> bool:
        events.append({"name": "restart_html_wallpaper", "args": [str(path or "")], "kwargs": {}})
        state["html"] = True
        state["html_last"] = str(path or state["html_last"])
        return True

    # Keep real configuration persistence and gradient generation, but replace
    # only native/long-running boundaries unavailable in this Openbox session.
    core.stop_slideshow = record("stop_slideshow")
    core.start_slideshow = record("start_slideshow")
    core.restart_slideshow = record("restart_slideshow")
    core.previous_wallpaper = record("previous_wallpaper")
    core.next_wallpaper = record("next_wallpaper")
    core.random_wallpaper = record("random_wallpaper")
    core.set_fit_mode = record("set_fit_mode")
    core.set_wallpaper = record("set_wallpaper")
    core.set_wallpaper_direct = record("set_wallpaper_direct")
    core.apply_solid = record("apply_solid")
    core.start_video_wallpaper = start_video
    core.stop_video_wallpaper = stop_video
    core.is_video_wallpaper_running = lambda: bool(state["video"])
    core.set_video_volume = record("set_video_volume")
    core.set_video_paused = record("set_video_paused")
    core.start_html_wallpaper = start_html
    core.stop_html_wallpaper = stop_html
    core.restart_html_wallpaper = restart_html
    core.is_html_wallpaper_running = lambda: bool(state["html"])
    core.html_wallpaper_get_last_path = lambda: str(state["html_last"])
    core.html_wallpaper_runtime_set_option = record("html_wallpaper_runtime_set_option")
    core.refresh_global_hotkeys = record("refresh_global_hotkeys")
    core.stop_global_hotkeys = record("stop_global_hotkeys")
    core.configure_fit_mode = record("configure_fit_mode")
    core.set_wallpaper_platform = record("set_wallpaper_platform")
    core.refresh_shell_ui = record("refresh_shell_ui")
    core.get_current_wallpaper = lambda *args, **kwargs: str(img1)
    core.report_usage = record("report_usage")

    # Suppress modal UI during automation while preserving the code paths.
    QMessageBox.question = staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    QMessageBox.information = staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    QMessageBox.warning = staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    QMessageBox.critical = staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    mw.show_info = lambda *args, **kwargs: None
    mw.show_warning = lambda *args, **kwargs: None
    mw.ShangBackgroundWindow._deferred_gui_startup = lambda self: self.set_status("dynamic test ready")

    app = QApplication.instance() or QApplication(["shang-dynamic-ui"])
    app.setQuitOnLastWindowClosed(False)

    defaults = core.get_default_config()
    core.config.clear()
    core.config.update(defaults)
    core.config.update(
        {
            "tray_icon": False,
            "run_in_background": False,
            "auto_start": False,
            "silent_update_check_on_startup": False,
            "bing_auto_update_on_start": False,
            "bing_auto_delete_on_start": False,
            "slide_folder": str(slide_dir),
            "single_image": str(img1),
            "video_file": str(video),
            "html_file": str(html),
            "current_wallpaper": str(img1),
            "history": [str(img1), str(img2)],
            "favorites": [str(img1)],
            "video_muted": False,
            "video_volume": 72,
        }
    )
    core.save_config()

    window = mw.ShangBackgroundWindow()
    window.resize(1280, 820)
    window.show()

    def pump(ms: int = 80) -> None:
        deadline = time.monotonic() + ms / 1000.0
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        app.processEvents()

    def wait_idle(timeout: float = 8.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if not getattr(window, "_core_busy", False):
                thread = getattr(window, "_core_worker_thread", None)
                if thread is None or not thread.is_alive():
                    pump(50)
                    return
            time.sleep(0.01)
        raise AssertionError("UI core worker did not become idle")

    def click(widget: Any) -> None:
        assert widget is not None and widget.isEnabled(), f"widget not clickable: {widget!r}"
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        pump(100)

    pump(500)
    assert window.isVisible()
    assert window.mode_combo.count() == 6
    assert window.fit_combo.count() == 5
    assert window.tabs.count() >= 3

    # Visit every top-level tab and every wallpaper mode using the real visible widgets.
    for idx in range(window.tabs.count()):
        window.tabs.setCurrentIndex(idx)
        pump(80)
        assert window.tabs.currentIndex() == idx

    for mode in ("幻灯片放映", "图片", "视频", "纯色", "渐变", "HTML"):
        assert window.switch_to_mode(mode)
        wait_idle()
        assert core.config["mode"] == mode

    # Fit modes: ensure the combobox signal reaches persistence/core orchestration.
    for idx in range(window.fit_combo.count()):
        window.fit_combo.setCurrentIndex(idx)
        wait_idle()
        assert core.config["fit_mode"] == window.fit_combo.currentData()

    # Slideshow controls and navigation buttons.
    window.switch_to_mode("幻灯片放映")
    wait_idle()
    click(window.btn_prev)
    wait_idle()
    click(window.btn_next)
    wait_idle()
    click(window.btn_random)
    wait_idle()
    click(window.btn_start)
    wait_idle()
    click(window.btn_stop)
    wait_idle()
    window.seconds_spin.setValue(9)
    wait_idle()
    window.shuffle_check.setChecked(not window.shuffle_check.isChecked())
    wait_idle()

    # File chooser-driven controls use real button signals with deterministic paths.
    QFileDialog.getExistingDirectory = staticmethod(lambda *args, **kwargs: str(slide_dir))
    click(window.btn_browse_folder)
    wait_idle()
    assert core.config["slide_folder"] == str(slide_dir)

    window.switch_to_mode("图片")
    wait_idle()
    QFileDialog.getOpenFileName = staticmethod(lambda *args, **kwargs: (str(img2), ""))
    click(window.btn_single)
    wait_idle()
    assert core.config["single_image"] == str(img2)

    window.switch_to_mode("视频")
    wait_idle()
    QFileDialog.getOpenFileName = staticmethod(lambda *args, **kwargs: (str(video), ""))
    click(window.video_browse_btn)
    wait_idle()
    assert state["video"]
    click(window.video_stop_btn)
    wait_idle()
    window.video_edit.setText(str(video))
    click(window.video_start_btn)
    wait_idle()
    assert state["video"]
    window.video_muted_check.setChecked(True)
    pump(150)
    window.video_muted_check.setChecked(False)
    pump(150)
    window.video_volume_slider.setValue(37)
    pump(300)
    assert core.config["video_volume"] == 37
    for policy in ("none", "pause", "duck"):
        idx = window.video_focus_behavior_combo.findData(policy)
        assert idx >= 0
        window.video_focus_behavior_combo.setCurrentIndex(idx)
        pump(80)
        assert core.config["video_focus_behavior"] == policy

    window.switch_to_mode("HTML")
    wait_idle()
    QFileDialog.getOpenFileName = staticmethod(lambda *args, **kwargs: (str(html), ""))
    click(window.html_browse_btn)
    wait_idle()
    assert state["html"] and core.config["html_file"] == str(html)
    window.html_auto_pause_check.setChecked(not window.html_auto_pause_check.isChecked())
    pump(100)
    window.html_mouse_through_check.setChecked(not window.html_mouse_through_check.isChecked())
    pump(100)
    window.html_gpu_check.setChecked(not window.html_gpu_check.isChecked())
    wait_idle()
    click(window.html_restart_btn)
    wait_idle()
    click(window.html_stop_btn)
    wait_idle()
    assert not state["html"]

    # Color controls: use deterministic QColor results and execute generated-gradient flow.
    color_values = iter((QColor("#224466"), QColor("#335577"), QColor("#7799bb")))
    QColorDialog.getColor = staticmethod(lambda *args, **kwargs: next(color_values))
    window.switch_to_mode("纯色")
    wait_idle()
    click(window.solid_btn)
    wait_idle()
    assert core.config["solid_color"] == "#224466"
    window.switch_to_mode("渐变")
    wait_idle()
    click(window.grad1_btn)
    wait_idle()
    click(window.grad2_btn)
    wait_idle()
    window.angle_spin.setValue(123)
    click(window.angle_apply_btn)
    wait_idle()
    assert core.config["gradient_angle"] == 123
    assert any(e["name"] == "set_wallpaper_direct" for e in events)

    # History/favorites and settings dialog navigation.
    window.refresh_history_list()
    window._refresh_favorites_list()
    pump(100)
    assert window.history_list.count() >= 1
    assert window.favorites_list.count() >= 1
    window._toggle_favorite_current()
    pump(80)

    window.open_global_settings_from_home()
    pump(400)
    assert window._settings_dialog is not None and window._settings_dialog.isVisible()
    nav = getattr(window, "_settings_nav", None)
    assert nav is not None and nav.count() >= 4
    for idx in range(nav.count()):
        nav.setCurrentRow(idx)
        pump(50)
        assert nav.currentRow() == idx

    # Real XDG autostart writes/deletes in the isolated temporary HOME.
    window.set_auto_start(True)
    autostart = home / ".config" / "autostart" / "shangbackground.desktop"
    assert autostart.is_file()
    window.set_auto_start(False)
    assert not autostart.exists()

    # Hotkey toggle uses mocked native registration boundary but real persistence/UI.
    window.global_hotkeys_enabled_check.setChecked(True)
    pump(100)
    assert core.config["global_hotkeys_enabled"] is True
    window.global_hotkeys_enabled_check.setChecked(False)
    pump(100)
    assert core.config["global_hotkeys_enabled"] is False

    # Factory reset executes the real GUI path and must clear ratio/history state.
    core.config["fit_mode"] = "平铺"
    core.config["history"] = [str(img1), str(img2)]
    core.config["current_wallpaper"] = str(img1)
    core.save_config()
    window.restore_factory_settings()
    pump(500)
    assert core.config["fit_mode"] == "填充"
    assert core.config["history"] == []
    assert core.config["current_wallpaper"] == ""
    assert window.fit_combo.currentData() == "填充"
    assert any(e["name"] == "configure_fit_mode" for e in events)
    assert any(e["name"] == "set_wallpaper_platform" for e in events)

    # Verify configuration was actually serialized and remains parseable.
    config_path = Path(core.CONFIG_PATH)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["fit_mode"] == "填充"
    assert payload["history"] == []

    window._closing_for_exit = True
    window._perform_exit_cleanup_once(restore_wallpaper=False)
    window.close()
    if window._settings_dialog is not None:
        window._settings_dialog.close()
    pump(250)

    required_events = {
        "previous_wallpaper",
        "next_wallpaper",
        "random_wallpaper",
        "start_slideshow",
        "stop_slideshow",
        "set_wallpaper",
        "start_video_wallpaper",
        "stop_video_wallpaper",
        "start_html_wallpaper",
        "restart_html_wallpaper",
        "stop_html_wallpaper",
        "apply_solid",
        "set_wallpaper_direct",
        "set_fit_mode",
        "refresh_global_hotkeys",
        "stop_global_hotkeys",
    }
    observed = {e["name"] for e in events}
    missing = sorted(required_events - observed)
    assert not missing, f"missing dynamic paths: {missing}"

    result = {
        "status": "PASS",
        "platform": "Linux X11/Openbox",
        "modes": [window.mode_combo.itemData(i) for i in range(window.mode_combo.count())],
        "fit_modes": [window.fit_combo.itemData(i) for i in range(window.fit_combo.count())],
        "tabs_visited": window.tabs.count(),
        "settings_pages_visited": nav.count(),
        "event_count": len(events),
        "observed_events": sorted(observed),
        "kde_full_adaptation": "deferred_by_user_instruction",
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    print("PASS dynamic Linux UI workflows")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", action="store_true")
    args = parser.parse_args()
    return _child() if args.child else _parent()


if __name__ == "__main__":
    raise SystemExit(main())
