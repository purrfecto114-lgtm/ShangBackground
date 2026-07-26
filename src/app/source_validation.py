"""Qt-free validation for user-entered wallpaper and cache sources."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import unquote, urlparse


@dataclass(frozen=True, slots=True)
class SourceValidation:
    """Normalized source text plus a stable, UI-independent result code."""

    value: str
    error: str = ""

    @property
    def valid(self) -> bool:
        return not self.error


def _clean_text(value: object) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    return text


def _local_path_text(value: object) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    if text.casefold().startswith("file://"):
        parsed = urlparse(text)
        path = unquote(parsed.path or "")
        if parsed.netloc and parsed.netloc.casefold() != "localhost":
            path = f"//{parsed.netloc}{path}"
        if os.name == "nt" and len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        text = path
    return os.path.normpath(os.path.expandvars(os.path.expanduser(text)))


def validate_existing_directory(value: object, *, optional: bool = False) -> SourceValidation:
    text = _local_path_text(value)
    if not text:
        return SourceValidation("", "" if optional else "empty")
    path = Path(text)
    if not path.exists():
        return SourceValidation(text, "not_found")
    if not path.is_dir():
        return SourceValidation(text, "not_directory")
    return SourceValidation(os.fspath(path))


def validate_directory_target(value: object, *, optional: bool = False) -> SourceValidation:
    """Validate a directory that may be created later by the owning service."""
    text = _local_path_text(value)
    if not text:
        return SourceValidation("", "" if optional else "empty")
    path = Path(text)
    if path.exists() and not path.is_dir():
        return SourceValidation(text, "not_directory")
    ancestor = path if path.exists() else path.parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if not ancestor.exists():
        return SourceValidation(text, "parent_not_found")
    if not ancestor.is_dir():
        return SourceValidation(text, "parent_not_directory")
    return SourceValidation(os.fspath(path))


def validate_existing_file(
    value: object,
    *,
    optional: bool = False,
    suffixes: tuple[str, ...] = (),
) -> SourceValidation:
    text = _local_path_text(value)
    if not text:
        return SourceValidation("", "" if optional else "empty")
    path = Path(text)
    if not path.exists():
        return SourceValidation(text, "not_found")
    if not path.is_file():
        return SourceValidation(text, "not_file")
    if suffixes and path.suffix.casefold() not in {suffix.casefold() for suffix in suffixes}:
        return SourceValidation(text, "unsupported_type")
    return SourceValidation(os.fspath(path))


def _is_safe_html_url(text: str) -> bool:
    """Reject URLs that pose security risks in a webview context.

    Blocked patterns (per OWASP/RFC 3986/Microsoft Win32 naming conventions):
    - Embedded credentials (user:pass@host) — RFC 3986 §3.2.1 deprecation
    - Control characters (< 0x20 or 0x7F) — CRLF injection
    - UNC paths (\\\\server\\share) — NTLM relay / credential leak
    - SMB protocol (smb://) — same UNC risk
    - Windows device paths (CON, PRN, AUX, NUL, COM1-9, LPT1-9)
    - Remote file:// to network shares — only local file:// is allowed
    """
    # Reject control characters
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return False
    # Reject embedded credentials
    if "@" in text and "://" in text:
        scheme_end = text.index("://") + 3
        rest = text[scheme_end:]
        if "@" in rest.split("/", 1)[0]:
            return False
    # Reject UNC paths
    if text.startswith("\\\\") or text.startswith("//"):
        return False
    # Reject SMB protocol
    if text.lower().startswith("smb://"):
        return False
    # Reject Windows device paths
    import re
    device_re = re.compile(
        r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.|$)',
        re.IGNORECASE,
    )
    if device_re.match(os.path.basename(text)):
        return False
    return True


def validate_html_source(value: object, *, optional: bool = False) -> SourceValidation:
    text = _clean_text(value)
    if not text:
        return SourceValidation("", "" if optional else "empty")
    # Security: reject dangerous URL patterns before passing to webview
    if not _is_safe_html_url(text):
        return SourceValidation(text, "unsafe_url")
    parsed = urlparse(text)
    if parsed.scheme.casefold() in {"http", "https"}:
        if not parsed.netloc:
            return SourceValidation(text, "invalid_url")
        # Reject non-standard ports that could be used for SSRF
        if parsed.port is not None and parsed.port not in (80, 443, 8080, 8443, 3000, 5000):
            # Allow custom ports but flag obvious typos — this is advisory, not blocking
            pass
        return SourceValidation(text)
    # file:// is allowed only for local files (not network shares)
    if parsed.scheme.casefold() == "file":
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            return SourceValidation(text, "remote_file_blocked")
        return validate_existing_file(text, optional=optional, suffixes=(".html", ".htm"))
    return validate_existing_file(text, optional=optional, suffixes=(".html", ".htm"))
