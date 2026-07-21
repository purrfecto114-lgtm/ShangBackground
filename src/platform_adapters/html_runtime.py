"""Shared system-native HTML wallpaper runtime helpers."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit, urlunsplit
from urllib.request import url2pathname


@dataclass(frozen=True, slots=True)
class HtmlRuntimeSpec:
    name: str
    internal_flag: str
    source_module: str
    required_modules: tuple[str, ...]


NATIVE_RUNTIME = HtmlRuntimeSpec(
    name="native",
    internal_flag="--internal-native-html-wallpaper-runner",
    source_module="native_html_runner.py",
    required_modules=("webview",),
)
DISABLED_RUNTIME = HtmlRuntimeSpec(
    name="disabled",
    internal_flag="",
    source_module="",
    required_modules=(),
)

HTML_RUNTIME_NAMES = ("native", "auto", "disabled")

_NATIVE_BACKEND_LABELS = {
    "windows": "WebView2 (Edge Chromium) hosted in WorkerW",
    "macos": "WKWebView hosted in a desktop-level NSWindow",
    "linux": "WebKitGTK hosted in a GTK desktop window on X11",
}


def runtime_backend_label(
    spec: HtmlRuntimeSpec = NATIVE_RUNTIME,
    platform: str | None = None,
) -> str:
    """Return a human-readable backend description for diagnostics and capability UI."""
    if spec.name == DISABLED_RUNTIME.name:
        return "disabled by build profile"
    if spec.name != NATIVE_RUNTIME.name:
        raise ValueError(f"Unsupported HTML runtime: {spec.name}")
    target = platform or platform_id()
    try:
        return _NATIVE_BACKEND_LABELS[target]
    except KeyError as exc:
        raise ValueError(f"Unsupported HTML runtime platform: {target}") from exc


def platform_id() -> str:
    override = os.environ.get("SHANGBACKGROUND_PLATFORM_OVERRIDE", "").strip().lower()
    if override in {"windows", "linux", "macos"}:
        return override
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def default_html_mouse_through(platform: str | None = None) -> bool:
    """Return the platform desktop-layer input policy.

    Windows hosts the WebView below ``SHELLDLL_DefView`` inside WorkerW, so
    desktop icons already remain clickable and forced WS_EX_TRANSPARENT only
    removes useful page interaction.  X11 and macOS desktop-level windows need
    explicit input transparency to avoid taking clicks from the shell.
    """
    return (platform or platform_id()) != "windows"


def native_platform_modules(platform: str | None = None) -> tuple[str, ...]:
    """Return the pywebview backend modules required on *platform*."""
    target = platform or platform_id()
    modules = {
        "windows": ("webview.platforms.edgechromium", "clr"),
        "macos": ("webview.platforms.cocoa", "AppKit", "WebKit"),
        "linux": ("webview.platforms.gtk", "gi"),
    }
    try:
        return modules[target]
    except KeyError as exc:
        raise ValueError(f"Unsupported HTML runtime platform: {target}") from exc


def runtime_modules(
    spec: HtmlRuntimeSpec = NATIVE_RUNTIME,
    platform: str | None = None,
) -> tuple[str, ...]:
    if spec.name == DISABLED_RUNTIME.name:
        return ()
    if spec.name != NATIVE_RUNTIME.name:
        raise ValueError(f"Unsupported HTML runtime: {spec.name}")
    return (*spec.required_modules, *native_platform_modules(platform))


def missing_runtime_modules(
    spec: HtmlRuntimeSpec = NATIVE_RUNTIME,
    *,
    platform: str | None = None,
) -> tuple[str, ...]:
    """Return modules that cannot be resolved without importing the GUI engine."""
    missing: list[str] = []
    for module in runtime_modules(spec, platform):
        try:
            if importlib.util.find_spec(module) is None:
                missing.append(module)
        except (ImportError, ModuleNotFoundError, AttributeError, ValueError):
            missing.append(module)
    return tuple(dict.fromkeys(missing))


def runtime_import_errors(
    spec: HtmlRuntimeSpec = NATIVE_RUNTIME,
    *,
    platform: str | None = None,
) -> dict[str, str]:
    """Import native engine modules and return structured failures.

    ``find_spec`` is intentionally not treated as a runtime check: pywebview's
    GTK module can be discoverable while the WebKitGTK typelib is absent, and
    WebView2/PyObjC backends can fail while loading their native bindings.
    """
    errors: dict[str, str] = {}
    for module in runtime_modules(spec, platform):
        try:
            importlib.import_module(module)
        except Exception as exc:
            errors[module] = f"{type(exc).__name__}: {exc}"
    return errors


def configured_runtime_name() -> str:
    """Return the requested runtime, rejecting removed Qt WebEngine modes."""
    override = os.environ.get("SHANGBACKGROUND_HTML_RUNTIME", "").strip().lower()
    if override:
        if override == "qml":
            raise ValueError("The Qt WebEngine/QML HTML runtime has been removed; use native")
        if override not in HTML_RUNTIME_NAMES:
            raise ValueError("SHANGBACKGROUND_HTML_RUNTIME must be one of: " + ", ".join(HTML_RUNTIME_NAMES))
        return override
    try:
        from app.build_features import html_runtime_name

        value = str(html_runtime_name()).strip().lower()
        if value == "qml":
            raise ValueError("This package requests the removed Qt WebEngine/QML runtime")
        return value if value in HTML_RUNTIME_NAMES else "disabled"
    except ValueError:
        raise
    except Exception:
        # Source checkouts remain convenient, but a packaged executable must not
        # silently enable an optional renderer when its signed build manifest is
        # missing or unreadable.
        try:
            from app.paths import is_packaged_runtime

            if is_packaged_runtime():
                return "disabled"
        except Exception:
            pass
        return "native"


def select_html_runtime(preferred: str | None = None) -> HtmlRuntimeSpec:
    """Return the only supported HTML renderer: the system-native WebView."""
    value = (preferred or configured_runtime_name()).strip().lower()
    if value == "qml":
        raise ValueError("The Qt WebEngine/QML HTML runtime has been removed; use native")
    if value not in HTML_RUNTIME_NAMES:
        raise ValueError(f"Unknown HTML runtime: {value}")
    if value == "disabled":
        return DISABLED_RUNTIME
    return NATIVE_RUNTIME


def source_runtime_path(
    adapter_file: str | os.PathLike[str],
    spec: HtmlRuntimeSpec = NATIVE_RUNTIME,
) -> Path:
    """Resolve the shared native renderer script from a backend adapter file."""
    if spec.name != NATIVE_RUNTIME.name:
        raise ValueError(f"Unsupported HTML runtime: {spec.name}")
    return Path(adapter_file).resolve().parents[2] / spec.source_module


def normalize_html_source(value: str | os.PathLike[str] | None) -> str | None:
    """Return a safe canonical HTML source URL, or ``None`` when unsupported.

    Only existing local ``.html``/``.htm`` files and well-formed HTTP(S)
    URLs are accepted.  Local files are normalized to ``file:`` URLs so all
    three native WebView backends receive the same representation.
    """
    if value is None:
        return None
    raw = os.fspath(value).strip()
    if not raw:
        return None

    # Check a plain filesystem path before URL parsing.  This is essential on
    # Windows, where ``C:\\...`` would otherwise be mistaken for URL scheme
    # ``c``.
    local_candidate = Path(raw).expanduser()
    try:
        if local_candidate.is_file() and local_candidate.suffix.lower() in {".html", ".htm"}:
            return local_candidate.resolve().as_uri()
    except OSError:
        return None

    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"}:
        if not parsed.hostname:
            return None
        return raw
    if scheme != "file":
        return None

    path_text = url2pathname(unquote(parsed.path))
    if parsed.netloc and parsed.netloc.lower() != "localhost":
        path_text = f"//{parsed.netloc}{path_text}"
    local_file = Path(path_text).expanduser()
    try:
        if not local_file.is_file() or local_file.suffix.lower() not in {".html", ".htm"}:
            return None
        return local_file.resolve().as_uri()
    except OSError:
        return None


def describe_html_source(value: str) -> str:
    """Return a log-safe source description without credentials or query tokens."""
    parsed = urlsplit(value)
    if parsed.scheme.lower() in {"http", "https"}:
        host = parsed.hostname or "<invalid-host>"
        if parsed.port:
            host += f":{parsed.port}"
        clean = urlunsplit((parsed.scheme, host, parsed.path, "", ""))
        return clean + ("?<redacted>" if parsed.query else "")
    if parsed.scheme.lower() == "file":
        return f"file://.../{Path(unquote(parsed.path)).name}"
    return "<local-html>"


def build_wallpaper_api_script(platform_info: dict[str, object]) -> str:
    """Return the stable JavaScript API injected by the native renderer."""
    info_json = json.dumps(platform_info, ensure_ascii=False, separators=(",", ":"))
    return f"""
(function() {{
    if (window.shangbg) {{ return; }}
    var info = {info_json};
    window.shangbg = {{
        version: info.version,
        platform: info.platform,
        runtime: info.runtime,
        isWallpaper: true,
        isPreview: false,
        screen: info.screen,
        autoPauseEnabled: info.auto_pause,
        gpuEnabled: info.gpu_enabled,
        mouseThrough: info.mouse_through,
        frameRate: info.frame_rate,
        _listeners: {{}},
        on: function(name, cb) {{
            if (!this._listeners[name]) {{ this._listeners[name] = []; }}
            this._listeners[name].push(cb);
        }},
        off: function(name, cb) {{
            if (!this._listeners[name]) {{ return; }}
            this._listeners[name] = this._listeners[name].filter(function(fn) {{
                return fn !== cb;
            }});
        }},
        _dispatch: function(name, data) {{
            var event = new CustomEvent('shangbg-' + name, {{ detail: data }});
            window.dispatchEvent(event);
            var listeners = this._listeners[name] || [];
            for (var i = 0; i < listeners.length; i++) {{
                try {{ listeners[i](data); }} catch (error) {{ console.error(error); }}
            }}
        }}
    }};
    window.dispatchEvent(new CustomEvent('shangbg-ready', {{ detail: window.shangbg }}));
}})();
""".strip()



def build_frame_limiter_script(frame_rate: int) -> str:
    """Return a host-side RAF limiter with deterministic pause/resume behavior.

    ``0`` means unlimited. The wrapper batches callbacks onto one native frame,
    cancels the native request while paused, and also freezes CSS animations and
    media elements during host pause events.
    """
    try:
        fps = int(frame_rate)
    except (TypeError, ValueError):
        fps = 30
    if fps not in {0, 15, 24, 30, 45, 60}:
        fps = 30
    return f"""
(function() {{
    var requestedFps = {fps};
    if (window.__shangbgFrameLimiter) {{
        window.__shangbgFrameLimiter.setFrameRate(requestedFps);
        return;
    }}
    var nativeRequest = window.requestAnimationFrame.bind(window);
    var nativeCancel = window.cancelAnimationFrame.bind(window);
    var callbacks = new Map();
    var nextId = 1;
    var nativeHandle = 0;
    var lastDispatch = 0;
    var paused = false;
    var frameRate = requestedFps;
    var pausedMedia = new Set();

    var style = document.createElement('style');
    style.id = 'shangbg-host-pause-style';
    style.textContent = 'html.shangbg-host-paused *, html.shangbg-host-paused *::before, html.shangbg-host-paused *::after {{ animation-play-state: paused !important; }}';
    (document.head || document.documentElement).appendChild(style);

    function schedule() {{
        if (paused || nativeHandle || callbacks.size === 0) {{ return; }}
        nativeHandle = nativeRequest(dispatch);
    }}

    function dispatch(timestamp) {{
        nativeHandle = 0;
        if (paused) {{ return; }}
        var interval = frameRate > 0 ? (1000 / frameRate) : 0;
        if (interval > 0 && lastDispatch > 0 && timestamp - lastDispatch + 0.25 < interval) {{
            schedule();
            return;
        }}
        lastDispatch = timestamp;
        var batch = Array.from(callbacks.entries());
        callbacks.clear();
        for (var i = 0; i < batch.length; i++) {{
            try {{ batch[i][1](timestamp); }} catch (error) {{ setTimeout(function() {{ throw error; }}, 0); }}
        }}
        schedule();
    }}

    window.requestAnimationFrame = function(callback) {{
        var id = nextId++;
        callbacks.set(id, callback);
        schedule();
        return id;
    }};
    window.cancelAnimationFrame = function(id) {{
        callbacks.delete(id);
        if (callbacks.size === 0 && nativeHandle) {{
            nativeCancel(nativeHandle);
            nativeHandle = 0;
        }}
    }};

    function pauseMedia() {{
        document.querySelectorAll('audio,video').forEach(function(media) {{
            if (!media.paused && !media.ended) {{
                pausedMedia.add(media);
                try {{ media.pause(); }} catch (_error) {{}}
            }}
        }});
    }}

    function resumeMedia() {{
        var mediaItems = Array.from(pausedMedia);
        pausedMedia.clear();
        mediaItems.forEach(function(media) {{
            try {{ var promise = media.play(); if (promise && promise.catch) {{ promise.catch(function() {{}}); }} }} catch (_error) {{}}
        }});
    }}

    function setPaused(value) {{
        value = !!value;
        if (paused === value) {{ return; }}
        paused = value;
        document.documentElement.classList.toggle('shangbg-host-paused', paused);
        if (paused) {{
            if (nativeHandle) {{ nativeCancel(nativeHandle); nativeHandle = 0; }}
            pauseMedia();
        }} else {{
            lastDispatch = 0;
            resumeMedia();
            schedule();
        }}
    }}

    function setFrameRate(value) {{
        value = Number(value);
        frameRate = [0, 15, 24, 30, 45, 60].indexOf(value) >= 0 ? value : 30;
        if (window.shangbg) {{ window.shangbg.frameRate = frameRate; }}
        lastDispatch = 0;
        schedule();
    }}

    window.addEventListener('shangbg-pause', function() {{ setPaused(true); }});
    window.addEventListener('shangbg-resume', function() {{ setPaused(false); }});
    window.__shangbgFrameLimiter = {{ setPaused: setPaused, setFrameRate: setFrameRate }};
    setFrameRate(requestedFps);
}})();
""".strip()


def build_wallpaper_event_script(event_name: str, data: object | None = None) -> str:
    """Return JavaScript that dispatches a host lifecycle/options event."""
    event_json = json.dumps(str(event_name), ensure_ascii=False)
    data_json = "undefined" if data is None else json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"""
(function() {{
    if (window.shangbg && window.shangbg._dispatch) {{
        window.shangbg._dispatch({event_json}, {data_json});
    }} else {{
        window.dispatchEvent(new CustomEvent('shangbg-' + {event_json}, {{ detail: {data_json} }}));
    }}
}})();
""".strip()
