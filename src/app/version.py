"""Single source of truth for ShangBackground version metadata."""
from __future__ import annotations

APP_VERSION = "1.4.2"

_version_parts = tuple(int(part) for part in APP_VERSION.split("."))
if len(_version_parts) != 3:
    raise RuntimeError(f"APP_VERSION must use major.minor.patch form: {APP_VERSION!r}")
APP_VERSION_TUPLE = (*_version_parts, 0)
APP_VERSION_FILE = ".".join(str(part) for part in APP_VERSION_TUPLE)


def version_with_prefix(prefix: str = "v") -> str:
    return f"{prefix}{APP_VERSION}"
