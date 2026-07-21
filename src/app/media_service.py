"""Dynamic video/HTML wallpaper lifecycle service."""
from __future__ import annotations

from collections.abc import Callable, MutableMapping
import os
from threading import RLock
from typing import Any

from app.ports import MediaBackend, MediaKind
from app.runtime_state import DynamicWallpaperState
from app.wallpaper_repositories import normalize_wallpaper_path


class MediaServiceError(RuntimeError):
    """Base class for dynamic wallpaper failures."""


class MediaValidationError(MediaServiceError):
    pass


class MediaStartError(MediaServiceError):
    pass


class MediaStopError(MediaServiceError):
    pass


class MediaPersistenceError(MediaServiceError):
    pass


class MediaService:
    """Coordinate video/HTML exclusivity, backend truth and persistence."""

    _CONFIG_KEYS = (
        "mode",
        "video_file",
        "html_file",
        "html_url",
        "current_wallpaper",
    )

    def __init__(
        self,
        *,
        backend: MediaBackend,
        config: Callable[[], MutableMapping[str, Any]],
        persist: Callable[[], bool],
        state: DynamicWallpaperState,
        operation_lock: RLock,
        capture_session: Callable[[], bool] | None = None,
        log: Callable[[str], None] = lambda _message: None,
    ) -> None:
        self._backend = backend
        self._config_provider = config
        self._persist = persist
        self._state = state
        self._lock = operation_lock
        self._capture_session = capture_session
        self._log = log

    @property
    def _config(self) -> MutableMapping[str, Any]:
        config = self._config_provider()
        if not isinstance(config, MutableMapping):
            raise TypeError("config provider must return a mutable mapping")
        return config

    def start_video(self, path: str | None = None) -> bool:
        with self._lock:
            config = self._config
            target = normalize_wallpaper_path(path or config.get("video_file", ""))
            if not target:
                raise MediaValidationError("请先选择视频文件")
            self._ensure_exclusive("video")
            self._capture_once()
            try:
                raw_volume = int(config.get("video_volume", 100))
            except (TypeError, ValueError):
                raw_volume = 100
            options = {
                "muted": bool(config.get("video_muted", True)),
                "volume": max(0, min(100, raw_volume)),
            }
            return self._start_and_persist("video", target, options)

    def start_html(self, path: str | None = None) -> bool:
        with self._lock:
            config = self._config
            raw = path if path is not None else (
                config.get("html_file", "") or config.get("html_url", "")
            )
            target = str(raw or "").strip()
            if target and not target.lower().startswith(("http://", "https://")):
                target = normalize_wallpaper_path(target)
            if not target:
                raise MediaValidationError("请先选择 HTML 文件或输入 URL")
            if not self._backend.validate("html", target):
                raise MediaValidationError("所选文件不是有效的 HTML 文件或 URL")
            self._ensure_exclusive("html")
            self._capture_once()
            options = {
                "auto_pause": bool(config.get("html_auto_pause", True)),
                "frame_rate": int(config.get("html_frame_rate", 30)),
            }
            for key, value in options.items():
                try:
                    self._backend.set_option("html", key, value)
                except Exception as exc:
                    self._log(f"同步 HTML 壁纸运行选项失败({key}): {exc}")
            return self._start_and_persist("html", target, options)

    def stop(self, kind: MediaKind) -> bool:
        """Stop *kind* only when the backend reports an active renderer.

        Older mode-switch code called ``stop_all`` defensively.  ``stop`` then
        invoked both platform backends even when their PID/state was empty,
        producing misleading messages such as ``stop_html_wallpaper ... pid=None``
        and doing unnecessary process-state and option-file I/O.  The probe is
        the authoritative lifecycle gate: inactive backends are marked stopped
        in memory and otherwise left untouched.
        """
        with self._lock:
            was_running = self._probe(kind)
            if not was_running:
                self._state.mark_stopped(kind)
                return False
            try:
                self._backend.stop(kind)
            except Exception as exc:
                running = self._probe(kind)
                if running:
                    raise MediaStopError(f"停止 {kind} 壁纸失败: {exc}") from exc
                return False
            running = self._probe(kind)
            if running:
                raise MediaStopError(f"停止 {kind} 壁纸失败：后端仍在运行")
            self._state.mark_stopped(kind)
            return True

    def stop_all(self) -> bool:
        with self._lock:
            any_running = False
            errors: list[str] = []
            for kind in ("html", "video"):
                try:
                    any_running = self.stop(kind) or any_running
                except MediaStopError as exc:
                    errors.append(str(exc))
            if errors:
                raise MediaStopError("；".join(errors))
            return any_running

    def is_running(self, kind: MediaKind) -> bool:
        with self._lock:
            return self._probe(kind)

    def set_option(self, kind: MediaKind, key: str, value: Any) -> bool:
        with self._lock:
            try:
                return bool(self._backend.set_option(kind, str(key), value))
            except Exception as exc:
                self._log(f"热更新 {kind} 壁纸选项失败({key}={value}): {exc}")
                return False

    def last_target(self, kind: MediaKind) -> str:
        with self._lock:
            try:
                return str(self._backend.last_target(kind) or "")
            except Exception:
                return ""

    def restart_html(self, path: str | None = None) -> bool:
        with self._lock:
            config = self._config
            target = str(path or self.last_target("html") or config.get("html_file", "") or "")
            if not target:
                return False
            options = {
                "auto_pause": bool(config.get("html_auto_pause", True)),
                "frame_rate": int(config.get("html_frame_rate", 30)),
            }
            for key, value in options.items():
                self.set_option("html", key, value)
            try:
                result = self._backend.restart("html", target, options=options)
            except Exception as exc:
                self._probe("html")
                raise MediaStartError(str(exc)) from exc
            if not result.ok:
                self._probe("html")
                return False
            self._state.mark_started("html", target)
            return True

    def _start_and_persist(
        self,
        kind: MediaKind,
        target: str,
        options: dict[str, Any],
    ) -> bool:
        config = self._config
        previous = {key: config.get(key) for key in self._CONFIG_KEYS}
        try:
            result = self._backend.start(kind, target, options=options)
        except Exception as exc:
            self._probe(kind)
            raise MediaStartError(str(exc)) from exc
        if not result.ok:
            self._probe(kind)
            raise MediaStartError(result.message or f"{kind} 壁纸启动失败")

        if kind == "video":
            config["mode"] = "视频"
            config["video_file"] = target
        else:
            config["mode"] = "HTML"
            config["html_file"] = target
        config["current_wallpaper"] = target
        try:
            persisted = bool(self._persist())
        except Exception as exc:
            persisted = False
            persistence_error = exc
        else:
            persistence_error = None
        if not persisted:
            self._restore_config(config, previous)
            stop_error = ""
            try:
                self._backend.stop(kind)
            except Exception as exc:
                stop_error = str(exc)
            self._probe(kind)
            message = f"{kind} 壁纸已启动但配置保存失败"
            if persistence_error is not None:
                message += ": " + str(persistence_error)
            if stop_error:
                message += "；回滚停止失败: " + stop_error
            raise MediaPersistenceError(message)

        self._state.mark_started(kind, target)
        if result.message:
            self._log(f"{kind} 壁纸提示: {result.message}")
        self._log(f"{kind} 壁纸已启动: {os.path.basename(target)}")
        return True

    def _ensure_exclusive(self, requested: MediaKind) -> None:
        other: MediaKind = "html" if requested == "video" else "video"
        if self._probe(other):
            self.stop(other)
        if self._probe(other):
            raise MediaStopError(f"无法停止 {other} 壁纸，拒绝启动 {requested} 壁纸")

    def _capture_once(self) -> None:
        if self._capture_session is not None:
            self._capture_session()

    def _probe(self, kind: MediaKind) -> bool:
        try:
            running = bool(self._backend.is_running(kind))
        except Exception as exc:
            self._log(f"检查 {kind} 壁纸运行状态失败: {exc}")
            running = False
        config = self._config
        target = (
            config.get("video_file", "")
            if kind == "video"
            else config.get("html_file", "") or config.get("html_url", "")
        )
        self._state.reconcile(kind, running, str(target or ""))
        return running

    @staticmethod
    def _restore_config(
        config: MutableMapping[str, Any],
        previous: dict[str, Any],
    ) -> None:
        for key, value in previous.items():
            if value is None:
                config.pop(key, None)
            else:
                config[key] = value
