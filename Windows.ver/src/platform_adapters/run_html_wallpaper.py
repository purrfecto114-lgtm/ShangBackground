"""HTML wallpaper runtime — Windows (v6, refactored).

This script runs in a separate process to render an HTML page (local file or
remote URL) as the Windows desktop wallpaper.  It is launched by
``html_wallpaper.py`` with ``--path`` and several option flags.

Architecture (v6)
-----------------
The code is organised into clearly-separated sections, replacing the
patch-on-patch structure of v3..v5:

1. **Early-arg parsing & Chromium flags** (must run before Qt imports).
   - GPU toggle (``--disable-gpu``)
   - Anti-freeze flags (disable Chromium's occlusion / backgrounding /
     throttling so the embedded page keeps animating).

2. **Win32 helpers** — WorkerW discovery, window reparenting, mouse-through
   (WS_EX_TRANSPARENT), z-order control, foreground detection.

3. **Wallpaper API** — ``window.shangbg`` JavaScript object injected on
   ``loadFinished``.  Provides version / platform / screen / pause state,
   and dispatches ``shangbg-pause`` / ``shangbg-resume`` / ``shangbg-screenchange``
   CustomEvents.

4. **Options file polling** — child reads ``html_wallpaper_options.json`` every
   1.5s to hot-apply ``auto_pause``, ``mouse_through`` etc.

5. **Lifecycle timers**:
   - **maintain** (5s): re-embed into WorkerW if parent lost, re-fit on resize.
   - **visibility** (3s): auto-pause only when every display is visually covered.
   - **keepalive** (10s): triple nudge (lifecycle Active + SetWindowPos no-op +
     JS heartbeat) to defeat any residual Chromium throttling.

Key behaviours
--------------
* **WorkerW embedding** — ``SetParent(our_hwnd, workerw_hwnd)`` puts the
  wallpaper between the desktop background and the desktop icons.  Icons
  remain visible and clickable.
* **Mouse-through toggle** — when ``mouse_through=true`` (default),
  ``WS_EX_TRANSPARENT`` is set so clicks pass straight through to the
  desktop.  When ``false``, the wallpaper receives mouse events for
  interactive HTML pages.
* **Auto-pause** — native top-level window geometry is sampled across every
  display. The page freezes only when all displays are almost completely
  covered; ordinary focus changes and partially visible desktops keep running.
* **GPU toggle** — ``--disable-gpu`` sets ``QTWEBENGINE_CHROMIUM_FLAGS``
  before Qt WebEngine is imported.  Cannot be hot-reloaded; the GUI
  triggers a subprocess restart to apply.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_SRC_ROOT_FOR_IMPORTS = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _SRC_ROOT_FOR_IMPORTS not in sys.path:
    sys.path.insert(0, _SRC_ROOT_FOR_IMPORTS)

from platform_adapters.desktop_visibility import desktop_is_visible  # noqa: E402

try:
    from app.config import APP_VERSION
    from app.paths import APP_DATA_DIR, app_data_path
except Exception:
    APP_VERSION = "1.4.0"
    if sys.platform.startswith("win"):
        _fallback_root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        APP_DATA_DIR = Path(_fallback_root) / "ShangBackground"
    elif sys.platform == "darwin":
        APP_DATA_DIR = Path(os.path.expanduser("~/Library/Application Support")) / "ShangBackground"
    else:
        _fallback_root = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
        APP_DATA_DIR = Path(_fallback_root) / "shangbackground"
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

    def app_data_path(*parts):
        return os.fspath(APP_DATA_DIR.joinpath(*map(os.fspath, parts)))


# ----------------------------------------------------------------------------
# Logging — initialize as early as possible after paths are resolved.
# ----------------------------------------------------------------------------
import logging as _logging  # noqa: E402

try:
    from app.log_setup import configure_logging as _configure_logging
    from app.log_setup import get_html_wallpaper_logger as _get_hw_logger
except Exception:  # pragma: no cover
    _configure_logging = None
    _get_hw_logger = None


def _log():
    """Lazy accessor for the HTML-wallpaper logger."""
    if _get_hw_logger is not None:
        try:
            return _get_hw_logger()
        except Exception:  # pragma: no cover
            pass
    return _logging.getLogger("platform_adapters.run_html_wallpaper")


def _read_log_enabled_for_child() -> bool:
    """Bug 6 fix: read ``log_enabled`` from settings.json in the HTML wallpaper
    child process so it respects the user's global setting.

    The child process is separate from the main GUI process and has its own
    ``log_setup`` module state.  Without this check, the child would default
    to ``files_enabled=False`` (no file logging) even when the user enabled
    logging in the main process — OR, before the Batch 1 fix, it would
    default to ALWAYS writing files even when the user disabled logging.
    """
    try:
        # Reuse the same config path logic as core.engine
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
            config_path = os.path.join(base, "ShangBackground", "settings.json")
        elif os.environ.get("XDG_CONFIG_HOME"):
            config_path = os.path.join(os.environ["XDG_CONFIG_HOME"], "shangbackground", "settings.json")
        elif sys.platform == "darwin":
            config_path = os.path.join(os.path.expanduser("~/Library/Application Support"), "ShangBackground", "settings.json")
        else:
            config_path = os.path.join(os.path.expanduser("~/.config"), "shangbackground", "settings.json")
        if not os.path.exists(config_path):
            return False
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("log_enabled", False))
    except Exception:
        return False


try:
    if _configure_logging is not None:
        # Bug 6 fix: respect the user's ``log_enabled`` setting from
        # settings.json.  When False (default), NO log files are written
        # by the child process either.
        _child_files_enabled = _read_log_enabled_for_child()
        _configure_logging(level="DEBUG", console=True, files_enabled=_child_files_enabled)
    else:
        _logging.basicConfig(level=_logging.INFO)
except Exception as _log_init_exc:  # pragma: no cover
    try:
        sys.stderr.write(f"[run_html_wallpaper] log init failed: {_log_init_exc}\n")
    except Exception:
        pass

# v1.4.8: 强制确保 run_html_wallpaper logger 至少有一个 stderr handler，
# 这样可见性自动暂停的诊断日志能写入 html_wallpaper_subprocess.log
# （父进程已把 stderr 重定向到该文件）。
try:
    _rw_logger = _logging.getLogger("platform_adapters.run_html_wallpaper")
    if not _rw_logger.handlers:
        _rw_logger.setLevel(_logging.INFO)
        _rw_handler = _logging.StreamHandler(stream=sys.stderr)
        _rw_handler.setLevel(_logging.INFO)
        _rw_handler.setFormatter(_logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
        _rw_logger.addHandler(_rw_handler)
        _rw_logger.propagate = False
except Exception:
    pass


# ============================================================================
# Section 1 — Early arg parsing & Chromium flags
# ============================================================================
#
# This MUST run before any ``from PySide6...`` import, because Chromium reads
# QTWEBENGINE_CHROMIUM_FLAGS during its one-time initialisation.

def _parse_args_early() -> argparse.Namespace:
    """Parse only the flags that influence Chromium init (GPU, gpu_mode).

    A second, full ``parse_args()`` runs later for the rest.  Calling argparse
    twice is cheap and keeps the early-stage logic self-contained.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--disable-gpu", action="store_true")
    parser.add_argument("--enable-gpu", action="store_true")
    args, _ = parser.parse_known_args()
    return args


_EARLY_ARGS = _parse_args_early()


def _build_chromium_flags() -> str:
    """Compose the QTWEBENGINE_CHROMIUM_FLAGS string.

    Layers (in order):
      1. Existing env value (preserve user / integrator flags).
      2. GPU toggle: append ``--disable-gpu`` only when explicitly requested
         (Chromium enables GPU by default).
      3. Anti-freeze flags: disable every Chromium heuristic that could
         throttle or stop rendering of an occluded (WorkerW-embedded) window.
    """
    parts = [p for p in os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split() if p]

    # Strip stale GPU flags so we control them deterministically.
    parts = [p for p in parts if p not in ("--disable-gpu", "--enable-gpu")]
    if _EARLY_ARGS.disable_gpu:
        parts.append("--disable-gpu")

    # Anti-freeze flags — each entry is either ``--flag`` or
    # ``--disable-features=FeatureName``.  Multiple --disable-features entries
    # are merged into one comma-separated value to avoid Chromium's "last
    # flag wins" behaviour.
    anti_freeze: tuple[str, ...] = (
        "--disable-features=CalculateNativeWinOcclusion",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
        "--disable-background-media-suspend",
        "--disable-features=BackForwardCache",
        "--disable-features=OcclusionTrackingWithMultiMonitors",
        "--disable-features=CompositorThreadedScrollbarScrolling",
        "--disable-gpu-watchdog",
        "--enable-low-res-2d-canvas=false",
        "--disable-features=IntensiveWakeUpThrottling",
    )

    df_idx = next((i for i, p in enumerate(parts) if p.startswith("--disable-features=")), None)
    if df_idx is None and any(f.startswith("--disable-features=") for f in anti_freeze):
        parts.append("--disable-features=")
        df_idx = len(parts) - 1

    df_features: set[str] = set()
    if df_idx is not None:
        existing = parts[df_idx].split("=", 1)[1] if "=" in parts[df_idx] else ""
        df_features = {x.strip() for x in existing.split(",") if x.strip()}

    for f in anti_freeze:
        if f.startswith("--disable-features="):
            feat = f.split("=", 1)[1]
            df_features.add(feat)
        elif f.startswith("--enable-") and "=" in f:
            if f not in parts:
                parts.append(f)
        else:
            if f not in parts:
                parts.append(f)

    if df_idx is not None and df_features:
        parts[df_idx] = "--disable-features=" + ",".join(sorted(df_features))
    elif df_idx is not None and not df_features:
        # No features to disable — remove the placeholder entry.
        parts.pop(df_idx)

    # Keep Chromium in the supported multi-process model, but reduce scattered
    # renderer processes for wallpaper URLs. Do not override explicit user flags.
    if not any(p == "--single-process" or p.startswith("--process-per-") for p in parts):
        parts.append("--process-per-site")

    return " ".join(parts).strip()


os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = _build_chromium_flags()
# Keep Chromium sandboxing enabled unless the caller explicitly overrides it.
# Packaged desktop builds must not silently disable this security boundary.


# ============================================================================
# Qt imports (after env is set)
# ============================================================================

try:
    from PySide6.QtCore import QUrl, Qt, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage
except Exception:
    sys.stderr.write("Qt WebEngine is not available.\n")
    sys.exit(1)


def _set_share_opengl_contexts_attribute() -> None:
    """Set Qt WebEngine's shared OpenGL attribute before QApplication exists."""
    try:
        attr = getattr(Qt, "AA_ShareOpenGLContexts", None)
        if attr is None:
            attr = Qt.ApplicationAttribute.AA_ShareOpenGLContexts
        QApplication.setAttribute(attr, True)
    except Exception:
        _log().debug("ignored exception", exc_info=True)


class _FirstFrameGate:
    """Keep the native wallpaper window transparent until Chromium paints.

    ``loadFinished`` only means the document load completed; Chromium may not
    have submitted a compositor frame yet.  Revealing immediately can expose a
    white/black intermediate surface.  This gate schedules two animation frames,
    polls a DOM marker, and ignores callbacks from superseded navigations.
    """

    _ARM_SCRIPT = (
        "(function(){try{"
        "var e=document.documentElement;"
        "if(!e){return false;}"
        "e.removeAttribute('data-shangbg-first-frame');"
        "requestAnimationFrame(function(){requestAnimationFrame(function(){"
        "try{document.documentElement.setAttribute('data-shangbg-first-frame','1');}catch(e){}"
        "});});return true;}catch(e){return false;}})();"
    )
    _READY_SCRIPT = (
        "document.documentElement && "
        "document.documentElement.getAttribute('data-shangbg-first-frame') === '1'"
    )

    def __init__(self, view: QWebEngineView, page: QWebEnginePage, on_visible) -> None:
        self._view = view
        self._page = page
        self._on_visible = on_visible
        self._generation = 0
        self._visible = False

    def begin_navigation(self) -> None:
        self._generation += 1
        self._visible = False
        try:
            self._view.setWindowOpacity(0.0)
        except Exception:
            _log().debug("failed to hide HTML surface before navigation", exc_info=True)

    def finish_navigation(self, ok: bool) -> None:
        if not ok:
            return
        generation = self._generation
        try:
            self._page.runJavaScript(self._ARM_SCRIPT)
        except Exception:
            _log().debug("failed to arm first-frame marker", exc_info=True)
        self._poll(generation, attempts=0)
        QTimer.singleShot(1800, lambda: self._reveal(generation, "fallback"))

    def _poll(self, generation: int, attempts: int) -> None:
        if generation != self._generation or self._visible:
            return

        def _checked(ready: Any) -> None:
            if generation != self._generation or self._visible:
                return
            if bool(ready):
                self._reveal(generation, "first-frame")
                return
            if attempts < 50:
                QTimer.singleShot(30, lambda: self._poll(generation, attempts + 1))

        try:
            self._page.runJavaScript(self._READY_SCRIPT, _checked)
        except Exception:
            _log().debug("first-frame poll failed", exc_info=True)

    def _reveal(self, generation: int, reason: str) -> None:
        if generation != self._generation or self._visible:
            return
        self._visible = True
        try:
            self._view.setWindowOpacity(1.0)
            self._on_visible()
            _log().info("HTML surface visible after %s", reason)
        except Exception:
            _log().debug("failed to reveal HTML surface", exc_info=True)

# ============================================================================
# Section 2 — Win32 helpers
# ============================================================================

_user32 = ctypes.windll.user32

# z-order / positioning flags
HWND_BOTTOM = 1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOREDRAW = 0x0008
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200

# extended window styles
GWL_EXSTYLE = -20
GCL_STYLE = -26
CS_OWNDC = 0x0020
CS_CLASSDC = 0x0040
CS_PARENTDC = 0x0080
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020  # mouse click-through
WS_EX_LAYERED = 0x00080000
WS_EX_COMPOSITED = 0x02000000

# message sent to Progman to make it spawn a WorkerW sibling
WM_SPAWN_WORKERW = 0x052C
WM_SETREDRAW = 0x000B  # suppress/allow window redraw during reparent
RDW_INVALIDATE = 0x0007
RDW_UPDATENOW = 0x0100
RDW_ALLCHILDREN = 0x0080
_WORKERW_RESPAWN_DEBOUNCE_SEC = 30.0
_last_workerw_spawn_at = 0.0


def _find_workerw(*, spawn: bool = True) -> int:
    """Return the HWND of the WorkerW that sits behind the desktop icons.

    ``spawn=True`` (default) first pokes Progman with message 0x052C to ensure
    a WorkerW exists.  Pass ``spawn=False`` from periodic maintenance timers
    so we don't keep nudging Progman every few seconds.
    """
    progman = _user32.FindWindowW("Progman", None) or 0
    if spawn and progman:
        result = ctypes.c_ulong(0)
        try:
            _user32.SendMessageTimeoutW(
                progman, WM_SPAWN_WORKERW, 0, 0, 0x0002, 1000, ctypes.byref(result)
            )
        except Exception:
            _log().debug("ignored exception", exc_info=True)

    workerw = ctypes.c_void_p(0)
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _enum(hwnd: int, _lparam: int) -> bool:
        shell = _user32.FindWindowExW(hwnd, 0, "SHELLDLL_DefView", None)
        if shell:
            candidate = _user32.FindWindowExW(0, hwnd, "WorkerW", None)
            if candidate:
                workerw.value = candidate
                return False  # stop enumeration
        return True

    try:
        _user32.EnumWindows(EnumWindowsProc(_enum), 0)
    except Exception:
        _log().debug("ignored exception", exc_info=True)
    return int(workerw.value or 0)


def _get_window_class_style(hwnd: int) -> int:
    """Return the Win32 class style for *hwnd*.

    WS_EX_COMPOSITED is documented as incompatible with windows whose class
    style includes CS_OWNDC, CS_CLASSDC or CS_PARENTDC.  Qt/WebEngine may use
    GPU-backed/native child windows on some builds, so the host style must be
    guarded instead of blindly OR-ing the flag.
    """
    if not hwnd:
        return 0
    try:
        get_class_long_ptr = getattr(_user32, "GetClassLongPtrW", None)
        if get_class_long_ptr is not None:
            get_class_long_ptr.restype = ctypes.c_size_t
            return int(get_class_long_ptr(hwnd, GCL_STYLE) or 0)
    except Exception:
        _log().debug("ignored exception", exc_info=True)
    try:
        return int(_user32.GetClassLongW(hwnd, GCL_STYLE) or 0)
    except Exception:
        _log().debug("ignored exception", exc_info=True)
    return 0


def _apply_window_styles(hwnd: int) -> None:
    """Add the stable extended styles used by the wallpaper host window.

    - ``WS_EX_TOOLWINDOW``: hide from taskbar / alt-tab.
    - ``WS_EX_NOACTIVATE``: never steal focus.
    - ``WS_EX_LAYERED``: let DWM properly composite the window during
      reparent operations, eliminating the brief uncomposited repaint
      that manifests as a visible flash when ``SetParent`` detaches and
      re-attaches the window to WorkerW.
    - ``WS_EX_COMPOSITED``: optional double-buffering, guarded because Win32
      disallows it for CS_OWNDC / CS_CLASSDC / CS_PARENTDC classes.
    """
    if not hwnd:
        return
    try:
        ex = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        # Keep the style set minimal.  WS_EX_COMPOSITED looks tempting as a
        # double-buffering flag, but with Qt WebEngine/Chromium GPU child
        # surfaces it can trigger periodic white/black flashes during DWM
        # composition.  Layered + no-activate is enough for smooth embedding.
        new_ex = ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
        if new_ex != ex:
            _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_ex)
    except Exception:
        _log().debug("ignored exception", exc_info=True)


def _set_mouse_through(hwnd: int, enable: bool) -> None:
    """Toggle WS_EX_TRANSPARENT to enable / disable mouse click-through.

    When enabled (default for non-interactive wallpapers), mouse clicks pass
    straight through to the desktop icons.  When disabled, the wallpaper
    receives mouse events — useful for interactive HTML pages.
    """
    if not hwnd:
        return
    try:
        ex = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        new = (ex | WS_EX_TRANSPARENT) if enable else (ex & ~WS_EX_TRANSPARENT)
        if new != ex:
            _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new)
    except Exception:
        _log().debug("ignored exception", exc_info=True)


def _reparent_to_workerw(child_hwnd: int, workerw: int = 0) -> bool:
    """Embed our window inside WorkerW so it renders behind desktop icons.

    Wraps ``SetParent`` in ``WM_SETREDRAW`` FALSE / TRUE to suppress the
    brief flicker that occurs when the window detaches from its old
    parent and attaches to WorkerW.  Without this, DWM re-composites the
    desktop layer mid-reparent and the user sees a flash of the original
    wallpaper.  Pattern documented in Microsoft's WM_SETREDRAW reference

    If ``workerw`` is 0 (default), a WorkerW is located via
    ``_find_workerw(spawn=True)``.  Callers that already have a fresh
    WorkerW HWND (e.g. the maintenance timer) should pass it in to
    avoid re-spawning.
    """
    if not child_hwnd:
        return False
    if workerw:
        target_workerw = workerw
    else:
        target_workerw = _find_workerw(spawn=True)
    if not target_workerw:
        return False
    try:
        # Suppress redraw on the child while the reparent is in flight.
        _user32.SendMessageW(child_hwnd, WM_SETREDRAW, 0, 0)
        try:
            _user32.SetParent(child_hwnd, target_workerw)
        finally:
            _user32.SendMessageW(child_hwnd, WM_SETREDRAW, 1, 0)
            # Force a single, deferred composite instead of many mid-flight ones.
            _user32.SetWindowPos(
                child_hwnd, 0, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                | SWP_NOOWNERZORDER | SWP_NOREDRAW,
            )
            # Do not call RedrawWindow(...UPDATENOW...) here.  For WebEngine it
            # forces a synchronous DWM composite while Chromium is still handing
            # over its surface, which is the main source of startup flicker.
            pass
        # Invalidate the fit-size cache: the new parent likely has a
        # different client rect, so the next _refit_to_workerw must run.
        global _last_fit_size
        _last_fit_size = (0, 0)
        return True
    except Exception:
        _log().debug("ignored exception in _reparent_to_workerw", exc_info=True)
        return False


def _lower_to_bottom(hwnd: int) -> None:
    """Fallback only: push to HWND_BOTTOM of the regular z-order.

    Used when WorkerW cannot be found.  In this fallback the window WILL
    cover desktop icons.
    """
    if not hwnd:
        return
    try:
        _user32.SetWindowPos(
            hwnd, HWND_BOTTOM, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
    except Exception:
        _log().debug("ignored exception", exc_info=True)


# Cache the last WorkerW client size we fitted to.  MoveWindow triggers DWM
# to re-composite the desktop layer even when nothing changed — that manifests
# as a periodic flicker.  Skip the no-op call by remembering the last size.
_last_fit_size: tuple[int, int] = (0, 0)


def _refit_to_workerw(child_hwnd: int, *, force: bool = False) -> None:
    """Resize ``child_hwnd`` to cover the WorkerW client area.

    ``force=True`` bypasses the size cache (use after SetParent).
    ``bRepaint=False`` in MoveWindow: Chromium manages its own paint pipeline
    and we don't want Win32 to trigger a DWM re-composite.
    """
    global _last_fit_size
    if not child_hwnd:
        return
    workerw = _find_workerw(spawn=False)
    if not workerw:
        return
    try:
        rect = ctypes.wintypes.RECT()
        _user32.GetClientRect(workerw, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
    except Exception:
        return
    if w <= 0 or h <= 0:
        return
    if not force and (w, h) == _last_fit_size:
        return
    try:
        _user32.SendMessageW(child_hwnd, WM_SETREDRAW, 0, 0)
        try:
            _user32.MoveWindow(child_hwnd, 0, 0, w, h, False)
        finally:
            _user32.SendMessageW(child_hwnd, WM_SETREDRAW, 1, 0)
            # Do not call RedrawWindow(...UPDATENOW...) here.  For WebEngine it
            # forces a synchronous DWM composite while Chromium is still handing
            # over its surface, which is the main source of startup flicker.
            pass
        _last_fit_size = (w, h)
    except Exception:
        _log().debug("ignored exception", exc_info=True)


# ============================================================================
# Section 3 — Wallpaper API (window.shangbg)
# ============================================================================

def _build_api_injection_script(platform_info: dict[str, Any]) -> str:
    """Compose the JS that injects ``window.shangbg`` and dispatches the
    ``shangbg-ready`` CustomEvent.

    The script is idempotent — if ``window.shangbg`` already exists it exits
    without overwriting, so re-injection after a navigation is safe.
    """
    info_json = json.dumps(platform_info, ensure_ascii=False)
    return f"""
(function() {{
    if (window.shangbg) {{ return; }}
    var info = {info_json};
    window.shangbg = {{
        version: info.version,
        platform: info.platform,
        isWallpaper: true,
        isPreview: false,
        screen: info.screen,
        autoPauseEnabled: info.auto_pause,
        gpuEnabled: info.gpu_enabled,
        mouseThrough: info.mouse_through,
        // Called by host on state changes (pause / resume / screenchange).
        // Page can also listen via window.addEventListener('shangbg-<event>').
        _listeners: {{}},
        on: function(name, cb) {{
            if (!this._listeners[name]) {{ this._listeners[name] = []; }}
            this._listeners[name].push(cb);
        }},
        off: function(name, cb) {{
            if (!this._listeners[name]) {{ return; }}
            this._listeners[name] = this._listeners[name].filter(function(f) {{
                return f !== cb;
            }});
        }},
        _dispatch: function(name, data) {{
            var evt = new CustomEvent('shangbg-' + name, {{ detail: data }});
            window.dispatchEvent(evt);
            var ls = this._listeners[name] || [];
            for (var i = 0; i < ls.length; i++) {{
                try {{ ls[i](data); }} catch (e) {{ console.error(e); }}
            }}
        }}
    }};
    window.dispatchEvent(new CustomEvent('shangbg-ready', {{ detail: window.shangbg }}));
}})();
"""


def _dispatch_event_script(event_name: str, data: Any = None) -> str:
    """Compose the JS that dispatches a ``shangbg-<event>`` CustomEvent and
    calls any registered listeners.
    """
    data_json = "undefined" if data is None else json.dumps(data, ensure_ascii=False)
    return f"""
(function() {{
    if (window.shangbg && window.shangbg._dispatch) {{
        window.shangbg._dispatch({json.dumps(event_name)}, {data_json});
    }} else {{
        var evt = new CustomEvent('shangbg-' + {json.dumps(event_name)}, {{ detail: {data_json} }});
        window.dispatchEvent(evt);
    }}
}})();
"""


# ============================================================================
# Section 4 — Options file polling
# ============================================================================

# Mirrors the data dir used by html_wallpaper.py.
_DATA_DIR = os.fspath(APP_DATA_DIR)
os.makedirs(_DATA_DIR, exist_ok=True)
_OPTIONS_FILE = app_data_path("html_wallpaper_options.json")


def _read_options() -> dict[str, Any]:
    try:
        if not os.path.exists(_OPTIONS_FILE):
            return {}
        with open(_OPTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# ============================================================================
# Section 5 — Lifecycle helpers (freeze / thaw)
# ============================================================================

# QWebEnginePage.LifecycleState is available since Qt 6.2.
_LIFECYCLE_AVAILABLE = hasattr(QWebEnginePage, "LifecycleState")


def _inject_pause_css(page: QWebEnginePage) -> None:
    """Inject comprehensive CSS rules that pause all CSS-based animations.

    Covers:
    - CSS @keyframes animations (animation-play-state: paused)
    - CSS transitions (transition: none — prevents new transitions; running
      ones can't be paused mid-flight but this prevents further movement)
    - SVG SMIL animations (display: none would hide them; instead we rely on
      svg.pauseAnimations() in JS)
    - content-visibility hint to skip off-screen subtrees

    All rules are gated behind html[data-shangbg-paused="1"] so toggling the
    attribute does a single style recalculation — no element iteration.
    """
    try:
        page.runJavaScript(
            "(function(){try{"
            "if(document.getElementById('shangbg-pause-css'))return;"
            "var s=document.createElement('style');"
            "s.id='shangbg-pause-css';"
            "s.textContent='"
            # CSS @keyframes: animation-play-state: paused
            "html[data-shangbg-paused=\"1\"] *,"
            "html[data-shangbg-paused=\"1\"] *::before,"
            "html[data-shangbg-paused=\"1\"] *::after{"
            "animation-play-state:paused !important;"
            "-webkit-animation-play-state:paused !important;"
            "}"
            # CSS transitions: disable new transitions while paused
            "html[data-shangbg-paused=\"1\"] *{"
            "transition:none !important;"
            "}"
            # content-visibility: skip rendering off-screen subtrees
            "html[data-shangbg-paused=\"1\"] [data-shangbg-skip]{"
            "content-visibility:hidden !important;"
            "}"
            "';"
            "document.head.appendChild(s);"
            "}catch(e){}})();"
        )
    except Exception:
        _log().debug("inject pause css failed", exc_info=True)


def _freeze_page(page: QWebEnginePage) -> None:
    """Pause ALL animation types in the page.

    v1.4.8: Comprehensive pause covering every animation mechanism:
    1. CSS @keyframes — via injected CSS rule (animation-play-state: paused)
    2. CSS transitions — via injected CSS rule (transition: none)
    3. Web Animations API — document.getAnimations().forEach(pause)
    4. SVG SMIL — svg.pauseAnimations()
    5. requestAnimationFrame — intercepted to no-op
    6. setInterval/setTimeout — intercepted (timers queued but not fired)
    7. visibilitychange event — dispatched so libraries auto-pause
    8. <video>/<audio> — .pause()
    9. Web Audio API — AudioContext.suspend()
    10. Canvas/WebGL — stopped via rAF interception (draw calls gated)
    """
    _inject_pause_css(page)
    try:
        page.runJavaScript(
            "(function(){try{"
            # 1. CSS 动画暂停（通过 data 属性触发注入的 CSS 规则）
            "document.documentElement.dataset.shangbgPaused='1';"
            # 2. 覆盖 visibilityState/hidden，派发事件让动画库自动暂停
            "if(!window._shangbgOrigHidden){"
            "  Object.defineProperty(document,'hidden',{get:function(){return window._shangbgPaused===true;},configurable:true});"
            "  Object.defineProperty(document,'visibilityState',{get:function(){return window._shangbgPaused===true?'hidden':'visible';},configurable:true});"
            "}"
            "window._shangbgPaused=true;"
            "document.dispatchEvent(new Event('visibilitychange'));"
            "document.dispatchEvent(new Event('webkitvisibilitychange'));"
            "window.dispatchEvent(new Event('blur'));"
            # 3. Web Animations API — pause all animations (CSS + WAAPI + scroll-driven)
            "if(document.getAnimations){try{document.getAnimations().forEach(function(a){try{a.pause();}catch(e){}});}catch(e){}}"
            # 4. SVG SMIL — pause all SVG animations
            "try{document.querySelectorAll('svg').forEach(function(s){try{if(s.pauseAnimations)s.pauseAnimations();}catch(e){}});}catch(e){}"
            # 5. 拦截 requestAnimationFrame
            "if(!window._shangbgOrigRAF){window._shangbgOrigRAF=window.requestAnimationFrame.bind(window);}"
            "window.requestAnimationFrame=function(){return 0;};"
            "if(!window._shangbgOrigCAF){window._shangbgOrigCAF=window.cancelAnimationFrame.bind(window);}"
            "window.cancelAnimationFrame=function(){};"
            # 6. 拦截 setInterval（已注册的定时器继续运行，但新的不注册）
            "if(!window._shangbgOrigSI){window._shangbgOrigSI=window.setInterval.bind(window);}"
            "if(!window._shangbgOrigST){window._shangbgOrigST=window.setTimeout.bind(window);}"
            "if(!window._shangbgOrigCI){window._shangbgOrigCI=window.clearInterval.bind(window);}"
            "if(!window._shangbgOrigCT){window._shangbgOrigCT=window.clearTimeout.bind(window);}"
            # 7. 暂停 <video>/<audio>
            "document.querySelectorAll('video,audio').forEach(function(m){try{"
            "if(!m.paused){m.dataset.shangbgWasPlaying='1';m.pause();}"
            "}catch(e){}});"
            # 8. 暂停 Web Audio API
            "try{"
            "if(window._shangbgAudioCtxs){window._shangbgAudioCtxs.forEach(function(c){try{if(c.state==='running')c.suspend();}catch(e){}});}"
            "else{window._shangbgAudioCtxs=[];}"
            "}catch(e){}"
            "}catch(e){}})();"
        )
    except Exception:
        _log().debug("JS freeze failed", exc_info=True)


def _thaw_page(page: QWebEnginePage) -> None:
    """Resume all animation types — reverse of _freeze_page."""
    try:
        page.runJavaScript(
            "(function(){try{"
            # 1. 移除 CSS 暂停
            "delete document.documentElement.dataset.shangbgPaused;"
            # 2. 恢复可见状态
            "window._shangbgPaused=false;"
            "document.dispatchEvent(new Event('visibilitychange'));"
            "document.dispatchEvent(new Event('webkitvisibilitychange'));"
            "window.dispatchEvent(new Event('focus'));"
            # 3. 恢复 Web Animations API
            "if(document.getAnimations){try{document.getAnimations().forEach(function(a){try{a.play();}catch(e){}});}catch(e){}}"
            # 4. 恢复 SVG SMIL
            "try{document.querySelectorAll('svg').forEach(function(s){try{if(s.unpauseAnimations)s.unpauseAnimations();}catch(e){}});}catch(e){}"
            # 5. 恢复 requestAnimationFrame
            "if(window._shangbgOrigRAF){window.requestAnimationFrame=window._shangbgOrigRAF;delete window._shangbgOrigRAF;}"
            "if(window._shangbgOrigCAF){window.cancelAnimationFrame=window._shangbgOrigCAF;delete window._shangbgOrigCAF;}"
            # 6. 恢复 setInterval/setTimeout
            "if(window._shangbgOrigSI){window.setInterval=window._shangbgOrigSI;delete window._shangbgOrigSI;}"
            "if(window._shangbgOrigST){window.setTimeout=window._shangbgOrigST;delete window._shangbgOrigST;}"
            "if(window._shangbgOrigCI){window.clearInterval=window._shangbgOrigCI;delete window._shangbgOrigCI;}"
            "if(window._shangbgOrigCT){window.clearTimeout=window._shangbgOrigCT;delete window._shangbgOrigCT;}"
            # 7. 恢复 <video>/<audio>
            "document.querySelectorAll('video,audio').forEach(function(m){try{"
            "if(m.dataset.shangbgWasPlaying==='1'){delete m.dataset.shangbgWasPlaying;m.play().catch(function(){});}"
            "}catch(e){}});"
            # 8. 恢复 Web Audio API
            "try{"
            "if(window._shangbgAudioCtxs){window._shangbgAudioCtxs.forEach(function(c){try{if(c.state==='suspended')c.resume();}catch(e){}});}"
            "}catch(e){}"
            "}catch(e){}})();"
        )
    except Exception:
        _log().debug("JS thaw failed", exc_info=True)


# ============================================================================
# Section 6 — Argument parsing (full, run after Qt is available)
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run HTML wallpaper (Windows)")
    parser.add_argument("--path", required=True, help="Path to local HTML file or URL")
    parser.add_argument("--auto-pause", action="store_true",
                        help="Freeze only when native window coverage shows every display is almost fully covered")
    parser.add_argument("--disable-gpu", action="store_true",
                        help="Disable GPU acceleration in Qt WebEngine (software rendering)")
    parser.add_argument("--enable-gpu", action="store_true",
                        help="Enable GPU acceleration in Qt WebEngine (default)")
    parser.add_argument("--mouse-through", dest="mouse_through", action="store_true", default=True,
                        help="Let mouse events pass through to desktop icons (default)")
    parser.add_argument("--no-mouse-through", dest="mouse_through", action="store_false",
                        help="Let the HTML wallpaper receive mouse events")
    return parser.parse_args()


# ============================================================================
# Section 7 — Main
# ============================================================================

def main() -> int:
    _log().info("run_html_wallpaper main() entry, pid=%s, platform=%s", os.getpid(), sys.platform)
    args = parse_args()
    path = args.path.strip()
    auto_pause = bool(args.auto_pause)
    gpu_enabled = not bool(args.disable_gpu) or bool(args.enable_gpu)
    mouse_through = bool(args.mouse_through)

    _set_share_opengl_contexts_attribute()
    app = QApplication(sys.argv)
    try:
        app.setApplicationName("ShangBackground HTML Wallpaper")
        app.setApplicationDisplayName("ShangBackground HTML Wallpaper")
    except Exception:
        _log().debug("set application display name failed", exc_info=True)

    view = QWebEngineView()
    try:
        # The top-level QWidget is still painted by Qt before Chromium presents
        # its first frame.  Disable that system/palette background fill so the
        # transparent QWebEnginePage below is not hidden by an initial white
        # QWidget surface.
        view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        view.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        view.setAutoFillBackground(False)
    except Exception:
        _log().debug("transparent host widget setup failed", exc_info=True)
    page = view.page()

    # ---- WebEngine background colour (anti-white-flash) ----
    # QWebEngineView defaults to an opaque white surface.  Until the HTML
    # page paints, the user sees a bright white rectangle even when the
    # window is fully opaque-1.  Setting the page background to transparent
    # (Qt.transparent) makes the unpainted area show the desktop behind.
    # This must happen BEFORE view.load().
    try:
        from PySide6.QtGui import QColor
        page.setBackgroundColor(QColor(0, 0, 0, 0))  # fully transparent
    except Exception:
        _log().debug("setBackgroundColor failed", exc_info=True)

    # ---- Window setup ----
    # NOTE: showNormal() is deferred until AFTER WorkerW reparent to avoid the
    # brief flash where the window appears as a normal top-level Tool window
    # before being re-parented behind the desktop icons. We also start with
    # opacity=0 so even the first paint isn't visible.
    view.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
    view.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    screen = QGuiApplication.primaryScreen()
    if screen is None:
        sys.stderr.write("No screen available.\n")
        return 1
    view.setGeometry(screen.virtualGeometry())

    # Start invisible; we'll fade in after reparent.
    try:
        view.setWindowOpacity(0.0)
    except Exception:
        _log().debug("setWindowOpacity(0) failed", exc_info=True)

    # ---- Win32 styling + WorkerW embedding (BEFORE show) ----
    # We need the HWND before showing, but winId() actually creates the
    # native window. That's fine — creating it doesn't make it visible.
    try:
        wid = view.winId()
        hwnd = int(wid) if wid else 0
    except Exception:
        hwnd = 0

    _apply_window_styles(hwnd)

    # Default mouse-through ON so desktop icons stay clickable.  The options
    # polling timer below will adjust this if the user toggles it from the GUI.
    _set_mouse_through(hwnd, enable=mouse_through)

    # Show the window (still at opacity 0) so the WebView starts loading,
    # then immediately reparent. Order matters: reparent needs a visible
    # window on Windows for SetParent to take effect on the z-order.
    view.showNormal()
    embedded = _reparent_to_workerw(hwnd)
    if embedded:
        _refit_to_workerw(hwnd, force=True)
        if _LIFECYCLE_AVAILABLE:
            try:
                page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
            except Exception:
                _log().debug("ignored exception", exc_info=True)
    else:
        _lower_to_bottom(hwnd)

    # ---- State shared by timers ----
    state: dict[str, Any] = {
        "frozen": False,
        "auto_pause": auto_pause,
        "mouse_through": mouse_through,
        "embedded": embedded,
        "known_workerw": _find_workerw(spawn=False) if embedded else 0,
        "gpu_enabled": gpu_enabled,
        "last_event_pause": None,
        "loaded": False,
        "ready_for_pause": False,
        "started_at": time.monotonic(),
    }

    def _mark_surface_visible() -> None:
        state["ready_for_pause"] = True

    frame_gate = _FirstFrameGate(view, page, _mark_surface_visible)

    # ---- Platform info for API injection ----
    def _platform_info() -> dict[str, Any]:
        current_screen = QGuiApplication.primaryScreen() or screen
        geo = current_screen.virtualGeometry()
        return {
            "version": APP_VERSION,
            "platform": "windows",
            "screen": {
                "width": geo.width(),
                "height": geo.height(),
                "dpi": current_screen.logicalDotsPerInch(),
            },
            "auto_pause": state["auto_pause"],
            "gpu_enabled": state["gpu_enabled"],
            "mouse_through": state["mouse_through"],
        }

    def _inject_api() -> None:
        try:
            page.runJavaScript(_build_api_injection_script(_platform_info()))
        except Exception:
            _log().debug("ignored exception", exc_info=True)
        # v1.4.8: 注入 AudioContext 拦截，追踪所有创建的 AudioContext
        # 以便 freeze 时能 suspend 它们
        try:
            page.runJavaScript(
                "(function(){try{"
                "if(window._shangbgAudioHooked)return;"
                "window._shangbgAudioHooked=true;"
                "window._shangbgAudioCtxs=[];"
                "var OAC=window.OfflineAudioContext;"
                "var AC=window.AudioContext||window.webkitAudioContext;"
                "if(AC){"
                "  var origAC=AC;"
                "  window.AudioContext=function(){"
                "    var ctx=new origAC();"
                "    try{window._shangbgAudioCtxs.push(ctx);}catch(e){}"
                "    return ctx;"
                "  };"
                "  window.AudioContext.prototype=origAC.prototype;"
                "  if(window.webkitAudioContext){window.webkitAudioContext=window.AudioContext;}"
                "}"
                "}catch(e){}})();"
            )
        except Exception:
            _log().debug("AudioContext hook failed", exc_info=True)

    def _on_load_started() -> None:
        state["loaded"] = False
        state["ready_for_pause"] = False
        if state.get("frozen"):
            _thaw_page(page)
            state["frozen"] = False
        frame_gate.begin_navigation()

    def _on_load_finished(ok: bool) -> None:
        state["loaded"] = bool(ok)
        _log().info("HTML loadFinished ok=%s url=%s", bool(ok), page.url().toString())
        if not ok:
            return
        _inject_api()
        frame_gate.finish_navigation(True)

    def _on_render_process_terminated(status: Any, exit_code: int) -> None:
        _log().error("HTML renderer terminated: %s / %s", status, exit_code)
        state["loaded"] = False
        state["ready_for_pause"] = False
        state["frozen"] = False
        frame_gate.begin_navigation()
        QTimer.singleShot(500, view.reload)

    view.loadStarted.connect(_on_load_started)
    view.loadFinished.connect(_on_load_finished)
    try:
        page.renderProcessTerminated.connect(_on_render_process_terminated)
    except Exception:
        _log().debug("renderProcessTerminated signal unavailable", exc_info=True)

    # Also inject once shortly after startup in case a cached/local page loaded
    # unusually quickly, and after renderer recovery reloads.
    QTimer.singleShot(800, _inject_api)

    # ---- Load content (after signal hookup) ----
    # Determine scheme to support http/https/file URIs and plain paths.  If
    # path begins with a recognised URI scheme (http, https or file) load
    # it directly via QUrl.  Otherwise treat it as a local file path.
    _lower = path.lower()
    if _lower.startswith(("http://", "https://", "file://")):
        view.load(QUrl(path))
    else:
        view.load(QUrl.fromLocalFile(os.path.abspath(path)))

    # ---- Options polling ----
    def _poll_options() -> None:
        try:
            data = _read_options()
        except Exception:
            return
        if "auto_pause" in data:
            new_ap = bool(data["auto_pause"])
            if new_ap != state["auto_pause"]:
                state["auto_pause"] = new_ap
        if "mouse_through" in data:
            new_mt = bool(data["mouse_through"])
            if new_mt != state["mouse_through"]:
                state["mouse_through"] = new_mt
                try:
                    wid2 = view.winId()
                    hwnd2 = int(wid2) if wid2 else 0
                    _set_mouse_through(hwnd2, enable=new_mt)
                except Exception:
                    _log().debug("ignored exception", exc_info=True)

    # ---- Maintenance timer (5s) ----
    def _maintain() -> None:
        if view.isMinimized() or not view.isVisible():
            return
        try:
            wid2 = view.winId()
            cur_hwnd = int(wid2) if wid2 else 0
        except Exception:
            cur_hwnd = 0
        if not cur_hwnd:
            return

        try:
            cur_parent = int(_user32.GetParent(cur_hwnd) or 0)
        except Exception:
            cur_parent = 0

        if cur_parent:
            # We have a parent (presumably WorkerW).  Re-embed only if parent
            # changed unexpectedly.
            if cur_parent != state["known_workerw"]:
                new_workerw = _find_workerw(spawn=False)
                if new_workerw and new_workerw != cur_parent:
                    # Use the WM_SETREDRAW-wrapped reparent to suppress
                    # the flicker that occurs during the detach/attach.
                    if _reparent_to_workerw(cur_hwnd, workerw=new_workerw):
                        state["known_workerw"] = new_workerw
                        state["embedded"] = True
                        _refit_to_workerw(cur_hwnd, force=True)
                        if _LIFECYCLE_AVAILABLE:
                            try:
                                page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
                            except Exception:
                                _log().debug("ignored exception", exc_info=True)
                else:
                    state["known_workerw"] = cur_parent
            _refit_to_workerw(cur_hwnd)  # no-op if size unchanged
        else:
            # Lost parent — try to re-embed.  Avoid poking Progman every
            # maintenance tick; normally a non-spawning lookup is enough.
            global _last_workerw_spawn_at
            now = time.monotonic()
            should_spawn = (now - _last_workerw_spawn_at) >= _WORKERW_RESPAWN_DEBOUNCE_SEC
            new_workerw = _find_workerw(spawn=should_spawn)
            if should_spawn:
                _last_workerw_spawn_at = now
            if new_workerw:
                # Same WM_SETREDRAW-wrapped reparent as above.
                if _reparent_to_workerw(cur_hwnd, workerw=new_workerw):
                    state["known_workerw"] = new_workerw
                    state["embedded"] = True
                    _refit_to_workerw(cur_hwnd, force=True)
                    if _LIFECYCLE_AVAILABLE:
                        try:
                            page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
                        except Exception:
                            _log().debug("ignored exception", exc_info=True)
            elif not state["embedded"]:
                # Never embedded and still no WorkerW — fallback.
                _lower_to_bottom(cur_hwnd)

    maintain_timer = QTimer()
    maintain_timer.setInterval(5000)
    maintain_timer.timeout.connect(_maintain)
    maintain_timer.start()
    QTimer.singleShot(1000, _maintain)

    # ---- Desktop visibility monitor (3s) — auto-pause + event dispatch ----
    # v1.4.8: 只在状态变化时记日志（freeze/thaw 转换），不每 15 秒输出。

    def _tick_visibility() -> None:
        _poll_options()

        if (not state.get("loaded") or not state.get("ready_for_pause") or (time.monotonic() - float(state.get("started_at", 0.0)) < 1.8)):
            if state.get("frozen"):
                _thaw_page(page)
                state["frozen"] = False
            return

        # If auto-pause is disabled, ensure the page is unfrozen and return.
        if not state["auto_pause"]:
            if state["frozen"]:
                _thaw_page(page)
                state["frozen"] = False
                # Notify the page it has been resumed.
                try:
                    page.runJavaScript(_dispatch_event_script("resume"))
                except Exception:
                    _log().debug("ignored exception", exc_info=True)
            return

        # Freeze only when native window enumeration confirms that every
        # display is almost completely covered. Keyboard focus alone is not
        # sufficient: side-by-side windows may leave the desktop visible.
        on_desktop = desktop_is_visible()
        if on_desktop is None:
            # Unsupported/uncertain window systems (notably generic Wayland)
            # must not guess. Keep rendering rather than falsely pausing.
            on_desktop = True
            if not state.get("visibility_probe_warned"):
                state["visibility_probe_warned"] = True
                _log().info("auto-pause: desktop coverage unavailable; keeping HTML active")

        if on_desktop:
            if state["frozen"]:
                _thaw_page(page)
                state["frozen"] = False
                _log().info("auto-pause: RESUMED (desktop visible)")
                try:
                    page.runJavaScript(_dispatch_event_script("resume"))
                except Exception:
                    _log().debug("ignored exception", exc_info=True)
        else:
            if not state["frozen"]:
                _freeze_page(page)
                state["frozen"] = True
                _log().info("auto-pause: FROZEN (all displays covered)")
                try:
                    page.runJavaScript(_dispatch_event_script("pause"))
                except Exception:
                    _log().debug("ignored exception", exc_info=True)

    visibility_timer = QTimer()
    # Poll desktop coverage less frequently to reduce CPU usage.  A 3-second
    # interval maintains responsive pause/resume behaviour while halving
    # timer wake-ups compared to the previous 1.5s default.
    visibility_timer.setInterval(3000)
    visibility_timer.timeout.connect(_tick_visibility)
    visibility_timer.start()
    QTimer.singleShot(3600, _tick_visibility)

    # ---- Keepalive (10s) — triple nudge to defeat residual throttling ----
    def _keep_alive() -> None:
        if state["frozen"]:
            return  # respect auto-pause

        # (1) Force lifecycle Active (idempotent).
        if _LIFECYCLE_AVAILABLE:
            try:
                page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
            except Exception:
                _log().debug("ignored exception", exc_info=True)

        # (2) SetWindowPos no-op to nudge Chromium's visibility tracker.
        try:
            wid2 = view.winId()
            ka_hwnd = int(wid2) if wid2 else 0
            if ka_hwnd:
                _user32.SetWindowPos(
                    ka_hwnd, 0, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
                    | SWP_NOOWNERZORDER | SWP_NOZORDER,
                )
        except Exception:
            _log().debug("ignored exception", exc_info=True)

        # (3) JS heartbeat — forces V8 to wake up.
        try:
            page.runJavaScript("void(0);")
        except Exception:
            _log().debug("ignored exception", exc_info=True)

    keepalive_timer = QTimer()
    # Extend the keepalive interval to 30 seconds.  Chromium pages remain
    # active longer than 10 seconds without input; reducing these
    # heartbeat calls lowers background CPU/GPU utilisation.
    keepalive_timer.setInterval(30000)
    keepalive_timer.timeout.connect(_keep_alive)
    keepalive_timer.start()
    QTimer.singleShot(1500, _keep_alive)

    # ---- Screen-change notification ----
    def _on_screen_changed(_new_screen: Any = None) -> None:
        try:
            _refit_to_workerw(hwnd, force=True)
            page.runJavaScript(_dispatch_event_script("screenchange", _platform_info()["screen"]))
        except Exception:
            _log().debug("screen-change handling failed", exc_info=True)

    def _connect_screen_geometry(screen_obj: Any) -> None:
        try:
            screen_obj.geometryChanged.connect(_on_screen_changed)
            screen_obj.availableGeometryChanged.connect(_on_screen_changed)
        except Exception:
            _log().debug("screen geometry signals unavailable", exc_info=True)

    try:
        app.primaryScreenChanged.connect(_on_screen_changed)
        def _on_screen_added(added: Any) -> None:
            _connect_screen_geometry(added)
            _on_screen_changed(added)

        app.screenAdded.connect(_on_screen_added)
        app.screenRemoved.connect(_on_screen_changed)
        for screen_obj in app.screens():
            _connect_screen_geometry(screen_obj)
    except Exception:
        _log().debug("screen topology signals unavailable", exc_info=True)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
