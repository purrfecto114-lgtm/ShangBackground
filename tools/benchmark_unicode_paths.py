#!/usr/bin/env python3
"""Benchmark ASCII and CJK wallpaper paths in real application-side hot paths.

This does not invoke a host desktop API. It measures path resolution/stat, Qt
preview decoding, URI conversion, and ShangBackground's wallpaper orchestration
with the native boundary replaced by a no-op. The purpose is to catch accidental
filename-dependent copying or encoding conversion in shared Python/Qt code.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "Linux.ver(beta)" / "src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import QApplication
from core import engine as core
from platform_adapters import integration


def median_ms(fn, rounds: int, warmups: int = 3) -> float:
    for _ in range(warmups):
        fn()
    values = []
    for _ in range(rounds):
        started = time.perf_counter_ns()
        fn()
        values.append((time.perf_counter_ns() - started) / 1_000_000)
    return statistics.median(values)


def main() -> int:
    QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory(prefix="shang-unicode-bench-") as td:
        folder = Path(td)
        ascii_path = folder / "wallpaper_3840x2160.bmp"
        cjk_path = folder / "中文壁纸_3840x2160.bmp"
        # BMP is intentionally uncompressed (~24 MiB) so the benchmark also
        # represents the large-file copy penalty of the removed Windows bridge.
        Image.new("RGB", (3840, 2160), "#527aa5").save(ascii_path)
        cjk_path.write_bytes(ascii_path.read_bytes())

        def preview(path: Path) -> None:
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            reader.setScaledSize(QSize(640, 360))
            image = reader.read()
            if image.isNull():
                raise RuntimeError(reader.errorString())

        originals = {
            "configure_fit_mode": core.configure_fit_mode,
            "set_wallpaper_platform": core.set_wallpaper_platform,
            "refresh_shell_ui": core.refresh_shell_ui,
            "save_config": core.save_config,
            "is_video_wallpaper_running": core.is_video_wallpaper_running,
            "_queue_ui_preview_update": core._queue_ui_preview_update,
            "log": core.log,
        }
        try:
            core.configure_fit_mode = lambda *_a, **_k: None
            core.set_wallpaper_platform = lambda *_a, **_k: None
            core.refresh_shell_ui = lambda: None
            core.save_config = lambda: True
            core.is_video_wallpaper_running = lambda: False
            core._queue_ui_preview_update = lambda *_a, **_k: None
            core.log = lambda *_a, **_k: None

            def orchestrate(path: Path) -> None:
                core.config = core.get_default_config()
                core.config["mode"] = "图片"
                if not core.set_wallpaper_direct(str(path)):
                    raise RuntimeError("set_wallpaper_direct returned false")

            rows = {}
            for label, path in (("ascii", ascii_path), ("cjk", cjk_path)):
                rows[label] = {
                    "path_resolve_stat_ms": median_ms(lambda p=path: (p.resolve(), p.stat()), 3000, 30),
                    "qt_preview_decode_ms": median_ms(lambda p=path: preview(p), 25, 3),
                    "file_uri_roundtrip_ms": median_ms(
                        lambda p=path: integration._path_from_uri(integration._file_uri(str(p))), 3000, 30
                    ),
                    "app_orchestration_ms": median_ms(lambda p=path: orchestrate(p), 300, 10),
                }
        finally:
            for name, value in originals.items():
                setattr(core, name, value)

        ratios = {
            key: rows["cjk"][key] / rows["ascii"][key] if rows["ascii"][key] else 0.0
            for key in rows["ascii"]
        }
        legacy_copy_ms = None
        shm = Path("/dev/shm")
        if shm.is_dir() and shm.stat().st_dev != folder.stat().st_dev:
            source = shm / f"shang_unicode_copy_{os.getpid()}.bmp"
            destination = folder / "legacy_bridge_copy.bmp"
            try:
                source.write_bytes(cjk_path.read_bytes())

                def legacy_copy() -> None:
                    destination.unlink(missing_ok=True)
                    shutil.copy2(source, destination)

                legacy_copy_ms = median_ms(legacy_copy, 5, 1)
            finally:
                source.unlink(missing_ok=True)
                destination.unlink(missing_ok=True)

        result = {
            "host": sys.platform,
            "python": sys.version.split()[0],
            "file_bytes": ascii_path.stat().st_size,
            "median_ms": rows,
            "cjk_over_ascii_ratio": ratios,
            "removed_legacy_cross_filesystem_copy_ms": legacy_copy_ms,
            "note": "native desktop APIs are excluded; Windows fallback path is separately contract-tested with the exact Unicode path",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        # Allow ordinary timing noise but reject the old class of filename-only
        # slow path (copy/transcode), which would be many times slower.
        for key, ratio in ratios.items():
            if ratio > 3.0 and rows["cjk"][key] - rows["ascii"][key] > 1.0:
                raise AssertionError((key, rows, ratios))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
