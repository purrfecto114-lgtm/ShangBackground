from __future__ import annotations

try:
    from PySide6.QtCore import QTimer
except Exception:  # pragma: no cover - import-safe before GUI dependencies exist
    QTimer = None


def _call_startup_callback(callback) -> None:
    if not callable(callback):
        return
    try:
        callback()
    except Exception:
        # Startup scheduling must never prevent the window from reaching the
        # event loop.  Individual callbacks log their own domain-specific errors.
        pass


def schedule_startup_tasks(
    runtime_startup,
    update_startup=None,
    *,
    runtime_delay_ms: int = 700,
    update_delay_ms: int = 1800,
    update_enabled: bool = True,
) -> None:
    """Stage non-critical startup work after the first window paint.

    The runtime callback keeps local IPC, usage reporting and mode restoration out
    of the blocking construction path.  The optional update callback is even more
    delayed and is intended for silent network checks only, so launch remains fast
    and users are not interrupted by transient network failures.
    """
    runtime_delay_ms = max(0, int(runtime_delay_ms))
    update_delay_ms = max(runtime_delay_ms + 500, int(update_delay_ms))
    if QTimer is None:
        _call_startup_callback(runtime_startup)
        return
    QTimer.singleShot(runtime_delay_ms, lambda: _call_startup_callback(runtime_startup))
    if update_enabled and callable(update_startup):
        QTimer.singleShot(update_delay_ms, lambda: _call_startup_callback(update_startup))
