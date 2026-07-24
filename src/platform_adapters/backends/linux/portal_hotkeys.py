"""Wayland global-hotkey registration through XDG Desktop Portal.

The backend deliberately runs its own asyncio loop in a daemon thread because
ShangBackground's application services are synchronous and Qt owns the main
thread.  Portal registration may display a compositor-provided consent dialog;
``start`` therefore reports that the portal session was created while binding
continues asynchronously.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import secrets
from threading import Event, Thread
from typing import Any

from platform_adapters.hotkey_bindings import parse_hotkey

_PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
_PORTAL_OBJECT_PATH = "/org/freedesktop/portal/desktop"
_PORTAL_INTERFACE = "org.freedesktop.portal.GlobalShortcuts"
_REQUEST_INTERFACE = "org.freedesktop.portal.Request"
_SESSION_INTERFACE = "org.freedesktop.portal.Session"

_ACTION_DESCRIPTIONS = {
    "previous": "Previous wallpaper",
    "next": "Next wallpaper",
    "random": "Random wallpaper",
    "jump": "Open wallpaper library",
    "mode": "Switch wallpaper mode",
}


def to_xdg_shortcut(value: str | None) -> str | None:
    """Convert the project's ``Ctrl+Alt+n`` syntax to the XDG shortcut spec."""
    parsed = parse_hotkey(value)
    if parsed is None:
        return None
    modifier_names = {
        "ctrl": "CTRL",
        "alt": "ALT",
        "shift": "SHIFT",
        "super": "LOGO",
        "cmd": "LOGO",
    }
    modifiers = [modifier_names[name] for name in parsed.modifiers]
    key = parsed.key.upper() if parsed.key.startswith("f") else parsed.key
    return "+".join((*modifiers, key))


class PortalGlobalShortcuts:
    """Own one XDG GlobalShortcuts session and dispatch activation signals."""

    def __init__(self, *, startup_timeout: float = 3.0, request_timeout: float = 45.0) -> None:
        self._startup_timeout = max(0.1, float(startup_timeout))
        self._request_timeout = max(self._startup_timeout, float(request_timeout))
        self._thread: Thread | None = None
        self._ready = Event()
        self._stop_requested = Event()
        self._available = False
        self._dispatch: Callable[[str], None] | None = None
        self._bindings: dict[str, str] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bus: Any = None
        self._session_path = ""
        self.last_error = ""

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive() and self._available)

    def start(self, bindings: Mapping[str, str], dispatch: Callable[[str], None]) -> bool:
        """Create a portal session; shortcut consent/binding continues in-thread."""
        self.stop()
        normalized = {
            str(action): trigger
            for action, value in bindings.items()
            if (trigger := to_xdg_shortcut(value))
        }
        if not normalized or not callable(dispatch):
            return False
        self._bindings = normalized
        self._dispatch = dispatch
        self._ready.clear()
        self._stop_requested.clear()
        self._available = False
        self.last_error = ""
        self._thread = Thread(
            target=self._thread_main,
            daemon=True,
            name="ShangBackground-XDG-GlobalShortcuts",
        )
        self._thread.start()
        self._ready.wait(self._startup_timeout)
        return self._available

    def stop(self) -> None:
        self._stop_requested.set()
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(lambda: None)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        self._thread = None
        self._available = False
        self._dispatch = None
        self._bindings = {}

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self.last_error = str(exc)
            self._available = False
            self._ready.set()
        finally:
            self._loop = None
            self._bus = None
            self._session_path = ""

    async def _run(self) -> None:
        try:
            from dbus_next import Variant
            from dbus_next.aio import MessageBus
            from dbus_next.constants import BusType
        except Exception as exc:
            raise RuntimeError("dbus-next is required for Wayland global shortcuts") from exc

        self._loop = asyncio.get_running_loop()
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        self._bus = bus
        try:
            introspection = await bus.introspect(_PORTAL_BUS_NAME, _PORTAL_OBJECT_PATH)
            proxy = bus.get_proxy_object(_PORTAL_BUS_NAME, _PORTAL_OBJECT_PATH, introspection)
            portal = proxy.get_interface(_PORTAL_INTERFACE)
            version = int(await portal.get_version())
            if version < 1:
                raise RuntimeError(f"XDG GlobalShortcuts portal version is unsupported: {version}")

            create_token = _token("create")
            request_path = await portal.call_create_session({
                "handle_token": Variant("s", create_token),
                "session_handle_token": Variant("s", _token("session")),
            })
            response, results = await self._wait_request(bus, str(request_path))
            if response != 0:
                raise RuntimeError(f"GlobalShortcuts CreateSession rejected (response={response})")
            session_variant = results.get("session_handle")
            session_path = str(getattr(session_variant, "value", session_variant) or "")
            if not session_path.startswith("/"):
                raise RuntimeError("GlobalShortcuts portal returned no session handle")
            self._session_path = session_path

            def activated(session_handle: str, shortcut_id: str, _timestamp: int, _options: Any) -> None:
                if str(session_handle) != self._session_path or self._stop_requested.is_set():
                    return
                callback = self._dispatch
                if callback is not None:
                    callback(str(shortcut_id))

            portal.on_activated(activated)
            self._available = True
            self._ready.set()

            shortcuts = [
                (
                    action,
                    {
                        "description": Variant("s", _ACTION_DESCRIPTIONS.get(action, action)),
                        "preferred_trigger": Variant("s", trigger),
                    },
                )
                for action, trigger in self._bindings.items()
            ]
            bind_task = asyncio.create_task(
                self._bind(portal, bus, session_path, shortcuts, Variant)
            )
            stop_task = asyncio.create_task(self._wait_until_stopped())
            done, pending = await asyncio.wait(
                {bind_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if bind_task in done:
                bind_task.result()
                await stop_task
            for task in pending:
                task.cancel()
            for task in pending:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        finally:
            await self._close_session(bus)
            try:
                bus.disconnect()
            except Exception:
                pass
            self._available = False
            self._ready.set()

    async def _bind(self, portal: Any, bus: Any, session_path: str, shortcuts: list[Any], Variant: Any) -> None:
        request_path = await portal.call_bind_shortcuts(
            session_path,
            shortcuts,
            "",
            {"handle_token": Variant("s", _token("bind"))},
        )
        response, results = await self._wait_request(bus, str(request_path))
        if response != 0:
            raise RuntimeError(f"GlobalShortcuts BindShortcuts rejected (response={response})")
        bound_variant = results.get("shortcuts")
        bound = getattr(bound_variant, "value", bound_variant)
        if bound is not None and len(bound) == 0:
            raise RuntimeError("GlobalShortcuts portal accepted no shortcuts")

    async def _wait_request(self, bus: Any, request_path: str) -> tuple[int, dict[str, Any]]:
        introspection = await bus.introspect(_PORTAL_BUS_NAME, request_path)
        proxy = bus.get_proxy_object(_PORTAL_BUS_NAME, request_path, introspection)
        request = proxy.get_interface(_REQUEST_INTERFACE)
        future: asyncio.Future[tuple[int, dict[str, Any]]] = asyncio.get_running_loop().create_future()

        def on_response(response: int, results: dict[str, Any]) -> None:
            if not future.done():
                future.set_result((int(response), dict(results)))

        request.on_response(on_response)
        try:
            return await asyncio.wait_for(future, timeout=self._request_timeout)
        except asyncio.TimeoutError as exc:
            try:
                await request.call_close()
            except Exception:
                pass
            raise RuntimeError(
                f"GlobalShortcuts portal request timed out after {self._request_timeout:g}s"
            ) from exc
        finally:
            try:
                request.off_response(on_response)
            except Exception:
                pass

    async def _wait_until_stopped(self) -> None:
        while not self._stop_requested.is_set():
            await asyncio.sleep(0.2)

    async def _close_session(self, bus: Any) -> None:
        path = self._session_path
        if not path:
            return
        try:
            introspection = await bus.introspect(_PORTAL_BUS_NAME, path)
            proxy = bus.get_proxy_object(_PORTAL_BUS_NAME, path, introspection)
            session = proxy.get_interface(_SESSION_INTERFACE)
            await session.call_close()
        except Exception:
            pass


def _token(prefix: str) -> str:
    return f"shangbackground_{prefix}_{secrets.token_hex(8)}"
