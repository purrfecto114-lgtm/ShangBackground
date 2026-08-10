"""Idempotent application shutdown transaction.

The service is intentionally Qt-free.  Presentation and entry-point code may
call it from normal exit, closeEvent, aboutToQuit or signal handlers; only the
first caller performs cleanup and every later caller receives the same report.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from threading import RLock
from typing import Any

CleanupCallback = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class ExitStepResult:
    """Outcome of one cleanup step."""

    name: str
    attempted: bool
    success: bool
    detail: str = ""
    error: str = ""

    @property
    def skipped(self) -> bool:
        return not self.attempted


@dataclass(frozen=True, slots=True)
class ExitReport:
    """Immutable report for one completed shutdown transaction."""

    reason: str
    restore_wallpaper: bool
    restarting: bool
    steps: tuple[ExitStepResult, ...]
    repeated: bool = False

    @property
    def success(self) -> bool:
        return all(step.success for step in self.steps if step.attempted)

    @property
    def failures(self) -> tuple[ExitStepResult, ...]:
        return tuple(step for step in self.steps if step.attempted and not step.success)


class ExitService:
    """Run resource cleanup exactly once and continue after individual failures."""

    ORDER = (
        "cancel_operations",
        "stop_slideshow",
        "stop_media",
        "stop_hotkeys",
        "restore_wallpaper",
        "close_ipc",
        "release_single_instance",
    )

    def __init__(
        self,
        *,
        request_cancel: CleanupCallback,
        stop_slideshow: CleanupCallback,
        stop_media: CleanupCallback,
        stop_hotkeys: CleanupCallback,
        restore_wallpaper: CleanupCallback,
        has_restore_candidate: Callable[[], bool] = lambda: True,
        close_ipc: CleanupCallback | None = None,
        release_single_instance: CleanupCallback | None = None,
        log: Callable[[str], None] = lambda _message: None,
    ) -> None:
        self._request_cancel = request_cancel
        self._stop_slideshow = stop_slideshow
        self._stop_media = stop_media
        self._stop_hotkeys = stop_hotkeys
        self._restore_wallpaper = restore_wallpaper
        self._has_restore_candidate = has_restore_candidate
        self._close_ipc = close_ipc
        self._release_single_instance = release_single_instance
        self._log = log
        self._lock = RLock()
        self._report: ExitReport | None = None

    def configure_runtime_resources(
        self,
        *,
        close_ipc: CleanupCallback | None = None,
        release_single_instance: CleanupCallback | None = None,
    ) -> None:
        """Attach resources that are created after application bootstrap."""
        with self._lock:
            if self._report is not None:
                return
            if close_ipc is not None:
                self._close_ipc = close_ipc
            if release_single_instance is not None:
                self._release_single_instance = release_single_instance

    def run(
        self,
        *,
        reason: str = "application_exit",
        restore_wallpaper: bool = True,
        restarting: bool = False,
    ) -> ExitReport:
        """Perform the ordered cleanup transaction once.

        A restart always suppresses wallpaper restoration, including when a
        later ``aboutToQuit`` callback asks for normal-exit behavior.
        """
        with self._lock:
            if self._report is not None:
                return replace(self._report, repeated=True)

            restore_requested = bool(restore_wallpaper) and not bool(restarting)
            steps: list[ExitStepResult] = []
            steps.append(self._execute("cancel_operations", self._request_cancel))
            steps.append(
                self._execute("stop_slideshow", self._stop_slideshow, false_is_failure=True)
            )
            # ``MediaService.stop_all`` returns False when nothing was running;
            # that is a successful no-op, not a cleanup failure.
            steps.append(self._execute("stop_media", self._stop_media, false_is_failure=False))
            steps.append(
                self._execute("stop_hotkeys", self._stop_hotkeys, false_is_failure=True)
            )

            if not restore_requested:
                detail = "restart" if restarting else "disabled"
                steps.append(self._skipped("restore_wallpaper", detail))
            elif not self._safe_has_restore_candidate():
                steps.append(self._skipped("restore_wallpaper", "no restore candidate"))
            else:
                steps.append(
                    self._execute(
                        "restore_wallpaper",
                        self._restore_wallpaper,
                        false_is_failure=True,
                    )
                )

            if self._close_ipc is None:
                steps.append(self._skipped("close_ipc", "not configured"))
            else:
                steps.append(self._execute("close_ipc", self._close_ipc))

            if restarting:
                # Keep the authoritative singleton guard until this process
                # actually terminates.  The replacement child waits for this
                # exact parent PID, so releasing here would create a handoff
                # window where an unrelated third launch could become primary.
                steps.append(self._skipped("release_single_instance", "restart handoff"))
            elif self._release_single_instance is None:
                steps.append(self._skipped("release_single_instance", "not configured"))
            else:
                steps.append(
                    self._execute(
                        "release_single_instance",
                        self._release_single_instance,
                        false_is_failure=True,
                    )
                )

            report = ExitReport(
                reason=str(reason or "application_exit"),
                restore_wallpaper=restore_requested,
                restarting=bool(restarting),
                steps=tuple(steps),
            )
            self._report = report
            self._log_report(report)
            return report

    @property
    def report(self) -> ExitReport | None:
        with self._lock:
            return self._report

    def _safe_has_restore_candidate(self) -> bool:
        try:
            return bool(self._has_restore_candidate())
        except Exception as exc:
            self._log(f"检查启动前壁纸恢复候选失败: {exc}")
            # Attempt restoration so the service can still inspect persisted
            # session files and return a structured failure if necessary.
            return True

    @staticmethod
    def _skipped(name: str, detail: str) -> ExitStepResult:
        return ExitStepResult(name=name, attempted=False, success=True, detail=detail)

    def _execute(
        self,
        name: str,
        callback: CleanupCallback,
        *,
        false_is_failure: bool = False,
    ) -> ExitStepResult:
        try:
            value = callback()
        except Exception as exc:
            self._log(f"退出清理步骤失败({name}): {exc}")
            return ExitStepResult(
                name=name,
                attempted=True,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        if false_is_failure and value is False:
            detail = "callback returned False"
            self._log(f"退出清理步骤失败({name}): {detail}")
            return ExitStepResult(name=name, attempted=True, success=False, detail=detail)
        return ExitStepResult(
            name=name,
            attempted=True,
            success=True,
            detail="" if value is None else repr(value),
        )

    def _log_report(self, report: ExitReport) -> None:
        failed = ", ".join(step.name for step in report.failures)
        if failed:
            self._log(f"退出资源释放完成，但以下步骤失败: {failed}")
        else:
            self._log("退出资源释放事务完成")
