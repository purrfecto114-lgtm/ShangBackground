from __future__ import annotations

import os
from pathlib import Path
import platform
import re
import subprocess
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
PYSIDE6_ESSENTIALS_VERSION = "6.11.1"
PYWEBVIEW_VERSION = "6.2.1"


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
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86_64": "x86_64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "i386": "x86",
        "i486": "x86",
        "i586": "x86",
        "i686": "x86",
        "x86": "x86",
    }
    normalized = aliases.get(raw, raw)
    if normalized not in ARCHES:
        raise ValueError(f"Unsupported architecture: {value or platform.machine()!r}")
    return normalized


def _console_python(path: str | os.PathLike[str]) -> Path:
    candidate = Path(os.path.abspath(os.fspath(path)))
    if os.name == "nt" and candidate.name.lower() == "pythonw.exe":
        console = candidate.with_name("python.exe")
        if console.is_file():
            return console
    return candidate


def _project_venv_python() -> Path:
    if os.name == "nt":
        return PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    return PROJECT_ROOT / ".venv" / "bin" / "python"


def _running_in_virtualenv() -> bool:
    return bool(
        os.environ.get("VIRTUAL_ENV")
        or getattr(sys, "base_prefix", sys.prefix) != sys.prefix
        or hasattr(sys, "real_prefix")
    )


def _explicit_build_python() -> Path | None:
    override = str(os.environ.get("SHANGBACKGROUND_BUILD_PYTHON", "")).strip()
    if not override:
        return None
    candidate = _console_python(Path(override).expanduser())
    if not candidate.is_file():
        raise RuntimeError(f"SHANGBACKGROUND_BUILD_PYTHON does not exist: {candidate}")
    return candidate


def python_executable() -> str:
    """Return the interpreter that owns the reproducible build environment.

    An explicit override wins. Otherwise the project-local ``.venv`` is always
    preferred, even when the launcher happens to run inside an unrelated active
    virtual environment. This prevents the build tool from mutating or silently
    depending on a caller's development environment.
    """
    override = _explicit_build_python()
    if override is not None:
        return os.fspath(override)
    project = _project_venv_python()
    if project.is_file():
        return os.fspath(project)
    return os.fspath(_console_python(sys.executable))


def ensure_build_python_environment(*, dry_run: bool) -> str:
    """Create and select the isolated project-local build environment."""
    override = _explicit_build_python()
    if override is not None:
        return os.fspath(override)
    project_python = _project_venv_python()
    if project_python.is_file() or dry_run:
        return os.fspath(project_python if project_python.is_file() else _console_python(sys.executable))
    bootstrap = _console_python(sys.executable)
    command = [os.fspath(bootstrap), "-m", "venv", os.fspath(PROJECT_ROOT / ".venv")]
    print("  $", subprocess.list2cmdline(command) if os.name == "nt" else " ".join(command), flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    if not project_python.is_file():
        raise RuntimeError(f"Project build environment was not created correctly: {project_python}")
    return os.fspath(project_python)


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
