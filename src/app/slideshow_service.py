"""Generation-safe slideshow application service."""
from __future__ import annotations

from collections.abc import Callable, MutableMapping, Sequence
import os
import random
from threading import RLock, Timer
from typing import Any, Protocol

from app.runtime_state import SlideshowState
from app.wallpaper_repositories import normalize_wallpaper_path, wallpaper_path_key


class TimerHandle(Protocol):
    daemon: bool

    def start(self) -> None: ...

    def cancel(self) -> None: ...


class SlideshowService:
    """Own slideshow start/stop/reset, generations and timer rollback."""

    def __init__(
        self,
        *,
        state: SlideshowState,
        config: Callable[[], MutableMapping[str, Any]],
        operation_lock: RLock,
        image_source: Callable[[str], Sequence[str]],
        apply_wallpaper: Callable[[str, str], bool],
        weighted_choice: Callable[[str, str], str | None] | None = None,
        stop_dynamic: Callable[[], bool] | None = None,
        is_cancelled: Callable[[], bool] = lambda: False,
        normalize_mode: Callable[[str], str] = lambda value: value,
        timer_factory: Callable[[float, Callable[..., Any], tuple[Any, ...]], TimerHandle] | None = None,
        log: Callable[[str], None] = lambda _message: None,
        cancel_timer: Callable[[Any], None] | None = None,
        shuffle: Callable[[list[str]], None] = random.shuffle,
    ) -> None:
        self._state = state
        self._config_provider = config
        self._lock = operation_lock
        self._image_source = image_source
        self._apply_wallpaper = apply_wallpaper
        self._weighted_choice = weighted_choice
        self._stop_dynamic = stop_dynamic
        self._is_cancelled = is_cancelled
        self._normalize_mode = normalize_mode
        self._timer_factory = timer_factory or self._default_timer_factory
        self._log = log
        self._cancel_timer_callback = cancel_timer
        self._shuffle = shuffle

    @property
    def _config(self) -> MutableMapping[str, Any]:
        config = self._config_provider()
        if not isinstance(config, MutableMapping):
            raise TypeError("config provider must return a mutable mapping")
        return config

    @staticmethod
    def _default_timer_factory(
        delay: float,
        callback: Callable[..., Any],
        args: tuple[Any, ...],
    ) -> Timer:
        timer = Timer(delay, callback, args=args)
        timer.daemon = True
        return timer

    def start(self, *, is_startup: bool = False) -> bool:
        with self._lock:
            config = self._config
            if self._is_cancelled():
                self._log("幻灯片启动已终止")
                return False
            if self._stop_dynamic is not None:
                try:
                    self._stop_dynamic()
                except Exception as exc:
                    self._log("停止动态壁纸失败: " + str(exc))
            if self._normalize_mode(str(config.get("mode", ""))) != "幻灯片放映":
                return False
            folder = normalize_wallpaper_path(config.get("slide_folder", ""))
            if not folder or not os.path.isdir(folder):
                return False
            try:
                images = [normalize_wallpaper_path(path) for path in self._image_source(folder)]
            except Exception as exc:
                self._log("加载幻灯片图片失败: " + str(exc))
                return False
            images = [path for path in images if path]
            if not images:
                return False
            if bool(config.get("shuffle", False)):
                self._shuffle(images)

            previous_timer, generation, slideshow_images = self._state.start(images)
            self._cancel(previous_timer)
            self._log(f"加载 {len(slideshow_images)} 张图片")
            anchor = (
                self.find(config.get("slideshow_last_wallpaper", ""), slideshow_images)
                or self.find(config.get("current_wallpaper", ""), slideshow_images)
            )
            target = slideshow_images[0] if not anchor and not is_startup else anchor
            if target and self._state.is_active(generation) and not self._is_cancelled():
                operation = "幻灯片恢复" if anchor else "幻灯片启动"
                if not self._apply_wallpaper(target, operation):
                    self._rollback_start("幻灯片启动失败：初始壁纸应用失败")
                    return False
            if not self._state.is_active(generation):
                return True
            if not self._schedule(generation, config):
                self._rollback_start("幻灯片启动失败：无法创建定时器")
                return False
            self._log(f"幻灯片启动，间隔 {config.get('slide_seconds', 300)} 秒")
            return True

    def stop(self, *, stop_dynamic: bool = True) -> bool:
        with self._lock:
            if stop_dynamic and self._stop_dynamic is not None:
                try:
                    self._stop_dynamic()
                except Exception as exc:
                    self._log("停止动态壁纸失败: " + str(exc))
            timer, was_running = self._state.stop()
            self._cancel(timer)
            if was_running:
                self._log("幻灯片已停止")
            return True

    def restart(self) -> bool:
        with self._lock:
            self.stop(stop_dynamic=False)
            config = self._config
            if self._is_cancelled():
                self._log("幻灯片重启已终止")
                return False
            if (
                self._normalize_mode(str(config.get("mode", ""))) == "幻灯片放映"
                and config.get("slide_folder")
            ):
                return self.start()
            self._log("当前不是幻灯片放映模式或未设置文件夹，跳过重启")
            return False

    def reset(self) -> bool:
        with self._lock:
            config = self._config
            snapshot = self._state.snapshot()
            if not snapshot.enabled or not snapshot.images:
                return False
            current = (
                self.find(config.get("current_wallpaper", ""), snapshot.images)
                or self.find(config.get("slideshow_last_wallpaper", ""), snapshot.images)
            )
            if current not in snapshot.images:
                return False
            renewed = self._state.renew_timer_generation()
            if renewed is None:
                return False
            previous_timer, generation = renewed
            self._cancel(previous_timer)
            if self._schedule(generation, config):
                return True
            timer, _ = self._state.stop()
            self._cancel(timer)
            self._log("幻灯片已停止：无法重置定时器")
            return False

    def advance(self, generation: int | None = None) -> bool:
        with self._lock:
            config = self._config
            if self._is_cancelled():
                self._log("幻灯片切换已终止")
                return False
            if generation is None:
                generation = self._state.snapshot().generation
            images = self._state.timer_fired(generation)
            if not images:
                return False
            if bool(config.get("shuffle", False)) and self._weighted_choice is not None:
                folder = normalize_wallpaper_path(config.get("slide_folder", ""))
                current = str(config.get("current_wallpaper", ""))
                next_image = (
                    self._weighted_choice(folder, current)
                    if folder and os.path.isdir(folder)
                    else None
                ) or self.next_image(images, config)
            else:
                next_image = self.next_image(images, config)
            if not next_image or not self._state.is_active(generation) or self._is_cancelled():
                return False
            success = bool(self._apply_wallpaper(next_image, "幻灯片"))
            if self._state.is_active(generation) and not self._is_cancelled():
                if not self._schedule(generation, config):
                    timer, _ = self._state.stop()
                    self._cancel(timer)
                    self._log("幻灯片已停止：无法重新安排定时器")
            return success

    def replace_images(self, images: Sequence[str]) -> tuple[str, ...]:
        with self._lock:
            return self._state.replace_images(
                [normalize_wallpaper_path(path) for path in images if path]
            )

    @staticmethod
    def find(path: object, images: Sequence[str]) -> str:
        identity = wallpaper_path_key(path)
        if not identity:
            return ""
        for image in images:
            if wallpaper_path_key(image) == identity:
                return image
        return ""

    @staticmethod
    def next_image(
        images: Sequence[str],
        config: MutableMapping[str, Any],
    ) -> str | None:
        if not images:
            return None
        current_key = wallpaper_path_key(config.get("current_wallpaper", ""))
        last_key = wallpaper_path_key(config.get("slideshow_last_wallpaper", ""))
        for index, image in enumerate(images):
            identity = wallpaper_path_key(image)
            if identity and identity in {current_key, last_key}:
                return images[(index + 1) % len(images)]
        return images[0]

    def _schedule(self, generation: int, config: MutableMapping[str, Any]) -> bool:
        try:
            delay = max(5, int(config.get("slide_seconds", 300)))
        except (TypeError, ValueError):
            delay = 300
        try:
            timer = self._timer_factory(delay, self.advance, (generation,))
            timer.daemon = True
        except Exception as exc:
            self._log("创建幻灯片定时器失败: " + str(exc))
            return False
        if not self._state.attach_timer(timer, generation):
            return False
        try:
            timer.start()
        except Exception as exc:
            self._state.discard_timer(timer, generation)
            self._log("启动幻灯片定时器失败: " + str(exc))
            return False
        return True

    def _rollback_start(self, message: str) -> None:
        timer, _ = self._state.stop()
        self._cancel(timer)
        self._log(message)

    def _cancel(self, timer: Any) -> None:
        if timer is None:
            return
        try:
            if self._cancel_timer_callback is not None:
                self._cancel_timer_callback(timer)
            else:
                timer.cancel()
        except Exception as exc:
            self._log("取消幻灯片定时器失败: " + str(exc))
