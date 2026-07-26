"""Privacy-aware system information for the About page and bug reports."""
from __future__ import annotations

from collections.abc import Mapping
import os
import platform
import subprocess
import sys
from typing import Any

from app.build_features import enabled_features
from app.config import APP_VERSION, PLATFORM_ID
from app.paths import (
    app_executable_path,
    config_path,
    is_nuitka_compiled,
    is_packaged_runtime,
    resolve_mpv_path,
)


def _private_path(value: str | os.PathLike[str] | None) -> str:
    if not value:
        return "not found"
    text = os.path.abspath(os.path.expanduser(os.fspath(value)))
    home = os.path.abspath(os.path.expanduser("~"))
    try:
        if os.path.commonpath([home, text]) == home:
            return "~" + text[len(home):]
    except (OSError, ValueError):
        pass
    return text


def _runtime_label() -> str:
    if is_nuitka_compiled():
        return "Nuitka packaged"
    if bool(getattr(sys, "frozen", False)):
        return "PyInstaller packaged"
    if is_packaged_runtime():
        return "packaged"
    return "source"



def _desktop_session() -> str:
    values = []
    for key in ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION"):
        value = str(os.environ.get(key, "") or "").strip()
        if value and value not in values:
            values.append(value)
    if sys.platform.startswith("win"):
        return "Windows shell"
    if sys.platform == "darwin":
        return "macOS Aqua"
    return " / ".join(values) or "unknown"

def _mpv_summary(*, probe: bool) -> str:
    try:
        from app.libmpv_runtime import resolve_libmpv_path
        from app.build_features import video_runtime_mode

        _video_mode = video_runtime_mode()
        # v1.4.4: Skip libmpv probe in system mode — loading the DLL into
        # the GUI process is wasteful and can cause DLL conflicts.
        if _video_mode not in ("system", "disabled"):
            library = resolve_libmpv_path()
            if library:
                label = "libmpv (direct): " + _private_path(library)
                if probe:
                    from app.libmpv_runtime import probe_libmpv
                    ok, detail = probe_libmpv(library)
                    if ok and "|" in detail:
                        label += " | " + detail.rsplit("|", 1)[-1].strip()
                    elif not ok:
                        label += " | load failed: " + detail
                return label
    except Exception:
        pass
    executable = resolve_mpv_path()
    if not executable:
        return "not found"
    result = _private_path(executable)
    if not probe:
        return result
    try:
        completed = subprocess.run(
            [executable, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
            check=False,
        )
        first_line = next((line.strip() for line in completed.stdout.splitlines() if line.strip()), "")
        if first_line:
            result += " | " + first_line
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def collect_system_info(
    config: Mapping[str, Any] | None = None,
    *,
    probe_external: bool = False,
) -> dict[str, str]:
    cfg = config if isinstance(config, Mapping) else {}
    architecture = platform.architecture()[0] or "unknown"
    info: dict[str, str] = {
        "Application": f"ShangBackground {APP_VERSION}",
        "Runtime": _runtime_label(),
        "Build features": ", ".join(enabled_features()) or "core-only",
        "Platform": PLATFORM_ID,
        "Operating system": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "Desktop / session": _desktop_session(),
        "Process": f"{architecture} | PID {os.getpid()}",
        "Python": platform.python_version(),
        "Executable": _private_path(app_executable_path()),
        "Configuration": _private_path(config_path()),
    }
    try:
        from PySide6 import __version__ as pyside_version
        from PySide6.QtCore import qVersion

        info["Qt / PySide6"] = f"{qVersion()} / {pyside_version}"
    except Exception:
        info["Qt / PySide6"] = "unavailable"
    info.update(
        {
            "Mode": str(cfg.get("mode", "")),
            "Performance": str(cfg.get("performance_level", "balanced")),
            "Language": str(cfg.get("language", "zh")),
            "DPI scale": str(cfg.get("dpi_scale", 1.0)),
            "Video backend": _mpv_summary(probe=probe_external),
        }
    )
    return info


def render_system_info(info: Mapping[str, Any], *, extra_lines: tuple[str, ...] = ()) -> str:
    width = max((len(str(key)) for key in info), default=0)
    lines = [f"{str(key):<{width}} : {value}" for key, value in info.items()]
    lines.extend(str(line) for line in extra_lines if str(line).strip())
    return "\n".join(lines)
