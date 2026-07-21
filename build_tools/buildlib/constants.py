from __future__ import annotations

import os
from pathlib import Path
import platform
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
ENTRY_SCRIPT = SOURCE_ROOT / "main.pyw"
APP_NAME = "ShangBackground"
COMPANY_NAME = "XXDZ Studio"
PRODUCT_NAME = "Previous Desktop Background"
DESCRIPTION = PRODUCT_NAME

TARGETS = ("windows", "linux", "macos")
TOOLS = ("pyinstaller", "nuitka")
PROFILES = ("full", "lite")
MODES = ("standalone", "onefile")
MPV_MODES = ("auto", "bundled", "system")
ARCHES = ("x86_64", "arm64", "x86")
WINDOWS_CONSOLE_MODES = ("disable", "hide", "attach", "force")
PYINSTALLER_CONTENTS_DIRECTORY = "_internal"

NUITKA_VERSION = "4.1.3"
PYINSTALLER_VERSION = "6.21.0"
PYINSTALLER_HOOKS_VERSION = "2026.6"


def host_target() -> str:
    if os.name == "nt" or sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def current_host() -> str:
    return host_target()


def normalize_arch(value: str | None = None) -> str:
    raw = str(value or platform.machine() or "").strip().lower().replace("-", "_")
    if raw in {"", "auto"}:
        raw = str(platform.machine() or "").strip().lower().replace("-", "_")
    aliases = {
        "amd64": "x86_64", "x64": "x86_64", "x86_64": "x86_64",
        "aarch64": "arm64", "arm64": "arm64",
        "i386": "x86", "i486": "x86", "i586": "x86", "i686": "x86", "x86": "x86",
    }
    normalized = aliases.get(raw, raw)
    if normalized not in ARCHES:
        raise ValueError(f"Unsupported architecture: {value or platform.machine()!r}")
    return normalized


def python_executable() -> str:
    return os.fspath(Path(sys.executable).resolve())


def ensure_project_layout(project: Path = PROJECT_ROOT) -> None:
    required = (project / "src" / "main.pyw", project / "src" / "app", project / "requirements")
    missing = [os.fspath(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError("Invalid project root; missing: " + ", ".join(missing))


def read_version() -> str:
    text = (SOURCE_ROOT / "app" / "version.py").read_text(encoding="utf-8")
    match = re.search(r"^APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", text, re.MULTILINE)
    if not match:
        raise RuntimeError("APP_VERSION is missing from src/app/version.py")
    return match.group(1)


def windows_numeric_version() -> str:
    values = [int(token) for token in re.findall(r"\d+", read_version())[:4]]
    values.extend([0] * (4 - len(values)))
    return ".".join(map(str, values[:4]))


def effective_profile(profile: str) -> str:
    return "full" if profile == "system" else profile


def read_app_version(project: Path = PROJECT_ROOT) -> tuple[str, str]:
    del project
    return read_version(), windows_numeric_version()


BUILD_TOOLS = TOOLS
