"""Global settings dialog lifecycle and chrome.

The dialog owns close/reject semantics and the autosave affordance.  It does
not know how settings are stored or how pages are built; the main-window
controller supplies content and handles ``about_to_close``.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class GlobalSettingsDialog(QDialog):
    about_to_close = Signal()

    def __init__(self, parent=None, *, autosave_text: str = "", close_text: str = "Close"):
        super().__init__(parent)
        self.setObjectName("GlobalSettingsDialog")
        self.setModal(False)

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(18, 18, 18, 14)
        self._root_layout.setSpacing(10)
        self._content: QWidget | None = None
        self._closing = False

        self.autosave_label = QLabel(autosave_text)
        self.autosave_label.setObjectName("SettingsAutosaveHint")
        self.autosave_label.setProperty("muted", True)
        self.autosave_label.setWordWrap(True)
        self.autosave_label.setAccessibleName(autosave_text)
        self.autosave_label.setVisible(bool(autosave_text.strip()))
        self.close_button: QPushButton | None = None

        if autosave_text.strip() or close_text.strip():
            footer = QHBoxLayout()
            footer.setSpacing(10)
            if autosave_text.strip():
                footer.addWidget(self.autosave_label, 1)
            else:
                footer.addStretch(1)
            if close_text.strip():
                close_button = QPushButton(close_text)
                self.close_button = close_button
                close_button.setObjectName("SettingsCloseButton")
                close_button.setProperty("secondary", True)
                close_button.setDefault(False)
                close_button.setAutoDefault(False)
                close_button.setAccessibleName(close_text)
                close_button.clicked.connect(self.close)
                footer.addWidget(close_button)
            self._root_layout.addLayout(footer)

    def set_content(self, widget: QWidget) -> None:
        if self._content is widget:
            return
        if self._content is not None:
            self._root_layout.removeWidget(self._content)
            self._content.setParent(None)
        self._content = widget
        self._root_layout.insertWidget(0, widget, 1)

    def _begin_close_notification(self) -> bool:
        if self._closing:
            return False
        self._closing = True
        self.about_to_close.emit()
        return True

    def reject(self) -> None:
        """Route Escape through the same pre-close transaction as title-bar close."""
        owns_guard = self._begin_close_notification()
        try:
            super().reject()
        finally:
            if owns_guard:
                self._closing = False

    def closeEvent(self, event):  # noqa: N802 - Qt API
        owns_guard = self._begin_close_notification()
        try:
            super().closeEvent(event)
        finally:
            if owns_guard:
                self._closing = False
