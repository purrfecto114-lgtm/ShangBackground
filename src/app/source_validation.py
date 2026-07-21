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


def validate_html_source(value: object, *, optional: bool = False) -> SourceValidation:
    text = _clean_text(value)
    if not text:
        return SourceValidation("", "" if optional else "empty")
    parsed = urlparse(text)
    if parsed.scheme.casefold() in {"http", "https"}:
        if not parsed.netloc:
            return SourceValidation(text, "invalid_url")
        return SourceValidation(text)
    return validate_existing_file(text, optional=optional, suffixes=(".html", ".htm"))
