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
  build pipeline runs UPX via Nuitka's ``--upx-binary`` flag, which
  applies compression during the freeze step before any signing.
- **Reproducibility**: UPX compression is deterministic for a given UPX
  version + input binary, so pinning ``UPX_MIN_VERSION`` keeps release
  artifacts reproducible.
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

from .constants import UPX_MIN_VERSION, UPX_TARGETS


_UPX_VERSION_RE = re.compile(r"upx\s+(\d+\.\d+\.\d+)", re.IGNORECASE)


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


def resolve_upx_for_build(target: str, *, enabled: bool) -> str | None:
    """Resolve the UPX binary path for a build, or ``None`` if UPX should
    not be used for this build.

    - If ``enabled`` is False, returns ``None`` (caller explicitly disabled UPX).
    - If ``target`` is not in :data:`UPX_TARGETS` (e.g. macOS), returns ``None``
      and logs nothing - UPX is unsupported on that target.
    - If UPX is not installed, raises :class:`RuntimeError` with an actionable
      message - the caller asked for UPX and we cannot silently skip it.

    The returned path is suitable for passing directly to Nuitka's
    ``--upx-binary=PATH`` flag.
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
    return binary
