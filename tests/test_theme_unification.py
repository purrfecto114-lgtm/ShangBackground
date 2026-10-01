"""Contract tests: Windows mixin stylesheet copy unification.

The audit found 629/878 lines (72%) of ``_WindowsMainWindowMixin`` were
86-97% copies of the shared base methods, shadowing them via MRO — so the
platform with the most real-user coverage ran the copies, and any theme fix
applied to the shared base never reached Windows users.

The unification deleted the six copy methods and folded the genuine
platform differences into explicit ``core.IS_WINDOWS`` branches inside the
shared implementations. These tests pin that state so the copies cannot
silently regrow.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "ui" / "main_window.py"

UNIFIED_METHODS = (
    "_init_icon",
    "_render_svg_to_pixmap",
    "_combo_popup_stylesheet",
    "_extra_theme_qss",
    "_rebuild_stylesheet",
    "_settings_nav_stylesheet",
)


def _class_defs(tree: ast.Module) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            out.setdefault(node.name, set()).update(methods)
    return out


def test_windows_mixin_no_longer_redefines_unified_methods():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    classes = _class_defs(tree)
    mixin = classes.get("_WindowsMainWindowMixin", set())
    regrown = sorted(mixin & set(UNIFIED_METHODS))
    assert not regrown, (
        f"_WindowsMainWindowMixin re-defines {regrown}; the shared base owns "
        "these methods now — fold platform differences into explicit "
        "core.IS_WINDOWS branches instead of shadowing copies"
    )


def test_shared_base_still_owns_the_unified_methods():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    classes = _class_defs(tree)
    shared = classes.get("_SharedShangBackgroundWindow", set())
    missing = sorted(set(UNIFIED_METHODS) - shared)
    assert not missing, f"shared base lost unified methods: {missing}"


def test_platform_branches_preserved_in_shared_base():
    """The two genuinely platform-dependent behaviors keep explicit
    ``core.IS_WINDOWS`` branches (icon format preference, QSS icon URL form)."""
    source = SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)
    shared = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "_SharedShangBackgroundWindow")
    init_icon = next(n for n in shared.body if isinstance(n, ast.FunctionDef) and n.name == "_init_icon")
    # icon order branch present
    src_segment = ast.get_source_segment(source, init_icon) or ""
    assert '"LOGO.ico" if core.IS_WINDOWS else "LOGO.png"' in src_segment
    assert '"LOGO.png" if core.IS_WINDOWS else "LOGO.ico"' in src_segment

    extra = next(n for n in shared.body if isinstance(n, ast.FunctionDef) and n.name == "_extra_theme_qss")
    seg = ast.get_source_segment(source, extra) or ""
    assert "if core.IS_WINDOWS:" in seg, "extra_theme_qss lost the Windows icon-URL branch"
    assert "as_posix()" in seg


def test_combo_popup_metrics_are_cross_style_consistent():
    """The QListView-based popup and the QMenu-based popup must render the
    same item metrics (the audit's 'pixel-identical claim that wasn't')."""
    source = SRC.read_text(encoding="utf-8")
    # QListView-based popup (template) and QMenu-based popup (method) metrics
    assert "min-height: 28px;" in source and "padding: 4px 12px;" in source
    # The stale 30px / 6px 12px variant must be gone.
    assert "min-height: 30px; padding: 6px 12px" not in source


def test_desktop_foreground_remains_platform_polymorphic():
    """_is_desktop_foreground is the ONE legitimately different pair (real
    Win32 window-class detection vs X11/Wayland delegation) — it must stay
    overridden in the Windows mixin."""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    classes = _class_defs(tree)
    assert "_is_desktop_foreground" in classes.get("_WindowsMainWindowMixin", set())
    assert "_is_desktop_foreground" in classes.get("_SharedShangBackgroundWindow", set())
