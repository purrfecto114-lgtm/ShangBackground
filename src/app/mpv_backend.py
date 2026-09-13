"""Stable mpv lifecycle/control abstraction.

Platform modules in ``platform_adapters.backends.*.video`` intentionally keep
legacy function entry points for compatibility with existing plugins and CLI
helpers.  ``LegacyModuleMpvBackend`` adapts those functions to one explicit
contract so application services do not need to know whether playback uses
ctypes/libmpv, an external mpv process, mpvpaper, or a native platform player.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from types import ModuleType
from typing import Any

from app.ports import BackendResult

PropertyObserver = Callable[[str, Any], None]

# 轮询观察器的默认间隔：兼顾进度上报的实时性与 IPC 连接开销。
_POLL_INTERVAL_SECONDS = 0.2
_UNSET = object()


class PollingPropertyObserver:
    """后台线程轮询 getter 的属性观察器（平台无 observe 能力时的兜底）。

    - 仅在值变化时触发回调；首轮也会同步一次当前值（含 None），便于订阅方
      立即拿到初始状态。
    - 线程安全：add/remove/start/stop 可在任意线程调用；回调列表每轮快照，
      并以代际（generation）失效检查保证：已移除的回调不会被过期快照触发，
      stop() 后旧线程立即退出。
    - 后台线程为 daemon，stop() 会 join（有界等待），进程退出不被阻塞。
    """

    def __init__(self, getter: Callable[[], Any], *, interval: float = _POLL_INTERVAL_SECONDS) -> None:
        self._getter = getter
        self._interval = min(max(float(interval), 0.02), 5.0)
        self._lock = threading.RLock()
        self._callbacks: list[Callable[[Any], None]] = []
        self._callbacks_version = 0
        self._generation = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_value: Any = _UNSET

    def add(self, callback: Callable[[Any], None]) -> bool:
        """注册一个值回调（重复注册幂等）。"""
        if not callable(callback):
            return False
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)
            self._callbacks_version += 1
        return True

    def remove(self, callback: Callable[[Any], None]) -> bool:
        """移除回调；返回是否存在并成功移除。"""
        with self._lock:
            if callback not in self._callbacks:
                return False
            self._callbacks.remove(callback)
            self._callbacks_version += 1
            return True

    def start(self, callback: Callable[[Any], None] | None = None) -> bool:
        """启动后台轮询线程；可选同时注册首个回调。已运行时幂等返回 True。"""
        if callback is not None and not self.add(callback):
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._generation += 1
            self._last_value = _UNSET
            self._stop_event.clear()
            thread = threading.Thread(
                target=self._run,
                args=(self._generation,),
                name="mpv-property-poll",
                daemon=True,
            )
            self._thread = thread
        thread.start()
        return True

    def stop(self) -> None:
        """停止轮询并 join 后台线程（可安全重复调用）。"""
        with self._lock:
            self._generation += 1
            self._stop_event.set()
            thread = self._thread
            self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def _run(self, generation: int) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                if generation != self._generation:
                    return  # 代际失效：本线程已被 stop()/重启取代。
                version = self._callbacks_version
                snapshot = tuple(self._callbacks)
            try:
                value = self._getter()
            except Exception:
                value = None
            deliver: tuple[Callable[[Any], None], ...] = ()
            with self._lock:
                if (
                    generation == self._generation
                    and version == self._callbacks_version
                    and value != self._last_value
                ):
                    self._last_value = value
                    deliver = snapshot
            for callback in deliver:
                try:
                    callback(value)
                except Exception:
                    pass
            self._stop_event.wait(self._interval)


class MpvBackend(ABC):
    """Unified lifecycle and control surface for video-wallpaper players."""

    @abstractmethod
    def start(
        self,
        target: str,
        *,
        muted: bool = True,
        volume: int = 100,
    ) -> BackendResult:
        """Start playback and return a normalized result."""

    @abstractmethod
    def pause(self, paused: bool) -> bool:
        """Pause or resume the active player without restarting it."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the active video wallpaper."""

    @abstractmethod
    def is_running(self) -> bool:
        """Return whether the backend owns a live player."""

    @abstractmethod
    def set_property(self, name: str, value: Any) -> bool:
        """Set a normalized runtime property such as ``volume`` or ``pause``."""

    @abstractmethod
    def observe_property(self, name: str, callback: PropertyObserver) -> bool:
        """Subscribe to a player property when supported by the backend."""

    @abstractmethod
    def ipc(self, command: Sequence[Any] | Mapping[str, Any]) -> bool:
        """Send a raw backend command when a compatible IPC channel is exposed."""

    @abstractmethod
    def last_target(self) -> str:
        """Return the most recently started video target, if known."""


class LegacyModuleMpvBackend(MpvBackend):
    """Adapt the project's existing module-level video API to :class:`MpvBackend`.

    Unsupported optional operations deliberately return ``False`` instead of
    raising.  This keeps Windows WorkerW, Linux mpvpaper/xwinwrap, and macOS
    AVPlayer paths behavior-compatible while giving callers one capability
    boundary.
    """

    def __init__(self, module: ModuleType) -> None:
        self._module = module
        self._poll_lock = threading.Lock()
        self._poll_observers: dict[str, PollingPropertyObserver] = {}

    @property
    def module(self) -> ModuleType:
        return self._module

    def start(
        self,
        target: str,
        *,
        muted: bool = True,
        volume: int = 100,
    ) -> BackendResult:
        raw = self._module.start_video_wallpaper(
            target,
            muted=bool(muted),
            volume=max(0, min(100, int(volume))),
        )
        return _backend_result(raw)

    def pause(self, paused: bool) -> bool:
        setter = getattr(self._module, "set_video_paused", None)
        return bool(setter and setter(bool(paused)))

    def stop(self) -> None:
        self._module.stop_video_wallpaper()

    def is_running(self) -> bool:
        return bool(self._module.is_video_wallpaper_running())

    def set_property(self, name: str, value: Any) -> bool:
        normalized = str(name or "").strip().lower().replace("-", "_")
        if normalized in {"pause", "paused"}:
            return self.pause(bool(value))
        if normalized in {"volume", "audio"}:
            setter = getattr(self._module, "set_video_volume", None)
            if setter is None:
                return False
            if isinstance(value, (tuple, list)) and len(value) == 2:
                muted, volume = value
            elif isinstance(value, Mapping):
                muted = bool(value.get("muted", False))
                volume = value.get("volume", 100)
            else:
                muted = False
                volume = value
            return bool(setter(bool(muted), max(0, min(100, int(volume)))))
        generic = getattr(self._module, "set_video_property", None)
        return bool(generic and generic(normalized, value))

    def observe_property(self, name: str, callback: PropertyObserver) -> bool:
        """订阅属性：平台实现优先，否则退化为轮询 get_video_property。

        - 平台模块提供 ``observe_video_property`` 时直接委托（真实现）。
        - 仅有 ``get_video_property`` 时，用 :class:`PollingPropertyObserver`
          小间隔轮询并在值变化时回调（同名重复订阅会替换旧观察者）。
        - 两者都缺失时返回 False（能力探测不误报）。
        """
        normalized = str(name or "").strip()
        if not normalized or not callable(callback):
            return False
        observer = getattr(self._module, "observe_video_property", None)
        if observer is not None:
            try:
                return bool(observer(normalized, callback))
            except Exception:
                return False
        getter = getattr(self._module, "get_video_property", None)
        if getter is None:
            return False
        return self._start_polling_fallback(normalized, callback, getter)

    def _start_polling_fallback(self, name: str, callback: PropertyObserver, getter) -> bool:
        """用轮询观察器兜底订阅；观察者随 backend 生存，替换同名旧订阅。"""
        with self._poll_lock:
            previous = self._poll_observers.pop(name, None)
            if previous is not None:
                previous.stop()
            observer = PollingPropertyObserver(lambda: self._poll_value(getter, name))
            self._poll_observers[name] = observer
        if not observer.start(lambda value: callback(name, value)):
            with self._poll_lock:
                if self._poll_observers.get(name) is observer:
                    del self._poll_observers[name]
            return False
        return True

    @staticmethod
    def _poll_value(getter, name: str) -> Any:
        try:
            ok, value = getter(name)
        except Exception:
            return None
        return value if ok else None

    def ipc(self, command: Sequence[Any] | Mapping[str, Any]) -> bool:
        sender = getattr(self._module, "send_video_ipc", None)
        return bool(sender and sender(command))

    def last_target(self) -> str:
        getter = getattr(self._module, "get_last_path", None)
        return str(getter() or "") if getter else ""


def _backend_result(raw: Any) -> BackendResult:
    if isinstance(raw, BackendResult):
        return raw
    if isinstance(raw, tuple):
        ok = bool(raw[0]) if raw else False
        message = str(raw[1]) if len(raw) > 1 else ""
        return BackendResult(ok, message)
    return BackendResult(bool(raw), "")
