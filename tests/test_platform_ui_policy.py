from __future__ import annotations

from ui.platform_ui_policy import get_platform_ui_policy


def test_windows_home_can_offer_desktop_context_menu():
    assert get_platform_ui_policy("windows").show_desktop_context_menu is True


def test_non_windows_home_never_offers_windows_desktop_context_menu():
    assert get_platform_ui_policy("linux").show_desktop_context_menu is False
    assert get_platform_ui_policy("macos").show_desktop_context_menu is False
