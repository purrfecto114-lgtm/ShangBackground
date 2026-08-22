from __future__ import annotations

from pathlib import Path

import pytest


def test_signing_reports_missing_signtool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from build_tools import signing

    monkeypatch.setattr(signing, "find_signtool", lambda: None)
    target = tmp_path / "ShangBackground.exe"
    target.write_bytes(b"MZ")
    result = signing.sign_and_verify(target, certificate=None)
    assert result.status == "unsigned"
    assert "signtool" in result.reason.lower()


def test_signing_requires_certificate_before_invoking_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from build_tools import signing

    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(signing, "run_signtool", lambda *args, **kwargs: calls.append(args) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    # Ensure tool would be found if it were checked, but cert is missing so it must not be called.
    monkeypatch.setattr(signing, "find_signtool", lambda: Path("C:/fake/signtool.exe"))
    target = tmp_path / "app.exe"
    target.write_bytes(b"MZ")
    result = signing.sign_and_verify(target, certificate=None)
    assert result.status == "unsigned"
    assert "certificate" in result.reason.lower()
    assert calls == []


def test_signing_succeeds_with_mocked_tool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from build_tools import signing

    fake_tool = Path("C:/fake/signtool.exe")
    monkeypatch.setattr(signing, "find_signtool", lambda: fake_tool)

    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        # Both sign and verify succeed
        return type("R", (), {"returncode": 0, "stdout": "OK", "stderr": ""})()

    monkeypatch.setattr(signing, "run_signtool", fake_run)
    target = tmp_path / "app.exe"
    target.write_bytes(b"MZ")
    result = signing.sign_and_verify(
        target, certificate="cert.pfx", timestamp_url="http://timestamp.example.com"
    )
    assert result.status == "signed"
    assert result.reason.lower() != "unsigned"
    # Must have invoked sign and verify (at least 2 calls)
    assert len(calls) == 2


def test_signing_verify_failure_returns_failed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from build_tools import signing

    fake_tool = Path("C:/fake/signtool.exe")
    monkeypatch.setattr(signing, "find_signtool", lambda: fake_tool)

    def fake_run(args, **kwargs):
        cmd = " ".join(str(x) for x in args)
        if "verify" in cmd:
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": "verify failed"})()
        return type("R", (), {"returncode": 0, "stdout": "signed", "stderr": ""})()

    monkeypatch.setattr(signing, "run_signtool", fake_run)
    target = tmp_path / "app.exe"
    target.write_bytes(b"MZ")
    result = signing.sign_and_verify(target, certificate="cert.pfx")
    assert result.status == "failed"
    assert "verify" in result.reason.lower()
