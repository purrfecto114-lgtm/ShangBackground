"""File-based frozen-runtime verification used by the release builder.

The report is written to a caller-provided path so Windows GUI executables can
be validated without relying on stdout, which is unavailable in noconsole mode.
"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
import struct
import tempfile
from typing import Any

from app.build_features import BUILD_HTML_RUNTIME, BUILD_VIDEO_RUNTIME, enabled_features
from app.diagnostics import collect_diagnostics
from app.paths import RESOURCE_ROOT, is_packaged_runtime
from app.version import APP_VERSION


def _runtime_architecture() -> str:
    machine = platform.machine().strip().lower().replace("-", "_")
    if struct.calcsize("P") * 8 == 32:
        return "x86"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"amd64", "x64", "x86_64"}:
        return "x86_64"
    return machine or "unknown"


def _qt_smoke_test() -> dict[str, Any]:
    try:
        import PySide6
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication, QWidget

        application = QApplication.instance()
        owns_application = application is None
        if application is None:
            application = QApplication(["ShangBackground-build-verification"])
        widget = QWidget()
        widget.setObjectName("build-verification-widget")
        widget.resize(32, 32)
        widget.show()
        application.processEvents()
        platform_name = QGuiApplication.platformName()
        if not widget.isVisible():
            raise RuntimeError("Qt created the verification widget but did not make it visible")
        widget.close()
        application.processEvents()
        if owns_application:
            application.quit()
        return {
            "ok": True,
            "binding_version": getattr(PySide6, "__version__", "unknown"),
            "platform_plugin": platform_name,
        }
    except BaseException as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def collect_build_verification() -> dict[str, Any]:
    diagnostics = collect_diagnostics().as_dict()
    qt_smoke = _qt_smoke_test()
    return {
        "schema": 1,
        "verification": {
            "mode": "real" if is_packaged_runtime() else "source",
            "executed": True,
            "source": "in-process build verification",
        },
        "app_version": APP_VERSION,
        "platform": diagnostics.get("platform"),
        "architecture": _runtime_architecture(),
        "packaged": is_packaged_runtime(),
        "resource_root": os.fspath(RESOURCE_ROOT),
        "enabled_features": list(enabled_features()),
        "html_runtime": BUILD_HTML_RUNTIME,
        "video_runtime": dict(BUILD_VIDEO_RUNTIME),
        "qt_smoke": qt_smoke,
        "diagnostics": diagnostics,
    }


def write_build_verification(path: str | os.PathLike[str]) -> int:
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = collect_build_verification()
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    healthy = bool(payload.get("diagnostics", {}).get("healthy"))
    qt_healthy = bool(payload.get("qt_smoke", {}).get("ok"))
    return 0 if payload.get("packaged") and healthy and qt_healthy else 2
