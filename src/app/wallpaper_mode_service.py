"""Wallpaper-mode switching coordinator."""
from __future__ import annotations

from collections.abc import Callable, MutableMapping, Sequence
from dataclasses import dataclass
from threading import RLock
from typing import Any


class WallpaperModeError(RuntimeError):
    """Raised when a mode transition or its persistence transaction fails."""


@dataclass(frozen=True, slots=True)
class ModeActivationResult:
    """Describe whether activation succeeded and already committed the config."""

    ok: bool
    persisted: bool = False


class WallpaperModeService:
    """Coordinate mutually exclusive wallpaper modes with compensation rollback."""

    def __init__(
        self,
        *,
        config: Callable[[], MutableMapping[str, Any]],
        persist: Callable[[], bool],
        operation_lock: RLock,
        mode_order: Sequence[str],
        normalize_mode: Callable[[str], str],
        activate: Callable[
            [str, MutableMapping[str, Any]],
            bool | ModeActivationResult,
        ],
        log: Callable[[str], None] = lambda _message: None,
    ) -> None:
        self._config_provider = config
        self._persist = persist
        self._lock = operation_lock
        self._normalize_mode = normalize_mode
        self._activate = activate
        self._log = log
        order: list[str] = []
        for item in mode_order:
            normalized = normalize_mode(str(item))
            if normalized and normalized not in order:
                order.append(normalized)
        if "HTML" not in order:
            order.append("HTML")
        self._order = tuple(order)

    @property
    def _config(self) -> MutableMapping[str, Any]:
        config = self._config_provider()
        if not isinstance(config, MutableMapping):
            raise TypeError("config provider must return a mutable mapping")
        return config

    def resolve(self, target: str | None) -> str:
        raw = str(target or "next").strip()
        if raw.lower() in {"next", "cycle"}:
            current = self._normalize_mode(self._config.get("mode", ""))
            try:
                index = self._order.index(current)
            except ValueError:
                index = -1
            return self._order[(index + 1) % len(self._order)]
        return self._normalize_mode(raw)

    def switch(self, target: str | None = "next") -> bool:
        with self._lock:
            config = self._config
            selected = self.resolve(target)
            if selected not in self._order:
                raise WallpaperModeError(f"不支持的壁纸模式: {selected or target}")
            before = dict(config)
            previous_mode = self._normalize_mode(before.get("mode", ""))
            config["mode"] = selected
            try:
                activation = self._activation_result(self._activate(selected, config))
            except Exception as exc:
                self._compensate(config, before, previous_mode)
                raise WallpaperModeError(f"切换壁纸模式失败({selected}): {exc}") from exc
            if not activation.ok:
                self._compensate(config, before, previous_mode)
                raise WallpaperModeError(f"切换壁纸模式失败: {selected}")
            config["mode"] = selected
            if activation.persisted:
                return True
            try:
                persisted = bool(self._persist())
            except Exception as exc:
                self._compensate(config, before, previous_mode)
                raise WallpaperModeError(f"保存壁纸模式失败({selected}): {exc}") from exc
            if not persisted:
                self._compensate(config, before, previous_mode)
                raise WallpaperModeError(f"保存壁纸模式失败: {selected}")
            return True


    @staticmethod
    def _activation_result(raw: bool | ModeActivationResult) -> ModeActivationResult:
        if isinstance(raw, ModeActivationResult):
            return raw
        return ModeActivationResult(bool(raw), persisted=False)

    def _compensate(
        self,
        config: MutableMapping[str, Any],
        before: dict[str, Any],
        previous_mode: str,
    ) -> None:
        config.clear()
        config.update(before)
        if previous_mode in self._order:
            try:
                self._activate(previous_mode, config)
            except Exception as exc:
                self._log(f"回滚壁纸模式运行状态失败({previous_mode}): {exc}")
        config.clear()
        config.update(before)
        try:
            if not bool(self._persist()):
                self._log("回滚壁纸模式配置失败")
        except Exception as exc:
            self._log(f"回滚壁纸模式配置失败: {exc}")
