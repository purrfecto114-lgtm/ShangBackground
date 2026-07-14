#!/usr/bin/env python3
"""Low-risk smoke tests for the three HTML adapter control paths."""
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TREES = ("Windows.ver", "Linux.ver(beta)", "MacOS.ver(alpha)")


def child(tree: str) -> int:
    src = ROOT / tree / "src"
    sys.path.insert(0, str(src))
    module = importlib.import_module("platform_adapters.html_wallpaper")

    original = module._qt_webengine_available
    try:
        module._qt_webengine_available = lambda: False
        ok, message = module.start_html_wallpaper("https://www.example.com/")
        assert ok is False and "Qt WebEngine" in message

        module._qt_webengine_available = lambda: True
        ok, message = module.start_html_wallpaper("not-a-real-wallpaper")
        assert ok is False and message

        with tempfile.TemporaryDirectory() as temp:
            html = Path(temp) / "index.html"
            html.write_text("<!doctype html><title>smoke</title>", encoding="utf-8")
            assert module.validate_html_path(str(html)) is True
            assert module.validate_html_path(html.as_uri()) is True

        log_path = Path(module.get_subprocess_log_path())
        assert log_path.name == "html_wallpaper_subprocess.log"
    finally:
        module._qt_webengine_available = original
    print(f"PASS html adapter: {tree}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child")
    args = parser.parse_args()
    if args.child:
        return child(args.child)

    with tempfile.TemporaryDirectory() as home:
        for tree in TREES:
            env = os.environ.copy()
            env.update({"HOME": home, "USERPROFILE": home, "LOCALAPPDATA": home, "APPDATA": home})
            subprocess.run([sys.executable, __file__, "--child", tree], check=True, env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
