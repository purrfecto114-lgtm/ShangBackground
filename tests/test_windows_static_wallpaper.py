from __future__ import annotations

import ctypes
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from platform_adapters.backends.windows import integration


@pytest.fixture(autouse=True)
def reset_shell_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(integration, "_observed_progman_hwnd", 0)
    monkeypatch.setattr(integration, "_primed_progman_hwnd", 0)
    monkeypatch.setattr(integration, "_last_position_mode", None)


class _FakeComObject:
    def __init__(self) -> None:
        self.wallpapers: list[str] = []
        self.positions: list[int] = []
        self.release_count = 0

        callback = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
        self._release_callback = callback(ctypes.c_uint32, ctypes.c_void_p)(self._release)
        self._set_wallpaper_callback = callback(
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
        )(self._set_wallpaper)
        self._set_position_callback = callback(
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.c_uint32,
        )(self._set_position)

        self._vtable = (ctypes.c_void_p * 11)()
        self._vtable[integration._IDW_RELEASE_INDEX] = ctypes.cast(self._release_callback, ctypes.c_void_p).value
        self._vtable[integration._IDW_SET_WALLPAPER_INDEX] = ctypes.cast(
            self._set_wallpaper_callback, ctypes.c_void_p
        ).value
        self._vtable[integration._IDW_SET_POSITION_INDEX] = ctypes.cast(
            self._set_position_callback, ctypes.c_void_p
        ).value
        self._instance = (ctypes.POINTER(ctypes.c_void_p) * 1)()
        self._instance[0] = ctypes.cast(self._vtable, ctypes.POINTER(ctypes.c_void_p))
        self.pointer = ctypes.cast(self._instance, ctypes.c_void_p)

    def _release(self, _this) -> int:
        self.release_count += 1
        return 0

    def _set_wallpaper(self, _this, _monitor, path) -> int:
        self.wallpapers.append(str(path))
        return 0

    def _set_position(self, _this, position) -> int:
        self.positions.append(int(position))
        return 0


class _FakeOle32:
    def __init__(self, com_object: _FakeComObject, *, init_result: int = 0, create_result: int = 0) -> None:
        self.com_object = com_object
        self.init_result = init_result
        self.create_result = create_result
        self.initialize_count = 0
        self.uninitialize_count = 0
        self.create_count = 0
        self.free_count = 0

    def CoInitializeEx(self, _reserved, _flags):
        self.initialize_count += 1
        return self.init_result

    def CoCreateInstance(self, _clsid, _outer, _context, _iid, output):
        self.create_count += 1
        if self.create_result >= 0:
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = self.com_object.pointer
        return self.create_result

    def CoUninitialize(self):
        self.uninitialize_count += 1

    def CoTaskMemFree(self, _pointer):
        self.free_count += 1


def test_com_session_initializes_creates_calls_releases_and_balances(monkeypatch: pytest.MonkeyPatch):
    com_object = _FakeComObject()
    ole32 = _FakeOle32(com_object)
    monkeypatch.setattr(integration, "_load_windows_dll", lambda name: ole32 if name == "ole32" else None)

    with integration._open_idesktop_wallpaper() as wallpaper:
        assert wallpaper is not None
        assert wallpaper.set_wallpaper(r"C:\wallpapers\one.jpg") == 0
        assert wallpaper.set_position(integration.DWPOS_FILL) == 0

    assert com_object.wallpapers == [r"C:\wallpapers\one.jpg"]
    assert com_object.positions == [integration.DWPOS_FILL]
    assert com_object.release_count == 1
    assert ole32.initialize_count == 1
    assert ole32.create_count == 1
    assert ole32.uninitialize_count == 1


def test_changed_mode_uses_existing_apartment_without_uninitializing(monkeypatch: pytest.MonkeyPatch):
    com_object = _FakeComObject()
    ole32 = _FakeOle32(com_object, init_result=integration._RPC_E_CHANGED_MODE)
    monkeypatch.setattr(integration, "_load_windows_dll", lambda _name: ole32)

    with integration._open_idesktop_wallpaper() as wallpaper:
        assert wallpaper is not None

    assert ole32.create_count == 1
    assert ole32.uninitialize_count == 0
    assert com_object.release_count == 1


def test_failed_com_creation_returns_none_and_balances_apartment(monkeypatch: pytest.MonkeyPatch):
    com_object = _FakeComObject()
    ole32 = _FakeOle32(com_object, create_result=-2147467259)
    monkeypatch.setattr(integration, "_load_windows_dll", lambda _name: ole32)

    with integration._open_idesktop_wallpaper() as wallpaper:
        assert wallpaper is None

    assert ole32.uninitialize_count == 1
    assert com_object.release_count == 0


class _FakeUser32:
    def __init__(self, hwnd: int = 101, *, send_result: int = 1) -> None:
        self.hwnd = hwnd
        self.send_result = send_result
        self.send_count = 0
        self.messages: list[int] = []

    def FindWindowW(self, class_name, _title):
        assert class_name == "Progman"
        return self.hwnd

    def SendMessageTimeoutW(self, _hwnd, message, *_args):
        self.send_count += 1
        self.messages.append(int(message))
        return self.send_result


def test_explorer_host_is_primed_once_per_progman_window(monkeypatch: pytest.MonkeyPatch):
    user32 = _FakeUser32(hwnd=101)
    monkeypatch.setattr(integration, "_load_windows_dll", lambda name: user32 if name == "user32" else None)

    assert integration._prime_explorer_wallpaper_host() is True
    assert integration._prime_explorer_wallpaper_host() is True
    assert user32.send_count == 1
    assert user32.messages == [integration._PROGMAN_SPAWN_WORKERW]

    integration._last_position_mode = "填充"
    user32.hwnd = 202
    assert integration._prime_explorer_wallpaper_host() is True
    assert user32.send_count == 2
    assert integration._last_position_mode is None


def test_failed_explorer_prime_is_retried(monkeypatch: pytest.MonkeyPatch):
    user32 = _FakeUser32(hwnd=101, send_result=0)
    monkeypatch.setattr(integration, "_load_windows_dll", lambda _name: user32)

    assert integration._prime_explorer_wallpaper_host() is False
    assert integration._prime_explorer_wallpaper_host() is False
    assert user32.send_count == 2


@pytest.mark.parametrize(
    ("animate", "com_result", "expected"),
    [
        (True, True, ["com"]),
        (True, False, ["com", "legacy"]),
        (False, True, ["legacy"]),
    ],
)
def test_static_apply_has_deterministic_transition_and_fallback_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    animate: bool,
    com_result: bool,
    expected: list[str],
):
    image = tmp_path / "wallpaper.jpg"
    image.write_bytes(b"image")
    calls: list[str] = []
    monkeypatch.setattr(
        integration,
        "_set_wallpaper_via_com",
        lambda _path: calls.append("com") or com_result,
    )
    monkeypatch.setattr(
        integration,
        "_set_windows_wallpaper_legacy",
        lambda _path: calls.append("legacy"),
    )

    integration._set_windows_wallpaper(str(image), animate=animate)

    assert calls == expected



def test_static_apply_never_touches_workerw_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    image = tmp_path / "wallpaper.jpg"
    image.write_bytes(b"image")
    monkeypatch.setattr(
        integration,
        "_prime_explorer_wallpaper_host",
        lambda: pytest.fail("static wallpaper path must not send Progman/WorkerW messages"),
    )
    monkeypatch.setattr(integration, "_set_wallpaper_via_com", lambda _path: True)

    integration._set_windows_wallpaper(str(image), animate=True)


def test_com_activation_allows_inproc_or_local_shell_server(monkeypatch: pytest.MonkeyPatch):
    com_object = _FakeComObject()
    contexts: list[int] = []

    class RecordingOle32(_FakeOle32):
        def CoCreateInstance(self, _clsid, _outer, context, _iid, output):
            contexts.append(int(context))
            return super().CoCreateInstance(_clsid, _outer, context, _iid, output)

    ole32 = RecordingOle32(com_object)
    monkeypatch.setattr(integration, "_load_windows_dll", lambda _name: ole32)

    with integration._open_idesktop_wallpaper() as wallpaper:
        assert wallpaper is not None

    assert contexts == [integration._CLSCTX_SERVER]

def test_position_cache_is_invalidated_after_explorer_restart(monkeypatch: pytest.MonkeyPatch):
    handles = iter((101, 202))
    monkeypatch.setattr(integration, "_find_progman_hwnd", lambda: next(handles))
    integration._last_position_mode = "填充"
    integration._observed_progman_hwnd = 101

    assert integration._sync_explorer_generation() == 101
    assert integration._last_position_mode == "填充"
    assert integration._sync_explorer_generation() == 202
    assert integration._last_position_mode is None


@contextmanager
def _fake_wallpaper_session(fake):
    yield fake


def test_position_is_only_cached_after_success(monkeypatch: pytest.MonkeyPatch):
    fake = SimpleNamespace(set_position=lambda _position: -1)
    monkeypatch.setattr(integration, "_sync_explorer_generation", lambda: 101)
    monkeypatch.setattr(integration, "_open_idesktop_wallpaper", lambda: _fake_wallpaper_session(fake))

    assert integration._set_position_via_com("填充") is False
    assert integration._last_position_mode is None

    fake.set_position = lambda _position: 0
    assert integration._set_position_via_com("填充") is True
    assert integration._last_position_mode == "填充"


def test_registry_fit_fallback_always_closes_key_on_write_error(monkeypatch: pytest.MonkeyPatch):
    events: list[str] = []

    class FakeWinreg:
        HKEY_CURRENT_USER = object()
        KEY_WRITE = 1
        REG_SZ = 1

        @staticmethod
        def OpenKey(*_args):
            events.append("open")
            return "key"

        @staticmethod
        def SetValueEx(_key, name, *_args):
            events.append(f"write:{name}")
            raise OSError("simulated registry failure")

        @staticmethod
        def CloseKey(_key):
            events.append("close")

    errors: list[str] = []
    monkeypatch.setattr(integration, "_set_position_via_com", lambda _mode: False)

    integration.configure_fit_mode("填充", FakeWinreg, errors.append)

    assert events == ["open", "write:WallpaperStyle", "close"]
    assert errors and "simulated registry failure" in errors[-1]
