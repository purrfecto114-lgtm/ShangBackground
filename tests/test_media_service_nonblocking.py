"""MediaService hot-path reads must never block on the operation lock.

v1.6.1 audit P1-1: mode-switch transactions hold the operation lock for
14-18 seconds while the GUI polls is_running/set_option/last_target at
700ms/40ms. With the lock taken here, every poll queued behind the whole
transaction — a single measured UI stall reached 2900ms (video-mode GUI
freeze). These tests hold THE SERVICE'S OWN lock (as a long transaction
would) on the test thread and require each hot-path read to complete
promptly on another thread. RLock semantics make this sound: only the
owner may re-enter, so a lock-taking implementation blocks the reader.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from app.media_service import MediaService


class _FakeBackend:
    """Minimal backend double: reads are instant, never touch real state."""

    def __init__(self) -> None:
        self.options: dict[tuple[str, str], Any] = {}

    def is_running(self, kind: str) -> bool:
        return True

    def set_option(self, kind: str, key: str, value: Any) -> bool:
        self.options[(kind, key)] = value
        return True

    def last_target(self, kind: str) -> str:
        return f"/tmp/fake-{kind}.mp4"


class _FakeState:
    def reconcile(self, kind: str, running: bool, target: str) -> None:
        pass

    def mark_started(self, kind: str, target: str) -> None:
        pass

    def mark_stopped(self, kind: str) -> None:
        pass


def _make_service() -> tuple[MediaService, threading.RLock]:
    lock = threading.RLock()
    service = MediaService(
        backend=_FakeBackend(),  # type: ignore[arg-type]
        config=lambda: {"video_file": "/tmp/a.mp4", "html_file": "/tmp/b.html"},
        persist=lambda: True,
        state=_FakeState(),  # type: ignore[arg-type]
        operation_lock=lock,
        log=lambda _m: None,
    )
    return service, lock


def _assert_completes_while_service_lock_held(service: MediaService, lock: threading.RLock, call) -> None:
    """Hold the service's operation lock (transaction simulation) on THIS
    thread; `call` must finish on another thread within the timeout."""
    lock.acquire()
    try:
        done = threading.Event()

        def _run() -> None:
            call()
            done.set()

        threading.Thread(target=_run, daemon=True).start()
        started = time.monotonic()
        assert done.wait(1.0), (
            "hot-path read blocked >1s on the held operation lock — GUI "
            "polling freezes for the whole 14-18s mode transition"
        )
        assert time.monotonic() - started < 1.0
    finally:
        lock.release()


def test_is_running_does_not_block_on_held_operation_lock():
    service, lock = _make_service()
    _assert_completes_while_service_lock_held(service, lock, lambda: service.is_running("video"))


def test_set_option_does_not_block_on_held_operation_lock():
    service, lock = _make_service()
    _assert_completes_while_service_lock_held(service, lock, lambda: service.set_option("video", "volume", 42))


def test_last_target_does_not_block_on_held_operation_lock():
    service, lock = _make_service()
    _assert_completes_while_service_lock_held(service, lock, lambda: service.last_target("html"))


def test_unlocked_reads_still_reach_the_backend():
    service, _ = _make_service()
    assert service.is_running("video") is True
    assert service.set_option("video", "muted", True) is True
    assert service._backend.options[("video", "muted")] is True
    assert service.last_target("html") == "/tmp/fake-html.mp4"
