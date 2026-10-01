from __future__ import annotations

from pathlib import Path


def test_main_bing_set_latest_uses_mode_transaction_and_checks_failure():
    text = Path("src/ui/main_window.py").read_text(encoding="utf-8")
    block = text.split("if set_latest:", 1)[1].split("else:", 1)[0]
    assert "core.switch_wallpaper_mode(" in block
    assert '"图片", updates={"single_image": latest}' in block
    assert "if not core.switch_wallpaper_mode(" in block
    assert "core.set_wallpaper(latest" not in block
    assert 'self._emit_bing_result(False, reason, "")' in block
