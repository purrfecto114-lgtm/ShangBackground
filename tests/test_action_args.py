from __future__ import annotations

import argparse

from app import support


def _args(**overrides):
    values = dict(
        hide=False, from_context_menu=False, previous=False, next=False, random=False,
        set_wallpaper=None, jump_to_wallpaper=False, show=False, quit=False, wait_for_exit=False,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


def test_action_args_missing_wallpaper_returns_usage_failure(tmp_path):
    code = support._handle_action_args(_args(set_wallpaper=str(tmp_path / "missing.jpg")))
    assert code == 2


def test_action_args_exception_returns_failure(monkeypatch):
    def boom():
        raise RuntimeError("boom")
    monkeypatch.setattr(support.core, "previous_wallpaper", boom)
    assert support._handle_action_args(_args(previous=True)) == 1


def test_action_args_no_action_continues_gui():
    assert support._handle_action_args(_args()) is None


def test_action_args_set_wallpaper_uses_mode_transaction(monkeypatch, tmp_path):
    target = tmp_path / "wallpaper.jpg"
    target.write_bytes(b"image")
    calls = []

    monkeypatch.setattr(
        support.core,
        "switch_wallpaper_mode",
        lambda mode, *, updates=None: calls.append((mode, dict(updates or {}))) or True,
    )
    monkeypatch.setattr(
        support.core,
        "set_wallpaper",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must use mode transaction")),
    )

    assert support._handle_action_args(_args(set_wallpaper=str(target))) == 0
    assert calls == [("图片", {"single_image": str(target.absolute())})]


def test_action_args_set_wallpaper_propagates_mode_transaction_failure(monkeypatch, tmp_path):
    target = tmp_path / "wallpaper.jpg"
    target.write_bytes(b"image")
    monkeypatch.setattr(support.core, "switch_wallpaper_mode", lambda *_args, **_kwargs: False)
    assert support._handle_action_args(_args(set_wallpaper=str(target))) == 1


def test_entry_propagates_direct_ipc_failure():
    from pathlib import Path
    text = Path("src/app/entry.py").read_text(encoding="utf-8")
    # Static regression guard: direct actions must no longer fall through to unconditional success.
    assert "if direct_action_launch and not forwarded:" in text
    assert "return 1" in text.split("if direct_action_launch and not forwarded:", 1)[1].split("return 0", 1)[0]


def test_source_entry_action_does_not_require_pyside_for_missing_file(tmp_path):
    import subprocess
    import sys
    missing = tmp_path / "missing.jpg"
    completed = subprocess.run(
        [sys.executable, "src/main.py", "--set-wallpaper", str(missing)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, check=False,
    )
    assert completed.returncode == 2
    assert "壁纸文件不存在" in completed.stderr + completed.stdout
