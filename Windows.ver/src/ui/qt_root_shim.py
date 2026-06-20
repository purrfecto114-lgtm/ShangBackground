# Qt compatibility shim with explicit dependencies.
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from ui.main_window import ShangBackgroundWindow

class QtRootShim(QObject):
    """给核心模块提供最小 root.after/deiconify 兼容层。"""

    def __init__(self, window: "ShangBackgroundWindow"):
        super().__init__(window)
        self.window = window
        self._timers: dict[str, QTimer] = {}
        self._seq = 0

    def after(self, ms: int, func=None, *args):
        self._seq += 1
        timer_id = f"qt-after-{self._seq}"
        timer = QTimer(self)
        timer.setSingleShot(True)

        def _fire():
            self._timers.pop(timer_id, None)
            if callable(func):
                func(*args)

        timer.timeout.connect(_fire)
        self._timers[timer_id] = timer
        timer.start(max(0, int(ms)))
        return timer_id

    def after_cancel(self, timer_id):
        timer = self._timers.pop(str(timer_id), None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def deiconify(self):
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def state(self, value=None):
        if value == "normal":
            self.deiconify()
        return "normal"

    def lift(self):
        self.window.raise_()

    def focus_force(self):
        self.window.activateWindow()


    def show_message(self, title, message, level="info"):
        # Delegate to the shared ui.dialog_style helper so the dialog
        # inherits the application-level QSS cascade (no need to copy
        # ``_theme_stylesheet`` onto the box any more — that historic
        # pattern was a source of style drift across the three platforms).
        from ui.dialog_style import show_message as _show_message
        _show_message(self.window, title, message, level=level)

    def winfo_id(self):
        return int(self.window.winId())

    def winfo_exists(self):
        return True

    def winfo_screenwidth(self):
        screen = QApplication.primaryScreen()
        return screen.geometry().width() if screen else 1920

    def winfo_screenheight(self):
        screen = QApplication.primaryScreen()
        return screen.geometry().height() if screen else 1080

    def quit(self):
        QApplication.instance().quit()

    def destroy(self):
        self.window.close()
