"""Complete, platform-specific pywebview hook.

The upstream hook contributes native files but intentionally does not declare
the dynamically selected GUI backend. ShangBackground pins one backend per OS,
so collect that exact chain and avoid Qt/CEF fallback bloat.
"""
from PyInstaller.compat import is_darwin, is_linux, is_win  # pyright: ignore[reportMissingImports]
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs  # pyright: ignore[reportMissingImports]

datas = collect_data_files("webview", subdir="lib") + collect_data_files("webview", subdir="js")
binaries = collect_dynamic_libs("webview")

if is_win:
    hiddenimports = [
        "webview.guilib", "webview.platforms.winforms", "webview.platforms.win32",
        "webview.platforms.edgechromium", "webview.platforms.mshtml",
        "clr", "clr_loader", "pythonnet",
    ]
elif is_darwin:
    hiddenimports = ["webview.guilib", "webview.platforms.cocoa", "AppKit", "WebKit"]
elif is_linux:
    hiddenimports = ["webview.guilib", "webview.platforms.gtk", "gi"]
else:
    hiddenimports = []
