from __future__ import annotations

from pathlib import Path
import re

from app.version import APP_VERSION, APP_VERSION_FILE, APP_VERSION_TUPLE


def test_windows_file_metadata_matches_runtime_version():
    metadata = Path("src/main_version_info.txt").read_text(encoding="utf-8-sig")
    tuple_literal = ", ".join(str(part) for part in APP_VERSION_TUPLE)
    assert f"filevers=({tuple_literal})" in metadata
    assert f"prodvers=({tuple_literal})" in metadata
    assert f"u'{APP_VERSION_FILE}'" in metadata
    assert re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", APP_VERSION)
