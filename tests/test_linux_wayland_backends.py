from __future__ import annotations

import pytest

from platform_adapters.backends.linux import capabilities, hotkeys, video
from platform_adapters.backends.linux.portal_hotkeys import to_xdg_shortcut


def test_kde_wayland_capabilities_enable_layer_shell_and_portal(monkeypatch: pytest.MonkeyPatch):
    env = {
        "XDG_SESSION_TYPE": "wayland",
        "XDG_CURRENT_DESKTOP": "KDE",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    }
    monkeypatch.setattr(capabilities, "_has", lambda name: name == "dbus_next")
    found = {"mpvpaper": "/usr/bin/mpvpaper", "plasma-apply-wallpaperimage": "/usr/bin/plasma-apply-wallpaperimage"}

    result = capabilities.probe_capabilities(env, which=found.get)

    assert result["static_wallpaper"]["runtime_ready"] is True
    assert result["video_wallpaper"]["runtime_ready"] is True
    assert "KWin" in result["video_wallpaper"]["backend"]
    assert result["global_hotkeys"]["runtime_ready"] is True
    assert "GlobalShortcuts" in result["global_hotkeys"]["backend"]


def test_gnome_wayland_does_not_claim_mpvpaper_backend(monkeypatch: pytest.MonkeyPatch):
    env = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME"}
    monkeypatch.setattr(capabilities, "_has", lambda _name: False)

    result = capabilities.probe_capabilities(env, which=lambda name: "/usr/bin/mpvpaper" if name == "mpvpaper" else None)

    assert result["video_wallpaper"]["runtime_ready"] is False
    assert result["video_wallpaper"]["state"] == "unsupported"


def test_linux_video_recognizes_kde_wayland_layer_shell(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert video._wayland_layer_shell_session() is True


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
