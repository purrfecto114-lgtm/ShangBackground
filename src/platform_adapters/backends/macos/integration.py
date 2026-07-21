from __future__ import annotations

import os
import subprocess
from pathlib import Path



def _run_args(args: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """Run a command without a shell and return (returncode, stdout, stderr).

    Always decodes subprocess output as UTF-8.  ``text=True`` would otherwise
    default to the system locale; on a non-UTF-8 locale this would mangle
    wallpaper paths containing CJK characters when passed to osascript,
    causing the slideshow to stutter on Chinese-named images.
    """
    try:
        result = subprocess.run(
            args,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return -1, "", str(exc)


def _ensure_existing_file(path: str) -> str:
    """Return a direct absolute Unicode path and fail early."""
    try:
        raw = os.fspath(path)
    except (TypeError, ValueError, OSError) as exc:
        raise FileNotFoundError(f"Invalid wallpaper path: {path!r}") from exc
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    abs_path = os.path.abspath(os.path.expanduser(str(raw)))
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"Wallpaper file does not exist: {abs_path}")
    return abs_path


def run_osascript(script: str) -> str:
    rc, out, err = _run_args(["osascript", "-e", script], timeout=10)
    if rc != 0:
        raise RuntimeError(err or "osascript execution failed")
    return out


def get_screen_size(root=None):
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            screen = app.primaryScreen()
            if screen is not None:
                geo = screen.geometry()
                return geo.width(), geo.height()
    except Exception:
        pass
    try:
        out = run_osascript('tell application "Finder" to get bounds of window of desktop')
        parts = [p.strip() for p in out.split(",")]
        if len(parts) >= 4:
            return int(parts[2]), int(parts[3])
    except Exception:
        pass
    try:
        if root is not None:
            return root.winfo_screenwidth(), root.winfo_screenheight()
    except Exception:
        pass
    return 1920, 1080


MACOS_LAUNCH_AGENT_LABEL = "com.xxdz.shangbackground"
MACOS_LEGACY_LAUNCH_AGENT_LABELS = ("org.dcstudio.ShangBackground",)


def macos_launch_agents_dir() -> str:
    return os.path.expanduser("~/Library/LaunchAgents")


def macos_launch_agent_path(label: str = MACOS_LAUNCH_AGENT_LABEL) -> str:
    return os.path.join(macos_launch_agents_dir(), f"{label}.plist")


def _run_launchctl_variants(variants: list[list[str]], timeout: int = 8) -> tuple[bool, str]:
    """Try launchctl commands in order; return success and joined diagnostics."""
    diagnostics: list[str] = []
    for args in variants:
        rc, out, err = _run_args(args, timeout=timeout)
        if rc == 0:
            return True, out
        diagnostics.append(f"{' '.join(args)} -> {err or out or f'exit {rc}'}")
    return False, " | ".join(diagnostics)


def _macos_unload_agent(plist_path: str) -> tuple[bool, str]:
    uid = os.getuid() if hasattr(os, "getuid") else None
    variants: list[list[str]] = []
    if uid is not None:
        variants.append(["launchctl", "bootout", f"gui/{uid}", plist_path])
    variants.append(["launchctl", "unload", plist_path])
    return _run_launchctl_variants(variants)


def _macos_load_agent(plist_path: str) -> tuple[bool, str]:
    uid = os.getuid() if hasattr(os, "getuid") else None
    variants: list[list[str]] = []
    if uid is not None:
        variants.append(["launchctl", "bootstrap", f"gui/{uid}", plist_path])
    variants.append(["launchctl", "load", plist_path])
    return _run_launchctl_variants(variants)


def quote_applescript_text(value: str) -> str:
    """Escape a Python string for a double-quoted AppleScript string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _nsurl_file_url(path: str):
    from Foundation import NSURL
    return NSURL.fileURLWithPath_(path)



_MACOS_FIT_MODE = "填充"


def _macos_desktop_image_options(workspace, screen) -> dict:
    """Preserve existing options and apply the supported ShangBackground fit."""
    try:
        import AppKit
        options = dict(workspace.desktopImageOptionsForScreen_(screen) or {})
        mode = str(_MACOS_FIT_MODE or "填充")
        mapping = {
            "填充": (AppKit.NSImageScaleProportionallyUpOrDown, True),
            "适应": (AppKit.NSImageScaleProportionallyUpOrDown, False),
            "拉伸": (AppKit.NSImageScaleAxesIndependently, True),
            "居中": (AppKit.NSImageScaleNone, True),
        }
        if mode in mapping:
            scaling, clipping = mapping[mode]
            options[AppKit.NSWorkspaceDesktopImageScalingKey] = scaling
            options[AppKit.NSWorkspaceDesktopImageAllowClippingKey] = bool(clipping)
        return options
    except Exception:
        return {}


def _set_macos_wallpaper_appkit(path: str) -> tuple[bool, str]:
    """Use Apple's NSWorkspace API when PyObjC/AppKit is available."""
    try:
        from AppKit import NSScreen, NSWorkspace
    except Exception as exc:
        return False, f"AppKit unavailable: {exc}"
    try:
        workspace = NSWorkspace.sharedWorkspace()
        url = _nsurl_file_url(path)
        screens = list(NSScreen.screens() or [])
        main_screen = NSScreen.mainScreen()
        if not screens and main_screen is not None:
            screens = [main_screen]
        if not screens:
            return False, "AppKit returned no NSScreen"
        errors: list[str] = []
        changed = 0
        for screen in screens:
            result = workspace.setDesktopImageURL_forScreen_options_error_(url, screen, _macos_desktop_image_options(workspace, screen), None)
            if isinstance(result, tuple):
                ok = bool(result[0])
                err = result[1] if len(result) > 1 else None
            else:
                ok = bool(result)
                err = None
            if ok:
                changed += 1
            else:
                errors.append(str(err or "setDesktopImageURL returned false"))
        if changed:
            return True, f"AppKit changed {changed} screen(s)"
        return False, " | ".join(errors) or "AppKit did not change any screen"
    except Exception as exc:
        return False, str(exc)


def _get_macos_wallpaper_appkit() -> tuple[bool, str]:
    try:
        from AppKit import NSScreen, NSWorkspace
    except Exception as exc:
        return False, f"AppKit unavailable: {exc}"
    try:
        workspace = NSWorkspace.sharedWorkspace()
        screen = NSScreen.mainScreen()
        if screen is None:
            screens = list(NSScreen.screens() or [])
            screen = screens[0] if screens else None
        if screen is None:
            return False, "AppKit returned no NSScreen"
        url = workspace.desktopImageURLForScreen_(screen)
        if url is None:
            return True, ""
        path = str(url.path() or "")
        return True, path if Path(path).is_file() else ""
    except Exception as exc:
        return False, str(exc)


def _set_macos_wallpaper(path: str) -> None:
    abs_path = _ensure_existing_file(path)
    errors: list[str] = []

    ok, detail = _set_macos_wallpaper_appkit(abs_path)
    if ok:
        return
    errors.append(detail)

    escaped = quote_applescript_text(abs_path)
    scripts = [
        f'tell application "System Events" to set picture of every desktop to POSIX file "{escaped}"',
        f'tell application "Finder" to set desktop picture to POSIX file "{escaped}"',
    ]
    for script in scripts:
        try:
            run_osascript(script)
            return
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError(
        "macOS wallpaper change failed; install pyobjc-framework-Cocoa or grant Automation/System Events permission to the app. "
        + " | ".join(errors)
    )


def refresh_shell_ui() -> None:
    """No-op shell repaint hook for non-Windows platforms."""
    return


def set_wallpaper_platform(path: str) -> None:
    _set_macos_wallpaper(path)


def get_current_wallpaper_platform() -> str:
    ok, detail = _get_macos_wallpaper_appkit()
    if ok:
        return detail
    try:
        return run_osascript('tell application "System Events" to get picture of current desktop')
    except Exception as exc:
        raise RuntimeError(f"无法读取当前 macOS 壁纸: AppKit: {detail} | osascript: {exc}") from exc



def configure_fit_mode(fit_mode, winreg_module=None, log=None):
    """Store the fit mode applied through NSWorkspace on the next image set."""
    del winreg_module
    global _MACOS_FIT_MODE
    mode = str(fit_mode or "填充")
    if mode == "平铺":
        # NSWorkspace exposes scaling/clipping but no stable tile option.
        _MACOS_FIT_MODE = "适应"
        if log:
            log("macOS NSWorkspace 不提供稳定的平铺选项，已降级为适应。")
        return
    _MACOS_FIT_MODE = mode if mode in {"填充", "适应", "拉伸", "居中"} else "填充"
    if log:
        log(f"macOS 壁纸缩放模式已设置为：{_MACOS_FIT_MODE}")


# ── Bug 5 fix: desktop foreground detection ──────────────────────────────
# Used by MainWindow._is_desktop_foreground() to implement the
# "桌面失焦时暂停" video policy and the HTML wallpaper auto-pause feature.
# Previously this returned True unconditionally on macOS, silently disabling
# both features.

# Cache for 0.8s to avoid spawning osascript on every video-focus policy tick.
_LAST_FOREGROUND_CACHE: tuple[bool, float] = (True, 0.0)
_FOREGROUND_CACHE_TTL = 0.8  # seconds


def is_desktop_foreground() -> bool:
    """Return True when the Finder desktop (or Dock) is the frontmost process.

    Bug 5 fix: uses ``osascript`` to query System Events for the frontmost
    process name.  Returns True if the frontmost is Finder (the macOS
    desktop surface is rendered by Finder) or Dock.  Returns False for any
    other application.  Conservative True on any error (don't pause video
    if we can't tell).
    """
    import time as _time
    global _LAST_FOREGROUND_CACHE
    now = _time.monotonic()
    cached_val, cached_at = _LAST_FOREGROUND_CACHE
    if now - cached_at < _FOREGROUND_CACHE_TTL:
        return cached_val

    result = _detect_desktop_foreground_uncached()
    _LAST_FOREGROUND_CACHE = (result, now)
    return result


def _detect_desktop_foreground_uncached() -> bool:
    """Use NSWorkspace without triggering System Events Automation prompts."""
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        bundle = str(app.bundleIdentifier() or "").strip().lower() if app is not None else ""
        return not bundle or bundle in {"com.apple.finder", "com.apple.dock"} or "shangbackground" in bundle
    except Exception:
        return True
