"""KDE 能力契约（v1.6.2 审查必须修复项 1）。

REVIEW_REPORT_V1.6.1 指出：capabilities.py 在 Wayland 会话中只要桌面令牌
包含 kde/plasma 且找到 mpvpaper，就返回 runtime_ready=True——把"命令存在"
当成"后端可运行"。mpvpaper 官方定位是 wlroots 系合成器；KWin 的
layer-shell 兼容性未经独立探针验证，不得宣称 ready。

本文件钉死以下契约：
1. KDE Wayland + mpvpaper 在 PATH：runtime_ready 必须为 False，
   state 为 best_effort（可尝试）或 unsupported，绝不出现"已验证"措辞。
2. SHANGBACKGROUND_ALLOW_MPVPAPER=1 只是显式实验开关：只影响
   video.py 启动路径的放行，不改变能力探测声明。
3. wlroots 系合成器（sway/hyprland，mpvpaper 官方支持平台）保持 ready。
4. KDE Wayland 的静态壁纸与热键能力不受视频收紧影响。
5. KDE 无 mpvpaper 时视频为 unsupported。
6. X11 KDE 会话不受影响（x11 分支走 xwinwrap 判定）。
"""

from __future__ import annotations

import pytest

from platform_adapters.backends.linux import capabilities, video


_ONLY_MPVPAPER = lambda name: "/usr/bin/mpvpaper" if name == "mpvpaper" else None  # noqa: E731


def _kde_wayland_env() -> dict[str, str]:
    return {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"}


def test_kde_wayland_does_not_claim_mpvpaper_as_verified_backend(monkeypatch: pytest.MonkeyPatch):
    """报告给出的原型契约：KDE Wayland 即使有 mpvpaper 也不得宣称 ready。"""
    monkeypatch.setattr(capabilities, "_has", lambda name: name == "dbus_next")
    result = capabilities.probe_capabilities(_kde_wayland_env(), which=_ONLY_MPVPAPER)
    assert result["video_wallpaper"]["runtime_ready"] is False
    assert result["video_wallpaper"]["state"] in {"best_effort", "unsupported"}
    assert "verified" not in str(result["video_wallpaper"]).lower()


def test_kde_wayland_video_backend_declares_untested_and_opt_in(monkeypatch: pytest.MonkeyPatch):
    """backend 描述必须自带"未验证 + 显式实验开关"的用户可见提示。"""
    monkeypatch.setattr(capabilities, "_has", lambda _name: False)
    result = capabilities.probe_capabilities(_kde_wayland_env(), which=_ONLY_MPVPAPER)
    assert "untested" in result["video_wallpaper"]["backend"]
    assert "SHANGBACKGROUND_ALLOW_MPVPAPER" in result["video_wallpaper"]["backend"]


def test_kde_wayland_without_mpvpaper_is_unsupported(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(capabilities, "_has", lambda _name: False)
    result = capabilities.probe_capabilities(_kde_wayland_env(), which=lambda _name: None)
    assert result["video_wallpaper"]["runtime_ready"] is False
    assert result["video_wallpaper"]["state"] == "unsupported"


def test_allow_mpvpaper_env_does_not_change_capability_claim(monkeypatch: pytest.MonkeyPatch):
    """报告要求 3：实验开关只放行启动尝试，不得改变能力声明。"""
    monkeypatch.setattr(capabilities, "_has", lambda _name: False)
    monkeypatch.setenv("SHANGBACKGROUND_ALLOW_MPVPAPER", "1")
    result = capabilities.probe_capabilities(_kde_wayland_env(), which=_ONLY_MPVPAPER)
    assert result["video_wallpaper"]["runtime_ready"] is False
    assert result["video_wallpaper"]["state"] == "best_effort"


def test_allow_mpvpaper_env_gates_startup_path_only(monkeypatch: pytest.MonkeyPatch):
    """同一开关在启动路径上的行为：默认拒绝，显式设置后放行（后果自负）。"""
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "plasma")
    monkeypatch.delenv("SHANGBACKGROUND_ALLOW_MPVPAPER", raising=False)
    assert video._wayland_layer_shell_session() is False
    monkeypatch.setenv("SHANGBACKGROUND_ALLOW_MPVPAPER", "1")
    assert video._wayland_layer_shell_session() is True


def test_wlroots_compositors_still_ready(monkeypatch: pytest.MonkeyPatch):
    """sway/hyprland 是 mpvpaper 官方支持平台：命令存在即 best_effort ready。"""
    monkeypatch.setattr(capabilities, "_has", lambda _name: False)
    for desktop in ("sway", "Hyprland"):
        env = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": desktop}
        result = capabilities.probe_capabilities(env, which=_ONLY_MPVPAPER)
        assert result["video_wallpaper"]["runtime_ready"] is True
        assert result["video_wallpaper"]["state"] == "best_effort"


def test_explicit_compositor_socket_overrides_desktop_tokens(monkeypatch: pytest.MonkeyPatch):
    """显式合成器 socket 证据优先于桌面令牌（即使令牌是 KDE）。"""
    monkeypatch.setattr(capabilities, "_has", lambda _name: False)
    env = {
        "XDG_SESSION_TYPE": "wayland",
        "XDG_CURRENT_DESKTOP": "KDE",
        "SWAYSOCK": "/run/user/1000/sway-ipc.1000.1",
    }
    result = capabilities.probe_capabilities(env, which=_ONLY_MPVPAPER)
    assert result["video_wallpaper"]["runtime_ready"] is True


def test_kde_wayland_static_and_hotkeys_unaffected(monkeypatch: pytest.MonkeyPatch):
    """视频收紧不得殃及静态壁纸与热键（报告：静态路径可独立验收）。"""
    monkeypatch.setattr(capabilities, "_has", lambda name: name == "dbus_next")
    env = {
        "XDG_SESSION_TYPE": "wayland",
        "XDG_CURRENT_DESKTOP": "KDE",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    }
    found = {"plasma-apply-wallpaperimage": "/usr/bin/plasma-apply-wallpaperimage"}
    result = capabilities.probe_capabilities(env, which=found.get)
    assert result["static_wallpaper"]["runtime_ready"] is True
    assert result["global_hotkeys"]["runtime_ready"] is True


def test_kde_x11_session_not_affected(monkeypatch: pytest.MonkeyPatch):
    """X11 会话走 xwinwrap 判定，与 Wayland 收紧无关。"""
    monkeypatch.setattr(capabilities, "_has", lambda _name: False)
    monkeypatch.setattr(capabilities, "_libmpv_ready", lambda: True)
    env = {"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "KDE"}
    result = capabilities.probe_capabilities(
        env, which=lambda name: "/usr/bin/xwinwrap" if name == "xwinwrap" else None
    )
    assert result["video_wallpaper"]["runtime_ready"] is True
