#!/usr/bin/env python3
"""Cross-platform behavior parity checks using Windows as the product baseline.

Runs each source tree in an isolated subprocess so fixed platform constants and
module globals cannot leak between imports.  Native OS calls are not executed;
backend registration is exercised with deterministic fakes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TREES = ("Windows.ver", "Linux.ver(beta)", "MacOS.ver(alpha)")


def _child(tree: str) -> int:
    src = ROOT / tree / "src"
    sys.path.insert(0, str(src))

    from core import engine as core

    expected_flags = {
        "Windows.ver": (True, False, False, "windows"),
        "Linux.ver(beta)": (False, False, True, "linux"),
        "MacOS.ver(alpha)": (False, True, False, "macos"),
    }
    from app import config as app_config
    assert (
        app_config.IS_WINDOWS,
        app_config.IS_MACOS,
        app_config.IS_LINUX,
        app_config.PLATFORM_ID,
    ) == expected_flags[tree]

    defaults = core.get_default_config()
    for key in (
        "global_hotkeys_enabled",
        "hotkey_focus_guard",
        "app_shortcuts_enabled",
        "performance_level",
        "font_weight",
        "font_size",
        "log_enabled",
        "log_file_path",
        "html_auto_pause",
        "html_gpu_enabled",
        "html_mouse_through",
    ):
        assert key in defaults, f"{tree}: missing default key {key}"
    assert defaults["global_hotkeys_enabled"] is False
    assert defaults["app_shortcuts_enabled"] is True

    # Old settings files must be upgraded to the same opt-in hotkey contract.
    with tempfile.TemporaryDirectory() as temp:
        temp_path = Path(temp)
        core.CONFIG_PATH = str(temp_path / "settings.json")
        core.BUNDLED_CONFIG_PATH = str(temp_path / "missing-bundled.json")
        core.LEGACY_CONFIG_PATH = str(temp_path / "missing-legacy.json")
        core.LEGACY_BUNDLED_CONFIG_PATH = str(temp_path / "missing-legacy-bundled.json")
        Path(core.CONFIG_PATH).write_text('{"mode":"图片"}', encoding="utf-8")
        migrated = core.load_config()
        assert migrated["global_hotkeys_enabled"] is False
        assert "hotkey_focus_guard" in migrated
        assert migrated["app_shortcuts_enabled"] is True

        # User-selected log files must receive core.log messages on every tree.
        user_log = temp_path / "selected-user.log"
        core.config = dict(migrated)
        core.config["log_enabled"] = True
        core.config["log_file_path"] = str(user_log)
        core.log(f"PARITY_LOG::{tree}")
        assert user_log.is_file(), f"{tree}: selected log file not created"
        assert f"PARITY_LOG::{tree}" in user_log.read_text(encoding="utf-8")

    if not core.IS_WINDOWS:
        created: list[object] = []

        class FakeListener:
            def __init__(self, combos):
                self.combos = combos
                self.daemon = False
                self.started = False
                self.stopped = False
                created.append(self)

            def start(self):
                self.started = True

            def stop(self):
                self.stopped = True

        class FakeKeyboard:
            GlobalHotKeys = FakeListener

        core._pynput_keyboard = FakeKeyboard
        core._pynput_hotkey_listener = None
        core.config = core.get_default_config()
        core.refresh_global_hotkeys()
        assert created == [], f"{tree}: hooks registered while opt-in is off"

        core.config["global_hotkeys_enabled"] = True
        core.refresh_global_hotkeys()
        assert len(created) == 1 and created[0].started
        assert len(created[0].combos) == 4
        core.stop_global_hotkeys()
        assert created[0].stopped

        # The dormant mode action should still dispatch correctly when supplied
        # by a migrated/custom settings file.
        calls: list[str] = []
        original_thread = core.threading.Thread
        original_switch = core.switch_wallpaper_mode
        original_save = core.save_config

        class InlineThread:
            def __init__(self, target, args=(), kwargs=None, **_):
                self.target = target
                self.args = args
                self.kwargs = kwargs or {}

            def start(self):
                self.target(*self.args, **self.kwargs)

        try:
            core.threading.Thread = InlineThread
            core.switch_wallpaper_mode = lambda target="next": calls.append(str(target)) or True
            core.save_config = lambda: calls.append("saved") or True
            core._on_pynput_global_hotkey("mode")
        finally:
            core.threading.Thread = original_thread
            core.switch_wallpaper_mode = original_switch
            core.save_config = original_save
        assert calls == ["next", "saved"], (tree, calls)

    # Build the full settings UI offscreen and verify the shared opt-in control.
    # Reset the in-memory config because the registration lifecycle test above
    # intentionally enabled the setting.
    core.config = core.get_default_config()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    core.schedule_next_slide = lambda *_a, **_k: None
    core.start_slideshow = lambda *_a, **_k: None
    core.start_video_wallpaper = lambda *_a, **_k: None
    core.start_html_wallpaper = lambda *_a, **_k: False
    from ui.main_window import ShangBackgroundWindow

    window = ShangBackgroundWindow()
    settings_page = window._settings_tab_full()
    app.processEvents()
    assert settings_page is not None
    assert window.tabs.count() == 3
    assert hasattr(window, "global_hotkeys_enabled_check")
    assert window.global_hotkeys_enabled_check.isChecked() is False
    assert window._settings_nav.count() >= 6

    calls: list[str] = []
    original_save = core.save_config
    original_refresh = core.refresh_global_hotkeys
    original_stop = core.stop_global_hotkeys
    try:
        core.save_config = lambda: calls.append("save") or True
        core.refresh_global_hotkeys = lambda: calls.append("refresh") or True
        core.stop_global_hotkeys = lambda: calls.append("stop")
        window.on_global_hotkeys_enabled_changed(True)
        window.on_global_hotkeys_enabled_changed(False)
    finally:
        core.save_config = original_save
        core.refresh_global_hotkeys = original_refresh
        core.stop_global_hotkeys = original_stop
    assert calls == ["save", "refresh", "save", "stop"], (tree, calls)

    window.close()
    app.processEvents()
    print("DEFAULT_KEYS=" + json.dumps(sorted(defaults), ensure_ascii=False))
    print(f"PASS cross-platform parity: {tree}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child")
    args = parser.parse_args()
    if args.child:
        return _child(args.child)

    key_sets: dict[str, list[str]] = {}
    with tempfile.TemporaryDirectory() as home:
        for tree in TREES:
            env = os.environ.copy()
            env.update(
                {
                    "HOME": home,
                    "USERPROFILE": home,
                    "LOCALAPPDATA": home,
                    "APPDATA": home,
                    "XDG_CONFIG_HOME": str(Path(home) / ".config"),
                    "XDG_DATA_HOME": str(Path(home) / ".local/share"),
                    "XDG_RUNTIME_DIR": str(Path(home) / "runtime"),
                    "QT_QPA_PLATFORM": "offscreen",
                }
            )
            Path(env["XDG_RUNTIME_DIR"]).mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(
                [sys.executable, __file__, "--child", tree],
                check=True,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            print(proc.stdout, end="")
            marker = next(line for line in proc.stdout.splitlines() if line.startswith("DEFAULT_KEYS="))
            key_sets[tree] = json.loads(marker.split("=", 1)[1])

    baseline = key_sets["Windows.ver"]
    for tree, keys in key_sets.items():
        assert keys == baseline, f"default config key drift: Windows vs {tree}"
    print("PASS default config key parity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
