#!/usr/bin/env python3
"""Regression checks for the cross-platform HTML first-frame presentation gate."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TREES = ("Windows.ver", "Linux.ver(beta)", "MacOS.ver(alpha)")


def static_contracts() -> None:
    required = (
        "class _FirstFrameGate",
        "requestAnimationFrame(function(){requestAnimationFrame(function()",
        "view.loadStarted.connect(_on_load_started)",
        "frame_gate.finish_navigation(True)",
        "frame_gate.begin_navigation()",
        "renderProcessTerminated.connect(_on_render_process_terminated)",
    )
    forbidden = ("_restore_opacity", "_opacity_restored")
    for tree in TREES:
        path = ROOT / tree / "src" / "platform_adapters" / "run_html_wallpaper.py"
        text = path.read_text(encoding="utf-8")
        for item in required:
            assert item in text, f"{tree}: missing first-frame contract {item!r}"
        for item in forbidden:
            assert item not in text, f"{tree}: stale opacity timer {item!r}"


def dynamic_gate_contract() -> None:
    src = ROOT / "Linux.ver(beta)" / "src"
    sys.path.insert(0, str(src))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    module = importlib.import_module("platform_adapters.run_html_wallpaper")

    tasks: list[tuple[int, object]] = []

    class FakeTimer:
        @staticmethod
        def singleShot(delay: int, callback) -> None:
            tasks.append((int(delay), callback))

    class FakeView:
        def __init__(self) -> None:
            self.opacity: list[float] = []

        def setWindowOpacity(self, value: float) -> None:
            self.opacity.append(float(value))

    class FakePage:
        def __init__(self) -> None:
            self.ready = False

        def runJavaScript(self, script: str, callback=None) -> None:
            if "data-shangbg-first-frame" in script and "requestAnimationFrame" in script:
                self.ready = False
            if callback is not None:
                callback(self.ready)

    original_timer = module.QTimer
    module.QTimer = FakeTimer
    try:
        view = FakeView()
        page = FakePage()
        visible: list[str] = []
        gate = module._FirstFrameGate(view, page, lambda: visible.append("visible"))

        gate.begin_navigation()
        gate.finish_navigation(True)
        assert view.opacity == [0.0]
        assert visible == []

        page.ready = True
        short = [item for item in tasks if item[0] == 30]
        assert short, tasks
        short[0][1]()
        assert view.opacity == [0.0, 1.0]
        assert visible == ["visible"]

        # A fallback queued for an older generation must never reveal a later load.
        stale_fallback = [item for item in tasks if item[0] == 1800][0][1]
        gate.begin_navigation()
        stale_fallback()
        assert view.opacity == [0.0, 1.0, 0.0]
        assert visible == ["visible"]
    finally:
        module.QTimer = original_timer
        sys.path.remove(str(src))


def main() -> int:
    static_contracts()
    dynamic_gate_contract()
    print("PASS HTML first-frame gate contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
