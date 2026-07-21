"""Smooth video focus-policy behavior shared by every platform UI."""
from __future__ import annotations

from app.audio_fade import volume_fade_steps
from app.config import normalize_mode_key
from core import engine as core


class VideoFocusMixin:
    """Apply pause/duck policies without blocking the Qt main thread."""

    def on_video_focus_behavior_changed(self, *_args):
        value = "none"
        try:
            data = self.video_focus_behavior_combo.currentData()
            value = str(data or "none")
        except Exception:
            value = "none"
        if value not in {"none", "pause", "duck"}:
            value = "none"
        core.config["video_focus_behavior"] = value
        try:
            core.save_config()
        except Exception:
            pass
        # 切换策略时立即复位旧策略造成的临时暂停/降音量。
        self._restore_video_focus_policy()

    def _cancel_video_volume_ramp(self) -> None:
        timer = getattr(self, "_video_volume_ramp_timer", None)
        if timer is not None:
            timer.stop()
        steps = getattr(self, "_video_volume_ramp_steps", None)
        if steps is not None:
            steps.clear()
        self._video_volume_ramp_callback = None
        self._video_volume_ramp_target = None

    def _set_video_runtime_volume(self, muted: bool, volume: int) -> bool:
        clamped = max(0, min(100, int(volume)))
        try:
            ok = bool(core.set_video_volume(bool(muted), clamped))
        except Exception:
            ok = False
        if ok:
            self._video_runtime_volume = 0 if muted else clamped
        return ok

    def _start_video_volume_ramp(
        self,
        target: int,
        *,
        duration_ms: int,
        callback=None,
        start: int | None = None,
    ) -> None:
        target_value = max(0, min(100, int(target)))
        start_value = (
            max(0, min(100, int(start)))
            if start is not None
            else max(0, min(100, int(getattr(self, "_video_runtime_volume", target_value))))
        )
        self._cancel_video_volume_ramp()
        self._video_volume_ramp_steps.extend(
            volume_fade_steps(
                start_value,
                target_value,
                duration_ms=duration_ms,
                interval_ms=max(10, int(self._video_volume_ramp_timer.interval())),
            )
        )
        self._video_volume_ramp_callback = callback
        self._video_volume_ramp_target = target_value
        self._video_volume_ramp_timer.start()

    def _finish_video_volume_ramp(self, success: bool) -> None:
        callback = self._video_volume_ramp_callback
        self._video_volume_ramp_timer.stop()
        self._video_volume_ramp_steps.clear()
        self._video_volume_ramp_callback = None
        self._video_volume_ramp_target = None
        if callback is not None:
            try:
                callback(bool(success))
            except Exception:
                pass

    def _video_volume_ramp_tick(self) -> None:
        if not self._video_volume_ramp_steps:
            self._finish_video_volume_ramp(True)
            return
        value = self._video_volume_ramp_steps.popleft()
        if not self._set_video_runtime_volume(False, value):
            self._finish_video_volume_ramp(False)
            return
        if not self._video_volume_ramp_steps:
            self._finish_video_volume_ramp(True)

    def _finish_focus_pause_after_fade(self, _volume_success: bool) -> None:
        if not getattr(self, "_video_focus_pause_pending", False):
            return
        self._video_focus_pause_pending = False
        should_pause = (
            normalize_mode_key(core.config.get("mode")) == "视频"
            and str(core.config.get("video_focus_behavior", "none") or "none") == "pause"
            and core.is_video_wallpaper_running()
            and not self._is_desktop_foreground()
        )
        if not should_pause:
            self._restore_video_focus_policy()
            return
        try:
            if core.set_video_paused(True):
                self._video_focus_paused = True
                return
        except Exception:
            pass
        # The backend could not pause. Restore the saved level rather than
        # leaving a live video silently at volume zero.
        base = max(0, min(100, int(core.config.get("video_volume", 100))))
        if not bool(core.config.get("video_muted", True)):
            self._set_video_runtime_volume(False, base)

    def _restore_video_focus_policy(self) -> None:
        try:
            base = max(0, min(100, int(core.config.get("video_volume", 100))))
            muted = bool(core.config.get("video_muted", True))
            was_pending = bool(getattr(self, "_video_focus_pause_pending", False))
            if was_pending:
                self._video_focus_pause_pending = False
                self._cancel_video_volume_ramp()

            if getattr(self, "_video_focus_paused", False):
                if core.set_video_paused(False):
                    self._video_focus_paused = False
                    if not muted and base > 0:
                        # Resume at silence, then fade in to avoid a sudden
                        # audio jump when the desktop regains focus.
                        self._set_video_runtime_volume(False, 0)
                        self._start_video_volume_ramp(
                            base, duration_ms=480, start=0
                        )
                    else:
                        self._set_video_runtime_volume(muted, base)
                return

            if getattr(self, "_video_focus_ducked", False) or was_pending:
                self._video_focus_ducked = False
                if muted or base <= 0:
                    self._cancel_video_volume_ramp()
                    self._set_video_runtime_volume(muted, base)
                elif not (
                    self._video_volume_ramp_timer.isActive()
                    and self._video_volume_ramp_target == base
                ):
                    self._start_video_volume_ramp(base, duration_ms=420)
        except Exception:
            pass

    def _apply_video_focus_policy_tick(self) -> None:
        try:
            if normalize_mode_key(core.config.get("mode")) != "视频" or not core.is_video_wallpaper_running():
                self._restore_video_focus_policy()
                return
            policy = str(core.config.get("video_focus_behavior", "none") or "none")
            if policy not in {"pause", "duck"}:
                self._restore_video_focus_policy()
                return
            on_desktop = self._is_desktop_foreground()
            if on_desktop:
                self._restore_video_focus_policy()
                return

            muted = bool(core.config.get("video_muted", True))
            base = max(0, min(100, int(core.config.get("video_volume", 100))))
            if policy == "pause":
                if getattr(self, "_video_focus_ducked", False):
                    self._video_focus_ducked = False
                    self._cancel_video_volume_ramp()
                if getattr(self, "_video_focus_paused", False) or getattr(self, "_video_focus_pause_pending", False):
                    return
                if muted or base <= 0:
                    if core.set_video_paused(True):
                        self._video_focus_paused = True
                    return
                self._video_focus_pause_pending = True
                self._start_video_volume_ramp(
                    0,
                    duration_ms=320,
                    callback=self._finish_focus_pause_after_fade,
                )
                return

            # Ducking uses the same ramp machinery so changing focus does not
            # produce an audible step even when the saved volume is high.
            if getattr(self, "_video_focus_paused", False) or getattr(self, "_video_focus_pause_pending", False):
                self._restore_video_focus_policy()
            if muted:
                return
            duck = max(0, min(base, int(core.config.get("video_focus_duck_volume", 20))))
            if not getattr(self, "_video_focus_ducked", False):
                self._video_focus_ducked = True
                self._start_video_volume_ramp(duck, duration_ms=280)
        except Exception:
            pass

