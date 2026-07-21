"""System-native HTML wallpaper runner.

This runtime deliberately avoids Qt WebEngine.  pywebview is forced to the
platform renderer so a packaged build uses WebView2 on Windows, WKWebView on
macOS, and WebKitGTK on Linux.  Desktop-layer placement remains owned by the
small platform adapters in ``backends/<platform>/native_webview_desktop.py``.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from typing import Any, TextIO

_SOURCE_ROOT = str(Path(__file__).resolve().parents[1])
if _SOURCE_ROOT not in sys.path:
    sys.path.insert(0, _SOURCE_ROOT)

_LOG_STREAM: TextIO | None = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ShangBackground native HTML wallpaper runtime")
    parser.add_argument("--path", default="")
    parser.add_argument("--auto-pause", action="store_true")
    parser.add_argument("--frame-rate", type=int, choices=(0, 15, 24, 30, 45, 60), default=30)
    mouse = parser.add_mutually_exclusive_group()
    mouse.add_argument("--mouse-through", dest="mouse_through", action="store_true", default=None)
    mouse.add_argument("--no-mouse-through", dest="mouse_through", action="store_false")
    parser.add_argument("--log-file", default="")
    parser.add_argument("--windowed-test-mode", action="store_true")
    parser.add_argument("--exit-after", type=float, default=0.0, metavar="SECONDS")
    parser.add_argument("--test-width", type=int, default=960)
    parser.add_argument("--test-height", type=int, default=640)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Print a dependency/backend probe without opening a native window.",
    )
    return parser.parse_args(argv)


def _open_log(path: str) -> None:
    global _LOG_STREAM
    if not path:
        return
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    _LOG_STREAM = target.open("a", encoding="utf-8", buffering=1)


def _emit(message: str, *, level: str = "INFO") -> None:
    stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
    line = f"[{stamp}] [{level}] [native-html] {message}"
    print(line, file=sys.stderr, flush=True)
    if _LOG_STREAM is not None:
        try:
            _LOG_STREAM.write(line + "\n")
        except Exception:
            pass


def _platform_id() -> str:
    from platform_adapters.html_runtime import platform_id

    return platform_id()


def _forced_gui(platform: str) -> str:
    return {"windows": "edgechromium", "macos": "cocoa", "linux": "gtk"}[platform]


def _desktop_backend(platform: str):
    return importlib.import_module(f"platform_adapters.backends.{platform}.native_webview_desktop")


def _source_url(raw: str) -> str:
    from platform_adapters.html_runtime import normalize_html_source

    source = normalize_html_source(raw)
    if source is None:
        raise ValueError("unsupported or missing HTML source")
    return source


def _read_options_if_changed(path: str, cache: dict[str, object]) -> dict[str, Any] | None:
    """Read options only when the file identity changes.

    The worker still performs a cheap stat, but avoids decoding and parsing JSON
    every 0.8 seconds while settings are unchanged.
    """
    target = Path(path)
    try:
        stat = target.stat()
        identity = (int(stat.st_mtime_ns), int(stat.st_size))
    except OSError:
        identity = None
    if cache.get("identity") == identity:
        return None
    cache["identity"] = identity
    if identity is None:
        return {}
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}




def _profile_path_for_source(source: str, *, max_profiles: int = 12) -> Path:
    """Return an isolated WebView profile and prune old unused profiles.

    Long-lived wallpaper URLs can otherwise create an unbounded collection of
    cache/cookie directories, especially when query parameters rotate. Only
    direct child directories under the application-controlled profile root are
    considered, symlinks are ignored, and the active profile is never removed.
    """
    from app.paths import app_data_path

    profile_root = Path(app_data_path("native-webview-profile"))
    profile_root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(source.encode("utf-8", errors="surrogatepass")).hexdigest()[:20]
    current = profile_root / key
    try:
        candidates = [
            item
            for item in profile_root.iterdir()
            if item.is_dir() and not item.is_symlink() and item != current
        ]
        candidates.sort(
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )
        for stale in candidates[max(0, int(max_profiles) - 1):]:
            try:
                shutil.rmtree(stale)
            except OSError:
                pass
    except OSError:
        pass
    return current


def dependency_probe(
    platform: str | None = None,
    *,
    load_runtime: bool | None = None,
) -> dict[str, object]:
    from platform_adapters.html_runtime import (
        NATIVE_RUNTIME,
        missing_runtime_modules,
        runtime_import_errors,
    )

    target = platform or _platform_id()
    missing = missing_runtime_modules(NATIVE_RUNTIME, platform=target)
    if load_runtime is None:
        load_runtime = target == _platform_id()
    import_errors = runtime_import_errors(NATIVE_RUNTIME, platform=target) if load_runtime else {}
    backend = f"platform_adapters.backends.{target}.native_webview_desktop"
    backend_error = ""
    environment_error = ""
    try:
        backend_module = importlib.import_module(backend)
        checker = getattr(backend_module, "environment_error", None)
        if load_runtime and callable(checker):
            environment_error = str(checker() or "")
    except Exception as exc:
        backend_error = f"{type(exc).__name__}: {exc}"
    return {
        "runtime": "native",
        "platform": target,
        "gui": _forced_gui(target),
        "missing_modules": list(missing),
        "runtime_import_errors": import_errors,
        "desktop_backend": backend,
        "desktop_backend_error": backend_error,
        "environment_error": environment_error,
        "healthy": not missing and not import_errors and not backend_error and not environment_error,
    }


def _safe_run_js(window, script: str) -> None:
    try:
        window.run_js(script)
    except Exception as exc:
        _emit(f"JavaScript injection failed: {exc}", level="WARNING")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _open_log(args.log_file)
    platform = _platform_id()
    if args.self_test:
        payload = dependency_probe(platform)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload["healthy"] else 2
    if not str(args.path).strip():
        _emit("--path is required unless --self-test is used", level="ERROR")
        return 2

    probe = dependency_probe(platform)
    if not probe["healthy"]:
        _emit("native runtime dependency probe failed: " + json.dumps(probe, ensure_ascii=False), level="ERROR")
        return 3
    if (
        platform == "linux"
        and (os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY")))
        and os.environ.get("SHANGBACKGROUND_ALLOW_UNSAFE_WAYLAND_HTML") != "1"
    ):
        _emit(
            "native WebKitGTK wallpaper currently requires X11; Wayland desktop-layer placement is unavailable",
            level="ERROR",
        )
        return 4

    import webview  # pyright: ignore[reportMissingImports]  # optional build feature

    # Pin security-sensitive pywebview settings instead of relying on upstream
    # defaults, which may change between releases.  File access is enabled only
    # for a validated local wallpaper source.
    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["IGNORE_SSL_ERRORS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

    from app.paths import app_data_path
    from app.version import APP_VERSION
    from platform_adapters.html_runtime import (
        build_frame_limiter_script,
        build_wallpaper_api_script,
        build_wallpaper_event_script,
        default_html_mouse_through,
    )

    backend = _desktop_backend(platform)
    mouse_through = (
        default_html_mouse_through(platform)
        if args.mouse_through is None
        else bool(args.mouse_through)
    )
    if args.windowed_test_mode:
        geometry = (80, 80, max(320, args.test_width), max(240, args.test_height))
    else:
        geometry = tuple(int(value) for value in backend.virtual_geometry())
    x, y, width, height = geometry
    try:
        source = _source_url(args.path)
    except ValueError as exc:
        _emit(f"invalid HTML source: {exc}", level="ERROR")
        return 2

    from platform_adapters.html_runtime import describe_html_source

    webview.settings["ALLOW_FILE_URLS"] = source.lower().startswith("file:")

    _emit(
        f"starting platform={platform} gui={_forced_gui(platform)} source={describe_html_source(source)!r} "
        f"geometry={geometry} auto_pause={bool(args.auto_pause)} mouse_through={bool(mouse_through)} "
        f"frame_rate={int(args.frame_rate)}"
    )
    window = webview.create_window(
        "ShangBackground Native HTML Wallpaper",
        url=source,
        width=width,
        height=height,
        x=x,
        y=y,
        resizable=False,
        frameless=True,
        easy_drag=False,
        shadow=False,
        focus=bool(args.windowed_test_mode),
        on_top=False,
        background_color="#000000",
        text_select=False,
        zoomable=False,
    )
    if window is None:
        _emit("pywebview did not create a window", level="ERROR")
        return 5

    stop_event = threading.Event()
    loaded_event = threading.Event()
    configured_event = threading.Event()
    state: dict[str, Any] = {
        "mouse_through": bool(mouse_through),
        "auto_pause": bool(args.auto_pause),
        "frame_rate": int(args.frame_rate),
        "paused": False,
        "configured": bool(args.windowed_test_mode),
        "window_id": None,
        "coverage_warning_logged": False,
        "covered_samples": 0,
        "visible_samples": 0,
    }
    api_script = build_wallpaper_api_script(
        {
            "version": APP_VERSION,
            "platform": platform,
            "runtime": "native",
            "screen": {"x": x, "y": y, "width": width, "height": height},
            "auto_pause": bool(args.auto_pause),
            "gpu_enabled": True,
            "mouse_through": bool(mouse_through),
            "frame_rate": int(args.frame_rate),
        }
    )
    pause_script = build_wallpaper_event_script("pause", {})
    resume_script = build_wallpaper_event_script("resume", {})

    def on_before_show() -> None:
        try:
            if args.windowed_test_mode:
                state["configured"] = True
                return
            state["configured"] = bool(backend.configure(window.native, bool(mouse_through)))
            state["window_id"] = backend.native_window_id(window.native)
        except Exception as exc:
            state["configured"] = False
            _emit(f"desktop-layer configuration failed: {exc}", level="ERROR")
        finally:
            configured_event.set()

    def on_loaded() -> None:
        _safe_run_js(window, api_script)
        _safe_run_js(window, build_frame_limiter_script(int(state["frame_rate"])))
        # A coverage probe can finish before a slow remote page emits ``loaded``.
        # Replaying the state here guarantees the newly installed limiter and
        # media hooks observe the pending pause.
        if bool(state["paused"]):
            _safe_run_js(window, pause_script)
        loaded_event.set()
        _emit("HTML loadFinished ok=True")

    def on_closed() -> None:
        stop_event.set()

    window.events.before_show += on_before_show
    window.events.loaded += on_loaded
    window.events.closed += on_closed

    def set_pause_state(paused: bool) -> None:
        paused = bool(paused)
        if paused == bool(state["paused"]):
            return
        state["paused"] = paused
        try:
            set_visible = getattr(backend, "set_render_visible", None)
            if paused:
                _safe_run_js(window, pause_script)
                if callable(set_visible) and not args.windowed_test_mode:
                    set_visible(window.native, False)
            else:
                if callable(set_visible) and not args.windowed_test_mode:
                    set_visible(window.native, True)
                _safe_run_js(window, resume_script)
        except Exception as exc:
            _emit(f"render visibility update failed: {exc}", level="WARNING")
            _safe_run_js(window, pause_script if paused else resume_script)
        _emit("PAUSED (all displays covered)" if paused else "RESUMED (desktop visible)")

    def background_worker() -> None:
        # pywebview starts this worker before it creates the native window.
        # Waiting for ``before_show`` avoids racing the desktop attachment and
        # destroying a healthy window while ``window.native`` is still unset.
        if not configured_event.wait(timeout=12.0):
            _emit("native window configuration timed out", level="ERROR")
            try:
                window.destroy()
            except Exception:
                pass
            return
        if not bool(state["configured"]):
            _emit("native window could not be attached to the desktop layer", level="ERROR")
            try:
                window.destroy()
            except Exception:
                pass
            return
        options_file = app_data_path("html_wallpaper_options.json")
        started = time.monotonic()
        next_visibility = 0.0
        options_cache: dict[str, object] = {}
        while not stop_event.wait(0.8):
            if args.exit_after > 0 and time.monotonic() - started >= args.exit_after:
                _emit(f"automatic exit after {args.exit_after:.1f}s")
                try:
                    window.destroy()
                except Exception:
                    pass
                return
            changed = _read_options_if_changed(options_file, options_cache)
            values = changed or {}
            if changed is not None:
                state["auto_pause"] = bool(values.get("auto_pause", state["auto_pause"]))
            try:
                requested_fps = int(values.get("frame_rate", state["frame_rate"]))
            except (TypeError, ValueError):
                requested_fps = int(state["frame_rate"])
            if requested_fps not in {0, 15, 24, 30, 45, 60}:
                requested_fps = 30
            if requested_fps != int(state["frame_rate"]):
                state["frame_rate"] = requested_fps
                _safe_run_js(window, build_frame_limiter_script(requested_fps))
                _emit(f"frame rate limit updated: {requested_fps or 'unlimited'}")
            if not bool(state["auto_pause"]):
                state["covered_samples"] = 0
                state["visible_samples"] = 0
                set_pause_state(False)
                continue
            if not loaded_event.is_set() or args.windowed_test_mode or time.monotonic() < next_visibility:
                continue
            next_visibility = time.monotonic() + 1.25
            try:
                from platform_adapters.desktop_visibility import desktop_is_visible

                window_id = state.get("window_id")
                excluded = {int(window_id)} if isinstance(window_id, int) and window_id > 0 else set()
                visible = desktop_is_visible(excluded_window_ids=excluded)
                if visible is None:
                    state["covered_samples"] = 0
                    state["visible_samples"] = 0
                    if not state["coverage_warning_logged"]:
                        state["coverage_warning_logged"] = True
                        _emit("auto-pause coverage unavailable; keeping HTML active", level="WARNING")
                    set_pause_state(False)
                elif visible:
                    state["coverage_warning_logged"] = False
                    state["covered_samples"] = 0
                    state["visible_samples"] = int(state["visible_samples"]) + 1
                    # Resume promptly; pausing requires two consecutive covered
                    # samples to reject transient shell/window geometry changes.
                    if int(state["visible_samples"]) >= 1:
                        set_pause_state(False)
                else:
                    state["coverage_warning_logged"] = False
                    state["visible_samples"] = 0
                    state["covered_samples"] = int(state["covered_samples"]) + 1
                    if int(state["covered_samples"]) >= 2:
                        set_pause_state(True)
            except Exception as exc:
                state["covered_samples"] = 0
                state["visible_samples"] = 0
                if not state["coverage_warning_logged"]:
                    state["coverage_warning_logged"] = True
                    _emit(f"auto-pause probe failed: {exc}", level="WARNING")
                set_pause_state(False)

    try:
        profile_path = os.fspath(_profile_path_for_source(source))
        webview.start(
            background_worker,
            gui=_forced_gui(platform),
            debug=False,
            private_mode=False,
            # Isolate cookies/cache/local storage between unrelated local and
            # remote wallpapers. This also bounds profile corruption impact.
            storage_path=profile_path,
        )
    except Exception as exc:
        _emit(f"native webview startup failed: {type(exc).__name__}: {exc}", level="ERROR")
        return 6
    finally:
        stop_event.set()
    if not loaded_event.is_set():
        _emit("native window exited before a page load completed", level="WARNING")
    _emit("runtime exiting code=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
