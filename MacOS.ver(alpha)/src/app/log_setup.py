"""Centralized logging setup for ShangBackground.

This module configures the standard `logging` framework with:
  - A main rotating file `shangbackground.log` (DEBUG/INFO level, kept 7 days).
  - An HTML-wallpaper-specific file `html_wallpaper.log` (kept 7 days).
  - An error-only file `error.log` (kept 14 days).
  - An optional console handler enabled in source mode or when the user enables
    verbose logging through settings.
  - An in-memory ring buffer (default 1000 entries) that the in-app log page
    reads from for instant display without file I/O.
  - A Qt message handler that routes `qDebug`/`qWarning`/`qCritical`/`qFatal`
    (including `qt.svg: Cannot open file ...`) into the same logging system,
    so Qt-internal warnings show up in the in-app log page instead of being
    silently lost to stderr.

Public API:
  - `configure_logging(log_dir=None, level="INFO", console=False)`: initialize
    once at startup. Idempotent — safe to call multiple times.
  - `get_logger(name=None)`: return a `logging.Logger` whose default propagation
    is wired to our handlers. Use this everywhere instead of `print()`.
  - `get_html_wallpaper_logger()`: return the dedicated HTML-wallpaper logger.
  - `LegacyAdapter`: a thin shim that exposes the same `log(msg, level=, exc_info=)`
    signature as the old `core.log()` function, so existing call sites can be
    migrated mechanically without breaking the public API.
  - `get_recent_logs(limit=200, level=None, search=None)`: return up to `limit`
    recent log entries from the in-memory ring buffer, optionally filtered by
    minimum level and/or a substring search. Each entry is a dict with
    `timestamp`, `level`, `logger`, `message` keys.
  - `install_qt_message_handler()`: install a handler that captures Qt's own
    debug/warning/critical messages and forwards them to the logging system.
    Safe to call multiple times.

Log file rotation uses `logging.handlers.TimedRotatingFileHandler` with
`when='midnight'`, `backupCount=7` (14 for the error log). On Windows the
file rotation may briefly hold a lock; we use small atomic appends.

The log directory defaults to `app.paths.user_data_dir("ShangBackground")/logs`
on every platform, which is `%LOCALAPPDATA%/ShangBackground/logs` on Windows,
`~/.config/shangbackground/logs` on Linux, and
`~/Library/Application Support/ShangBackground/logs` on macOS.
"""
from __future__ import annotations

import collections
import logging
import logging.handlers
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

# Defer the import to avoid circular imports at module load time.
def _resolve_log_dir(explicit: Optional[str] = None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p
    try:
        from app.paths import user_data_dir  # type: ignore
        base = Path(user_data_dir("ShangBackground"))
    except Exception:
        # Last-resort fallback
        if sys.platform.startswith("win"):
            base = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / "ShangBackground"
        elif sys.platform == "darwin":
            base = Path(os.path.expanduser("~/Library/Application Support")) / "ShangBackground"
        else:
            base = Path(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))) / "shangbackground"
    out = base / "logs"
    try:
        out.mkdir(parents=True, exist_ok=True)
    except Exception:
        out = Path(tempfile.gettempdir()) / "ShangBackground" / "logs"
        out.mkdir(parents=True, exist_ok=True)
    return out


_LOCK = threading.RLock()
_CONFIGURED = False
_LOG_DIR: Optional[Path] = None
_FILE_HANDLER: Optional[logging.Handler] = None
_HTML_HANDLER: Optional[logging.Handler] = None
_ERROR_HANDLER: Optional[logging.Handler] = None
_CONSOLE_HANDLER: Optional[logging.Handler] = None
# Whether file handlers are currently attached.  Reflects the user-facing
# "log_enabled" setting: when False, NO log files are written to disk so the
# app leaves no trace on the user's machine.  The ring buffer + console
# handler still work for in-app diagnostics.
_FILES_ENABLED = False
# Cached formatter + log_dir so set_file_logging_enabled() can (re)attach
# handlers without re-running configure_logging() from scratch.
_FORMATTER: Optional[logging.Formatter] = None

# In-memory ring buffer for the in-app log page.  Default 1000 entries (~250KB
# worst case).  Each entry is a dict {timestamp, level, logger, message}.
# Lock-protected; safe to read from the GUI thread while the logging thread
# writes.  Sized to keep ~1-2 days of typical usage without unbounded growth.
_RING_BUFFER_SIZE = 1000
_RING_BUFFER: collections.deque = collections.deque(maxlen=_RING_BUFFER_SIZE)
_RING_LOCK = threading.Lock()
_QT_HANDLER_INSTALLED = False

# Format: [2026-07-05 12:34:56] [INFO] [module.name] message
_LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _RingBufferHandler(logging.Handler):
    """Logging handler that appends formatted records to the in-memory ring
    buffer for instant display in the in-app log page.

    Stores raw fields (timestamp, level, logger name, message) rather than
    pre-formatted strings so the GUI can re-render with different filters
    without re-parsing.  Thread-safe via `_RING_LOCK`.
    """

    def emit(self, record: logging.LogRecord) -> None:  # noqa: A003
        try:
            entry = {
                "timestamp": time.strftime(
                    _DATE_FORMAT, time.localtime(record.created)
                ),
                "epoch": float(record.created),
                "level": record.levelname,
                "level_no": int(record.levelno),
                "logger": record.name or "",
                "message": self.format(record) if False else record.getMessage(),
            }
            # Include exception info if present, so the GUI can show tracebacks.
            if record.exc_info:
                import traceback
                entry["traceback"] = "".join(
                    traceback.format_exception(*record.exc_info)
                )
            with _RING_LOCK:
                _RING_BUFFER.append(entry)
        except Exception:
            # Never let logging infrastructure crash the app.
            pass


def get_recent_logs(
    limit: int = 200,
    *,
    min_level: Optional[str] = None,
    search: Optional[str] = None,
) -> list[dict]:
    """Return up to `limit` recent log entries from the ring buffer.

    Args:
        limit: Maximum number of entries to return (most recent last).
        min_level: If given (e.g. "WARNING"), only return entries at or above
            this level.  Case-insensitive.
        search: If given, only return entries whose message or logger name
            contains this substring (case-insensitive).

    Returns:
        List of dicts with keys: timestamp, epoch, level, level_no, logger,
        message, traceback (optional).
    """
    level_no = 0
    if min_level:
        level_no = getattr(logging, str(min_level).upper(), 0)
    search_lower = search.lower() if search else None
    with _RING_LOCK:
        snapshot = list(_RING_BUFFER)
    out: list[dict] = []
    for entry in snapshot:
        if level_no and int(entry.get("level_no", 0)) < level_no:
            continue
        if search_lower:
            haystack = (
                str(entry.get("message", "")) + " " + str(entry.get("logger", ""))
            ).lower()
            if search_lower not in haystack:
                continue
        out.append(entry)
    if limit > 0:
        out = out[-limit:]
    return out


def purge_log_files() -> tuple[int, int]:
    """Delete all persisted application log files from the per-user log directory.

    Active file handlers are detached and closed first so Windows does not keep
    the files locked. Returns ``(removed_files, failed_files)``.
    """
    global _FILE_HANDLER, _HTML_FILE_HANDLER, _ERROR_FILE_HANDLER

    handlers = tuple(
        handler
        for handler in (_FILE_HANDLER, _HTML_FILE_HANDLER, _ERROR_FILE_HANDLER)
        if handler is not None
    )
    root = logging.getLogger()
    for handler in handlers:
        try:
            root.removeHandler(handler)
        except Exception:
            pass
        try:
            logging.getLogger("platform_adapters.html_wallpaper").removeHandler(handler)
        except Exception:
            pass
        try:
            handler.flush()
            handler.close()
        except Exception:
            pass

    _FILE_HANDLER = None
    _HTML_FILE_HANDLER = None
    _ERROR_FILE_HANDLER = None

    removed = 0
    failed = 0
    log_dir = _LOG_DIR or _resolve_log_dir()
    if log_dir.is_dir():
        for path in sorted(log_dir.rglob("*"), reverse=True):
            try:
                if path.is_file() or path.is_symlink():
                    path.unlink()
                    removed += 1
                elif path.is_dir():
                    path.rmdir()
            except OSError:
                failed += 1
        try:
            log_dir.rmdir()
        except OSError:
            pass

    clear_recent_logs()
    return removed, failed


def clear_recent_logs() -> None:
    """Empty the in-memory ring buffer.  Does NOT touch log files."""
    with _RING_LOCK:
        _RING_BUFFER.clear()


def install_qt_message_handler() -> None:
    """Install a Qt message handler that routes Qt's own debug/warning/critical
    messages into the standard logging system.

    This captures `qt.svg: Cannot open file ...`, `qt.qpa.*`, `qt.network.*`
    and similar Qt-internal diagnostics that would otherwise be lost to
    stderr.  After installation, these messages appear in:
      - the in-app log page (via the ring buffer handler)
      - the standard log files (shangbackground.log, error.log)
      - the console (if enabled)

    Safe to call multiple times — re-installation just updates the handler.
    Should be called AFTER `configure_logging()` so the logging system is
    ready to receive the routed messages.
    """
    global _QT_HANDLER_INSTALLED
    try:
        from PySide6 import QtCore
    except Exception:
        return  # PySide6 not available (e.g. test environment)

    # Map Qt message types to logging levels.
    # QtDebugMsg -> DEBUG, QtInfoMsg -> INFO, QtWarningMsg -> WARNING,
    # QtCriticalMsg/QtFatalMsg -> ERROR.
    def _qt_message_handler(mode, context, message):
        try:
            qt_logger = logging.getLogger("qt")
            if mode == QtCore.QtMsgType.QtDebugMsg:
                level = logging.DEBUG
            elif mode == QtCore.QtMsgType.QtInfoMsg:
                level = logging.INFO
            elif mode == QtCore.QtMsgType.QtWarningMsg:
                level = logging.WARNING
            elif mode in (QtCore.QtMsgType.QtCriticalMsg, QtCore.QtMsgType.QtFatalMsg):
                level = logging.ERROR
            else:
                level = logging.INFO
            # Trim leading/trailing whitespace; Qt sometimes pads messages.
            msg = (message or "").strip()
            if not msg:
                return
            qt_logger.log(level, msg)
        except Exception:
            # Never let the Qt message handler crash the app.
            pass

    try:
        QtCore.qInstallMessageHandler(_qt_message_handler)
        _QT_HANDLER_INSTALLED = True
    except Exception:
        pass


class _HtmlWallpaperFilter(logging.Filter):
    """Route records tagged with `extra={'channel': 'html_wallpaper'}` to the
    HTML-wallpaper log file. Also include any record whose logger name starts
    with `html_wallpaper` or `platform_adapters.html_wallpaper` /
    `platform_adapters.run_html_wallpaper`."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if getattr(record, "channel", "") == "html_wallpaper":
            return True
        name = record.name or ""
        return (
            name == "html_wallpaper"
            or name.startswith("html_wallpaper.")
            or name.startswith("platform_adapters.html_wallpaper")
            or name.startswith("platform_adapters.run_html_wallpaper")
        )


class _SafeTimedRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """Timed rotating handler that tolerates Windows file locks.

    The app can have the GUI process and the HTML-wallpaper child process alive
    at the same time. On Windows, another process may keep yesterday's log file
    open during rollover; in that case the standard handler reports a logging
    error on every affected warning. This handler skips that rollover attempt,
    keeps appending to the current file, and schedules the next rollover window
    so logging never breaks the GUI path.
    """

    def _defer_rollover_after_lock(self) -> None:
        """Move rolloverAt forward after a transient file-lock failure."""
        try:
            if self.stream is None:
                self.stream = self._open()
        except Exception:
            self.stream = None
        try:
            current_time = int(time.time())
            new_rollover_at = self.computeRollover(current_time)
            while new_rollover_at <= current_time:
                new_rollover_at += self.interval
            self.rolloverAt = new_rollover_at
        except Exception:
            pass

    @staticmethod
    def _is_windows_file_lock(exc: BaseException) -> bool:
        return isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 32

    def doRollover(self) -> None:  # noqa: N802 - stdlib override name
        try:
            super().doRollover()
            return
        except OSError as exc:
            if not self._is_windows_file_lock(exc):
                raise
            # Windows can refuse rename/delete while another process has the log
            # open. Reopen the stream if the base implementation closed it, then
            # move rolloverAt forward to avoid a repeated stderr storm.
            self._defer_rollover_after_lock()

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 - stdlib override name
        exc = sys.exc_info()[1]
        if exc is not None and self._is_windows_file_lock(exc):
            self._defer_rollover_after_lock()
            return
        super().handleError(record)


def _build_file_handlers(log_path: Path, formatter: logging.Formatter, numeric_level: int) -> tuple[
    Optional[logging.Handler], Optional[logging.Handler], Optional[logging.Handler]
]:
    """Construct the three file handlers (main / html / error) without attaching
    them to the root logger.  Returns ``(file_h, html_h, error_h)``; any handler
    that fails to construct is returned as None.

    Called both from ``configure_logging`` (initial setup) and from
    ``set_file_logging_enabled`` (runtime toggle).
    """
    file_h: Optional[logging.Handler] = None
    html_h: Optional[logging.Handler] = None
    error_h: Optional[logging.Handler] = None

    # Main file handler — midnight rotation, 7-day keep
    try:
        main_file = log_path / "shangbackground.log"
        fh = _SafeTimedRotatingFileHandler(
            main_file, when="midnight", backupCount=7, encoding="utf-8", utc=False, delay=True
        )
        fh.setLevel(numeric_level)
        fh.setFormatter(formatter)
        fh._sb_managed = True  # type: ignore[attr-defined]

        class _NotHtmlFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
                if record.levelno >= logging.WARNING:
                    return True
                if getattr(record, "channel", "") == "html_wallpaper":
                    return False
                name = record.name or ""
                if (
                    name.startswith("html_wallpaper")
                    or name.startswith("platform_adapters.html_wallpaper")
                    or name.startswith("platform_adapters.run_html_wallpaper")
                ):
                    return False
                return True
        fh.addFilter(_NotHtmlFilter())
        file_h = fh
    except Exception as exc:
        sys.stderr.write(f"[log_setup] failed to init main file handler: {exc}\n")

    # HTML-wallpaper handler — separate file, DEBUG-level
    try:
        html_file = log_path / "html_wallpaper.log"
        hh = _SafeTimedRotatingFileHandler(
            html_file, when="midnight", backupCount=7, encoding="utf-8", utc=False, delay=True
        )
        hh.setLevel(logging.DEBUG)
        hh.setFormatter(formatter)
        hh.addFilter(_HtmlWallpaperFilter())
        hh._sb_managed = True  # type: ignore[attr-defined]
        html_h = hh
    except Exception as exc:
        sys.stderr.write(f"[log_setup] failed to init html_wallpaper handler: {exc}\n")

    # Error-only handler — kept 14 days
    try:
        err_file = log_path / "error.log"
        eh = _SafeTimedRotatingFileHandler(
            err_file, when="midnight", backupCount=14, encoding="utf-8", utc=False, delay=True
        )
        eh.setLevel(logging.WARNING)
        eh.setFormatter(formatter)
        eh._sb_managed = True  # type: ignore[attr-defined]
        error_h = eh
    except Exception as exc:
        sys.stderr.write(f"[log_setup] failed to init error handler: {exc}\n")

    return file_h, html_h, error_h


def configure_logging(
    log_dir: Optional[str] = None,
    level: str = "INFO",
    console: Optional[bool] = None,
    *,
    files_enabled: bool = False,
    force: bool = False,
) -> Path:
    """Initialize the root logger and our handlers. Idempotent.

    Args:
        log_dir: Explicit log directory. If None, uses ``<user_data_dir>/logs``.
        level: Threshold for the main file + console handlers.
        console: Whether to also emit to stderr. Defaults to True in source
            mode and False in packaged runs.
        files_enabled: If True, attach the three rotating file handlers
            (``shangbackground.log``, ``html_wallpaper.log``, ``error.log``).
            If False (DEFAULT), NO log files are written to disk — only the
            in-memory ring buffer (for the in-app log page) and optionally the
            console receive entries.  This matches the user-facing
            ``log_enabled`` setting which defaults to False.
        force: If True, remove existing handlers and reconfigure.

    Returns:
        The resolved log directory.
    """
    global _CONFIGURED, _LOG_DIR, _FILE_HANDLER, _HTML_HANDLER, _ERROR_HANDLER, _CONSOLE_HANDLER
    global _FILES_ENABLED, _FORMATTER

    with _LOCK:
        if _CONFIGURED and not force:
            assert _LOG_DIR is not None
            return _LOG_DIR

        log_path = _resolve_log_dir(log_dir)
        _LOG_DIR = log_path

        if console is None:
            try:
                from app.paths import is_packaged_runtime  # type: ignore
                console = not is_packaged_runtime()
            except Exception:
                console = not bool(getattr(sys, "frozen", False))

        numeric_level = getattr(logging, level.upper(), logging.INFO)

        root = logging.getLogger()
        for h in list(root.handlers):
            if getattr(h, "_sb_managed", False):
                root.removeHandler(h)
                try:
                    h.flush()
                except Exception:
                    pass
                try:
                    h.close()
                except Exception:
                    pass

        formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
        _FORMATTER = formatter

        # File handlers — only attached when files_enabled is True.
        # The in-app log page (ring buffer) and optional console still work
        # without files, so users can diagnose issues without leaving traces
        # on disk by default.
        _FILE_HANDLER = None
        _HTML_HANDLER = None
        _ERROR_HANDLER = None
        if files_enabled:
            fh, hh, eh = _build_file_handlers(log_path, formatter, numeric_level)
            if fh is not None:
                root.addHandler(fh)
                _FILE_HANDLER = fh
            if hh is not None:
                root.addHandler(hh)
                _HTML_HANDLER = hh
            if eh is not None:
                root.addHandler(eh)
                _ERROR_HANDLER = eh
        _FILES_ENABLED = bool(files_enabled)

        # Console handler (optional)
        if console:
            ch = logging.StreamHandler(stream=sys.stderr)
            ch.setLevel(numeric_level)
            ch.setFormatter(logging.Formatter("[%(levelname)s] [%(name)s] %(message)s"))
            ch._sb_managed = True  # type: ignore[attr-defined]
            root.addHandler(ch)
            _CONSOLE_HANDLER = ch
        else:
            _CONSOLE_HANDLER = None

        # In-memory ring buffer handler — always installed so the in-app log
        # page can display entries without file I/O.  DEBUG level so all
        # entries are captured; the GUI applies its own level filter.
        ring = _RingBufferHandler()
        ring.setLevel(logging.DEBUG)
        ring._sb_managed = True  # type: ignore[attr-defined]
        root.addHandler(ring)

        root.setLevel(logging.DEBUG)
        _CONFIGURED = True

        try:
            startup_logger = logging.getLogger("startup")
            startup_logger.info("=" * 60)
            startup_logger.info(
                "ShangBackground starting — log_dir=%s, level=%s, console=%s, files_enabled=%s",
                log_path, level, console, files_enabled,
            )
            startup_logger.info("Python %s on %s", sys.version.split()[0], sys.platform)
        except Exception:
            pass

        return log_path


def set_file_logging_enabled(enabled: bool) -> None:
    """Attach or detach the three rotating file handlers at runtime.

    Used by ``MainWindow.on_log_enabled_changed`` so toggling the "记录日志到文件"
    checkbox takes effect immediately without restarting the app.

    - ``enabled=True``: if not already attached, build the three handlers and
      attach them to the root logger.  If ``configure_logging`` was never
      called, auto-configure with defaults first.
    - ``enabled=False``: detach and close any currently-attached file handlers.

    Thread-safe via ``_LOCK``.  Safe to call multiple times.
    """
    global _FILE_HANDLER, _HTML_HANDLER, _ERROR_HANDLER, _FILES_ENABLED

    with _LOCK:
        if not _CONFIGURED:
            configure_logging(files_enabled=enabled)
            return

        # Idempotent: nothing to do if state already matches.
        if bool(enabled) == bool(_FILES_ENABLED):
            return

        log_path = _LOG_DIR or _resolve_log_dir()
        formatter = _FORMATTER or logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
        # Reuse the file handler's current level (was set from `level` arg).
        numeric_level = logging.INFO
        if _FILE_HANDLER is not None:
            try:
                numeric_level = int(_FILE_HANDLER.level)
            except Exception:
                pass

        root = logging.getLogger()

        if enabled:
            fh, hh, eh = _build_file_handlers(log_path, formatter, numeric_level)
            if fh is not None:
                root.addHandler(fh)
                _FILE_HANDLER = fh
            if hh is not None:
                root.addHandler(hh)
                _HTML_HANDLER = hh
            if eh is not None:
                root.addHandler(eh)
                _ERROR_HANDLER = eh
            _FILES_ENABLED = True
            try:
                logging.getLogger("startup").info("文件日志记录已开启")
            except Exception:
                pass
        else:
            for h in (_FILE_HANDLER, _HTML_HANDLER, _ERROR_HANDLER):
                if h is not None:
                    try:
                        root.removeHandler(h)
                    except Exception:
                        pass
                    try:
                        h.flush()
                    except Exception:
                        pass
                    try:
                        h.close()
                    except Exception:
                        pass
            _FILE_HANDLER = None
            _HTML_HANDLER = None
            _ERROR_HANDLER = None
            _FILES_ENABLED = False
            # Ring buffer still receives the message so the in-app log page
            # reflects the change immediately.
            try:
                logging.getLogger("startup").info("文件日志记录已关闭")
            except Exception:
                pass


def is_file_logging_enabled() -> bool:
    """Return whether the three rotating file handlers are currently attached."""
    return bool(_FILES_ENABLED)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a configured logger. Auto-configures with defaults if needed."""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name or "shangbackground")


def get_html_wallpaper_logger() -> logging.Logger:
    """Return the HTML-wallpaper logger."""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger("platform_adapters.html_wallpaper")


def get_log_dir() -> Optional[Path]:
    """Return the current log directory, or None if not yet configured."""
    return _LOG_DIR


def set_log_level(level: str) -> None:
    """Update the level of the main file handler + console handler (if any)."""
    with _LOCK:
        numeric = getattr(logging, level.upper(), logging.INFO)
        if _FILE_HANDLER is not None:
            _FILE_HANDLER.setLevel(numeric)
        if _CONSOLE_HANDLER is not None:
            _CONSOLE_HANDLER.setLevel(numeric)


class LegacyAdapter:
    """Adapter that exposes the old `core.log(msg, level=, exc_info=)` signature.

    Existing call sites like `core.log("text", level="WARNING", exc_info=True)`
    continue to work; the message is forwarded to the standard logging system.
    """

    _LEVEL_MAP = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or logging.getLogger("core")

    def log(self, msg, level: str = "INFO", exc_info=False):  # type: ignore[no-untyped-def]
        """Mirror of the original `core.log()` signature."""
        if not _CONFIGURED:
            configure_logging()
        lvl = self._LEVEL_MAP.get(str(level).upper(), logging.INFO)
        if exc_info:
            self._logger.log(lvl, msg, exc_info=True)
        else:
            self._logger.log(lvl, msg)
        return str(msg)

    def log_error(self, context: str, exc: Optional[BaseException] = None) -> None:
        """Mirror of `core.log_error()`."""
        if exc is None:
            self.log(context, level="ERROR", exc_info=True)
        else:
            self.log(f"{context}: {exc}", level="ERROR", exc_info=exc)


# Default singleton, used as a drop-in for `core.log` callers.
legacy = LegacyAdapter()
