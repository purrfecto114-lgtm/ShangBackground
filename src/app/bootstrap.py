"""Explicit dependency assembly for application services."""
from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
import importlib
import os
import sys
from threading import RLock
from types import ModuleType
from typing import Any

from app.exit_service import ExitService
from app.hotkey_service import HotkeyService
from app.media_service import MediaService
from app.ports import BackendResult, HotkeyBackend, MediaBackend, MediaKind, WallpaperBackend
from app.runtime_state import RuntimeState
from app.session_wallpaper_service import SessionWallpaperService
from app.slideshow_service import SlideshowService
from app.wallpaper_library import WallpaperLibrary
from app.wallpaper_mode_service import ModeActivationResult, WallpaperModeService
from app.wallpaper_service import WallpaperService

SUPPORTED_PLATFORMS = ("windows", "linux", "macos")


def select_platform_id(platform_id: str | None = None) -> str:
    value = str(platform_id or "").strip().lower()
    if not value:
        if sys.platform.startswith("win"):
            value = "windows"
        elif sys.platform == "darwin":
            value = "macos"
        else:
            value = "linux"
    if value not in SUPPORTED_PLATFORMS:
        raise ValueError(f"unsupported platform: {value}")
    return value


class ModuleWallpaperBackend:
    """Adapt a selected platform integration module to WallpaperBackend."""

    def __init__(self, module: ModuleType, *, fit_args: tuple[Any, ...] = ()) -> None:
        self._module = module
        self._fit_args = fit_args

    def get_current(self) -> str:
        return str(self._module.get_current_wallpaper_platform() or "")

    def configure_fit_mode(self, mode: str) -> None:
        self._module.configure_fit_mode(mode, *self._fit_args)

    def set_wallpaper(self, path: str) -> None:
        self._module.set_wallpaper_platform(path)


class ModuleMediaBackend:
    """Adapt selected video/HTML modules to the generic MediaBackend port."""

    def __init__(self, video: ModuleType | None, html: ModuleType | None) -> None:
        self._video = video
        self._html = html

    def _module(self, kind: MediaKind) -> ModuleType:
        module = self._video if kind == "video" else self._html
        if module is None:
            raise RuntimeError(f"{kind} wallpaper backend is unavailable")
        return module

    def validate(self, kind: MediaKind, target: str) -> bool:
        module = self._module(kind)
        validator = getattr(module, f"validate_{kind}_path", None)
        return True if validator is None else bool(validator(target))

    def start(
        self,
        kind: MediaKind,
        target: str,
        *,
        options: Mapping[str, Any] | None = None,
    ) -> BackendResult:
        module = self._module(kind)
        values = dict(options or {})
        if kind == "video":
            raw = module.start_video_wallpaper(
                target,
                muted=bool(values.get("muted", True)),
                volume=int(values.get("volume", 100)),
            )
        else:
            raw = module.start_html_wallpaper(target)
        return _backend_result(raw)

    def stop(self, kind: MediaKind) -> None:
        module = self._module(kind)
        getattr(module, f"stop_{kind}_wallpaper")()

    def is_running(self, kind: MediaKind) -> bool:
        module = self._module(kind)
        return bool(getattr(module, f"is_{kind}_wallpaper_running")())

    def set_option(self, kind: MediaKind, key: str, value: Any) -> bool:
        module = self._module(kind)
        if kind == "video":
            if key == "paused" and hasattr(module, "set_video_paused"):
                return bool(module.set_video_paused(bool(value)))
            if key == "volume" and hasattr(module, "set_video_volume"):
                muted, volume = value
                return bool(module.set_video_volume(bool(muted), int(volume)))
            return False
        setter = getattr(module, "runtime_set_option", None)
        return bool(setter and setter(str(key), value))

    def last_target(self, kind: MediaKind) -> str:
        module = self._module(kind)
        getter = getattr(module, "get_last_path", None)
        return str(getter() or "") if getter else ""

    def restart(
        self,
        kind: MediaKind,
        target: str,
        *,
        options: Mapping[str, Any] | None = None,
    ) -> BackendResult:
        module = self._module(kind)
        if kind == "html" and hasattr(module, "restart_html_wallpaper"):
            return _backend_result(module.restart_html_wallpaper(target))
        self.stop(kind)
        return self.start(kind, target, options=options)




class CallbackWallpaperBackend:
    """Wallpaper port backed by callbacks resolved at call time."""

    def __init__(
        self,
        *,
        get_current: Callable[[], str],
        configure_fit_mode: Callable[[str], None],
        set_wallpaper: Callable[[str], None],
    ) -> None:
        self._get_current = get_current
        self._configure_fit_mode = configure_fit_mode
        self._set_wallpaper = set_wallpaper

    def get_current(self) -> str:
        return str(self._get_current() or "")

    def configure_fit_mode(self, mode: str) -> None:
        self._configure_fit_mode(mode)

    def set_wallpaper(self, path: str) -> None:
        self._set_wallpaper(path)


class ProviderMediaBackend(ModuleMediaBackend):
    """Media adapter whose legacy modules are resolved for every operation."""

    def __init__(
        self,
        video_provider: Callable[[], ModuleType | None],
        html_provider: Callable[[], ModuleType | None],
    ) -> None:
        self._video_provider = video_provider
        self._html_provider = html_provider
        super().__init__(None, None)

    def _module(self, kind: MediaKind) -> ModuleType:
        module = self._video_provider() if kind == "video" else self._html_provider()
        if module is None:
            raise RuntimeError(f"{kind} wallpaper backend is unavailable")
        return module


class CallbackHotkeyBackend:
    """Adapt module/callback hotkey implementations to the stable port."""

    def __init__(
        self,
        refresh: Callable[[Mapping[str, str], Callable[[str], None]], bool],
        stop: Callable[[], None],
    ) -> None:
        self._refresh = refresh
        self._stop = stop

    def refresh(
        self,
        bindings: Mapping[str, str],
        dispatch: Callable[[str], None],
    ) -> bool:
        return bool(self._refresh(bindings, dispatch))

    def stop(self) -> None:
        self._stop()


@dataclass(slots=True)
class ApplicationServices:
    wallpaper: WallpaperService
    slideshow: SlideshowService
    media: MediaService
    session: SessionWallpaperService
    hotkeys: HotkeyService
    modes: WallpaperModeService
    exit: ExitService
    wallpaper_backend: WallpaperBackend
    media_backend: MediaBackend
    hotkey_backend: HotkeyBackend


def load_platform_backends(
    platform_id: str | None = None,
    *,
    importer: Callable[[str], ModuleType] = importlib.import_module,
    fit_args: tuple[Any, ...] = (),
) -> tuple[WallpaperBackend, MediaBackend]:
    selected = select_platform_id(platform_id)
    integration = importer(f"platform_adapters.backends.{selected}.integration")
    video = importer(f"platform_adapters.backends.{selected}.video")
    html = importer(f"platform_adapters.backends.{selected}.html_wallpaper")
    return ModuleWallpaperBackend(integration, fit_args=fit_args), ModuleMediaBackend(video, html)


def load_platform_hotkey_backend(
    platform_id: str | None = None,
    *,
    importer: Callable[[str], ModuleType] = importlib.import_module,
) -> tuple[HotkeyBackend, Callable[[str, str], str]]:
    selected = select_platform_id(platform_id)
    module = importer(f"platform_adapters.backends.{selected}.hotkeys")
    backend = CallbackHotkeyBackend(module.refresh, module.stop)
    guard = getattr(module, "focus_block_reason", lambda _action, _binding: "")
    return backend, guard


def build_services(
    *,
    wallpaper_backend: WallpaperBackend,
    media_backend: MediaBackend,
    hotkey_backend: HotkeyBackend,
    config: Callable[[], MutableMapping[str, Any]],
    persist: Callable[[], bool],
    library: WallpaperLibrary,
    runtime_state: RuntimeState,
    image_source: Callable[[str], list[str]],
    apply_wallpaper: Callable[[str, str], bool],
    hotkey_dispatch: Callable[[str], None],
    hotkey_focus_guard: Callable[[str, str], str] | None,
    session_primary_file: Callable[[], str],
    session_legacy_files: Callable[[], tuple[str, ...]],
    platform_name: Callable[[], str],
    app_base_dir: Callable[[], str],
    session_get_style: Callable[[], dict[str, Any]] = lambda: {},
    session_restore_style: Callable[[dict[str, Any]], None] = lambda _style: None,
    refresh_shell: Callable[[], None] | None = None,
    mode_order: tuple[str, ...] = (),
    apply_solid: Callable[[], bool] = lambda: False,
    apply_gradient: Callable[[], bool] = lambda: False,
    weighted_choice: Callable[[str, str], str | None] | None = None,
    slideshow_update: Callable[[str, MutableMapping[str, Any]], str | None] | None = None,
    preview: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] = lambda: False,
    normalize_mode: Callable[[str], str] = lambda value: value,
    normalize_fit_mode: Callable[[str], str] = lambda value: value,
    log: Callable[[str], None] = lambda _message: None,
    set_error: Callable[[str], None] = lambda _message: None,
    cancel_timer: Callable[[Any], None] | None = None,
    timer_factory: Callable[..., Any] | None = None,
    request_cancel: Callable[[], Any] | None = None,
    close_ipc: Callable[[], Any] | None = None,
    release_single_instance: Callable[[], Any] | None = None,
) -> ApplicationServices:
    operation_lock: RLock = runtime_state.wallpaper_operation_lock
    media_holder: dict[str, MediaService] = {}
    session = SessionWallpaperService(
        state=runtime_state.session_wallpaper,
        backend=wallpaper_backend,
        config=config,
        persist_config=persist,
        primary_file=session_primary_file,
        legacy_files=session_legacy_files,
        platform_name=platform_name,
        app_base_dir=app_base_dir,
        get_style=session_get_style,
        restore_style=session_restore_style,
        stop_dynamic=lambda: media_holder["service"].stop_all(),
        refresh_shell=refresh_shell,
        log=log,
        operation_lock=operation_lock,
    )
    media = MediaService(
        backend=media_backend,
        config=config,
        persist=persist,
        state=runtime_state.dynamic_wallpaper,
        operation_lock=operation_lock,
        capture_session=lambda: session.capture(inherit_existing=False, force_refresh=False),
        log=log,
    )
    media_holder["service"] = media
    wallpaper = WallpaperService(
        backend=wallpaper_backend,
        config=config,
        library=library,
        operation_lock=operation_lock,
        is_cancelled=is_cancelled,
        stop_dynamic=media.stop_all,
        slideshow_update=slideshow_update,
        preview=preview,
        log=log,
        set_error=set_error,
        normalize_fit_mode=normalize_fit_mode,
    )
    slideshow = SlideshowService(
        state=runtime_state.slideshow,
        config=config,
        operation_lock=operation_lock,
        image_source=image_source,
        apply_wallpaper=apply_wallpaper,
        weighted_choice=weighted_choice,
        stop_dynamic=media.stop_all,
        is_cancelled=is_cancelled,
        normalize_mode=normalize_mode,
        timer_factory=timer_factory,
        log=log,
        cancel_timer=cancel_timer,
    )

    def activate_mode(
        mode: str,
        cfg: MutableMapping[str, Any],
    ) -> ModeActivationResult:
        if mode == "幻灯片放映":
            folder = str(cfg.get("slide_folder", "") or "")
            if folder and os.path.isdir(folder):
                return ModeActivationResult(slideshow.restart(), persisted=True)
            slideshow.stop(stop_dynamic=False)
            media.stop_all()
            return ModeActivationResult(True, persisted=False)
        slideshow.stop(stop_dynamic=False)
        if mode == "图片":
            media.stop_all()
            image = str(cfg.get("single_image", "") or "")
            if image and os.path.exists(image):
                return ModeActivationResult(
                    wallpaper.apply(image, "切换单张图片模式"),
                    persisted=True,
                )
            return ModeActivationResult(True, persisted=False)
        if mode == "视频":
            target = str(cfg.get("video_file", "") or "")
            if target and os.path.exists(target):
                return ModeActivationResult(media.start_video(target), persisted=True)
            media.stop_all()
            return ModeActivationResult(True, persisted=False)
        if mode == "HTML":
            media.stop("video")
            target = str(cfg.get("html_file", "") or cfg.get("html_url", "") or "")
            if target:
                return ModeActivationResult(media.start_html(target), persisted=True)
            media.stop("html")
            return ModeActivationResult(True, persisted=False)
        media.stop_all()
        if mode == "纯色":
            return ModeActivationResult(bool(apply_solid()), persisted=True)
        if mode == "渐变":
            return ModeActivationResult(bool(apply_gradient()), persisted=True)
        return ModeActivationResult(True, persisted=False)

    modes = WallpaperModeService(
        config=config,
        persist=persist,
        operation_lock=operation_lock,
        mode_order=mode_order,
        normalize_mode=normalize_mode,
        activate=activate_mode,
        log=log,
    )
    hotkeys = HotkeyService(
        backend=hotkey_backend,
        config=config,
        dispatch=hotkey_dispatch,
        focus_guard=hotkey_focus_guard,
        log=log,
    )
    exit_service = ExitService(
        request_cancel=request_cancel or (lambda: runtime_state.cancellation.request("application exit")),
        stop_slideshow=lambda: slideshow.stop(stop_dynamic=False),
        stop_media=media.stop_all,
        stop_hotkeys=hotkeys.stop,
        # Dynamic backends were already stopped by the preceding step.  Calling
        # SessionWallpaperService with stop_dynamic=True here would interpret a
        # successful no-op False from MediaService.stop_all as a stop failure.
        restore_wallpaper=lambda: session.restore(stop_dynamic=False, finalize=True),
        has_restore_candidate=session.has_restore_candidate,
        close_ipc=close_ipc,
        release_single_instance=release_single_instance,
        log=log,
    )
    return ApplicationServices(
        wallpaper=wallpaper,
        slideshow=slideshow,
        media=media,
        session=session,
        hotkeys=hotkeys,
        modes=modes,
        exit=exit_service,
        wallpaper_backend=wallpaper_backend,
        media_backend=media_backend,
        hotkey_backend=hotkey_backend,
    )


def _backend_result(raw: Any) -> BackendResult:
    if isinstance(raw, BackendResult):
        return raw
    if isinstance(raw, tuple):
        ok = bool(raw[0]) if raw else False
        message = str(raw[1]) if len(raw) > 1 and raw[1] is not None else ""
        return BackendResult(ok, message)
    return BackendResult(bool(raw), "")
