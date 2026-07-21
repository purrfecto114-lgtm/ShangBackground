"""Small, dependency-free persistence helpers.

The GUI changes settings from several code paths and some of those calls can
happen on worker threads.  Keeping the actual disk transaction here makes the
write semantics easy to test without importing Qt or the platform backends.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

DEFAULT_MAX_JSON_BYTES = 4 * 1024 * 1024


class JsonStorageError(ValueError):
    """Raised when a persisted JSON document is unsafe or structurally invalid."""


def _fsync_directory(path: Path) -> None:
    """Best-effort directory sync after an atomic replace on POSIX."""
    if os.name == "nt":
        return
    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        fd = os.open(os.fspath(path), flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def serialize_json(value: Mapping[str, Any]) -> bytes:
    """Serialize a JSON object deterministically for writes and dirty checks."""
    if not isinstance(value, Mapping):
        raise JsonStorageError("JSON 根节点必须是对象")
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def load_json_object(
    path: str | os.PathLike[str],
    *,
    max_bytes: int = DEFAULT_MAX_JSON_BYTES,
) -> dict[str, Any]:
    """Load a bounded UTF-8 JSON object.

    Reading a size-bounded byte buffer avoids letting a corrupted or replaced
    settings file consume unbounded memory during application startup.
    """
    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise JsonStorageError(f"无法读取配置文件: {exc}") from exc
    if size > max_bytes:
        raise JsonStorageError(f"JSON 文件过大: {size} bytes")
    try:
        payload = target.read_bytes()
    except OSError as exc:
        raise JsonStorageError(f"无法读取配置文件: {exc}") from exc
    if len(payload) > max_bytes:
        raise JsonStorageError(f"JSON 文件过大: {len(payload)} bytes")
    try:
        data = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JsonStorageError(f"JSON 格式无效: {exc}") from exc
    if not isinstance(data, dict):
        raise JsonStorageError("JSON 根节点必须是对象")
    return data


def _write_file(path: Path, payload: bytes, *, mode: int) -> None:
    with path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def atomic_write_json(
    path: str | os.PathLike[str],
    value: Mapping[str, Any],
    *,
    backup_path: str | os.PathLike[str] | None = None,
    mode: int = 0o600,
) -> bytes:
    """Atomically replace *path* with a durable JSON document.

    The JSON is fully serialized before touching the destination.  A temporary
    file in the same directory is flushed and fsynced, then moved into place
    with ``os.replace``.  When requested, the previous valid file is copied to
    a separately atomically replaced backup first.

    The serialized bytes are returned so callers can cheaply skip later
    no-op writes.
    """
    payload = serialize_json(value)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    temp_name: str | None = None
    backup_temp_name: str | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=os.fspath(target.parent)
        )
        os.close(fd)
        temp_path = Path(temp_name)
        _write_file(temp_path, payload, mode=mode)

        if backup_path is not None and target.is_file():
            backup = Path(backup_path)
            backup.parent.mkdir(parents=True, exist_ok=True)
            bfd, backup_temp_name = tempfile.mkstemp(
                prefix=f".{backup.name}.", suffix=".tmp", dir=os.fspath(backup.parent)
            )
            os.close(bfd)
            backup_temp = Path(backup_temp_name)
            # copyfile avoids carrying surprising ACL/mode metadata from a
            # user-replaced settings file; permissions are applied explicitly.
            shutil.copyfile(target, backup_temp)
            # Never replace a known-good backup with a corrupted or externally
            # replaced primary file. Validate the copied snapshot before the
            # backup transaction is committed. An invalid snapshot is simply
            # discarded; the new primary can still be written safely.
            try:
                load_json_object(backup_temp)
            except JsonStorageError:
                backup_temp.unlink(missing_ok=True)
                backup_temp_name = None
            else:
                try:
                    with backup_temp.open("rb+") as handle:
                        os.fsync(handle.fileno())
                except OSError:
                    pass
                try:
                    os.chmod(backup_temp, mode)
                except OSError:
                    pass
                os.replace(backup_temp, backup)
                backup_temp_name = None
                _fsync_directory(backup.parent)

        os.replace(temp_path, target)
        temp_name = None
        _fsync_directory(target.parent)
        return payload
    finally:
        for leftover in (temp_name, backup_temp_name):
            if not leftover:
                continue
            try:
                Path(leftover).unlink(missing_ok=True)
            except OSError:
                pass
