# Branch-local preview widget with explicit dependencies.
from __future__ import annotations

import os

from app.i18n import t
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImageReader, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFrame, QSizePolicy

class PreviewCanvas(QFrame):
    """首页壁纸预览画布。

    只显示真实壁纸缩略图，不再把桌面示意图/文字遮罩叠到预览图上。
    画布尺寸由自身控制，路径、历史列表和按钮全部放在画布外部，避免挤压时互相覆盖。

    Bug 1 fix: 旧版用固定 PREVIEW_HEIGHT=240 + QSizePolicy.Fixed(v) + setMaximumHeight(240)，
    导致预览无法随窗口高度伸缩，加上 preview_box 用 QSizePolicy.Maximum(v) 会让右侧
    列在预览下方留出大块空带。现在改为 Expanding/Expanding + 220-360 高度区间，
    预览会随可用空间伸缩，消除空带。
    """

    PREVIEW_HEIGHT = 280  # 默认/首选高度（实际可在 220-360 间伸缩）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._caption = t("实际壁纸预览")
        # Bug 1 fix: 允许预览高度在 220-360 之间伸缩，填满右侧可用空间。
        self.setMinimumSize(280, 220)
        self.setMaximumHeight(360)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setToolTip(t("当前壁纸预览：不叠加文字或桌面示意图"))

    def sizeHint(self):  # noqa: N802 - Qt API
        return QSize(420, self.PREVIEW_HEIGHT)

    def _load_scaled_pixmap(self, image_path: str) -> QPixmap:
        """按预览控件尺寸读取缩略图，避免每次刷新都把原图完整解码到界面线程。"""
        # Decode near the actual image viewport size.  The widget can grow to
        # 360px, so using PREVIEW_HEIGHT here previously produced a needlessly
        # soft preview at larger window sizes.
        target = QSize(max(1, self.width() - 28), max(1, self.height() - 58)).boundedTo(QSize(900, 720))
        if target.width() <= 1 or target.height() <= 1:
            target = QSize(500, self.PREVIEW_HEIGHT)
        reader = QImageReader(image_path)
        reader.setAutoTransform(True)
        reader.setAllocationLimit(256)  # 限制单张图片解码内存上限 256MB，防止超大图 OOM
        original = reader.size()
        if original.isValid():
            scaled = original.scaled(target, Qt.KeepAspectRatio)
            if scaled.isValid():
                reader.setScaledSize(scaled)
        image = reader.read()
        return QPixmap.fromImage(image) if not image.isNull() else QPixmap()

    def set_preview(self, image_path: str = "", _overlay_path: str = ""):
        # _overlay_path 参数保留为兼容旧调用，但故意不再使用，避免文字/示意图压到壁纸预览上。
        if image_path and os.path.exists(image_path):
            self._pixmap = self._load_scaled_pixmap(image_path)
            self._caption = os.path.basename(image_path) or t("实际壁纸预览")
        else:
            self._pixmap = QPixmap()
            self._caption = t("暂无预览")
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = self.rect().adjusted(0, 0, -1, -1)
        if rect.width() <= 0 or rect.height() <= 0:
            painter.end()
            return

        # Use one path for both fill and stroke.  Filling a square QRect first
        # and then drawing a rounded outline leaves a second, darker contour at
        # every corner.
        try:
            dark = bool(self.window()._theme_is_dark())
        except (AttributeError, RuntimeError):
            dark = False
        outer_fill = QColor("#252638" if dark else "#f8fafc")
        outer_border = QColor("#3d3e56" if dark else "#d8dee9")
        inner_fill = QColor("#1f2032" if dark else "#eef2f7")
        inner_border = QColor("#3d3e56" if dark else "#e5e7eb")
        muted = QColor("#9b9bb0" if dark else "#64748b")

        outer_path = QPainterPath()
        outer_path.addRoundedRect(QRectF(rect), 12.0, 12.0)
        painter.fillPath(outer_path, outer_fill)
        painter.setPen(outer_border)
        painter.drawPath(outer_path)

        image_rect = rect.adjusted(14, 14, -14, -44)
        if image_rect.width() > 0 and image_rect.height() > 0:
            image_path = QPainterPath()
            image_path.addRoundedRect(QRectF(image_rect), 8.0, 8.0)
            painter.fillPath(image_path, inner_fill)

            if not self._pixmap.isNull():
                scaled = (self._pixmap if self._pixmap.size().boundedTo(image_rect.size()) == self._pixmap.size()
                          else self._pixmap.scaled(image_rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                x = image_rect.x() + (image_rect.width() - scaled.width()) // 2
                y = image_rect.y() + (image_rect.height() - scaled.height()) // 2
                painter.save()
                painter.setClipPath(image_path)
                painter.drawPixmap(x, y, scaled)
                painter.restore()
            else:
                painter.setPen(muted)
                painter.drawText(image_rect, Qt.AlignCenter, t("暂无预览"))

            painter.setPen(inner_border)
            painter.drawPath(image_path)

        caption_rect = rect.adjusted(14, rect.height() - 34, -14, -8)
        painter.setPen(muted)
        metrics = painter.fontMetrics()
        caption = metrics.elidedText(self._caption, Qt.ElideMiddle, max(0, caption_rect.width()))
        painter.drawText(caption_rect, Qt.AlignLeft | Qt.AlignVCenter, caption)

        painter.end()
