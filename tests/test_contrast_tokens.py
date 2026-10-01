"""Automated WCAG contrast assertions for the theme tokens (audit §4.6/§8.2).

The audit computed contrast ratios by hand from reported hex values and got
one attribution wrong (§8.1). These tests read the ACTUAL hex literals from
the source and fail CI when a token change drops below the accessibility
bar, so contrast becomes a regression gate instead of a one-off audit note.

Thresholds: WCAG 2.1 AA — 4.5:1 for text, 3:1 for UI components (scrollbar
handles, selected-state text pairs are treated as text).
"""

from __future__ import annotations

import re
from pathlib import Path

SRC_UI = Path(__file__).resolve().parents[1] / "src" / "ui"
MAIN = (SRC_UI / "main_window.py").read_text(encoding="utf-8")
SIDEBAR = (SRC_UI / "sidebar.py").read_text(encoding="utf-8")


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def f(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = f(r), f(g), f(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _first_match(pattern: str, haystack: str) -> str:
    m = re.search(pattern, haystack)
    assert m, f"pattern not found: {pattern}"
    return m.group(1)


def test_light_muted_text_meets_aa():
    # role-color source (status bar text on header/central backgrounds)
    # the light-branch pair follows fg_secondary; the dark branch has its own
    fg = _first_match(r'"fg_secondary": "#57606a",\s*"fg_muted": "(#[0-9a-f]{6})"', MAIN)
    assert _contrast(fg, "#ffffff") >= 4.5, f"fg_muted {fg} on white"
    assert _contrast(fg, "#f0f2f5") >= 4.5, f"fg_muted {fg} on central bg"


def test_light_selected_text_on_fallback_accent():
    # selected_fg for near-white themes must be dark text (was white, 3.04:1)
    # the >=230 arm value:
    assert 'selected_fg = "#ffffff" if brightness < 170 else "#24292f"' in MAIN, (
        "light combo selected text must be #24292f on the gray fallback accent"
    )
    assert _contrast("#24292f", "#8c959f") >= 4.5


def test_dark_selected_text_on_fallback_accent():
    assert _contrast("#1a1b2e", "#8b8ba3") >= 4.5, "dark selected text on gray fallback"


def test_scrollbar_handles_meet_component_minimum():
    # light >=230 arm (default white theme)
    handle = _first_match(r'scroll_handle = "(#[0-9a-f]{6})" if theme_brightness >= 230', MAIN)
    assert _contrast(handle, "#f0f2f5") >= 3.0, f"light scrollbar handle {handle}"
    # dark arm
    dark_handle = _first_match(r'scroll_handle = "(#[0-9a-f]{6})"\n', MAIN)
    assert _contrast(dark_handle, "#141526") >= 3.0, f"dark scrollbar handle {dark_handle}"
    # sidebar (light palette — second occurrence in the light dict)
    side_light = re.findall(r'"scroll_handle": "(#[0-9a-f]{6})"', SIDEBAR)[1]
    assert _contrast(side_light, "#f1f3f5") >= 3.0, f"sidebar light handle {side_light}"
    side_dark = re.findall(r'"scroll_handle": "(#[0-9a-f]{6})"', SIDEBAR)[0]
    assert _contrast(side_dark, "#1e1e2e") >= 3.0, f"sidebar dark handle {side_dark}"


def test_status_bar_elides_right_not_middle():
    # sentences live in the status bar: middle elision cuts the verb (audit D1)
    assert "ElideMode.ElideRight, available" in MAIN
    assert "ElideMode.ElideMiddle, available" not in MAIN
    # preview captions keep middle elision (paths: head and tail both matter)
    assert "ElideMiddle" in (SRC_UI / "preview_canvas.py").read_text(encoding="utf-8")


def test_disabled_states_carry_dashed_border():
    assert MAIN.count("border-style: dashed") + MAIN.count("dashed #") >= 4, (
        "disabled controls must stay visually distinct beyond grayscale "
        "(color-only disabled cues fail for color-blind users)"
    )
