"""Headless runtime diagnostics used by ``shangbackground --doctor``."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.build_features import is_feature_enabled
from app.config import APP_VERSION, PLATFORM_ID
from app.paths import IMAGE_DIR, LANG_DIR, RESOURCE_ROOT, resolve_mpv_path, user_data_dir
from app.storage import load_json_object


@dataclass(slots=True)
class DiagnosticCheck:
    name: str
    status: str
    detail: str
    required: bool = False


@dataclass(slots=True)
class DiagnosticReport:
    app_version: str
    platform: str
    python: str
    resource_root: str
    data_dir: str
    checks: list[DiagnosticCheck]

    @property
    def healthy(self) -> bool:
        return not any(check.required and check.status == "fail" for check in self.checks)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["healthy"] = self.healthy
        return data


def _module_check(
    module: str,
    label: str,
    *,
    required: bool,
    load: bool = False,
) -> DiagnosticCheck:
    try:
        if load:
            importlib.import_module(module)
            available = True
        else:
            available = importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, OSError, RuntimeError, ValueError):
        available = False
    return DiagnosticCheck(
        name=label,
        status="pass" if available else ("fail" if required else "warn"),
        detail=f"{module}: {'available' if available else 'missing'}",
        required=required,
    )


def _command_check(commands: tuple[str, ...], label: str, *, required: bool = False) -> DiagnosticCheck:
    found = [command for command in commands if shutil.which(command)]
    return DiagnosticCheck(
        name=label,
        status="pass" if found else ("fail" if required else "warn"),
        detail=("found: " + ", ".join(found)) if found else "not found: " + ", ".join(commands),
        required=required,
    )


def _directory_check(path: Path, label: str, *, required: bool = True) -> DiagnosticCheck:
    ok = path.is_dir()
    return DiagnosticCheck(
        name=label,
        status="pass" if ok else ("fail" if required else "warn"),
        detail=os.fspath(path),
        required=required,
    )


def _writable_directory_check(path: Path) -> DiagnosticCheck:
    try:
        path.mkdir(parents=True, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".doctor-", dir=os.fspath(path))
        os.close(fd)
        Path(probe).unlink(missing_ok=True)
    except OSError as exc:
        return DiagnosticCheck("user-data-writable", "fail", str(exc), required=True)
    return DiagnosticCheck("user-data-writable", "pass", os.fspath(path), required=True)


def _config_check(data_dir: Path) -> DiagnosticCheck:
    primary = data_dir / "settings.json"
    backup = data_dir / "settings.json.bak"
    if not primary.exists() and not backup.exists():
        return DiagnosticCheck("configuration", "pass", "not created yet")
    errors: list[str] = []
    for candidate in (primary, backup):
        if not candidate.is_file():
            continue
        try:
            load_json_object(candidate)
            recovered = candidate == backup and primary.exists()
            return DiagnosticCheck(
                "configuration",
                "warn" if recovered else "pass",
                f"readable: {candidate.name}" + (" (backup recovery available)" if recovered else ""),
            )
        except Exception as exc:
            errors.append(f"{candidate.name}: {exc}")
    return DiagnosticCheck("configuration", "fail", "; ".join(errors), required=True)


def collect_diagnostics() -> DiagnosticReport:
    data_dir = Path(user_data_dir("ShangBackground"))
    checks: list[DiagnosticCheck] = [
        DiagnosticCheck(
            "python-version",
            "pass" if sys.version_info >= (3, 10) else "fail",
            sys.version.split()[0],
            required=True,
        ),
        _directory_check(Path(RESOURCE_ROOT), "resource-root"),
        _directory_check(Path(IMAGE_DIR), "image-resources"),
        _directory_check(Path(LANG_DIR), "language-resources"),
        _writable_directory_check(data_dir),
        _config_check(data_dir),
        _module_check("PIL", "Pillow", required=True),
        _module_check("PySide6", "PySide6 Essentials", required=True),
        _module_check("psutil", "process safety", required=False),
    ]
    if is_feature_enabled("html"):
        from platform_adapters.native_html_runner import dependency_probe

        html_probe = dependency_probe(load_runtime=True)
        detail_parts: list[str] = []
        raw_missing_html = html_probe.get("missing_modules", [])
        missing_html = (
            [str(item) for item in raw_missing_html]
            if isinstance(raw_missing_html, (list, tuple, set, frozenset))
            else []
        )
        if missing_html:
            detail_parts.append("missing: " + ", ".join(missing_html))
        import_errors = html_probe.get("runtime_import_errors", {})
        if isinstance(import_errors, dict):
            detail_parts.extend(f"{module}: {error}" for module, error in import_errors.items())
        for key in ("desktop_backend_error", "environment_error"):
            value = str(html_probe.get(key, "") or "")
            if value:
                detail_parts.append(value)
        healthy_html = bool(html_probe.get("healthy"))
        checks.append(
            DiagnosticCheck(
                "native-html-wallpaper",
                "pass" if healthy_html else "fail",
                "; ".join(detail_parts) if detail_parts else "available, importable, and platform-ready",
                required=True,
            )
        )

    try:
        from app.libmpv_runtime import probe_libmpv, resolve_libmpv_path

        libmpv_path = resolve_libmpv_path()
        libmpv_ok, libmpv_detail = probe_libmpv(libmpv_path) if libmpv_path else (False, "libmpv not found")
    except Exception as exc:
        libmpv_path = None
        libmpv_ok, libmpv_detail = False, str(exc)

    resolved_mpv = resolve_mpv_path()
    system_mpv = shutil.which("mpv")
    bundled_mpv = bool(resolved_mpv and resolved_mpv != system_mpv)
    if libmpv_ok:
        video_detail = f"direct libmpv: {libmpv_detail}"
    elif bundled_mpv:
        video_detail = f"bundled/user mpv executable: {resolved_mpv}"
    elif system_mpv:
        video_detail = f"system mpv: {system_mpv}"
    elif libmpv_path:
        video_detail = f"libmpv found but failed to load: {libmpv_detail}"
    else:
        video_detail = "libmpv/mpv not found"
    checks.append(
        DiagnosticCheck(
            "video-backend",
            "pass" if libmpv_ok or bundled_mpv or system_mpv else "warn",
            video_detail,
        )
    )

    if PLATFORM_ID == "windows":
        checks.append(
            DiagnosticCheck(
                "Windows COM wallpaper",
                "pass",
                "built-in ctypes + ole32 IDesktopWallpaper",
            )
        )
    elif PLATFORM_ID == "linux":
        checks.extend(
            [
                _module_check("pynput", "global hotkeys", required=False),
                _command_check(("gsettings", "xfconf-query", "feh"), "static wallpaper backend"),
                _command_check(("xwinwrap", "mpvpaper"), "desktop video embedding"),
            ]
        )
    else:
        checks.extend(
            [
                _module_check("pynput", "global hotkeys", required=False),
                _module_check("AppKit", "macOS AppKit", required=False),
                _module_check("Quartz", "macOS Quartz", required=False),
            ]
        )

    return DiagnosticReport(
        app_version=APP_VERSION,
        platform=PLATFORM_ID,
        python=sys.version.split()[0],
        resource_root=os.fspath(RESOURCE_ROOT),
        data_dir=os.fspath(data_dir),
        checks=checks,
    )


def render_human(report: DiagnosticReport) -> str:
    icons = {"pass": "OK", "warn": "WARN", "fail": "FAIL"}
    lines = [
        f"ShangBackground {report.app_version} diagnostics",
        f"platform={report.platform} python={report.python}",
        f"resources={report.resource_root}",
        f"data={report.data_dir}",
        "",
    ]
    for check in report.checks:
        lines.append(f"[{icons.get(check.status, check.status.upper())}] {check.name}: {check.detail}")
    lines.append("")
    lines.append("result=healthy" if report.healthy else "result=missing required components")
    return "\n".join(lines)


def main(*, json_output: bool = False) -> int:
    report = collect_diagnostics()
    if json_output:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_human(report))
    return 0 if report.healthy else 2
