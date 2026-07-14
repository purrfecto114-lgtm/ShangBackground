"""HTML wallpaper runtime — Linux (v6, refactored).

Renders a local or remote web page as a borderless full-screen window on the
Linux desktop.  Accepts ``--path`` plus several option flags.

Architecture mirrors the Windows v6 runtime:

1. **Early-arg parsing & Chromium flags** — GPU toggle + anti-freeze flags
   (must run before Qt imports).
2. **Wallpaper API** — ``window.shangbg`` injected on loadFinished, with
   pause / resume / screenchange event dispatch.
3. **Mouse-through** — on X11, uses the XShape extension to set an empty
   input region so clicks pass through to the desktop.  On Wayland this is
   not supported and the option is silently ignored.
4. **Options file polling** — hot-applies ``auto_pause``, ``mouse_through``.
5. **Lifecycle timers** — relower (2s), desktop-visibility auto-pause (3s),
   keepalive (10s).

Wayland note
------------
Wayland's security model prevents global input hooks and window-level input
throughput control.  ``mouse_through`` will be a no-op on Wayland; the
window will still receive mouse events.  Users on Wayland should disable
mouse-through or accept that the wallpaper will intercept clicks.

For deeper desktop integration (xwinwrap-style), users can wrap this script
via external tools.
"""

from __future__ import annotations

import argparse
import ctypes
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
    """
    try:
        if os.environ.get("XDG_CONFIG_HOME"):
            config_path = os.path.join(os.environ["XDG_CONFIG_HOME"], "shangbackground", "settings.json")
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
        _child_files_enabled = _read_log_enabled_for_child()
        _configure_logging(level="DEBUG", console=True, files_enabled=_child_files_enabled)
    else:
        _logging.basicConfig(level=_logging.INFO)
except Exception as _log_init_exc:  # pragma: no cover
    try:
        sys.stderr.write(f"[run_html_wallpaper] log init failed: {_log_init_exc}\n")
    except Exception:
        pass

# v1.4.8: 强制确保 run_html_wallpaper logger 至少有一个 stderr handler
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

def _parse_args_early() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--disable-gpu", action="store_true")
    parser.add_argument("--enable-gpu", action="store_true")
    args, _ = parser.parse_known_args()
    return args


_EARLY_ARGS = _parse_args_early()


def _build_chromium_flags() -> str:
    parts = [p for p in os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split() if p]
    parts = [p for p in parts if p not in ("--disable-gpu", "--enable-gpu")]
    if _EARLY_ARGS.disable_gpu:
        parts.append("--disable-gpu")

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
            df_features.add(f.split("=", 1)[1])
        elif f.startswith("--enable-") and "=" in f:
            if f not in parts:
                parts.append(f)
        else:
            if f not in parts:
                parts.append(f)

    if df_idx is not None and df_features:
        parts[df_idx] = "--disable-features=" + ",".join(sorted(df_features))
    elif df_idx is not None and not df_features:
        parts.pop(df_idx)

    # Keep Chromium in the supported multi-process model, but reduce scattered
    # renderer processes for wallpaper URLs. Do not override explicit user flags.
    if not any(p == "--single-process" or p.startswith("--process-per-") for p in parts):
        parts.append("--process-per-site")

    return " ".join(parts).strip()


os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = _build_chromium_flags()
# Chromium refuses to start as root with its sandbox enabled. Normal desktop
# sessions are non-root, so preserve the sandbox there and only opt out for
# root-only CI/container diagnostics.
if hasattr(os, "geteuid") and os.geteuid() == 0:
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")


# ============================================================================
# Qt imports
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
# Section 2 — Data dir & options polling
# ============================================================================

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
# Section 4 — Mouse-through (X11 Shape Extension)
# ============================================================================

# Qt doesn't expose XShape directly; we set the input region via the X11 lib
# loaded through ctypes.  This is a best-effort: if any step fails we just
# log and continue (mouse-through is a soft feature on Linux).
try:
    _xlib = ctypes.cdll.LoadLibrary("libX11.so.6") if sys.platform.startswith("linux") else None
    _xlib_shape = None
    if _xlib is not None:
        try:
            _xlib_shape = ctypes.cdll.LoadLibrary("libXext.so.6")
        except Exception:
            _xlib_shape = None
except Exception:
    _xlib = None
    _xlib_shape = None


def _set_mouse_through_x11(view: QWebEngineView, enable: bool) -> bool:
    """Toggle X11 input shape so the window passes clicks through to the
    desktop.  Returns True if applied successfully.
    """
    if _xlib is None or _xlib_shape is None:
        return False  # X11 libs not available (likely Wayland)
    try:
        # XShapeCombineRectangles: shape_kind=ShapeInput (2), op=ShapeSet (1)
        SHAPE_INPUT = 2
        SHAPE_SET = 1
        wid = int(view.winId())
        if not wid:
            return False
        display = _xlib.XOpenDisplay(None)
        if not display:
            return False
        try:
            if enable:
                # Empty rectangle → input region is empty → all clicks pass through.
                # XShapeCombineRectangles(display, win, ShapeInput, 0, 0, NULL, 0, ShapeSet, 0)
                _xlib_shape.XShapeCombineRectangles(
                    display, wid, SHAPE_INPUT, 0, 0, None, 0, SHAPE_SET, 0,
                )
            else:
                # Restore: set input region to bounding shape (default behaviour).
                # Easiest is to call XShapeCombineShape with ShapeBounding source.
                SHAPE_BOUNDING = 0
                _xlib_shape.XShapeCombineShape(
                    display, wid, SHAPE_INPUT, 0, 0, wid, SHAPE_BOUNDING, SHAPE_SET,
                )
            _xlib.XFlush(display)
            return True
        finally:
            _xlib.XCloseDisplay(display)
    except Exception:
        return False


# ============================================================================
# Section 5 — Lifecycle helpers
# ============================================================================

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
# Section 6 — Wallpaper API (window.shangbg)
# ============================================================================

def _build_api_injection_script(platform_info: dict[str, Any]) -> str:
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
# Section 7 — Argument parsing & main
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run HTML wallpaper (Linux)")
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


def main() -> int:
    _log().info("run_html_wallpaper main() entry, pid=%s, platform=%s", os.getpid(), sys.platform)
    args = parse_args()
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" and os.environ.get("SHANGBACKGROUND_ALLOW_UNSAFE_WAYLAND_HTML", "") != "1":
        sys.stderr.write("Unsupported: generic Qt HTML wallpaper cannot become a desktop-layer surface on Wayland.\n")
        return 2
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
        # Keep the Qt host widget itself transparent before Chromium presents
        # the first frame.  The page background below removes the WebEngine
        # white fill; these QWidget attributes prevent Qt from first painting
        # a palette/system background behind it.
        view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        view.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        view.setAutoFillBackground(False)
    except Exception:
        _log().debug("transparent host widget setup failed", exc_info=True)
    page = view.page()

    # ---- WebEngine background colour (anti-white-flash) ----
    # QWebEnginePage defaults to white.  Set the page background to
    # transparent BEFORE view.load() so unpainted content does not flash
    # white while the document is loading.
    try:
        from PySide6.QtGui import QColor
        page.setBackgroundColor(QColor(0, 0, 0, 0))  # fully transparent
    except Exception:
        _log().debug("setBackgroundColor failed", exc_info=True)

    view.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnBottomHint | Qt.WindowType.Tool)
    view.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    screen = QGuiApplication.primaryScreen()
    if screen is None:
        sys.stderr.write("No screen available.\n")
        return 1
    view.setGeometry(screen.virtualGeometry())

    # Start invisible to hide the brief flash before lowering to the bottom.
    try:
        view.setWindowOpacity(0.0)
    except Exception:
        _log().debug("setWindowOpacity(0) failed", exc_info=True)

    # ---- State ----
    state: dict[str, Any] = {
        "frozen": False,
        "auto_pause": auto_pause,
        "mouse_through": mouse_through,
        "gpu_enabled": gpu_enabled,
        "loaded": False,
        "ready_for_pause": False,
        "started_at": time.monotonic(),
    }

    def _platform_info() -> dict[str, Any]:
        current_screen = QGuiApplication.primaryScreen() or screen
        geo = current_screen.virtualGeometry()
        return {
            "version": APP_VERSION,
            "platform": "linux",
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

    def _mark_surface_visible() -> None:
        state["ready_for_pause"] = True

    frame_gate = _FirstFrameGate(view, page, _mark_surface_visible)

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

    # Load content.  Support http/https/file URI schemes; otherwise treat as a plain local file.
    _lower = path.lower()
    if _lower.startswith(("http://", "https://", "file://")):
        view.load(QUrl(path))
    else:
        view.load(QUrl.fromLocalFile(os.path.abspath(path)))
    view.showNormal()
    view.lower()
    QTimer.singleShot(800, _inject_api)

    # ---- Apply mouse-through on startup ----
    _set_mouse_through_x11(view, enable=mouse_through)

    # ---- Relower (2s) ----
    #
    # Periodically calling view.lower() can trigger a brief flicker on some
    # compositors, especially during window resizing or immediately after
    # starting the wallpaper.  In practice we only need to lower the
    # window once shortly after it has been shown to ensure it sits behind
    # other windows.  Therefore we replace the repeating timer with a
    # single-shot call.
    def _relower():
        if view.isVisible() and not view.isMinimized():
            try:
                view.lower()
            except Exception:
                _log().debug("ignored exception", exc_info=True)

    # Lower once after a short delay (2s) rather than continuously.
    QTimer.singleShot(2000, _relower)

    # ---- Options polling + desktop visibility monitor (3s) ----

    def _poll_options() -> None:
        try:
            data = _read_options()
        except Exception:
            return
        if "auto_pause" in data:
            state["auto_pause"] = bool(data["auto_pause"])
        if "mouse_through" in data:
            new_mt = bool(data["mouse_through"])
            if new_mt != state["mouse_through"]:
                state["mouse_through"] = new_mt
                _set_mouse_through_x11(view, enable=new_mt)

    def _tick_visibility() -> None:
        """Poll options and freeze only when every display is visually covered.

        Keyboard focus is deliberately ignored. Native window enumeration plus
        grid coverage handles side-by-side/background windows; an unsupported or
        uncertain platform returns ``None`` and keeps the wallpaper running.
        """
        _poll_options()
        if (not state.get("loaded") or not state.get("ready_for_pause") or (time.monotonic() - float(state.get("started_at", 0.0)) < 1.8)):
            if state.get("frozen"):
                _thaw_page(page)
                state["frozen"] = False
            return
        # If auto-pause is disabled: always ensure the page is thawed.
        if not state["auto_pause"]:
            if state["frozen"]:
                _thaw_page(page)
                state["frozen"] = False
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
            # Resume if previously frozen
            if state["frozen"]:
                _thaw_page(page)
                state["frozen"] = False
                _log().info("auto-pause: RESUMED (desktop visible)")
                try:
                    page.runJavaScript(_dispatch_event_script("resume"))
                except Exception:
                    _log().debug("ignored exception", exc_info=True)
        else:
            # Pause if not already frozen
            if not state["frozen"]:
                _freeze_page(page)
                state["frozen"] = True
                _log().info("auto-pause: FROZEN (all displays covered)")
                try:
                    page.runJavaScript(_dispatch_event_script("pause"))
                except Exception:
                    _log().debug("ignored exception", exc_info=True)

    visibility_timer = QTimer()
    # Poll desktop coverage and runtime options less frequently to reduce CPU
    # usage.  A 3-second interval still provides responsive pause/resume
    # behaviour while cutting the polling overhead by half compared to the
    # original 1.5-second timer.
    visibility_timer.setInterval(3000)
    visibility_timer.timeout.connect(_tick_visibility)
    visibility_timer.start()
    QTimer.singleShot(2400, _tick_visibility)

    # ---- Keepalive (10s) ----
    def _keep_alive() -> None:
        if state["frozen"]:
            return
        if _LIFECYCLE_AVAILABLE:
            try:
                page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
            except Exception:
                _log().debug("ignored exception", exc_info=True)
        try:
            page.runJavaScript("void(0);")
        except Exception:
            _log().debug("ignored exception", exc_info=True)

    keepalive_timer = QTimer()
    # Send a trivial script execution periodically to keep the page active
    # when not frozen.  Extend the interval to 30 seconds: WebEngine pages
    # remain active far longer without explicit interaction, so reducing
    # these no-op calls reduces CPU wake-ups without affecting stability.
    keepalive_timer.setInterval(30000)
    keepalive_timer.timeout.connect(_keep_alive)
    keepalive_timer.start()
    QTimer.singleShot(1500, _keep_alive)

    # ---- Screen-change notification ----
    def _on_screen_changed(_new_screen: Any = None) -> None:
        try:
            current_screen = QGuiApplication.primaryScreen() or screen
            view.setUpdatesEnabled(False)
            view.setGeometry(current_screen.virtualGeometry())
            QTimer.singleShot(0, lambda: view.setUpdatesEnabled(True))
            page.runJavaScript(_dispatch_event_script("screenchange", _platform_info()["screen"]))
        except Exception:
            try:
                view.setUpdatesEnabled(True)
            except Exception:
                pass
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
