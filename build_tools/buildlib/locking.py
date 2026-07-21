from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
from typing import BinaryIO

from .constants import PROJECT_ROOT


class ExclusiveBuildLock(AbstractContextManager["ExclusiveBuildLock"]):
    """Cross-process lock for the shared build environment and output tree.

    The operating-system lock is released automatically if the launcher exits or
    crashes, so a stale marker file never blocks future builds.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (PROJECT_ROOT / "build-generated" / ".build.lock")
        self._stream: BinaryIO | None = None

    def __enter__(self) -> "ExclusiveBuildLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            self._lock(stream)
        except OSError as exc:
            stream.close()
            owner = self._read_owner()
            detail = f" ({owner})" if owner else ""
            raise RuntimeError(f"Another ShangBackground build is already in progress{detail}") from exc
        self._stream = stream
        metadata = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        stream.seek(0)
        stream.truncate()
        stream.write(json.dumps(metadata, ensure_ascii=True).encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            self._unlock(stream)
        finally:
            stream.close()

    @staticmethod
    def _lock(stream: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(stream: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _read_owner(self) -> str:
        try:
            raw = self.path.read_text(encoding="utf-8").strip("\0\r\n ")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return ""
        if not isinstance(payload, dict):
            return ""
        pid = payload.get("pid")
        host = payload.get("host")
        started = payload.get("started_at")
        return ", ".join(str(value) for value in (f"pid={pid}" if pid else "", host, started) if value)
