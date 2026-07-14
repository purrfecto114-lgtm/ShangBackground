#!/usr/bin/env python3
"""Regression checks for the single-layer rounded-corner rendering fix."""
from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TREES = (
    ROOT / "Windows.ver",
    ROOT / "Linux.ver(beta)",
    ROOT / "MacOS.ver(alpha)",
)


def check_sources() -> None:
    for tree in TREES:
        main = (tree / "src/ui/main_window.py").read_text(encoding="utf-8")
        preview = (tree / "src/ui/preview_canvas.py").read_text(encoding="utf-8")
        label = tree.name

        required = (
            'scroll.setObjectName("SettingsPageScroll")',
            'widget.setObjectName("SettingsPageSurface")',
            "widget.setAutoFillBackground(False)",
            "background-clip: padding",
            "QScrollArea#SettingsPageScroll > QWidget",
            'selected_bg = "#3a3a50" if brightness >= 230 else color',
        )
        for token in required:
            if token not in main:
                raise AssertionError(f"{label}: missing rounding fix token: {token}")

        if "QDialog#GlobalSettingsDialog { background-color: __BG_MAIN__; border-radius" in main:
            raise AssertionError(f"{label}: top-level settings dialog still has a second QSS radius")
        if "QMenu { background-color: __BG_WIDGET__; color: __FG_PRIMARY__; border: 1px solid __BORDER__; border-radius" in main:
            raise AssertionError(f"{label}: top-level menu still has a second QSS radius")

        for forbidden in ("painter.fillRect(", "painter.drawRoundedRect("):
            if forbidden in preview:
                raise AssertionError(f"{label}: preview still mixes square fill and rounded outline")
        for token in ("QPainterPath", "painter.setClipPath(image_path)", "self.height() - 58"):
            if token not in preview:
                raise AssertionError(f"{label}: preview fix missing: {token}")


def check_preview_render() -> None:
    try:
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
        from PySide6.QtWidgets import QApplication, QWidget
    except ImportError:
        print("SKIP preview render: PySide6 is not installed")
        return

    src = TREES[1] / "src"
    sys.path.insert(0, str(src))
    try:
        from ui.preview_canvas import PreviewCanvas

        app = QApplication.instance() or QApplication([])

        class Host(QWidget):
            def _theme_is_dark(self) -> bool:
                return False

        host = Host()
        host.resize(340, 260)
        canvas = PreviewCanvas(host)
        canvas.setGeometry(10, 10, 320, 240)
        canvas._pixmap = QPixmap(200, 160)
        canvas._pixmap.fill(QColor("#ff0000"))

        image = QImage(canvas.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        canvas.render(painter, QPoint())
        painter.end()

        # The outer top-left corner must remain transparent; a square under-fill
        # would make this pixel opaque. The image viewport corner must also not
        # leak the red pixmap through its rounded clip.
        corner = image.pixelColor(0, 0)
        center_outer = image.pixelColor(160, 4)
        if corner == center_outer:
            raise AssertionError("preview outer corner is still square-filled")
        if image.pixelColor(14, 14).red() > 220 and image.pixelColor(14, 14).green() < 40:
            raise AssertionError("preview pixmap leaks through the rounded image corner")
        if image.pixelColor(160, 100).alpha() == 0:
            raise AssertionError("preview center was not painted")
        app.processEvents()
    finally:
        try:
            sys.path.remove(str(src))
        except ValueError:
            pass


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    check_sources()
    check_preview_render()
    print("PASS rounded-corner regression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
