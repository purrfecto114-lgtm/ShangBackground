"""Bing progress protocol: language-independent sentinel (v1.6.1 P1-2).

The old progress decoder matched ``t("进度") in message`` against raw
Chinese-prefixed messages. On the English UI ``t("进度")`` resolves to
"progress", which never matches — EVERY progress message was mishandled
as a completion message: the progress bar jumped to 100% mid-download,
the sync buttons revived (allowing re-entrant concurrent downloads), and
the worker reference was cleared early.

The fix is a ``\\x00bing-progress:<pct>/<status>`` sentinel prefix that no
user-visible text can contain. These tests pin the protocol through the
real ``_on_bing_finished`` on a stubbed window instance.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ui.main_window import BING_PROGRESS_SENTINEL, ShangBackgroundWindow  # noqa: E402


class _FakeButton:
    def __init__(self) -> None:
        self.enabled = False
        self.calls: list[bool] = []

    def setEnabled(self, value: bool) -> None:
        self.enabled = value
        self.calls.append(value)


class _FakeBar:
    def __init__(self) -> None:
        self.values: list[int] = []

    def setValue(self, value: int) -> None:
        self.values.append(value)


class _FakeLabel:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.tooltips: list[str] = []

    def setText(self, value: str) -> None:
        self.texts.append(value)

    def setToolTip(self, value: str) -> None:
        self.tooltips.append(value)


class _FakeWindow:
    """Duck-typed stand-in carrying exactly the attributes
    _on_bing_finished touches."""

    def __init__(self):  # noqa: D107
        self._bing_worker_thread = object()
        self.bing_sync_btn = _FakeButton()
        self.bing_multi_btn = _FakeButton()
        self.bing_continue_btn = _FakeButton()
        self.bing_progress = _FakeBar()
        self.bing_status = _FakeLabel()
        self.finished_ops: list[str] = []
        self.statuses: list[str] = []
        self.refreshed = 0
        self.previewed = 0
        self.warnings: list[str] = []

    def finish_operation(self, message: str) -> None:
        self.finished_ops.append(message)

    def set_status(self, message: str) -> None:
        self.statuses.append(message)

    def refresh_bing_cache_list(self) -> None:
        self.refreshed += 1

    def update_preview(self) -> None:
        self.previewed += 1

    def _show_non_modal_warning(self, title: str, message: str) -> None:
        self.warnings.append(message)


def _call(fake, ok, message, path=""):
    # bind the REAL method onto the fake
    method = ShangBackgroundWindow._on_bing_finished.__get__(fake, type(fake))
    method(ok, message, path)


def test_progress_message_updates_bar_without_finishing():
    fake = _FakeWindow()
    worker_before = fake._bing_worker_thread
    _call(fake, True, BING_PROGRESS_SENTINEL + "42/Downloading 3/8…")
    assert fake.bing_progress.values == [42]
    assert fake.bing_status.texts == ["Downloading 3/8…"]
    assert fake._bing_worker_thread is worker_before, "worker cleared mid-download"
    assert not fake.bing_sync_btn.calls, "buttons revived mid-download"
    assert fake.finished_ops == [], "progress must not finish the operation"


def test_progress_is_detected_regardless_of_ui_language(monkeypatch):
    """The regression: with t('进度') matching, an English-locale t() must
    not change progress detection. Simulate the English locale by making
    every translation come back uppercase-English."""
    import ui.main_window as mw

    original_t = mw.t
    monkeypatch.setattr(mw, "t", lambda s: "PROGRESS" if "进度" in s else original_t(s))
    fake = _FakeWindow()
    _call(fake, True, BING_PROGRESS_SENTINEL + "55/whatever")
    assert fake.bing_progress.values == [55]
    assert fake.finished_ops == [], "English UI must still parse progress"


def test_completion_message_finishes_and_reenables():
    fake = _FakeWindow()
    _call(fake, True, "已同步 8 张必应壁纸到缓存目录", "/tmp/x.jpg")
    assert fake._bing_worker_thread is None
    assert fake.bing_sync_btn.enabled is True
    assert fake.finished_ops == ["已同步 8 张必应壁纸到缓存目录"]
    assert fake.bing_progress.values == [100]
    assert fake.refreshed == 1


def test_failure_completion_shows_warning():
    fake = _FakeWindow()
    _call(fake, False, "同步必应壁纸失败：boom")
    assert fake.bing_progress.values == [0]
    assert fake.warnings, "failure must surface a warning dialog"


def test_malformed_progress_payload_is_ignored_safely():
    fake = _FakeWindow()
    _call(fake, True, BING_PROGRESS_SENTINEL + "not-a-number/status")
    assert fake.bing_progress.values == []
    assert fake.finished_ops == []
    assert not fake.bing_sync_btn.calls, "malformed progress must not revive buttons"


def test_clamped_to_99():
    fake = _FakeWindow()
    _call(fake, True, BING_PROGRESS_SENTINEL + "120/too much")
    assert fake.bing_progress.values == [99]
