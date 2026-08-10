from __future__ import annotations

from pathlib import Path


def test_exit_cleanup_cancels_gui_owned_background_workers_before_core_cleanup():
    text = Path("src/ui/main_window.py").read_text(encoding="utf-8")
    block = text.split("def _perform_exit_cleanup_once(", 1)[1].split("def restart_program", 1)[0]
    local_cancel = block.index("self._current_operation_cancel.set()")
    core_cleanup = block.index("core.perform_exit_cleanup(")
    assert local_cancel < core_cleanup


def test_bing_rechecks_cancel_after_blocking_downloads_before_persistence():
    text = Path("src/ui/main_window.py").read_text(encoding="utf-8")
    block = text.split("def sync_bing_wallpaper", 1)[1].split("def _emit_bing_result", 1)[0]
    marker = "# A download may have been the final blocking call in the loop."
    post_download = block.index(marker)
    cancel_check = block.index("if self._current_operation_cancel.is_set():", post_download)
    persist = block.index('core.config["bing_next_index"]', post_download)
    mode_switch = block.index("core.switch_wallpaper_mode(", post_download)
    assert cancel_check < persist
    assert cancel_check < mode_switch


def test_regular_restart_cancels_gui_owned_background_workers_before_handoff():
    text = Path("src/ui/main_window.py").read_text(encoding="utf-8")
    block = text.split("def restart_program", 1)[1].split("def restart_as_admin", 1)[0]
    assert block.index("self._current_operation_cancel.set()") < block.index("core.restart_application(")


def test_windows_admin_restart_cancels_gui_owned_background_workers_before_handoff():
    text = Path("src/ui/main_window.py").read_text(encoding="utf-8")
    windows = text.split("class _WindowsMainWindowMixin", 1)[1].split("class _MacOSMainWindowMixin", 1)[0]
    block = windows.split("def restart_as_admin", 1)[1]
    assert block.index("self._current_operation_cancel.set()") < block.index("core.restart_as_admin(")
