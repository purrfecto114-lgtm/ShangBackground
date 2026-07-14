#!/usr/bin/env python3
"""Exercise target-specific wallpaper/video adapters with deterministic fakes.

The child processes import one platform tree at a time.  Real operating-system
APIs are not invoked when the source tree does not match the host; instead the
public orchestration contract is checked around a mocked native boundary.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TREES = ("Windows.ver", "Linux.ver(beta)", "MacOS.ver(alpha)")


def _child(tree: str) -> int:
    sys.path.insert(0, str(ROOT / tree / "src"))
    integration = importlib.import_module("platform_adapters.integration")
    video = importlib.import_module("platform_adapters.video")

    for name in ("set_wallpaper_platform", "get_current_wallpaper_platform", "configure_fit_mode"):
        assert callable(getattr(integration, name, None)), (tree, name)
    for name in (
        "validate_video_path",
        "start_video_wallpaper",
        "stop_video_wallpaper",
        "is_video_wallpaper_running",
        "set_video_volume",
        "set_video_paused",
    ):
        assert callable(getattr(video, name, None)), (tree, name)

    with tempfile.TemporaryDirectory() as temp:
        td = Path(temp)
        image = td / "wallpaper.png"
        image.write_bytes(b"image")
        movie = td / "wallpaper.mp4"
        movie.write_bytes(b"video")
        invalid = td / "wallpaper.txt"
        invalid.write_text("not video", encoding="utf-8")

        assert integration._ensure_existing_file(str(image)) == str(image.resolve())
        try:
            integration._ensure_existing_file(str(td / "missing.png"))
        except FileNotFoundError:
            pass
        else:
            raise AssertionError(f"{tree}: missing wallpaper path accepted")
        assert video.validate_video_path(str(movie)) is True
        assert video.validate_video_path(str(invalid)) is False
        assert video.validate_video_path(str(td / "missing.mp4")) is False
        ok, message = video.start_video_wallpaper(str(invalid), muted=True, volume=50)
        assert ok is False and message

        calls: list[object] = []
        if tree == "Windows.ver":
            original_set = integration._set_windows_wallpaper
            original_position = integration._set_position_via_com
            try:
                integration._set_windows_wallpaper = lambda path: calls.append(("wallpaper", str(Path(path).resolve())))
                integration.set_wallpaper_platform(str(image))
                integration._set_position_via_com = lambda mode: calls.append(("fit", mode)) or True
                integration.configure_fit_mode("填充")
            finally:
                integration._set_windows_wallpaper = original_set
                integration._set_position_via_com = original_position
            assert calls == [("wallpaper", str(image.resolve())), ("fit", "填充")]

            originals = (video.stop_video_wallpaper, video._mpv_command, video._vlc_command, video._start_player)
            try:
                video.stop_video_wallpaper = lambda: calls.append("stop-video")
                video._mpv_command = lambda path, muted, volume: (
                    "mpv",
                    ["mpv", path, str(muted), str(volume)],
                    "ipc",
                )
                video._vlc_command = lambda *_a, **_k: None
                video._start_player = lambda name, command, ipc_path="": (
                    calls.append(("start-video", name, command, ipc_path)) or True,
                    "",
                )
                ok, message = video.start_video_wallpaper(str(movie), muted=False, volume=42)
            finally:
                (
                    video.stop_video_wallpaper,
                    video._mpv_command,
                    video._vlc_command,
                    video._start_player,
                ) = originals
            assert ok is True and message == ""
            assert any(isinstance(item, tuple) and item[0] == "start-video" for item in calls)

            sent: list[list[dict]] = []
            original_send = video._send_mpv_ipc_commands
            try:
                video._send_mpv_ipc_commands = lambda commands: sent.append(commands) or True
                assert video.set_video_volume(False, 999) is True
                assert video.set_video_paused(True) is True
            finally:
                video._send_mpv_ipc_commands = original_send
            assert sent[0][1]["command"][-1] == 100
            assert sent[1][0]["command"] == ["set_property", "pause", True]

        elif tree == "Linux.ver(beta)":
            original_set = integration._set_linux_wallpaper
            original_kde = integration._is_kde_session
            original_xfce = integration._is_xfce_session
            original_lxde = integration._is_lxde_session
            original_gsettings_session = integration._is_gsettings_desktop_session
            original_which = integration.shutil.which
            original_run = integration._run_args
            original_get_gnome = integration._get_gnome_wallpaper
            try:
                integration._set_linux_wallpaper = lambda path: calls.append(("wallpaper", str(Path(path).resolve())))
                integration.set_wallpaper_platform(str(image))
                integration._is_kde_session = lambda: False
                integration._is_xfce_session = lambda: False
                integration._is_lxde_session = lambda: False
                integration._is_gsettings_desktop_session = lambda: True
                integration.shutil.which = lambda name: f"/usr/bin/{name}" if name == "gsettings" else None
                integration._run_args = lambda args, **_k: calls.append(("command", list(args))) or (0, "", "")
                integration.configure_fit_mode("填充")
                integration._get_gnome_wallpaper = lambda: (True, str(image.resolve()))
                assert integration.get_current_wallpaper_platform() == str(image.resolve())
            finally:
                integration._set_linux_wallpaper = original_set
                integration._is_kde_session = original_kde
                integration._is_xfce_session = original_xfce
                integration._is_lxde_session = original_lxde
                integration._is_gsettings_desktop_session = original_gsettings_session
                integration.shutil.which = original_which
                integration._run_args = original_run
                integration._get_gnome_wallpaper = original_get_gnome
            assert calls[0] == ("wallpaper", str(image.resolve()))
            assert ("command", ["gsettings", "set", "org.gnome.desktop.background", "picture-options", "zoom"]) in calls

            originals = (video.stop_video_wallpaper, video.shutil.which, video._start_process)
            old_session = os.environ.get("XDG_SESSION_TYPE")
            old_desktop = os.environ.get("XDG_CURRENT_DESKTOP")
            try:
                os.environ["XDG_SESSION_TYPE"] = "wayland"
                os.environ["XDG_CURRENT_DESKTOP"] = "sway"
                video.stop_video_wallpaper = lambda: calls.append("stop-video")
                video.shutil.which = lambda name: "/usr/bin/mpvpaper" if name == "mpvpaper" else None
                video._start_process = lambda command, name, ipc_path="": (
                    calls.append(("start-video", name, list(command), ipc_path)) or True,
                    "",
                )
                ok, message = video.start_video_wallpaper(str(movie), muted=False, volume=42)
            finally:
                video.stop_video_wallpaper, video.shutil.which, video._start_process = originals
                if old_session is None:
                    os.environ.pop("XDG_SESSION_TYPE", None)
                else:
                    os.environ["XDG_SESSION_TYPE"] = old_session
                if old_desktop is None:
                    os.environ.pop("XDG_CURRENT_DESKTOP", None)
                else:
                    os.environ["XDG_CURRENT_DESKTOP"] = old_desktop
            assert ok is True and message == ""
            start_call = next(item for item in calls if isinstance(item, tuple) and item[0] == "start-video")
            assert start_call[1] == "mpvpaper" and "volume=42" in start_call[2][2]
            assert start_call[2][-2] == "*"

            class FakeSocket:
                def __init__(self, *_a, **_k): self.payloads = []
                def settimeout(self, _value): pass
                def connect(self, path): calls.append(("connect", path))
                def sendall(self, payload): calls.append(("payload", payload.decode("utf-8")))
                def close(self): pass

            ipc = td / "mpv.sock"
            ipc.touch()
            Path(video.IPC_FILE).parent.mkdir(parents=True, exist_ok=True)
            Path(video.IPC_FILE).write_text(str(ipc), encoding="utf-8")
            original_socket = video.socket.socket
            try:
                video.socket.socket = FakeSocket
                assert video.set_video_volume(False, -8) is True
                assert video.set_video_paused(True) is True
            finally:
                video.socket.socket = original_socket
            payload_text = "".join(item[1] for item in calls if isinstance(item, tuple) and item[0] == "payload")
            assert '"volume", 0' in payload_text and '"pause", true' in payload_text

        else:
            original_set = integration._set_macos_wallpaper
            original_get = integration._get_macos_wallpaper_appkit
            try:
                integration._set_macos_wallpaper = lambda path: calls.append(("wallpaper", str(Path(path).resolve())))
                integration.set_wallpaper_platform(str(image))
                integration._get_macos_wallpaper_appkit = lambda: (True, str(image.resolve()))
                assert integration.get_current_wallpaper_platform() == str(image.resolve())
                integration.configure_fit_mode("填充", log=lambda msg: calls.append(("fit-note", msg)))
            finally:
                integration._set_macos_wallpaper = original_set
                integration._get_macos_wallpaper_appkit = original_get
            assert calls[0] == ("wallpaper", str(image.resolve()))
            assert any(isinstance(item, tuple) and item[0] == "fit-note" for item in calls)

            class FakeProcess:
                pid = 43210
                def poll(self): return None

            originals = (
                video.stop_video_wallpaper,
                video.importlib.util.find_spec,
                video.subprocess.Popen,
                video.time.sleep,
            )
            video.PID_FILE = str(td / "video.pid")
            video.IPC_FILE = str(td / "video.ipc")
            try:
                video.stop_video_wallpaper = lambda: calls.append("stop-video")
                video.importlib.util.find_spec = lambda _name: object()
                video.subprocess.Popen = lambda command, **_k: calls.append(("start-video", list(command))) or FakeProcess()
                video.time.sleep = lambda _seconds: None
                ok, message = video.start_video_wallpaper(str(movie), muted=False, volume=42)
            finally:
                (
                    video.stop_video_wallpaper,
                    video.importlib.util.find_spec,
                    video.subprocess.Popen,
                    video.time.sleep,
                ) = originals
            assert ok is True and message == ""
            start_call = next(item for item in calls if isinstance(item, tuple) and item[0] == "start-video")
            assert "--internal-video-player" in start_call[1]
            assert "--volume" in start_call[1] and "42" in start_call[1]

            class FakeSocket:
                def __init__(self, *_a, **_k): pass
                def settimeout(self, _value): pass
                def connect(self, path): calls.append(("connect", path))
                def sendall(self, payload): calls.append(("payload", payload.decode("utf-8")))
                def close(self): pass

            ipc = td / "avplayer.sock"
            ipc.touch()
            Path(video.IPC_FILE).write_text(str(ipc), encoding="utf-8")
            original_socket = video.socket.socket
            try:
                video.socket.socket = FakeSocket
                assert video.set_video_volume(True, 55) is True
            finally:
                video.socket.socket = original_socket
            payload = next(item[1] for item in calls if isinstance(item, tuple) and item[0] == "payload")
            parsed = json.loads(payload)
            assert parsed == {"muted": True, "volume": 0}

    print(f"PASS platform contracts: {tree}")
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
