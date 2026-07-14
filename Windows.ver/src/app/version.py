"""Single source of truth for ShangBackground version metadata.

Update this file only when bumping the application version.  Runtime code,
update checks and build helpers import/read these constants so the three
platform trees stay aligned without hunting for scattered literals.
"""
from __future__ import annotations

APP_VERSION = "1.4.0"
APP_VERSION_TUPLE = (1, 4, 0, 0)
APP_VERSION_FILE = "1.4.0.0"


def version_with_prefix(prefix: str = "v") -> str:
    return f"{prefix}{APP_VERSION}"
