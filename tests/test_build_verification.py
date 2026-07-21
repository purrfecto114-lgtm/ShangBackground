from __future__ import annotations

import json
from pathlib import Path

from app.build_verification import write_build_verification


def test_source_build_verification_writes_report_but_fails_packaged_gate(tmp_path: Path):
    report = tmp_path / "report.json"

    code = write_build_verification(report)

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert code == 2
    assert payload["schema"] == 1
    assert payload["packaged"] is False
    assert payload["app_version"] == "1.4.2"
