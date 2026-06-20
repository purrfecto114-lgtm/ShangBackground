from __future__ import annotations

import os
import subprocess
from pathlib import Path



def _run_args(args: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """Run a command without a shell and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return -1, "", str(exc)


def _ensure_existing_file(path: str) -> str:
    """Return an absolute path and fail early with a useful error."""
    abs_path = str(Path(path).expanduser().resolve())
    if not Path(abs_path).is_file():
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
            result = workspace.setDesktopImageURL_forScreen_options_error_(url, screen, {}, None)
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
    """Keep the cross-platform API; macOS controls scaling in System Settings."""
    del fit_mode, winreg_module
    if log:
        log("macOS picture scaling is controlled by System Settings; continuing with image change only.")
