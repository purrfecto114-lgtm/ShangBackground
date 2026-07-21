from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


RestartAction = Literal["normal", "admin"]


@dataclass(frozen=True, slots=True)
class PlatformUiPolicy:
    """Small, Qt-free description of platform-specific GUI behavior.

    Page builders consume this policy instead of maintaining full Windows,
    Linux, and macOS copies of the same widget tree.  Only real platform
    differences belong here; layout and common interactions stay shared.
    """

    platform_id: str
    auto_start_tooltip_key: str
    restart_action: RestartAction
    show_desktop_context_menu: bool
    show_hotkey_focus_guard: bool


_POLICIES = {
    "windows": PlatformUiPolicy(
        platform_id="windows",
        auto_start_tooltip_key="启用后会在启动文件夹生成 ShangBackground.vbs，开机时自动后台启动。",
        restart_action="admin",
        show_desktop_context_menu=True,
        show_hotkey_focus_guard=True,
    ),
    "linux": PlatformUiPolicy(
        platform_id="linux",
        auto_start_tooltip_key="启用后会写入 ~/.config/autostart，登录后自动后台启动。",
        restart_action="normal",
        show_desktop_context_menu=False,
        show_hotkey_focus_guard=True,
    ),
    "macos": PlatformUiPolicy(
        platform_id="macos",
        auto_start_tooltip_key="启用后会写入 LaunchAgents，登录后自动后台启动。",
        restart_action="normal",
        show_desktop_context_menu=False,
        show_hotkey_focus_guard=True,
    ),
}


def get_platform_ui_policy(platform_id: str) -> PlatformUiPolicy:
    normalized = str(platform_id or "").strip().lower()
    try:
        return _POLICIES[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported platform UI policy: {platform_id!r}") from exc
