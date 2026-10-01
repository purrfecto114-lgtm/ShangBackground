"""Regression tests for the v1.6.1 fixes.

Covers:
- startup restoration of the persisted UI language (was silently ignored)
- ``--volume-ipc`` forwarding in the internal video player dispatcher
  (macOS volume/pause hot path)
- feature-gated mode cycle order (no ghost "HTML" mode in core-only builds)
- slideshow fast-fail validation order (no dynamic-wallpaper stop before
  folder/image validation fails)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import i18n
from app.slideshow_service import SlideshowService
from app.wallpaper_mode_service import WallpaperModeService


# ---------------------------------------------------------------------------
# v1.6.1: persisted language must be re-applied after user config load
# ---------------------------------------------------------------------------


def test_initialize_application_reapplies_persisted_language():
    """``initialize_application`` must call ``init_i18n`` with the loaded
    user config. The import-time call in app.support only sees module
    defaults, so without this the saved "English" preference was ignored on
    every restart (source-level contract, same style as the IPC tests)."""
    from pathlib import Path

    text = Path("src/core/engine.py").read_text(encoding="utf-8")
    block = text.split("def initialize_application(", 1)[1].split("def ", 1)[0]
    assert "load_user_config" in block
    init_block = block.split("config = load_config()", 1)[1]
    assert "init_i18n" in init_block
    assert "init_i18n(config)" in init_block


def test_i18n_reapply_changes_active_language(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Behavioral half: re-running init_i18n with a config that requests
    English must actually switch the active language."""
    (tmp_path / "en.json").write_text(json.dumps({"设置": "Settings"}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(i18n, "LANG_DIR", str(tmp_path))
    monkeypatch.setattr(i18n, "_CURRENT_LANG", "zh")
    monkeypatch.setattr(i18n, "_TRANSLATIONS", {})
    i18n.init_i18n({"language": "en"})
    assert i18n.get_language() == "en"
    assert i18n.t("设置") == "Settings"


# ---------------------------------------------------------------------------
# v1.6.1: --volume-ipc must reach run_player (macOS volume/pause IPC)
# ---------------------------------------------------------------------------


def test_internal_video_player_dispatcher_forwards_volume_ipc(monkeypatch):
    """The main.py dispatcher must parse --volume-ipc and forward it to
    run_player; dropping it disabled all hot volume/pause changes on macOS."""
    import sys
    from types import SimpleNamespace

    import main as main_module

    captured: dict[str, object] = {}

    def _fake_run_player(path, *, muted, volume, volume_ipc=""):
        captured.update(path=path, muted=muted, volume=volume, volume_ipc=volume_ipc)

    # The internal-video-player subprocess is a macOS-only code path; on
    # Linux the real backend module has no run_player, so inject a fake
    # dispatch module (main.py imports it lazily inside the dispatcher).
    monkeypatch.setitem(
        sys.modules,
        "platform_adapters.video",
        SimpleNamespace(run_player=_fake_run_player),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--internal-video-player",
            "/tmp/wallpaper.mp4",
            "--muted",
            "--volume",
            "42",
            "--volume-ipc",
            "/tmp/sb-video-ipc.sock",
        ],
    )
    code = main_module._dispatch_internal_mode()
    assert code == 0
    assert captured["volume_ipc"] == "/tmp/sb-video-ipc.sock"
    assert captured["volume"] == 42
    assert captured["muted"] is True


def test_internal_video_player_dispatcher_without_volume_ipc(monkeypatch):
    import sys
    from types import SimpleNamespace

    import main as main_module

    captured: dict[str, object] = {}

    def _fake_run_player(path, *, muted, volume, volume_ipc=""):
        captured.update(volume_ipc=volume_ipc)

    monkeypatch.setitem(
        sys.modules,
        "platform_adapters.video",
        SimpleNamespace(run_player=_fake_run_player),
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "--internal-video-player", "a.mp4"])
    assert main_module._dispatch_internal_mode() == 0
    assert captured["volume_ipc"] == ""


# ---------------------------------------------------------------------------
# v1.6.1: feature-gated mode cycle (no ghost HTML mode)
# ---------------------------------------------------------------------------


def _make_mode_service(mode_order):
    return WallpaperModeService(
        config=lambda: {"mode": "图片"},
        persist=lambda: True,
        operation_lock=__import__("threading").RLock(),
        mode_order=mode_order,
        activate=lambda mode, cfg: True,
        normalize_mode=lambda value: value,
        log=lambda message: None,
    )


def test_mode_service_order_respects_explicit_html():
    service = _make_mode_service(("图片", "视频", "HTML"))
    assert "HTML" in service._order


def test_mode_service_appends_html_only_when_feature_enabled(monkeypatch):
    monkeypatch.setattr("app.wallpaper_mode_service._html_mode_available", lambda: False)
    service = _make_mode_service(("图片", "视频"))
    assert "HTML" not in service._order
    # resolve("next") must cycle within the available modes only, never
    # return the ghost HTML mode.
    assert service.resolve("next") == "视频"


def test_mode_service_appends_html_when_feature_enabled(monkeypatch):
    monkeypatch.setattr("app.wallpaper_mode_service._html_mode_available", lambda: True)
    service = _make_mode_service(("图片", "视频"))
    assert "HTML" in service._order


def test_engine_mode_order_no_longer_force_appends_html():
    from pathlib import Path

    text = Path("src/core/engine.py").read_text(encoding="utf-8")
    assert 'mode_order=tuple(MODE_KEYS) + ("HTML",)' not in text
    assert "mode_order=tuple(MODE_KEYS)," in text


# ---------------------------------------------------------------------------
# v1.6.1: slideshow fast-fail before stopping the dynamic wallpaper
# ---------------------------------------------------------------------------


class _State:
    def start(self, images):
        return None, 0, list(images)

    def is_active(self, generation):
        return True


def test_slideshow_start_fails_before_stopping_dynamic_for_empty_folder(tmp_path):
    """An empty/invalid folder must fail start() WITHOUT touching the running
    dynamic wallpaper (previously the old video was stopped first and then
    compensated back on, causing multi-second desktop flicker)."""
    stopped: list[bool] = []
    logged: list[str] = []
    service = SlideshowService(
        state=_State(),
        config=lambda: {"mode": "幻灯片放映", "slide_folder": str(tmp_path)},
        operation_lock=__import__("threading").RLock(),
        image_source=lambda folder: [],
        apply_wallpaper=lambda path, operation: True,
        stop_dynamic=lambda: stopped.append(True),
        normalize_mode=lambda value: value,
        log=logged.append,
    )
    assert service.start() is False
    assert stopped == []  # dynamic wallpaper untouched
    assert any("没有可用图片" in message for message in logged)


def test_slideshow_start_fails_before_stopping_dynamic_for_missing_folder():
    stopped: list[bool] = []
    logged: list[str] = []
    service = SlideshowService(
        state=_State(),
        config=lambda: {"mode": "幻灯片放映", "slide_folder": "/nonexistent-folder-xyz"},
        operation_lock=__import__("threading").RLock(),
        image_source=lambda folder: [],
        apply_wallpaper=lambda path, operation: True,
        stop_dynamic=lambda: stopped.append(True),
        normalize_mode=lambda value: value,
        log=logged.append,
    )
    assert service.start() is False
    assert stopped == []
    assert any("图片文件夹不可用" in message for message in logged)


# ---------------------------------------------------------------------------
# v1.6.1 acceptance follow-up: native event filter must stay alive
# ---------------------------------------------------------------------------


def test_setting_change_filter_is_kept_alive_after_install():
    """PySide6 keeps no Python reference for installed native event filters;
    the entry module must retain an owner or the filter dies with the
    temporary wrapper (crash or silent no-op)."""
    from pathlib import Path

    text = Path("src/app/entry.py").read_text(encoding="utf-8")
    assert "_INSTALLED_SETTING_CHANGE_FILTER" in text
    # The install path must assign the filter into the owner slot.
    assert "_WallpaperSettingChangeFilter._INSTALLED_SETTING_CHANGE_FILTER = self._qt_filter" in text


# ---------------------------------------------------------------------------
# v1.6.1: --doctor / --doctor-json actionable hints
# ---------------------------------------------------------------------------


def test_doctor_module_check_hint_gives_install_command():
    from app.diagnostics import _module_check

    missing = _module_check("definitely_not_a_module_xyz", "probe", required=True, install="some-package")
    assert missing.status == "fail"
    assert missing.hint == "python -m pip install some-package"

    present = _module_check("sys", "stdlib probe", required=True, install="anything")
    assert present.status == "pass"
    assert present.hint == ""


def test_doctor_command_check_hint_explains_fallback():
    from app.diagnostics import _command_check

    missing_optional = _command_check(("definitely-not-a-command-xyz",), "probe", required=False)
    assert missing_optional.status == "warn"
    assert "falls back" in missing_optional.hint

    found = _command_check(("python3",), "probe", required=False)
    assert found.status == "pass"
    assert found.hint == ""


def test_doctor_json_payload_includes_hint_field():
    import json

    from app.diagnostics import DiagnosticReport, render_human

    report = DiagnosticReport(
        app_version="1.6.1",
        platform="linux",
        python="3.12",
        resource_root="/r",
        data_dir="/d",
        checks=[
            __import__("app.diagnostics", fromlist=["DiagnosticCheck"]).DiagnosticCheck(
                "probe", "fail", "detail", required=True, hint="python -m pip install pkg"
            )
        ],
    )
    payload = json.loads(json.dumps(report.as_dict()))
    assert payload["checks"][0]["hint"] == "python -m pip install pkg"
    human = render_human(report)
    assert "hint: python -m pip install pkg" in human


# ---------------------------------------------------------------------------
# v1.6.1: internal CLI arg errors must print a message with exit code 2
# ---------------------------------------------------------------------------


def test_missing_cli_argument_errors_print_to_stderr(monkeypatch, capsys):
    import sys

    monkeypatch.setattr(sys, "argv", ["shangbackground", "--build-verify-file"])
    from main import main

    code = main()
    assert code == 2
    captured = capsys.readouterr()
    assert "--build-verify-file" in captured.err
    assert captured.err.strip() != ""


# ---------------------------------------------------------------------------
# v1.6.1 rework (16-b S2): startup Bing auto-update must not schedule work
# via QTimer.singleShot from the worker thread (the timer would start in a
# thread without an event loop and never fire — verified against PySide6
# 6.11.1 by the 16-b acceptance probe).
# ---------------------------------------------------------------------------


def test_bing_startup_auto_update_uses_queued_signal_not_worker_thread_timer():
    text = Path("src/ui/main_window.py").read_text(encoding="utf-8")
    worker_block = text.split("def _run_bing_startup_tasks():", 1)[1].split("\n    def ", 1)[0]
    # Only inspect executable lines: the rework comment legitimately mentions
    # the old QTimer.singleShot call it replaced.
    code_text = "\n".join(
        line for line in worker_block.splitlines() if line.strip() and not line.strip().startswith("#")
    )
    assert "QTimer.singleShot" not in code_text, (
        "worker thread must not schedule QTimer.singleShot (never fires without an event loop)"
    )
    assert "bing_auto_update_signal.emit" in code_text
    assert "bing_auto_update_signal = Signal(str, int)" in text
    connect_block = text.split("self.bing_auto_update_signal.connect(", 1)[1].split(")", 1)[0]
    assert "_start_bing_auto_update" in connect_block
    assert "QueuedConnection" in connect_block
