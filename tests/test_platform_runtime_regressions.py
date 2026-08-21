from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.backends.linux import dependencies as linux_dependencies
from platform_adapters.backends.linux import capabilities, portal_hotkeys, session, video


def test_session_detection_falls_back_to_wayland_display():
    assert session.detect_session_type({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}) == "wayland"
    assert session.detect_session_type({"DISPLAY": ":0"}) == "x11"
    assert session.detect_session_type({}) == "unknown"


def test_session_bus_uses_xdg_runtime_socket(tmp_path: Path):
    (tmp_path / "bus").touch()
    assert session.session_bus_available({"XDG_RUNTIME_DIR": os.fspath(tmp_path)}) is True


def test_capabilities_accept_runtime_bus_without_address(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    (tmp_path / "bus").touch()
    env = {
        "WAYLAND_DISPLAY": "wayland-0",
        "XDG_CURRENT_DESKTOP": "KDE",
        "XDG_RUNTIME_DIR": os.fspath(tmp_path),
    }
    monkeypatch.setattr(capabilities, "_has", lambda name: name == "dbus_next")
    result = capabilities.probe_capabilities(
        env,
        which=lambda name: f"/usr/bin/{name}" if name in {"mpvpaper", "qdbus6"} else None,
    )
    assert result["global_hotkeys"]["runtime_ready"] is True
    assert result["video_wallpaper"]["runtime_ready"] is True


def test_wayland_video_uses_current_mpvpaper_selector_and_safe_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"test")
    captured: dict[str, object] = {}

    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.delenv("SHANGBACKGROUND_MPVPAPER_OUTPUT", raising=False)
    monkeypatch.setattr(video, "stop_video_wallpaper", lambda: None)
    monkeypatch.setattr(video, "_mpv_ipc_path", lambda: "/tmp/shangbg-test.sock")
    monkeypatch.setattr(video, "external_media_runtime_allowed", lambda: True)
    monkeypatch.setattr(video.shutil, "which", lambda name: "/usr/bin/mpvpaper" if name == "mpvpaper" else None)

    def fake_start(cmd, fail_name, ipc_path=""):
        captured.update(cmd=cmd, fail_name=fail_name, ipc_path=ipc_path)
        return True, ""

    monkeypatch.setattr(video, "_start_process", fake_start)

    ok, message = video.start_video_wallpaper(os.fspath(media), muted=False, volume=61)

    assert (ok, message) == (True, "")
    cmd = captured["cmd"]
    assert cmd[-2] == "ALL"
    assert "no-config" in cmd[2]
    assert "load-scripts=no" in cmd[2]
    assert "volume=61" in cmd[2]


def test_wayland_mpvpaper_muting_keeps_audio_track_and_saved_volume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"test")
    captured: dict[str, object] = {}

    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.setattr(video, "stop_video_wallpaper", lambda: None)
    monkeypatch.setattr(video, "_mpv_ipc_path", lambda: "/tmp/shangbg-test.sock")
    monkeypatch.setattr(video, "external_media_runtime_allowed", lambda: True)
    monkeypatch.setattr(video.shutil, "which", lambda name: "/usr/bin/mpvpaper" if name == "mpvpaper" else None)

    def fake_start(cmd, fail_name, ipc_path=""):
        captured.update(cmd=cmd, fail_name=fail_name, ipc_path=ipc_path)
        return True, ""

    monkeypatch.setattr(video, "_start_process", fake_start)
    assert video.start_video_wallpaper(os.fspath(media), muted=True, volume=61)[0] is True

    options = captured["cmd"][2]
    assert "mute=yes" in options
    assert "volume=61" in options
    assert "no-audio" not in options


def test_wayland_video_output_can_be_selected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    media = tmp_path / "clip.webm"
    media.write_bytes(b"test")
    seen: list[str] = []
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.setenv("SHANGBACKGROUND_MPVPAPER_OUTPUT", "DP-1")
    monkeypatch.setattr(video, "stop_video_wallpaper", lambda: None)
    monkeypatch.setattr(video, "_mpv_ipc_path", lambda: "/tmp/shangbg-test.sock")
    monkeypatch.setattr(video, "external_media_runtime_allowed", lambda: True)
    monkeypatch.setattr(video.shutil, "which", lambda name: "/usr/bin/mpvpaper" if name == "mpvpaper" else None)
    monkeypatch.setattr(
        video,
        "_start_process",
        lambda cmd, _name, ipc_path="": (seen.extend(cmd) or True, ""),
    )
    assert video.start_video_wallpaper(os.fspath(media))[0] is True
    assert "DP-1" in seen


def test_portal_request_has_timeout_and_closes_request():
    class FakeRequest:
        def __init__(self):
            self.closed = False
            self.callback = None

        def on_response(self, callback):
            self.callback = callback

        def off_response(self, callback):
            assert callback is self.callback

        async def call_close(self):
            self.closed = True

    request = FakeRequest()

    class FakeProxy:
        def get_interface(self, _name):
            return request

    class FakeBus:
        async def introspect(self, _name, _path):
            return object()

        def get_proxy_object(self, _name, _path, _intro):
            return FakeProxy()

    backend = portal_hotkeys.PortalGlobalShortcuts(startup_timeout=0.01, request_timeout=0.02)
    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.run(backend._wait_request(FakeBus(), "/request/test"))
    assert request.closed is True


def test_linux_pip_followup_avoids_user_flag_inside_venv(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(linux_dependencies.sys, "prefix", "/tmp/venv")
    monkeypatch.setattr(linux_dependencies.sys, "base_prefix", "/usr")
    command = linux_dependencies._pip_install_command(["dbus-next"])
    assert command[:4] == [linux_dependencies.sys.executable, "-m", "pip", "install"]
    assert "--user" not in command


def test_support_exports_font_helper_without_qt_runtime():
    support = importlib.import_module("app.support")
    assert callable(support.apply_application_font)
    if not support.PYSIDE_AVAILABLE:
        assert support.apply_application_font(None) == ""


def test_platform_backend_public_api_parity():
    required_video = {
        "validate_video_path", "start_video_wallpaper", "stop_video_wallpaper",
        "is_video_wallpaper_running", "set_video_volume", "set_video_paused",
    }
    for platform in ("windows", "linux", "macos"):
        module = importlib.import_module(f"platform_adapters.backends.{platform}.video")
        assert required_video <= set(dir(module)), platform


def test_linux_install_plan_uses_active_venv_for_python_packages(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(linux_dependencies.sys, "prefix", "/tmp/venv")
    monkeypatch.setattr(linux_dependencies.sys, "base_prefix", "/usr")
    monkeypatch.setattr(linux_dependencies, "detect_linux_family", lambda: ("ubuntu", "apt"))
    monkeypatch.setattr(linux_dependencies, "_privilege_prefix", lambda: [])

    plan = linux_dependencies.build_install_plan(["PySide6-Essentials", "pynput"])

    assert plan.system_packages == []
    assert plan.command[:4] == [linux_dependencies.sys.executable, "-m", "pip", "install"]
    assert "PySide6-Essentials" in plan.command
    assert "pynput" in plan.command
    assert plan.followup_commands == ()


def test_windows_player_is_terminated_when_state_persistence_fails(monkeypatch: pytest.MonkeyPatch):
    from platform_adapters.backends.windows import video as windows_video

    process = SimpleNamespace(pid=4321)
    terminated: list[object] = []
    monkeypatch.setattr(windows_video, "_launch", lambda _cmd: process)
    monkeypatch.setattr(windows_video, "_wait_for_player_ready", lambda _proc, _ipc: True)
    monkeypatch.setattr(windows_video, "_write_state", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(windows_video, "_terminate_failed_player", terminated.append)
    monkeypatch.setattr(windows_video.process_state, "remove_state", lambda _path: None)

    with pytest.raises(OSError, match="disk full"):
        windows_video._start_player("mpv", ["mpv.exe", "--wid=1"], "pipe")
    assert terminated == [process]


def test_macos_immediate_player_exit_cleans_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from platform_adapters.backends.macos import video as macos_video

    media = tmp_path / "clip.mp4"
    media.write_bytes(b"test")
    ipc_file = tmp_path / "video.ipc"
    ipc_socket = tmp_path / "video.sock"
    removed: list[str] = []

    class FakeProcess:
        pid = 9876

        def poll(self):
            return 1

    monkeypatch.setattr(macos_video, "IPC_FILE", os.fspath(ipc_file))
    monkeypatch.setattr(macos_video, "PID_FILE", os.fspath(tmp_path / "video.pid"))
    monkeypatch.setattr(macos_video, "stop_video_wallpaper", lambda: None)
    monkeypatch.setattr(macos_video.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(macos_video, "_mpv_ipc_path", lambda: os.fspath(ipc_socket))
    monkeypatch.setattr(macos_video.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(macos_video.process_state, "write_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(macos_video.process_state, "remove_state", removed.append)
    monkeypatch.setattr(macos_video, "_reap_child", lambda _pid: None)
    monkeypatch.setattr(macos_video.time, "sleep", lambda _seconds: None)

    ok, message = macos_video.start_video_wallpaper(os.fspath(media))

    assert ok is False
    assert "立即退出" in message
    assert macos_video._CURRENT_PROC is None
    assert removed == [os.fspath(tmp_path / "video.pid")]
    assert not ipc_file.exists()


def test_windows_video_prefers_mpv_process_before_full_app_libmpv_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from platform_adapters.backends.windows import video as windows_video

    media = tmp_path / "clip.mp4"
    media.write_bytes(b"video")
    calls: list[str] = []
    monkeypatch.setattr(windows_video, "stop_video_wallpaper", lambda: None)
    monkeypatch.setattr(windows_video, "_find_workerw", lambda: 1234)
    monkeypatch.setattr(
        windows_video,
        "_mpv_command",
        lambda *_args: ("mpv", ["mpv.exe", "--wid=1234"], r"\\.\pipe\test"),
    )
    monkeypatch.setattr(
        windows_video,
        "_internal_libmpv_command",
        lambda *_args: calls.append("internal") or ("libmpv", ["ShangBackground.exe"], "pipe2"),
    )
    monkeypatch.setattr(
        windows_video,
        "_start_player",
        lambda name, _cmd, ipc_path="": (calls.append(name) or True, "ok"),
    )

    ok, message = windows_video.start_video_wallpaper(str(media))

    assert (ok, message) == (True, "ok")
    assert calls == ["mpv"]


def test_linux_video_terminates_child_when_state_persistence_fails(monkeypatch: pytest.MonkeyPatch):
    from platform_adapters.backends.linux import video as linux_video

    class FakeProcess:
        pid = 2468

        def __init__(self):
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.terminated = True

    process = FakeProcess()
    removed: list[str] = []
    monkeypatch.setattr(linux_video.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        linux_video.process_state,
        "write_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    monkeypatch.setattr(linux_video.process_state, "remove_state", removed.append)
    monkeypatch.setattr(
        linux_video,
        "_wait_for_ipc",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("ownership must be durable before readiness wait")),
    )

    ok, message = linux_video._start_process(["player"], "播放器", "/tmp/test.sock")

    assert ok is False
    assert "所有权" in message
    assert process.terminated is True
    assert linux_video._CURRENT_PROC is None
    assert removed == [linux_video.PID_FILE]


def test_macos_video_terminates_child_when_state_persistence_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from platform_adapters.backends.macos import video as macos_video

    media = tmp_path / "clip.mp4"
    media.write_bytes(b"test")

    class FakeProcess:
        pid = 9753

        def __init__(self):
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.terminated = True

    process = FakeProcess()
    monkeypatch.setattr(macos_video, "stop_video_wallpaper", lambda: None)
    monkeypatch.setattr(macos_video.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(macos_video, "_mpv_ipc_path", lambda: os.fspath(tmp_path / "video.sock"))
    monkeypatch.setattr(macos_video.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        macos_video.process_state,
        "write_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("read-only state dir")),
    )
    monkeypatch.setattr(macos_video.process_state, "remove_state", lambda _path: None)

    ok, message = macos_video.start_video_wallpaper(os.fspath(media))

    assert ok is False
    assert "read-only state dir" in message
    assert process.terminated is True
    assert macos_video._CURRENT_PROC is None


@pytest.mark.parametrize("platform", ["linux", "macos"])
def test_posix_html_terminates_child_when_state_persistence_fails(
    platform: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    module = importlib.import_module(f"platform_adapters.backends.{platform}.html_wallpaper")
    html = tmp_path / "wallpaper.html"
    html.write_text("<html></html>", encoding="utf-8")

    class FakeRuntime:
        name = "native"
        internal_flag = "--internal-html-runner"

    class FakeProcess:
        pid = 8642

        def __init__(self):
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.terminated = True

    process = FakeProcess()
    monkeypatch.setattr(module, "stop_html_wallpaper", lambda: None)
    monkeypatch.setattr(module, "normalize_html_source", lambda value: str(value) if value else None)
    monkeypatch.setattr(module, "missing_runtime_modules", lambda _runtime: ())
    monkeypatch.setattr(module, "select_html_runtime", lambda: FakeRuntime())
    if platform == "macos":
        monkeypatch.setattr(module.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(module, "is_packaged_runtime", lambda: True)
    monkeypatch.setattr(module, "app_executable_path", lambda: "/tmp/ShangBackground")
    monkeypatch.setattr(module, "runtime_set_option", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        module,
        "_write_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("state write failed")),
    )
    monkeypatch.setattr(module.process_state, "remove_state", lambda _path: None)
    monkeypatch.setattr(module, "_SUBPROCESS_LOG", os.fspath(tmp_path / f"{platform}.log"))

    ok, message = module.start_html_wallpaper(os.fspath(html))

    assert ok is False
    assert "所有权" in message
    assert process.terminated is True
    assert module._CURRENT_PROC is None
