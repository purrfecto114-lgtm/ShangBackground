"""Configuration persistence boundary.

This module owns file selection, bounded JSON loading, backup recovery and
atomic writes. Configuration defaults and migrations remain application policy
and are deliberately kept outside this repository.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.storage import JsonStorageError, atomic_write_json, load_json_object, serialize_json


@dataclass(frozen=True, slots=True)
class ConfigReadFailure:
    """A candidate file that could not be loaded safely."""

    path: Path
    error: str


@dataclass(frozen=True, slots=True)
class ConfigLoadResult:
    """Result of searching ordered configuration candidates."""

    data: dict[str, Any] | None
    source_path: Path | None
    failures: tuple[ConfigReadFailure, ...] = ()

    @property
    def found(self) -> bool:
        return self.data is not None and self.source_path is not None


class ConfigRepository:
    """Load and persist one settings document with a last-known-good backup."""

    def __init__(
        self,
        primary_path: str | os.PathLike[str],
        *,
        backup_path: str | os.PathLike[str] | None = None,
        fallback_paths: Iterable[str | os.PathLike[str]] = (),
    ) -> None:
        self.primary_path = Path(primary_path)
        self.backup_path = Path(backup_path) if backup_path is not None else None
        self.fallback_paths = tuple(Path(path) for path in fallback_paths if path)
        self._last_saved_payload: bytes | None = None

    @property
    def candidates(self) -> tuple[Path, ...]:
        ordered = [self.primary_path]
        if self.backup_path is not None:
            ordered.append(self.backup_path)
        ordered.extend(self.fallback_paths)
        # Preserve order while protecting against accidentally duplicated paths.
        return tuple(dict.fromkeys(ordered))

    def load_first_valid(self) -> ConfigLoadResult:
        failures: list[ConfigReadFailure] = []
        for candidate in self.candidates:
            if not candidate.is_file():
                continue
            try:
                data = load_json_object(candidate)
            except JsonStorageError as exc:
                failures.append(ConfigReadFailure(candidate, str(exc)))
                continue
            return ConfigLoadResult(data=data, source_path=candidate, failures=tuple(failures))
        return ConfigLoadResult(data=None, source_path=None, failures=tuple(failures))

    def save(self, value: Mapping[str, Any]) -> bool:
        """Persist *value* and return whether disk content changed.

        Serialization happens before touching the destination. Byte-identical
        writes are skipped after the first successful write in this process.
        """
        payload = serialize_json(value)
        if payload == self._last_saved_payload and self.primary_path.is_file():
            return False
        self._last_saved_payload = atomic_write_json(
            self.primary_path,
            value,
            backup_path=self.backup_path,
        )
        return True

    def reset_write_cache(self) -> None:
        """Forget the process-local no-op write cache (primarily for tests)."""
        self._last_saved_payload = None
