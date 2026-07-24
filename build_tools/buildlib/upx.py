"""UPX (Ultimate Packer for eXecutables) detection and validation.

UPX is an optional post-build compression step applied by Nuitka to the
frozen ELF/PE binaries. Compressed binaries are 30-60% smaller, which
matters for download size on a release that ships the full video +
HTML feature set across three platforms.

Security and reliability notes
------------------------------
- **AV false positives**: some antivirus vendors flag UPX-compressed
  binaries as suspicious. The release notes document this and recommend
  users verify the SHA-256 against ``SHA256SUMS.txt``.
- **macOS**: UPX is intentionally disabled. Compressed Mach-O binaries
  break codesign, notarization, and the Apple Silicon ABI. Nuitka also
  refuses to apply UPX on macOS by default.
- **Signed binaries**: UPX must run BEFORE code signing, not after.
  Compressing an already-signed binary invalidates the signature. The
  build pipeline runs UPX via Nuitka's ``--enable-plugin=upx`` flag,
  which applies compression during the freeze step before any signing.
- **Reproducibility**: UPX compression is deterministic for a given UPX
  version + input binary, so pinning ``UPX_MIN_VERSION`` keeps release
  artifacts reproducible.
- **Nuitka integration**: UPX is a Nuitka *plugin*, activated via
  ``--enable-plugin=upx``. The ``--upx-binary=PATH`` sub-option only
  becomes a recognized flag AFTER the plugin is enabled, so the build
  driver always passes both flags together.
- **LZMA avoidance**: Nuitka's UPX plugin hard-codes ``--best --lzma``.
  LZMA decompression is ~10x slower than NRV at runtime, which directly
  slows down every frozen-binary load (especially the video wallpaper
  player spawn). We use a wrapper script that strips ``--lzma`` from the
  UPX invocation, keeping ``--best`` (NRV2E) for good compression ratio
  with fast decompression (>500 MB/s).
- **vcruntime exclusion**: ``vcruntime140.dll`` / ``vcruntime140_1.dll``
  must NOT be UPX-compressed (known to cause crashes and startup
  failures). The wrapper script detects and skips them.
- **Trusted source**: CI installs UPX from the official Chocolatey
  package (Windows) or apt-get (Linux distro package). Local developers
  can override the path via ``SHANGBACKGROUND_UPX_BINARY``.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap

from .constants import UPX_MIN_VERSION, UPX_TARGETS


_UPX_VERSION_RE = re.compile(r"upx\s+(\d+\.\d+\.\d+)", re.IGNORECASE)

# DLLs that must NOT be UPX-compressed because they cause crashes or
# startup failures when packed. Nuitka's own UPX plugin already skips
# some of these, but we enforce it in our wrapper for belt-and-suspenders
# safety.
_UPX_EXCLUDE_PATTERNS = (
    "vcruntime",
    "msvcp",
    "ucrtbase",
    "api-ms-win-crt",
    "python3",
    "python3.dll",
    "shiboken",
)


def upx_supported_for_target(target: str) -> bool:
    """Return True if UPX compression is supported for ``target``."""
    return target in UPX_TARGETS


def find_upx_binary() -> str | None:
    """Locate ``upx`` on the host.

    Resolution order:
    1. ``SHANGBACKGROUND_UPX_BINARY`` env var (explicit absolute path).
    2. ``upx`` on ``PATH``.
    3. Well-known install paths under ``%ProgramFiles%`` (Windows only).

    Returns the path to ``upx`` (or ``upx.exe``) as a string, or ``None``
    if no UPX binary was found.
    """
    override = os.environ.get("SHANGBACKGROUND_UPX_BINARY", "").strip()
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return os.fspath(candidate)
        # Caller will get a clear error when they try to use it.
        return os.fspath(candidate)

    found = shutil.which("upx") or shutil.which("upx.exe")
    if found:
        return found

    if sys.platform == "win32" or os.name == "nt":
        for root in (
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        ):
            if not root:
                continue
            for sub in ("UPX", "upx"):
                candidate = Path(root) / sub / "upx.exe"
                if candidate.is_file():
                    return os.fspath(candidate)
    return None


def upx_version(binary: str) -> tuple[int, ...] | None:
    """Return the UPX version as a tuple of ints, or ``None`` if it cannot
    be determined. Examples: ``(4, 2, 4)``, ``(4, 3, 0)``."""
    try:
        result = subprocess.run(
            [binary, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    match = _UPX_VERSION_RE.search(result.stdout)
    if not match:
        return None
    try:
        return tuple(int(part) for part in match.group(1).split("."))
    except ValueError:
        return None


def _parse_version_spec(spec: str) -> tuple[int, ...]:
    return tuple(int(part) for part in spec.split("."))


def upx_meets_minimum(binary: str, *, minimum: str = UPX_MIN_VERSION) -> bool:
    """Return True if ``binary`` is a UPX release >= ``minimum``."""
    actual = upx_version(binary)
    if actual is None:
        return False
    required = _parse_version_spec(minimum)
    # Pad the shorter tuple with zeros for comparison.
    length = max(len(actual), len(required))
    actual_padded = actual + (0,) * (length - len(actual))
    required_padded = required + (0,) * (length - len(required))
    return actual_padded >= required_padded


def _create_upx_wrapper(real_upx: str, output_dir: Path) -> str:
    """Create a wrapper script that strips ``--lzma`` from UPX invocations.

    Nuitka's UPX plugin hard-codes ``upx -q --no-progress --best --lzma``.
    LZMA decompression is ~10x slower than NRV2E at runtime, directly
    slowing every frozen-binary load. The wrapper:

    1. Removes ``--lzma`` from the argument list (keeps ``--best``).
    2. Skips files matching ``_UPX_EXCLUDE_PATTERNS`` (vcruntime, etc.)
       by exiting 0 without calling the real UPX.

    Returns the path to the wrapper script. On Windows it's a .bat file;
    on Linux/macOS it's a shell script.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    is_windows = sys.platform == "win32" or os.name == "nt"
    if is_windows:
        wrapper_path = output_dir / "upx-wrapper.bat"
        # Windows .bat wrapper: check filename for excluded patterns,
        # then strip --lzma and call the real upx.
        exclude_checks = " ".join(
            f'(echo %~nx1 | findstr /i "{pattern}" >nul && exit /b 0)'
            for pattern in _UPX_EXCLUDE_PATTERNS
        )
        wrapper_path.write_text(
            textwrap.dedent(f"""\
            @echo off
            REM UPX wrapper: strip --lzma (10x slower decompression) and skip
            REM fragile runtime DLLs (vcruntime, ucrtbase, etc.).
            setlocal enabledelayedexpansion
            REM Check if the input file matches excluded patterns
            {exclude_checks}
            REM Rebuild args without --lzma
            set "NEW_ARGS="
            :argloop
            if "%~1"=="" goto run
            if /i "%~1"=="--lzma" shift /1 & goto argloop
            set "NEW_ARGS=!NEW_ARGS! %~1"
            shift /1
            goto argloop
            :run
            "{real_upx}" !NEW_ARGS!
            exit /b %errorlevel%
            """),
            encoding="utf-8",
        )
    else:
        wrapper_path = output_dir / "upx-wrapper.sh"
        exclude_pattern = "|".join(_UPX_EXCLUDE_PATTERNS)
        wrapper_path.write_text(
            textwrap.dedent(f"""\
            #!/bin/bash
            # UPX wrapper: strip --lzma (10x slower decompression) and skip
            # fragile runtime DLLs (vcruntime, ucrtbase, etc.).
            set -e
            # Find the file argument (last non-flag argument) and check exclusions
            for arg in "$@"; do
                case "$arg" in
                    -*) continue ;;
                esac
                basename=$(basename "$arg" 2>/dev/null || true)
                if echo "$basename" | grep -qiE '^({exclude_pattern})'; then
                    exit 0
                fi
            done
            # Strip --lzma from args
            args=()
            for arg in "$@"; do
                if [ "$arg" != "--lzma" ]; then
                    args+=("$arg")
                fi
            done
            exec "{real_upx}" "${{args[@]}}"
            """),
            encoding="utf-8",
        )
        wrapper_path.chmod(0o755)
    return os.fspath(wrapper_path)


def resolve_upx_for_build(target: str, *, enabled: bool) -> str | None:
    """Resolve the UPX binary path for a build, or ``None`` if UPX should
    not be used for this build.

    Returns the path to a *wrapper script* that strips ``--lzma`` and
    skips fragile DLLs, NOT the raw UPX binary. This is because Nuitka's
    UPX plugin hard-codes ``--best --lzma`` and LZMA decompression is
    ~10x slower at runtime.

    - If ``enabled`` is False, returns ``None`` (caller explicitly disabled UPX).
    - If ``target`` is not in :data:`UPX_TARGETS` (e.g. macOS), returns ``None``
      and logs nothing - UPX is unsupported on that target.
    - If UPX is not installed, raises :class:`RuntimeError` with an actionable
      message - the caller asked for UPX and we cannot silently skip it.

    The returned path is suitable for passing to Nuitka's
    ``--enable-plugin=upx --upx-binary=PATH`` flags.
    """
    if not enabled:
        return None
    if not upx_supported_for_target(target):
        return None
    binary = find_upx_binary()
    if binary is None:
        if target == "windows":
            hint = (
                "Install UPX via Chocolatey:  choco install upx -y\n"
                "or set SHANGBACKGROUND_UPX_BINARY to the absolute path of upx.exe."
            )
        else:
            hint = (
                "Install UPX via apt:  sudo apt-get install -y upx\n"
                "or set SHANGBACKGROUND_UPX_BINARY to the absolute path of upx."
            )
        raise RuntimeError(
            f"UPX was requested for the {target} build but no upx binary was found.\n{hint}"
        )
    if not upx_meets_minimum(binary):
        actual = upx_version(binary)
        raise RuntimeError(
            f"UPX {binary} reports version {actual or 'unknown'}, "
            f"but ShangBackground requires >= {UPX_MIN_VERSION}. "
            "Upgrade UPX or set SHANGBACKGROUND_UPX_BINARY to a newer binary."
        )
    # Create a wrapper that strips --lzma and skips fragile DLLs.
    # The wrapper lives in a temp directory that persists for the build
    # session; Nuitka invokes it as the "upx binary" via --upx-binary.
    wrapper_dir = Path(tempfile.gettempdir()) / "shangbackground-upx-wrapper"
    wrapper = _create_upx_wrapper(binary, wrapper_dir)
    return wrapper
