"""Static wallpaper application service."""

from __future__ import annotations

from collections.abc import Callable, MutableMapping
import os
from threading import RLock
import time
from typing import Any

from app.ports import WallpaperBackend
from app.wallpaper_library import WallpaperLibrary
from app.wallpaper_repositories import CollectionPersistenceError, normalize_wallpaper_path

ConfigProvider = Callable[[], MutableMapping[str, Any]]
LogCallback = Callable[[str], None]
ErrorCallback = Callable[[str], None]
ProgressCallback = Callable[[str, float], None]


class WallpaperServiceError(RuntimeError):
    """Base class for wallpaper use-case failures."""


class WallpaperBackendError(WallpaperServiceError):
    """The platform backend could not apply the wallpaper."""


class WallpaperPersistenceError(WallpaperServiceError):
    """The wallpaper was applied but application state could not be saved."""


class WallpaperService:
    """Apply static wallpapers and own current/history transaction semantics."""

    def __init__(
        self,
        *,
        backend: WallpaperBackend,
        config: ConfigProvider,
        library: WallpaperLibrary,
        operation_lock: RLock,
        is_cancelled: Callable[[], bool] = lambda: False,
        stop_dynamic: Callable[[], bool] | None = None,
        slideshow_update: Callable[[str, MutableMapping[str, Any]], str | None] | None = None,
        preview: Callable[[str], None] | None = None,
        log: LogCallback = lambda _message: None,
        set_error: ErrorCallback = lambda _message: None,
        normalize_fit_mode: Callable[[str], str] = lambda value: value,
        cache_ttl: float = 30.0,
    ) -> None:
        self._backend = backend
        self._config_provider = config
        self._library = library
        self._lock = operation_lock
        self._is_cancelled = is_cancelled
        self._stop_dynamic = stop_dynamic
        self._slideshow_update = slideshow_update
        self._preview = preview
        self._log = log
        self._set_error = set_error
        self._normalize_fit_mode = normalize_fit_mode
        self._cache_ttl = max(0.0, float(cache_ttl))
        self._cached_current = ""
        self._cached_at = 0.0
        self._query_error = ""
        self._query_error_at = 0.0

    @property
    def _config(self) -> MutableMapping[str, Any]:
        config = self._config_provider()
        if not isinstance(config, MutableMapping):
            raise TypeError("config provider must return a mutable mapping")
        return config

    def invalidate_current_cache(self) -> None:
        with self._lock:
            self._cached_current = ""
            self._cached_at = 0.0

    def note_current_wallpaper(self, path: object) -> None:
        """Record an externally observed current wallpaper (v1.6.3).

        The WM_SETTINGCHANGE handler queries the authoritative system value
        with ``use_cache=False``; feeding the answer back here keeps the
        navigation cache in sync immediately after external wallpaper changes
        instead of waiting for the TTL to expire. An empty/unformattable value
        clears the cache so the next read re-queries the backend.
        """
        normalized = normalize_wallpaper_path(path)
        with self._lock:
            self._prime_current_cache(normalized)

    def _prime_current_cache(self, path: str) -> None:
        """Record *path* as the known system wallpaper (caller holds the lock).

        v1.6.3: ``apply()`` used to invalidate the current-wallpaper cache after
        a successful backend set. Combined with history navigation querying
        with ``use_cache=False`` on every click, each 上一张/下一张 paid a
        fresh system query (Windows: a new COM apartment on the worker thread
        plus an IDesktopWallpaper round-trip to Explorer), and the
        WM_SETTINGCHANGE broadcast that follows our own switch paid another
        one on the GUI thread. After a successful set the system wallpaper is
        exactly ``path`` (the platform layer maps any non-ASCII alias back to
        the original on read), so priming the cache removes both per-switch
        queries while the 30-second TTL still bounds staleness for external
        changes.
        """
        self._cached_current = str(path or "")
        self._cached_at = time.monotonic() if path else 0.0

    def get_current(self, *, use_cache: bool = True) -> str:
        with self._lock:
            now = time.monotonic()
            if use_cache and self._cached_current and now - self._cached_at < self._cache_ttl:
                return self._cached_current
            try:
                path = str(self._backend.get_current() or "")
            except Exception as exc:
                message = str(exc)
                if message != self._query_error or now - self._query_error_at >= 20.0:
                    self._log("获取当前壁纸失败: " + message)
                    self._query_error = message
                    self._query_error_at = now
                return ""
            self._query_error = ""
            if path and use_cache:
                self._cached_current = path
                self._cached_at = now
            return path

    def record(
        self,
        path: str,
        *,
        update_current: bool = True,
        refresh_preview: bool = True,
    ) -> bool:
        normalized = normalize_wallpaper_path(path)
        if not normalized or not os.path.isfile(normalized):
            return False
        with self._lock:
            config = self._config
            updates: dict[str, Any] = {}
            if self._slideshow_update is not None:
                anchor = self._slideshow_update(normalized, config)
                if anchor:
                    updates["slideshow_last_wallpaper"] = anchor
            changed = self._library.remember_wallpaper(
                normalized,
                update_current=update_current,
                updates=updates,
            )
            if changed and refresh_preview and self._preview is not None:
                self._preview(normalized)
            return changed

    def apply(
        self,
        path: str,
        operation_name: str = "系统",
        *,
        record_history: bool = True,
        previous_path: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> bool:
        """Apply one wallpaper as a single service transaction.

        If persistence fails after the platform wallpaper changed, the service
        restores the previous system wallpaper when possible and leaves the
        config/history transaction rolled back.
        """
        with self._lock:
            if self._is_cancelled():
                return self._fail("壁纸操作已在开始前终止")
            normalized = normalize_wallpaper_path(path)
            if not normalized or not os.path.isfile(normalized):
                return self._fail("壁纸文件不存在: " + normalized)
            config = self._config
            previous_system = normalize_wallpaper_path(previous_path or "")
            if not previous_system:
                previous_system = normalize_wallpaper_path(self.get_current(use_cache=False))

            if self._stop_dynamic is not None:
                try:
                    stopped_dynamic = bool(self._stop_dynamic())
                except Exception as exc:
                    return self._fail("停止动态壁纸失败: " + str(exc))
                if stopped_dynamic:
                    self._emit(progress, "正在停止动态壁纸…", 0.1)

            fit_mode = self._normalize_fit_mode(str(config.get("fit_mode", "填充")))
            self._emit(progress, "正在应用适应方式…", 0.2)
            try:
                fit_ok = self._backend.configure_fit_mode(fit_mode)
            except Exception as exc:
                return self._fail("设置适应模式失败: " + str(exc))
            # v1.6.0 审计 P0-2: Windows 后端失败时返回 False（COM 与注册表
            # 两条路径都未生效）。旧代码只捕获异常，而后端从不抛异常，这个
            # 失败分支是死代码——失败信号在到达服务层前就被后端吃掉了。
            # ``None``（其余平台/旧后端）维持旧语义，不视为失败。
            if fit_ok is False:
                return self._fail("设置适应模式失败: 桌面接口与注册表回退均未生效")
            if self._is_cancelled():
                return self._fail("壁纸操作已终止，跳过系统壁纸设置")

            self._emit(progress, "正在设置壁纸…", 0.5)
            try:
                self._backend.set_wallpaper(normalized)
            except OSError as exc:
                return self._fail("设置壁纸失败（系统错误）: " + str(exc))
            except Exception as exc:
                return self._fail("设置壁纸失败（未知错误）: " + str(exc))

            try:
                if record_history:
                    self.record(normalized, update_current=True, refresh_preview=False)
                else:
                    updates: dict[str, Any] = {}
                    if self._slideshow_update is not None:
                        anchor = self._slideshow_update(normalized, config)
                        if anchor:
                            updates["slideshow_last_wallpaper"] = anchor
                    self._library.remember_current_without_reordering(
                        normalized,
                        updates=updates,
                    )
            except CollectionPersistenceError as exc:
                rollback_error = self._rollback_backend(previous_system)
                message = ("保存壁纸历史失败: " if record_history else "保存当前壁纸位置失败: ") + str(exc)
                if rollback_error:
                    message += "；恢复原壁纸失败: " + rollback_error
                return self._fail(message)

            self._prime_current_cache(normalized)
            if self._preview is not None:
                self._preview(normalized)
            self._log("设置壁纸成功: " + os.path.basename(normalized))
            self._log(f"{operation_name}: {normalized}")
            self._set_error("")
            self._emit(progress, "完成", 1.0)
            return True

    def _rollback_backend(self, previous_system: str) -> str:
        if not previous_system or not os.path.isfile(previous_system):
            return ""
        try:
            self._backend.set_wallpaper(previous_system)
            # v1.6.3: the rollback just restored *previous_system* through the
            # backend, so it is the authoritative current wallpaper now.
            self._prime_current_cache(previous_system)
            return ""
        except Exception as exc:
            self.invalidate_current_cache()
            return str(exc)

    def _fail(self, message: str) -> bool:
        self._log(message)
        self._set_error(message)
        return False

    @staticmethod
    def _emit(progress: ProgressCallback | None, status: str, value: float) -> None:
        if progress is None:
            return
        try:
            progress(status, value)
        except Exception:
            pass
