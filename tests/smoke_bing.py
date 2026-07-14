#!/usr/bin/env python3
"""Offline tests for the stdlib Bing downloader and download safeguards."""
from __future__ import annotations

import argparse
import importlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TREES = ("Windows.ver", "Linux.ver(beta)", "MacOS.ver(alpha)")


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, content_type: str = "image/jpeg"):
        super().__init__(payload)
        self.headers = {"content-type": content_type, "content-length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def child(tree: str) -> int:
    sys.path.insert(0, str(ROOT / tree / "src"))
    bing = importlib.import_module("services.bing")
    assert bing.BingDownloader._is_allowed_bing_url("https://www.bing.com/a.jpg")
    assert bing.BingDownloader._is_allowed_bing_url("https://cn.bing.com/a.jpg")
    assert not bing.BingDownloader._is_allowed_bing_url("http://www.bing.com/a.jpg")
    assert not bing.BingDownloader._is_allowed_bing_url("https://bing.com.example.org/a.jpg")

    with tempfile.TemporaryDirectory() as temp:
        downloader = bing.BingDownloader(cache_dir=temp)
        payload = b"\xff\xd8" + (b"x" * 2048)
        downloader._open = lambda *args, **kwargs: FakeResponse(payload)
        info = bing.WallpaperInfo(
            id="abc",
            title="offline",
            url="https://www.bing.com/fake.jpg",
            copyright="",
            date="20260711",
            resolution="1920x1080",
        )
        result = downloader.download_wallpaper(info)
        assert result and Path(result).is_file() and Path(result).stat().st_size == len(payload)
    print(f"PASS Bing downloader: {tree}")
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
