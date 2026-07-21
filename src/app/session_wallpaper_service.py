"""Capture, persist and restore the wallpaper that preceded this app session."""
from __future__ import annotations

from collections.abc import Callable, MutableMapping, Sequence
import json
import os
from threading import RLock
import time
from typing import Any

from app.ports import WallpaperBackend
from app.runtime_state import SessionWallpaperState
from app.storage import atomic_write_json
from app.wallpaper_repositories import normalize_wallpaper_path


class SessionWallpaperService:
    """Own the crash/restart-safe startup-wallpaper restoration transaction."""

    def __init__(
        self,
        *,
        state: SessionWallpaperState,
        backend: WallpaperBackend,
        config: Callable[[], MutableMapping[str, Any]],
        persist_config: Callable[[], bool],
        primary_file: Callable[[], str],
        legacy_files: Callable[[], Sequence[str]] = lambda: (),
        platform_name: Callable[[], str],
        app_base_dir: Callable[[], str],
        get_style: Callable[[], dict[str, Any]] = lambda: {},
        restore_style: Callable[[dict[str, Any]], None] = lambda _style: None,
        stop_dynamic: Callable[[], bool] | None = None,
        refresh_shell: Callable[[], None] | None = None,
        log: Callable[[str], None] = lambda _message: None,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        verify_delays: tuple[float, ...] = (0.12, 0.35),
        pid: Callable[[], int] = os.getpid,
        image_extensions: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".bmp", ".gif"),
        operation_lock: RLock | None = None,
    ) -> None:
        self._state = state
        self._backend = backend
        self._config_provider = config
        self._persist_config = persist_config
        self._primary_file = primary_file
        self._legacy_files = legacy_files
        self._platform_name = platform_name
        self._app_base_dir = app_base_dir
        self._get_style = get_style
        self._restore_style = restore_style
        self._stop_dynamic = stop_dynamic
        self._refresh_shell = refresh_shell
        self._log = log
        self._now = now
        self._sleep = sleep
        self._verify_delays = tuple(max(0.0, float(delay)) for delay in verify_delays)
        self._pid = pid
        self._image_extensions = tuple(ext.lower() for ext in image_extensions)
        self._operation_lock = operation_lock or RLock()

    @property
    def _config(self) -> MutableMapping[str, Any]:
        config = self._config_provider()
        if not isinstance(config, MutableMapping):
            raise TypeError("config provider must return a mutable mapping")
        return config

    def files(self) -> list[str]:
        result: list[str] = []
        for candidate in (self._primary_file(), *self._legacy_files()):
            path = str(candidate or "")
            if path and path not in result:
                result.append(path)
        return result

    def persist(self) -> bool:
        with self._state.lock:
            snapshot = self._state.snapshot()
            if not snapshot.wallpaper:
                return False
            payload = {
                "schema": 2,
                "platform": self._platform_name(),
                "wallpaper": normalize_wallpaper_path(snapshot.wallpaper),
                "style": dict(snapshot.style),
                "captured_at": self._now(),
                "pid": self._pid(),
                "app_base_dir": self._app_base_dir(),
            }
            try:
                atomic_write_json(self._primary_file(), payload, mode=0o600)
                return True
            except Exception as exc:
                self._log(f"保存启动前壁纸会话失败: {exc}")
                return False

    def clear_files(self) -> None:
        with self._state.lock:
            for path in self.files():
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except Exception as exc:
                    self._log(f"清除会话壁纸文件失败({path}): {exc}")

    def load(self, max_age_seconds: float = 24 * 3600) -> bool:
        with self._state.lock:
            for session_file in self.files():
                try:
                    if not os.path.exists(session_file):
                        continue
                    data = self._read_payload(session_file)
                    captured_at = float(data.get("captured_at", 0) or 0)
                    if captured_at <= 0 or self._now() - captured_at > max_age_seconds:
                        self._remove_quietly(session_file)
                        continue
                    platform = str(data.get("platform") or "")
                    if platform and platform != self._platform_name():
                        self._log(f"启动前壁纸会话平台不匹配，已忽略: {platform}")
                        continue
                    target = normalize_wallpaper_path(data.get("wallpaper") or "")
                    if not self.is_restorable(target):
                        self._log(f"启动前壁纸会话无效，已忽略: {target or '<empty>'}")
                        self._remove_quietly(session_file)
                        continue
                    self._state.replace(target, data.get("style") or {})
                    self._log(f"已从会话文件恢复启动前壁纸记录: {target}")
                    return True
                except Exception as exc:
                    self._log(f"读取启动前壁纸会话失败({session_file}): {exc}")
            return False

    def capture(self, *, inherit_existing: bool = False, force_refresh: bool = False) -> bool:
        with self._operation_lock, self._state.lock:
            if self._state.snapshot().captured and not force_refresh:
                return True
            if force_refresh:
                self._state.clear()
            if inherit_existing:
                if self.load():
                    return True
                if not force_refresh:
                    self._log("未找到有效的继承壁纸会话，避免误记录当前程序壁纸")
                    return False
            else:
                self.clear_files()
            try:
                current = normalize_wallpaper_path(self._backend.get_current())
                if not self.is_restorable(current):
                    self._state.clear()
                    self._log("启动前壁纸不是可恢复的本地图片文件，已跳过记录: " + (current or "<empty>"))
                    return False
                self._state.replace(current, self._get_style())
                if not self.persist():
                    # The in-process snapshot is still useful for a normal exit,
                    # but return False so callers know restart inheritance is unsafe.
                    return False
                self._log(f"已记录启动前壁纸: {current}")
                return True
            except Exception as exc:
                self._state.clear()
                self._log(f"记录启动前壁纸失败: {exc}")
                return False

    def restore(self, *, stop_dynamic: bool = True, finalize: bool = False) -> bool:
        with self._operation_lock:
            with self._state.lock:
                snapshot = self._state.snapshot()
                if not snapshot.wallpaper:
                    self.load()
                    snapshot = self._state.snapshot()
                target = normalize_wallpaper_path(snapshot.wallpaper)
                style = dict(snapshot.style)
            if not target:
                self._log("没有可恢复的启动前壁纸记录")
                return False
            if not self.is_restorable(target):
                self._log(f"启动前壁纸不可恢复或文件已不存在，跳过恢复: {target}")
                self.clear_files()
                self._state.clear()
                return False

            if stop_dynamic and self._stop_dynamic is not None:
                try:
                    self._stop_dynamic()
                except Exception as exc:
                    self._log(f"恢复前停止动态壁纸失败: {exc}")
                    return False
                # ``MediaService.stop_all`` returns False when neither dynamic
                # backend is active.  That is a successful no-op, not a restore
                # failure.  Real stop failures are reported by an exception.

            config = self._config
            previous_current = config.get("current_wallpaper")
            try:
                current = normalize_wallpaper_path(self._backend.get_current())
            except Exception:
                current = ""

            try:
                self._restore_and_verify_target(
                    target,
                    style,
                    already_current=self.same_path(current, target),
                )
                config["current_wallpaper"] = target
                if not bool(self._persist_config()):
                    raise RuntimeError("配置保存失败")
            except Exception as exc:
                if previous_current is None:
                    config.pop("current_wallpaper", None)
                else:
                    config["current_wallpaper"] = previous_current
                # Keep the session record. A later shutdown/restart can retry the
                # config commit even when the OS wallpaper is already restored.
                self._log(f"恢复启动前壁纸失败: {exc}")
                return False

            # A manual restore is intentionally idempotent. Keep the immutable
            # session anchor so a second click can refresh the same wallpaper and
            # normal application exit can still restore it after later changes.
            # Only the final shutdown transaction consumes the persisted anchor.
            if finalize:
                self.clear_files()
                self._state.clear()
            if self.same_path(current, target):
                self._log("启动前壁纸路径未变化，已强制刷新桌面绘制: " + os.path.basename(target))
            else:
                self._log("已恢复启动前壁纸: " + os.path.basename(target))
            return True

    def _restore_and_verify_target(
        self,
        target: str,
        style: dict[str, Any],
        *,
        already_current: bool,
    ) -> None:
        """Apply the startup wallpaper and survive delayed desktop-shell repaint.

        Dynamic wallpaper windows are terminated before this method runs, but
        Explorer/Finder/X11 desktop components may repaint their previous static
        background a fraction of a second later.  The old implementation cleared
        the session anchor immediately after one successful ``set_wallpaper``
        call, so that delayed repaint could leave the slideshow image behind with
        no restore record left to retry.

        Query failures or an empty backend result are treated as unverifiable,
        not as a failed restore.  A concrete non-matching path is retried.
        """
        # A backend path match does not prove that Explorer/Finder is currently
        # painting that bitmap. Dynamic wallpaper windows can be removed while
        # the OS API still reports the old static path, leaving a black WorkerW.
        # Therefore restoration is deliberately idempotent and always reapplies.
        self._restore_style(style)
        self._backend.set_wallpaper(target)
        self._refresh_shell_quietly()

        for delay in self._verify_delays:
            if delay:
                self._sleep(delay)
            try:
                observed = normalize_wallpaper_path(self._backend.get_current())
            except Exception:
                return
            if not observed:
                return
            if self.same_path(observed, target):
                # A first matching observation is not enough: desktop shells
                # can repaint again later. Keep checking the full stability
                # window before clearing the crash-recovery anchor.
                continue
            self._log(
                "检测到动态壁纸退出后的桌面重绘覆盖了恢复结果，正在重试: "
                + observed
            )
            self._restore_style(style)
            self._backend.set_wallpaper(target)
            self._refresh_shell_quietly()

        try:
            observed = normalize_wallpaper_path(self._backend.get_current())
        except Exception:
            return
        if observed and not self.same_path(observed, target):
            raise RuntimeError(f"系统壁纸恢复后仍被覆盖: {observed}")

    def _refresh_shell_quietly(self) -> None:
        if self._refresh_shell is None:
            return
        try:
            self._refresh_shell()
        except Exception:
            pass

    def has_restore_candidate(self) -> bool:
        """Return whether memory or a persisted session file may be restorable."""
        with self._state.lock:
            if self._state.snapshot().wallpaper:
                return True
        return any(os.path.isfile(path) for path in self.files())

    def is_restorable(self, path: str | None) -> bool:
        if not path:
            return False
        try:
            normalized = normalize_wallpaper_path(path)
            return bool(
                normalized
                and os.path.isfile(normalized)
                and (not self._image_extensions or normalized.lower().endswith(self._image_extensions))
            )
        except Exception:
            return False

    @staticmethod
    def same_path(left: str | None, right: str | None) -> bool:
        try:
            return bool(
                left
                and right
                and os.path.normcase(os.path.abspath(left))
                == os.path.normcase(os.path.abspath(right))
            )
        except Exception:
            return False

    @staticmethod
    def _read_payload(path: str) -> dict[str, Any]:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("session wallpaper payload must be a JSON object")
        return data

    @staticmethod
    def _remove_quietly(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass
