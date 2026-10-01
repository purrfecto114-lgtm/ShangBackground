"""Contract tests: destructive-action visual safety (audit §4.1 hard blocker).

The audit found ``QPushButton[danger]`` was never defined in any stylesheet —
danger_bg existed as a theme role but no selector referenced it, so「恢复出厂
设置」(wipes all configuration) looked identical to every other button and sat
in the left-most (primary) position of the safe-mode page. These tests pin the
fix: the danger selector exists in both theme branches, and the safe-mode
button row puts the destructive action at the far right behind a copy-details
button.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "ui" / "main_window.py"


def _method_source(class_name: str, method_name: str) -> str:
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    source = SRC.read_text(encoding="utf-8")
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    seg = ast.get_source_segment(source, item)
                    assert seg is not None
                    return seg
    raise AssertionError(f"method {class_name}.{method_name} not found")


def test_danger_selector_exists_in_both_theme_branches():
    seg = _method_source("_SharedShangBackgroundWindow", "_rebuild_stylesheet")
    # dark branch (f-string block) and light branch (literal block)
    assert seg.count('QPushButton[danger="true"]') >= 8, (
        "danger button rules must cover base/hover/pressed/disabled in both the dark and light template branches"
    )
    # AA-checked colors: light #cf222e on white text 5.36:1, dark #da3633 4.61:1
    assert "#cf222e" in seg and "#da3633" in seg


def test_safe_mode_button_order_and_copy_button():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    source = SRC.read_text(encoding="utf-8")
    # find the function that builds the safe-mode page (contains both buttons)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        seg2 = ast.get_source_segment(source, node) or ""
        if 't("复制错误信息")' in seg2 and 'reset_btn.setProperty("danger", True)' in seg2:
            # order: stretch first, reset added last
            stretch_pos = seg2.find("buttons.addStretch(1)")
            reset_pos = seg2.find("buttons.addWidget(reset_btn)")
            copy_pos = seg2.find("buttons.addWidget(copy_btn)")
            close_pos = seg2.find("buttons.addWidget(close_btn)")
            assert -1 not in (stretch_pos, reset_pos, copy_pos, close_pos)
            assert stretch_pos < close_pos < copy_pos < reset_pos, (
                "safe-mode layout must be [stretch][close][copy error details]"
                "[factory reset] — destructive action at the far right"
            )
            break
    else:
        raise AssertionError("safe-mode builder function not found")


def test_danger_property_still_set_on_factory_reset_buttons():
    source = SRC.read_text(encoding="utf-8")
    assert source.count('setProperty("danger", True)') >= 3, (
        "factory-reset entry points must keep the danger property so the danger styling applies"
    )
