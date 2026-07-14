# Qt compatibility shim with explicit dependencies.
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from ui.main_window import ShangBackgroundWindow

class QtRootShim(QObject):
    """给核心模块提供最小 root.after/deiconify 兼容层。

    ``root.after`` is often called from worker threads (global hotkeys,
    wallpaper operations and IPC callbacks).  QTimer must be created in the
    QObject's owning Qt thread, so scheduling/cancelling is marshalled through
    queued signals before touching the timer map.
    """

    _after_requested = Signal(str, int, object, object)
    _cancel_requested = Signal(str)

    def __init__(self, window: "ShangBackgroundWindow"):
        super().__init__(window)
        self.window = window
        self._timers: dict[str, QTimer] = {}
        self._seq = 0
        self._after_requested.connect(self._schedule_after, Qt.ConnectionType.QueuedConnection)
        self._cancel_requested.connect(self._cancel_after, Qt.ConnectionType.QueuedConnection)

    def after(self, ms: int, func=None, *args):
        self._seq += 1
        timer_id = f"qt-after-{self._seq}"
        try:
            delay = max(0, int(ms))
        except Exception:
            delay = 0
        self._after_requested.emit(timer_id, delay, func, args)
        return timer_id

    @Slot(str, int, object, object)
    def _schedule_after(self, timer_id: str, ms: int, func, args_obj):
        old_timer = self._timers.pop(str(timer_id), None)
        if old_timer is not None:
            old_timer.stop()
            old_timer.deleteLater()

        timer = QTimer(self)
        timer.setSingleShot(True)

        def _fire():
            self._timers.pop(timer_id, None)
            try:
                timer.deleteLater()
            except Exception:
                pass
            if callable(func):
                args = args_obj if isinstance(args_obj, tuple) else ()
                func(*args)

        timer.timeout.connect(_fire)
        self._timers[timer_id] = timer
        timer.start(max(0, int(ms)))

    def after_cancel(self, timer_id):
        self._cancel_requested.emit(str(timer_id))

    @Slot(str)
    def _cancel_after(self, timer_id: str):
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
