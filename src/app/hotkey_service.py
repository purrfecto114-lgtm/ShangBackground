"""Global-hotkey application service.

The service owns opt-in policy, configuration snapshots, focus-guard decisions
and dispatch throttling. Platform backends only register/unregister key chords.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from threading import RLock
from time import monotonic
from typing import Any

from app.ports import HotkeyBackend

HotkeyDispatch = Callable[[str], None]
HotkeyFocusGuard = Callable[[str, str], str]


class HotkeyService:
    """Coordinate hotkey configuration with one injected platform backend."""

    ACTIONS = ("previous", "next", "random", "jump", "mode")

    def __init__(
        self,
        *,
        backend: HotkeyBackend,
        config: Callable[[], MutableMapping[str, Any]],
        dispatch: HotkeyDispatch,
        focus_guard: HotkeyFocusGuard | None = None,
        log: Callable[[str], None] = lambda _message: None,
    ) -> None:
        self._backend = backend
        self._config_provider = config
        self._dispatch = dispatch
        self._focus_guard = focus_guard
        self._log = log
        self._lock = RLock()
        self._bindings: dict[str, str] = {}
        self._guard_logs: dict[str, float] = {}
        self._generation = 0

    @property
    def _config(self) -> MutableMapping[str, Any]:
        config = self._config_provider()
        if not isinstance(config, MutableMapping):
            raise TypeError("config provider must return a mutable mapping")
        return config

    def refresh(self) -> bool:
        """Apply the current opt-in setting and hotkey bindings."""
        with self._lock:
            self._generation += 1
            generation = self._generation
            config = self._config
            if not bool(config.get("global_hotkeys_enabled", False)):
                self._bindings.clear()
                self._safe_stop()
                self._log("全局热键未启用，已跳过系统级注册")
                return False

            bindings = {
                action: str(config.get(f"hotkey_{action}", "") or "").strip()
                for action in self.ACTIONS
            }
            bindings = {action: chord for action, chord in bindings.items() if chord}
            if not bindings:
                self._bindings.clear()
                self._safe_stop()
                self._log("无有效的全局热键配置")
                return False

            self._bindings = dict(bindings)
            callback = lambda action: self._handle_action(action, generation)
            try:
                registered = bool(self._backend.refresh(bindings, callback))
            except Exception as exc:
                self._log(f"注册全局热键失败: {exc}")
                registered = False
            if not registered:
                self._bindings.clear()
                self._safe_stop()
            return registered

    def stop(self) -> bool:
        with self._lock:
            self._generation += 1
            self._bindings.clear()
            return self._safe_stop()

    def _safe_stop(self) -> bool:
        try:
            self._backend.stop()
            return True
        except Exception as exc:
            self._log(f"停止全局热键失败: {exc}")
            return False

    def _handle_action(self, action: str, generation: int) -> None:
        action = str(action or "").strip().lower()
        with self._lock:
            if generation != self._generation:
                return
            binding = self._bindings.get(action, "")
        if not action or not binding:
            return
        config = self._config
        if bool(config.get("hotkey_focus_guard", True)) and self._focus_guard is not None:
            try:
                reason = str(self._focus_guard(action, binding) or "")
            except Exception as exc:
                # Focus probing is a safety enhancement, not a reason to disable
                # every hotkey when a desktop API is temporarily unavailable.
                self._log(f"全局热键焦点检测失败，已放行本次热键: {exc}")
                reason = ""
            if reason:
                self._log_guard(reason)
                return
        try:
            with self._lock:
                if generation != self._generation:
                    return
                self._dispatch(action)
        except Exception as exc:
            self._log(f"全局热键派发失败({action}): {exc}")

    def _log_guard(self, reason: str) -> None:
        now = monotonic()
        previous = self._guard_logs.get(reason, 0.0)
        if now - previous >= 1.2:
            self._guard_logs[reason] = now
            self._log(f"全局热键已忽略：{reason}")

    @property
    def bindings(self) -> Mapping[str, str]:
        with self._lock:
            return dict(self._bindings)
