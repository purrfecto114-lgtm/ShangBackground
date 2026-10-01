"""Contract tests: sidebar wallpaper list keyboard accessibility (audit §3.1-5).

The audit found the sidebar's core action — switching wallpaper — was
reachable ONLY through mouseReleaseEvent with a manhattan-length gate: the
whole 897-line module had exactly one focus API call, and it *disabled*
focus (the outside-click shield). Keyboard and screen-reader users were
double-excluded from the app's primary feature.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "ui" / "sidebar.py"


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _methods(cls: ast.ClassDef) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}


def test_thumbnail_item_is_focusable_and_announced():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    source = SRC.read_text(encoding="utf-8")
    cls = _class(tree, "ThumbnailItem")
    methods = _methods(cls)
    build = methods["_build_ui"]
    seg = ast.get_source_segment(source, build) or ""
    assert "setFocusPolicy(Qt.FocusPolicy.StrongFocus)" in seg, (
        "ThumbnailItem must accept focus (was NoFocus by default)"
    )
    assert "setAccessibleName" in seg and "setAccessibleDescription" in seg, (
        "screen readers need the wallpaper name announced"
    )


def test_thumbnail_item_activates_on_enter_and_space():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    source = SRC.read_text(encoding="utf-8")
    cls = _class(tree, "ThumbnailItem")
    methods = _methods(cls)
    assert "keyPressEvent" in methods, "ThumbnailItem must handle key presses"
    seg = ast.get_source_segment(source, methods["keyPressEvent"]) or ""
    assert "Key_Return" in seg and "Key_Enter" in seg and "Key_Space" in seg
    assert "self.clicked.emit(self.img_path)" in seg, (
        "Enter/Space must trigger the same clicked signal as a mouse click"
    )


def test_thumbnail_item_has_visible_focus_style():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    source = SRC.read_text(encoding="utf-8")
    cls = _class(tree, "ThumbnailItem")
    methods = _methods(cls)
    assert "focusInEvent" in methods and "focusOutEvent" in methods
    style = methods["_item_style"]
    seg = ast.get_source_segment(source, style) or ""
    assert '"focus"' in seg, "a dedicated focus style must exist"


def test_sidebar_chains_tab_order_and_arrow_navigation():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    source = SRC.read_text(encoding="utf-8")
    cls = _class(tree, "WallpaperSidebar")
    methods = _methods(cls)
    create = methods["_create_items"]
    seg = ast.get_source_segment(source, create) or ""
    assert "setTabOrder" in seg, "thumbnails must be chained into the tab order"

    key = methods["keyPressEvent"]
    seg = ast.get_source_segment(source, key) or ""
    assert "Key_Up" in seg and "Key_Down" in seg, "Up/Down must move focus between thumbnails"
    assert "Key_Escape" in seg, "Esc-to-close must keep working"


def test_current_wallpaper_receives_initial_focus():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    source = SRC.read_text(encoding="utf-8")
    cls = _class(tree, "WallpaperSidebar")
    seg = ast.get_source_segment(source, _methods(cls)["highlight_current"]) or ""
    assert "setFocus" in seg, "highlight_current must hand keyboard focus to the current wallpaper"
