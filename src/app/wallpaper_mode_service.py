"""Wallpaper-mode switching coordinator."""
from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from threading import RLock
from typing import Any


class WallpaperModeError(RuntimeError):
    """Raised when a mode transition or its persistence transaction fails.

    v1.6.1: carries an optional :class:`ModeSwitchReport` describing what the
    compensation rollback actually restored, so callers (engine facade, UI)
    can tell the user whether their previous mode and its persisted
    configuration survived, instead of only logging it.
    """

    def __init__(self, message: str, *, report: "ModeSwitchReport | None" = None) -> None:
        super().__init__(message)
        self.report = report


def _html_mode_available() -> bool:
    """Report whether the html wallpaper feature is enabled for this build.

    v1.6.1: feature-gated builds must not expose a ghost "HTML" mode in the
    cycle order. Source-run defaults keep the feature on, so behavior for
    developers is unchanged.
    """
    try:
        from app.build_features import is_feature_enabled

        return bool(is_feature_enabled("html"))
    except Exception:
        # Fail-open only for source runs where the manifest is absent; the
        # build_features loader itself defaults all features on in that case.
        return True


@dataclass(frozen=True, slots=True)
class ModeActivationResult:
    """Describe whether activation succeeded and already committed the config.

    v1.6.1 rework: ``__bool__`` mirrors ``ok`` so a bare truthiness check
    cannot mistake a failed ``ModeActivationResult`` for success (dataclass
    instances without ``__bool__`` are always truthy).
    """

    ok: bool
    persisted: bool = False

    def __bool__(self) -> bool:
        return self.ok


@dataclass(frozen=True, slots=True)
class ModeRollbackOutcome:
    """What the compensation rollback actually achieved (v1.6.1).

    - ``config_restored``: the in-memory config dict was rolled back.
    - ``runtime_restored``: the previous mode's renderer was re-activated.
      ``None`` means no rollback was attempted (previous mode not in the
      active order, e.g. first switch from an unknown mode).
    - ``persisted``: the rolled-back config was written to disk.
      ``None`` means the persist step was not reached.
    - ``errors``: human-readable rollback failures (logged as before).
    """

    config_restored: bool = True
    runtime_restored: bool | None = None
    persisted: bool | None = None
    errors: tuple[str, ...] = ()

    def summary(self) -> str:
        """One-line status for logs and UI dialogs."""
        parts = ["配置已恢复" if self.config_restored else "配置未能恢复"]
        if self.runtime_restored is None:
            parts.append("无旧模式需要恢复")
        else:
            parts.append("旧模式已恢复运行" if self.runtime_restored else "旧模式恢复失败")
        if self.persisted is None:
            parts.append("未尝试落盘")
        else:
            parts.append("恢复结果已保存" if self.persisted else "恢复结果保存失败")
        return "；".join(parts)


@dataclass(frozen=True, slots=True)
class ModeSwitchReport:
    """Structured outcome of a *failed* wallpaper-mode switch (v1.6.1).

    Successful switches keep the plain ``True`` return value.  This report
    rides on :class:`WallpaperModeError` so the UI can answer the question
    the old flat message could not: "my switch failed — is my desktop and
    saved config still on the previous mode?"
    """

    mode: str
    previous_mode: str
    stage: str  # "activate" (new mode failed to start) | "persist" (new mode ran, saving failed)
    error: str
    rollback: ModeRollbackOutcome = ModeRollbackOutcome()


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
        # v1.6.1 fix: only auto-append the HTML mode when the html feature is
        # actually packaged/enabled. In feature-gated builds (e.g. core-only)
        # the forced append created a ghost mode: the cycle hotkey switched to
        # "HTML", either failed (no backend) or persisted an unrenderable mode
        # that the startup restore gate then skipped forever.
        if "HTML" not in order and _html_mode_available():
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

    def switch(
        self,
        target: str | None = "next",
        *,
        updates: Mapping[str, Any] | None = None,
    ) -> bool:
        with self._lock:
            config = self._config
            selected = self.resolve(target)
            if selected not in self._order:
                raise WallpaperModeError(f"不支持的壁纸模式: {selected or target}")
            # Snapshot *before* staging source/runtime-option changes.  This lets
            # same-mode replacements (video -> new video, HTML -> refreshed
            # HTML) restore both the previous renderer and its previous source
            # if destructive backend startup fails after stopping the old one.
            before = dict(config)
            previous_mode = self._normalize_mode(before.get("mode", ""))
            for key, value in dict(updates or {}).items():
                if key == "mode":
                    continue
                config[str(key)] = value
            config["mode"] = selected
            try:
                activation = self._activation_result(self._activate(selected, config))
            except Exception as exc:
                rollback = self._compensate(config, before, previous_mode)
                raise WallpaperModeError(
                    f"切换壁纸模式失败({selected}): {exc}",
                    report=ModeSwitchReport(
                        mode=selected,
                        previous_mode=previous_mode,
                        stage="activate",
                        error=str(exc),
                        rollback=rollback,
                    ),
                ) from exc
            if not activation.ok:
                rollback = self._compensate(config, before, previous_mode)
                raise WallpaperModeError(
                    f"切换壁纸模式失败: {selected}",
                    report=ModeSwitchReport(
                        mode=selected,
                        previous_mode=previous_mode,
                        stage="activate",
                        error="mode activation returned failure",
                        rollback=rollback,
                    ),
                )
            config["mode"] = selected
            if activation.persisted:
                return True
            try:
                persisted = bool(self._persist())
            except Exception as exc:
                rollback = self._compensate(config, before, previous_mode)
                raise WallpaperModeError(
                    f"保存壁纸模式失败({selected}): {exc}",
                    report=ModeSwitchReport(
                        mode=selected,
                        previous_mode=previous_mode,
                        stage="persist",
                        error=str(exc),
                        rollback=rollback,
                    ),
                ) from exc
            if not persisted:
                rollback = self._compensate(config, before, previous_mode)
                raise WallpaperModeError(
                    f"保存壁纸模式失败: {selected}",
                    report=ModeSwitchReport(
                        mode=selected,
                        previous_mode=previous_mode,
                        stage="persist",
                        error="config persistence returned failure",
                        rollback=rollback,
                    ),
                )
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
    ) -> ModeRollbackOutcome:
        """Best-effort rollback; returns what actually succeeded (v1.6.1).

        Behavior is unchanged from v1.6.0 — the only addition is that every
        partial failure is captured into a :class:`ModeRollbackOutcome`
        instead of vanishing into the log.
        """
        errors: list[str] = []
        config_restored = True
        # v1.6.1 fix: apply the rollback diff key-by-key instead of
        # clear()+update(); clear() briefly emptied the shared config dict,
        # racing concurrent readers outside the operation lock.
        try:
            for key in list(config.keys()):
                if key not in before:
                    del config[key]
            config.update(before)
        except Exception as exc:
            config_restored = False
            errors.append(f"配置回滚失败: {exc}")
            self._log(f"回滚壁纸模式配置失败: {exc}")
        runtime_restored: bool | None = None
        if previous_mode in self._order:
            try:
                # v1.6.1 rework (16-b M1): production activate callbacks return
                # ModeActivationResult (a dataclass whose bare truthiness was
                # always True), so bool() here mis-reported failed rollbacks as
                # "runtime restored". Normalize through _activation_result,
                # exactly like the forward path in switch().
                runtime_restored = self._activation_result(
                    self._activate(previous_mode, config)
                ).ok
                if not runtime_restored:
                    errors.append(f"旧模式重新激活返回失败({previous_mode})")
                    self._log(f"回滚壁纸模式运行状态失败({previous_mode}): 激活返回 False")
            except Exception as exc:
                runtime_restored = False
                errors.append(f"回滚壁纸模式运行状态失败({previous_mode}): {exc}")
                self._log(f"回滚壁纸模式运行状态失败({previous_mode}): {exc}")
        try:
            for key in list(config.keys()):
                if key not in before:
                    del config[key]
            config.update(before)
        except Exception as exc:
            config_restored = False
            errors.append(f"配置二次回滚失败: {exc}")
            self._log(f"回滚壁纸模式配置失败: {exc}")
        persisted: bool | None
        try:
            persisted = bool(self._persist())
            if not persisted:
                errors.append("回滚壁纸模式配置失败")
                self._log("回滚壁纸模式配置失败")
        except Exception as exc:
            persisted = False
            errors.append(f"回滚壁纸模式配置失败: {exc}")
            self._log(f"回滚壁纸模式配置失败: {exc}")
        return ModeRollbackOutcome(
            config_restored=config_restored,
            runtime_restored=runtime_restored,
            persisted=persisted,
            errors=tuple(errors),
        )
