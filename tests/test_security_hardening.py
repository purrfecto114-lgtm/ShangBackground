"""Regression tests for v1.4.4 security hardening.

Tests cover:
1. HTML URL validation (credential injection, UNC, SMB, device paths)
2. IPC token comparison (hmac.compare_digest)
3. Pillow decompression bomb protection
4. Update asset architecture matching
"""
from __future__ import annotations

import pytest



# ─── HTML URL Validation ────────────────────────────────────────────────────

def test_html_url_rejects_embedded_credentials():
    from app.source_validation import validate_html_source
    result = validate_html_source("https://user:pass@evil.com/page.html")
    assert not result.valid
    assert result.error == "unsafe_url"


def test_html_url_rejects_control_characters():
    from app.source_validation import validate_html_source
    result = validate_html_source("https://example.com/\r\nSet-Cookie:evil")
    assert not result.valid
    assert result.error == "unsafe_url"


def test_html_url_rejects_unc_path():
    from app.source_validation import validate_html_source
    result = validate_html_source("\\\\server\\share\\page.html")
    assert not result.valid
    assert result.error == "unsafe_url"


def test_html_url_rejects_smb_protocol():
    from app.source_validation import validate_html_source
    result = validate_html_source("smb://server/share/page.html")
    assert not result.valid
    assert result.error == "unsafe_url"


def test_html_url_accepts_normal_https():
    from app.source_validation import validate_html_source
    result = validate_html_source("https://example.com/wallpaper.html")
    assert result.valid
    assert result.error == ""


def test_html_url_accepts_normal_http():
    from app.source_validation import validate_html_source
    result = validate_html_source("http://localhost:3000/wallpaper.html")
    assert result.valid


def test_html_url_accepts_local_file():
    """Local file:// should still be accepted for user's HTML wallpaper."""
    from app.source_validation import validate_html_source
    # This will fail on 'not_found' because the file doesn't exist,
    # but it should NOT fail on 'unsafe_url' or 'remote_file_blocked'
    result = validate_html_source("file:///tmp/test.html")
    # It's OK if it says not_found — that means the URL was accepted but file doesn't exist
    assert result.error != "unsafe_url"
    assert result.error != "remote_file_blocked"


def test_html_url_rejects_remote_file():
    from app.source_validation import validate_html_source
    result = validate_html_source("file://server/share/page.html")
    assert not result.valid
    assert result.error == "remote_file_blocked"


# ─── IPC Token Comparison ───────────────────────────────────────────────────

def test_ipc_token_comparison_uses_compare_digest():
    """Verify that local_ipc.py uses hmac.compare_digest, not == operator."""
    import inspect
    from core import local_ipc
    source = inspect.getsource(local_ipc)
    assert "compare_digest" in source, "IPC must use hmac.compare_digest for token comparison"
    # Verify the old == pattern is not present for token comparison
    assert '== str(identity.get("ipc_token")' not in source, "IPC must not use == for token comparison"


# ─── Pillow Decompression Bomb ─────────────────────────────────────────────

def test_thumbnail_handles_decompression_bomb(monkeypatch):
    """generate_thumbnail_fast should return a placeholder for bomb images."""
    try:
        from PIL import Image
        from ui.sidebar import generate_thumbnail_fast
    except ModuleNotFoundError:
        pytest.skip("PySide6 not available in this environment")

    # Mock Image.open to raise DecompressionBombError
    def mock_open(path):
        raise Image.DecompressionBombError("test bomb")

    monkeypatch.setattr(Image, "open", mock_open)
    result = generate_thumbnail_fast("/fake/path.jpg", (148, 94))
    assert result.size == (148, 94)  # placeholder is correct size


def test_thumbnail_handles_generic_error(monkeypatch):
    """generate_thumbnail_fast should return a placeholder for any error."""
    try:
        from PIL import Image
        from ui.sidebar import generate_thumbnail_fast
    except ModuleNotFoundError:
        pytest.skip("PySide6 not available in this environment")

    def mock_open(path):
        raise OSError("test error")

    monkeypatch.setattr(Image, "open", mock_open)
    result = generate_thumbnail_fast("/fake/path.jpg", (148, 94))
    assert result.size == (148, 94)


# ─── Update Architecture Matching ──────────────────────────────────────────

def test_arch_detection_returns_valid():
    from services.updates import _detect_host_arch
    arch = _detect_host_arch()
    assert arch in ("x86_64", "arm64", "x86", "universal")


def test_arch_markers_defined():
    from services.updates import ARCH_ASSET_MARKERS
    assert "x86_64" in ARCH_ASSET_MARKERS
    assert "arm64" in ARCH_ASSET_MARKERS
    assert "x86" in ARCH_ASSET_MARKERS
    assert "universal" in ARCH_ASSET_MARKERS


def test_asset_score_prefers_matching_arch():
    from services.updates import _asset_score
    # Create a fake asset that matches both platform and arch
    asset_x64 = {"name": "ShangBackground-windows-x86_64-setup.exe", "download_url": ""}
    asset_arm = {"name": "ShangBackground-windows-arm64-setup.exe", "download_url": ""}
    score_x64 = _asset_score(asset_x64, "windows")
    score_arm = _asset_score(asset_arm, "windows")
    # On x86_64 host, x64 asset should score higher than arm64
    from services.updates import _detect_host_arch
    if _detect_host_arch() == "x86_64":
        assert score_x64 > score_arm, f"x64 ({score_x64}) should beat arm64 ({score_arm}) on x86_64 host"


# ─── Config Size Limit ─────────────────────────────────────────────────────

def test_config_storage_has_size_limit():
    from app.storage import DEFAULT_MAX_JSON_BYTES
    assert DEFAULT_MAX_JSON_BYTES == 4 * 1024 * 1024  # 4 MiB


def test_config_save_uses_rlock():
    """Verify that config save uses threading.RLock."""
    import inspect
    from core import engine
    source = inspect.getsource(engine)
    assert "RLock" in source, "Config must use RLock for thread safety"
