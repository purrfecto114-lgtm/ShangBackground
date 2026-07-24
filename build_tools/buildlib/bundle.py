"""Shared frozen-module and data-file decisions.

The application intentionally uses pywebview's native platform backend for HTML
wallpaper. Qt Quick/QML/QtWebEngine are not part of that feature and must not be
accidentally dragged back into either bundle.
"""
from __future__ import annotations

from pathlib import Path

from .constants import PROJECT_ROOT
from .plan import BuildPlan

BASE_DYNAMIC_MODULES = (
    "PySide6.QtSvg",
    "ui.main_window",
    "ui.preview_canvas",
    "ui.qt_root_shim",
    "ui.sidebar",
    "ui.probability_dialog",
    "ui.dialog_style",
)

ALWAYS_EXCLUDED_MODULES = (
    "tkinter", "PyQt5", "PyQt6", "PySide2", "matplotlib", "pandas", "scipy",
    "IPython", "notebook", "pytest", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick", "PySide6.QtWebEngineWidgets",
)

WEBVIEW_MODULES = {
    "windows": (
        "webview", "webview.guilib", "webview.platforms.winforms",
        "webview.platforms.win32", "webview.platforms.edgechromium",
        "webview.platforms.mshtml", "clr", "clr_loader", "pythonnet",
    ),
    "linux": ("webview", "webview.guilib", "webview.platforms.gtk", "gi"),
    "macos": ("webview", "webview.guilib", "webview.platforms.cocoa", "AppKit", "WebKit"),
}

WEBVIEW_EXCLUDED_PLATFORMS = {
    "windows": ("webview.platforms.gtk", "webview.platforms.qt", "webview.platforms.cocoa", "webview.platforms.android", "webview.platforms.cef"),
    "linux": ("webview.platforms.winforms", "webview.platforms.win32", "webview.platforms.edgechromium", "webview.platforms.mshtml", "webview.platforms.cocoa", "webview.platforms.android", "webview.platforms.cef"),
    "macos": ("webview.platforms.winforms", "webview.platforms.win32", "webview.platforms.edgechromium", "webview.platforms.mshtml", "webview.platforms.gtk", "webview.platforms.qt", "webview.platforms.android", "webview.platforms.cef"),
}



def assert_plain_json_resources(root: Path | None = None) -> None:
    """Reject gzip payloads that still use a ``.json`` suffix.

    The runtime loader retains magic-byte recovery for old installations, but
    new frozen artifacts must contain normal UTF-8 JSON so every build backend
    and external inspection tool sees the same resource format.
    """
    directory = Path(root) if root is not None else PROJECT_ROOT / "src" / "lang"
    if not directory.is_dir():
        raise RuntimeError(f"language resource directory is missing: {directory}")
    for path in sorted(directory.rglob("*.json")):
        try:
            magic = path.read_bytes()[:2]
        except OSError as exc:
            raise RuntimeError(f"cannot read JSON resource: {path}: {exc}") from exc
        if magic == b"\x1f\x8b":
            raise RuntimeError(
                f"gzip-compressed resource keeps a .json suffix: {path}. "
                "Store plain UTF-8 JSON or rename the payload to .json.gz."
            )


def dynamic_modules(plan: BuildPlan) -> tuple[str, ...]:
    target = plan.target
    modules: list[str] = [
        *BASE_DYNAMIC_MODULES,
        f"platform_adapters.backends.{target}.integration",
        f"platform_adapters.backends.{target}.capabilities",
        f"app.backends.{target}.dependencies",
        f"core.backends.{target}.display",
    ]
    if target == "windows":
        modules.append("platform_adapters.backends.windows.wallpaper_cli")
    if "video" in plan.features:
        modules.extend((
            "app.mpv_backend",
            "platform_adapters.video",
            f"platform_adapters.backends.{target}.video",
        ))
        if target != "macos":
            modules.append("app.libmpv_runtime")
    if "html" in plan.features:
        modules.extend((
            "platform_adapters.html_runtime", "platform_adapters.html_wallpaper",
            f"platform_adapters.backends.{target}.html_wallpaper",
            "platform_adapters.native_html_runner",
            f"platform_adapters.backends.{target}.native_webview_desktop",
            *WEBVIEW_MODULES[target],
        ))
    if "bing" in plan.features:
        modules.extend(("services.bing", "services.bing_sync"))
    if "updates" in plan.features:
        modules.append("services.updates")
    if "hotkeys" in plan.features:
        modules.extend(("platform_adapters.hotkeys", "platform_adapters.hotkey_bindings", f"platform_adapters.backends.{target}.hotkeys"))
        if target == "linux":
            modules.extend(("platform_adapters.backends.linux.portal_hotkeys", "dbus_next"))
    return tuple(dict.fromkeys(modules))


def excluded_modules(plan: BuildPlan) -> tuple[str, ...]:
    modules: list[str] = list(ALWAYS_EXCLUDED_MODULES)
    if "video" not in plan.features:
        modules.extend(("platform_adapters.video", f"platform_adapters.backends.{plan.target}.video", "app.libmpv_runtime", "mpv"))
    if "html" not in plan.features:
        modules.extend(("platform_adapters.html_runtime", "platform_adapters.html_wallpaper", "platform_adapters.native_html_runner", "webview"))
    else:
        modules.extend(WEBVIEW_EXCLUDED_PLATFORMS[plan.target])
    if "bing" not in plan.features:
        modules.extend(("services.bing", "services.bing_sync"))
    if "updates" not in plan.features:
        modules.append("services.updates")
    if "hotkeys" not in plan.features:
        modules.extend(("platform_adapters.hotkeys", "platform_adapters.hotkey_bindings"))
        if plan.target == "linux":
            modules.extend(("platform_adapters.backends.linux.portal_hotkeys", "dbus_next"))
    return tuple(dict.fromkeys(modules))


def data_directories(plan: BuildPlan) -> tuple[tuple[Path, str], ...]:
    assert_plain_json_resources()
    result: list[tuple[Path, str]] = [
        (PROJECT_ROOT / "src" / "img", "img"),
        (PROJECT_ROOT / "src" / "lang", "lang"),
    ]
    fonts = PROJECT_ROOT / "fonts"
    if "fonts" in plan.features and fonts.is_dir():
        result.append((fonts, "fonts"))
    return tuple(result)
