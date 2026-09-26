from __future__ import annotations

import pytest

from platform_adapters.backends.linux import capabilities, hotkeys, video
from platform_adapters.backends.linux.portal_hotkeys import to_xdg_shortcut


def test_kde_wayland_capabilities_enable_layer_shell_and_portal(monkeypatch: pytest.MonkeyPatch):
    """v1.6.2 审查修正后的契约：KDE Wayland 静态壁纸/热键照常 ready，
    但视频壁纸不得因 mpvpaper 在 PATH 中而宣称 runtime_ready——
    mpvpaper 面向 wlroots 合成器，KWin 兼容性未经独立探针验证。"""
    env = {
        "XDG_SESSION_TYPE": "wayland",
        "XDG_CURRENT_DESKTOP": "KDE",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    }
    monkeypatch.setattr(capabilities, "_has", lambda name: name == "dbus_next")
    found = {"mpvpaper": "/usr/bin/mpvpaper", "plasma-apply-wallpaperimage": "/usr/bin/plasma-apply-wallpaperimage"}

    result = capabilities.probe_capabilities(env, which=found.get)

    assert result["static_wallpaper"]["runtime_ready"] is True
    assert result["video_wallpaper"]["runtime_ready"] is False
    assert result["video_wallpaper"]["state"] == "best_effort"
    assert "KWin" in result["video_wallpaper"]["backend"]
    assert "untested" in result["video_wallpaper"]["backend"]
    assert result["global_hotkeys"]["runtime_ready"] is True
    assert "GlobalShortcuts" in result["global_hotkeys"]["backend"]


def test_gnome_wayland_does_not_claim_mpvpaper_backend(monkeypatch: pytest.MonkeyPatch):
    env = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME"}
    monkeypatch.setattr(capabilities, "_has", lambda _name: False)

    result = capabilities.probe_capabilities(env, which=lambda name: "/usr/bin/mpvpaper" if name == "mpvpaper" else None)

    assert result["video_wallpaper"]["runtime_ready"] is False
    assert result["video_wallpaper"]["state"] == "unsupported"


def test_linux_video_kde_wayland_requires_explicit_opt_in(monkeypatch: pytest.MonkeyPatch):
    """v1.6.2 审查修正：KDE Wayland 启动路径默认拒绝 mpvpaper，
    仅显式实验开关 SHANGBACKGROUND_ALLOW_MPVPAPER=1 允许尝试。"""
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.delenv("SHANGBACKGROUND_ALLOW_MPVPAPER", raising=False)
    assert video._wayland_layer_shell_session() is False

    monkeypatch.setenv("SHANGBACKGROUND_ALLOW_MPVPAPER", "1")
    assert video._wayland_layer_shell_session() is True


def test_linux_video_wlroots_wayland_still_recognized(monkeypatch: pytest.MonkeyPatch):
    """wlroots 系合成器（mpvpaper 官方支持平台）不受 KDE 收紧影响。"""
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "sway")
    assert video._wayland_layer_shell_session() is True

    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    assert video._wayland_layer_shell_session() is False


def test_wayland_hotkeys_use_portal_and_stop(monkeypatch: pytest.MonkeyPatch):
    events: list[str] = []

    class FakePortal:
        def __init__(self):
            self.stopped = False

        def start(self, bindings, dispatch):
            assert bindings == {"next": "Ctrl+Alt+n"}
            dispatch("next")
            return True

        def stop(self):
            self.stopped = True

    portal = FakePortal()
    monkeypatch.setattr(hotkeys, "_PORTAL_OVERRIDE", portal)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")

    assert hotkeys.refresh({"next": "Ctrl+Alt+n"}, events.append) is True
    assert events == ["next"]
    assert hotkeys.focus_block_reason("next", "Ctrl+Alt+n") == ""
    hotkeys.stop()
    assert portal.stopped is True


def test_xdg_shortcut_conversion_uses_spec_names():
    assert to_xdg_shortcut("Ctrl+Alt+n") == "CTRL+ALT+n"
    assert to_xdg_shortcut("Super+Shift+F12") == "LOGO+SHIFT+F12"
    assert to_xdg_shortcut("n") is None
