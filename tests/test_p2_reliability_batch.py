"""Contract tests: v1.6.1 P2 batch (update checker, log throttle bound,
thread-start fallbacks, tray balloon wiring)."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core import engine as core  # noqa: E402


def test_log_throttle_state_is_bounded():
    """Flooding with unique messages must not grow the throttle dict past
    the hard cap (the 0.75s window cannot outpace a flood of new keys)."""
    state = core._LOG_THROTTLE_STATE
    with core._LOG_THROTTLE_LOCK:
        state.clear()
    try:
        for i in range(3000):
            core._should_emit_log(f"flood-unique-{i}", "INFO")
        with core._LOG_THROTTLE_LOCK:
            size = len(state)
        assert size <= core._LOG_THROTTLE_MAX_KEYS, f"throttle state grew to {size} > {core._LOG_THROTTLE_MAX_KEYS}"
    finally:
        with core._LOG_THROTTLE_LOCK:
            state.clear()


def test_log_throttle_still_suppresses_repeats():
    with core._LOG_THROTTLE_LOCK:
        core._LOG_THROTTLE_STATE.clear()
    try:
        assert core._should_emit_log("repeat-me", "INFO") is True
        assert core._should_emit_log("repeat-me", "INFO") is False
        assert core._should_emit_log("repeat-me", "INFO") is False
        # warnings/errors always pass
        assert core._should_emit_log("repeat-me", "WARNING") is True
    finally:
        with core._LOG_THROTTLE_LOCK:
            core._LOG_THROTTLE_STATE.clear()


def test_thread_start_failure_emits_result_signal():
    """A failed thread start must emit core_result_signal so _on_core_finished
    resets _core_busy — otherwise the app is permanently 'busy'."""
    source = (SRC / "ui" / "main_window.py").read_text(encoding="utf-8")
    # both dispatcher sites carry the fallback
    assert source.count("self.core_result_signal.emit(False, str(exc), None)") >= 3, (
        "expected the two thread-start fallbacks (plus the in-worker handler) "
        "to emit failure through core_result_signal"
    )
    # the fallback is attached to start(), not the thread creation
    for anchor in ("模式切换线程启动失败", "后台壁纸操作线程启动失败"):
        assert anchor in source, f"missing fallback for: {anchor}"


def test_update_checker_delete_later_before_reference_drop():
    source = (SRC / "ui" / "main_window.py").read_text(encoding="utf-8")
    seg_start = source.index("def on_startup_update_checked")
    seg = source[seg_start : seg_start + 1200]
    checker_pos = seg.index("checker = getattr(")
    none_pos = seg.index("self._startup_update_checker = None")
    later_pos = seg.index("checker.deleteLater()")
    assert checker_pos < none_pos < later_pos, (
        "capture reference, drop it, then deleteLater — the order must keep "
        "the slot reentrant while releasing the QThread promptly"
    )


def test_tray_object_registered_on_core():
    source = (SRC / "ui" / "main_window.py").read_text(encoding="utf-8")
    seg_start = source.index("def create_or_update_tray")
    seg = source[seg_start : seg_start + 2000]
    assert "core.tray_icon_obj = self.tray" in seg, (
        "the live tray must be registered on core so engine's IPC-failure balloon path can reach it"
    )
    seg2_start = source.index("def on_tray_changed")
    seg2 = source[seg2_start : seg2_start + 800]
    assert "core.tray_icon_obj = None" in seg2, "disabling the tray must unregister it"
    # engine reads the same global
    engine_src = (SRC / "core" / "engine.py").read_text(encoding="utf-8")
    assert "tray_icon_obj" in engine_src
