# Branch-local preview widget with explicit dependencies.
from __future__ import annotations

import os

from app.i18n import t
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QSizePolicy

class PreviewCanvas(QFrame):
    """首页壁纸预览画布。

    只显示真实壁纸缩略图，不再把桌面示意图/文字遮罩叠到预览图上。
    画布尺寸由自身控制，路径、历史列表和按钮全部放在画布外部，避免挤压时互相覆盖。
    """

    PREVIEW_HEIGHT = 280

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._caption = t("实际壁纸预览")
        self.setMinimumSize(360, self.PREVIEW_HEIGHT)
        self.setMaximumHeight(self.PREVIEW_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip(t("当前壁纸预览：不叠加文字或桌面示意图"))

    def sizeHint(self):  # noqa: N802 - Qt API
        return QSize(500, self.PREVIEW_HEIGHT)

    def _load_scaled_pixmap(self, image_path: str) -> QPixmap:
        """按预览控件尺寸读取缩略图，避免每次刷新都把原图完整解码到界面线程。"""
        target = self.size().boundedTo(QSize(900, self.PREVIEW_HEIGHT))
        if target.width() <= 0 or target.height() <= 0:
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
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.fillRect(rect, QColor("#f8fafc"))
        painter.setPen(QColor("#d8dee9"))
        painter.drawRoundedRect(rect, 12, 12)

        image_rect = rect.adjusted(14, 14, -14, -44)
        painter.fillRect(image_rect, QColor("#eef2f7"))
        painter.setPen(QColor("#e5e7eb"))
        painter.drawRoundedRect(image_rect, 8, 8)

        if not self._pixmap.isNull():
            scaled = self._pixmap if self._pixmap.size().boundedTo(image_rect.size()) == self._pixmap.size() else self._pixmap.scaled(image_rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = image_rect.x() + (image_rect.width() - scaled.width()) // 2
            y = image_rect.y() + (image_rect.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            painter.setPen(QColor("#6b7280"))
            painter.drawText(image_rect, Qt.AlignCenter, t("暂无预览"))

        caption_rect = rect.adjusted(14, rect.height() - 34, -14, -8)
        painter.setPen(QColor("#64748b"))
        metrics = painter.fontMetrics()
        caption = metrics.elidedText(self._caption, Qt.ElideMiddle, caption_rect.width())
        painter.drawText(caption_rect, Qt.AlignLeft | Qt.AlignVCenter, caption)

        painter.end()
