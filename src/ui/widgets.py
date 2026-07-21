"""Reusable application widgets with no business or configuration knowledge."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QMenu,
    QSizePolicy,
    QSpinBox,
)


class ShangComboBox(QComboBox):
    """Compact combo box backed by one guarded menu popup."""

    _active_menu: QMenu | None = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._popup_menu: QMenu | None = None

    @staticmethod
    def _menu_alive(menu) -> bool:
        try:
            return menu is not None and menu.isVisible()
        except (RuntimeError, TypeError):
            return False

    def _clear_popup_menu_ref(self, menu) -> None:
        if self._popup_menu is menu:
            self._popup_menu = None
        if ShangComboBox._active_menu is menu:
            ShangComboBox._active_menu = None
        try:
            QComboBox.hidePopup(self)
        except RuntimeError:
            pass

    def hidePopup(self) -> None:  # noqa: N802 - Qt API
        menu = self._popup_menu
        try:
            if self._menu_alive(menu):
                menu.close()
        finally:
            self._clear_popup_menu_ref(menu)

    def showPopup(self) -> None:  # noqa: N802 - Qt API
        if self.count() <= 0:
            return

        active = ShangComboBox._active_menu
        if self._menu_alive(active):
            same_owner = active.parent() is self
            active.close()
            ShangComboBox._active_menu = None
            if same_owner:
                self._popup_menu = None
                return

        menu = QMenu(self)
        menu.setObjectName("ComboBoxMenu")
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        style_owner = self.window()
        if hasattr(style_owner, "_combo_popup_stylesheet"):
            menu.setStyleSheet(style_owner._combo_popup_stylesheet())
        width = max(176, min(max(self.width(), self.minimumSizeHint().width()), 320))
        menu.setFixedWidth(width)
        current = self.currentIndex()
        for row in range(self.count()):
            action = QAction(self.itemText(row), menu)
            action.setCheckable(True)
            action.setChecked(row == current)
            index = self.model().index(row, self.modelColumn(), self.rootModelIndex())
            action.setEnabled(bool(index.flags() & Qt.ItemFlag.ItemIsEnabled))
            action.triggered.connect(lambda _checked=False, idx=row: self.setCurrentIndex(idx))
            menu.addAction(action)

        item_height = max(36, menu.fontMetrics().height() + 22)
        visible_rows = max(1, min(self.count(), 12))
        target_height = visible_rows * item_height + 12
        if self.count() <= 12:
            menu.setMinimumHeight(target_height)
        else:
            menu.setMaximumHeight(target_height)
        menu.aboutToHide.connect(lambda menu=menu: self._clear_popup_menu_ref(menu))
        self._popup_menu = menu
        ShangComboBox._active_menu = menu

        position = self.mapToGlobal(self.rect().bottomLeft())
        screen = QGuiApplication.screenAt(self.mapToGlobal(self.rect().center())) or QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            menu_size = menu.sizeHint()
            if position.x() + menu_size.width() > available.right():
                position.setX(self.mapToGlobal(self.rect().topRight()).x() - menu_size.width())
            if position.y() + menu_size.height() > available.bottom():
                position.setY(self.mapToGlobal(self.rect().topLeft()).y() - menu_size.height())
            position.setX(max(available.left(), position.x()))
            position.setY(max(available.top(), position.y()))
        menu.popup(position)


class CompactSpinBox(QSpinBox):
    """Fixed-width numeric input for dense option rows."""

    def __init__(self, width: int = 64, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._compact_width = int(width)
        self.setObjectName("CompactNumberSpin")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedWidth(self._compact_width)

    def sizeHint(self):  # noqa: N802 - Qt API
        hint = super().sizeHint()
        hint.setWidth(self._compact_width)
        return hint

    def minimumSizeHint(self):  # noqa: N802 - Qt API
        hint = super().minimumSizeHint()
        hint.setWidth(self._compact_width)
        return hint
