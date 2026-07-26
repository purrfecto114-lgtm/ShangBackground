"""Small direct ``ctypes`` binding for the bundled libmpv runtime.

The video-wallpaper helper intentionally talks to libmpv's stable C client API
instead of importing a third-party Python wrapper.  This removes one packaging
layer, avoids version skew between ``python-mpv`` and libmpv, and keeps the
internal player usable in source, PyInstaller, and Nuitka builds.

A platform-compatible libmpv *and its native dependencies* are still required.
Use ``build_tools/build.py mpv download`` to install a versioned runtime below
``src/bin/mpv/<platform>/<arch>/<runtime-id>/``.  Build backends bundle only the
selected runtime into ``bin/mpv``.  A per-user runtime remains supported.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
from pathlib import Path
import platform
import signal
import sys
from typing import Final

from app.paths import RESOURCE_ROOT, is_packaged_runtime, mpv_user_install_path

MPV_EVENT_NONE: Final = 0
MPV_EVENT_SHUTDOWN: Final = 1

# Keep native search-directory cookies and loaded libraries alive for the
# process lifetime.  On Windows, dropping the object returned by
# os.add_dll_directory() immediately removes that directory from DLL search.
_DLL_DIRECTORY_HANDLES: list[object] = []
_LOADED_LIBRARIES: dict[str, ctypes.CDLL] = {}


class _MpvEvent(ctypes.Structure):
    _fields_ = [
        ("event_id", ctypes.c_int),
        ("error", ctypes.c_int),
        ("reply_userdata", ctypes.c_uint64),
        ("data", ctypes.c_void_p),
    ]


class LibMpvError(RuntimeError):
    """Raised when the libmpv C API cannot be initialized or commanded."""


def _platform_id() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _library_names() -> tuple[str, ...]:
    if sys.platform.startswith("win"):
        return ("libmpv-2.dll", "mpv-2.dll", "libmpv.dll")
    if sys.platform == "darwin":
        return ("libmpv.2.dylib", "libmpv.dylib")
    return ("libmpv.so.2", "libmpv.so.1", "libmpv.so")


def _architecture_id() -> str:
    raw = str(platform.machine() or "").lower().replace("-", "_")
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
        "i386": "x86",
        "i486": "x86",
        "i586": "x86",
        "i686": "x86",
        "x86": "x86",
    }
    return aliases.get(raw, raw or "unknown")


def _source_versioned_roots(bundled: Path) -> tuple[Path, ...]:
    root = bundled / "mpv" / _platform_id() / _architecture_id()
    if not root.is_dir():
        return ()
    result: list[Path] = []
    active = ""
    try:
        active = root.joinpath("ACTIVE").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        pass
    if active:
        candidate = root / active
        if candidate.is_dir():
            result.append(candidate)
    for candidate in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if candidate.is_dir() and candidate not in result:
            result.append(candidate)
    return tuple(result)


def _external_runtime_allowed() -> bool:
    if not is_packaged_runtime():
        return True
    if os.environ.get("SHANGBACKGROUND_ALLOW_EXTERNAL_MPV") == "1":
        return True
    try:
        from app.build_features import video_runtime_mode
        return video_runtime_mode() == "system"
    except Exception:
        return False


def _candidate_roots() -> tuple[Path, ...]:
    bundled = Path(RESOURCE_ROOT) / "bin"
    executable_dir = Path(sys.executable).absolute().parent
    roots: list[Path] = [
        bundled / "mpv",
        *_source_versioned_roots(bundled),
        bundled / _platform_id(),
        bundled,
        executable_dir / "bin" / "mpv",
        executable_dir / "bin",
    ]
    if _external_runtime_allowed():
        user = mpv_user_install_path(create=False)
        roots.extend((user, user / _platform_id()))
    result: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            key = os.path.normcase(os.path.abspath(os.fspath(root)))
        except Exception:
            continue
        if key not in seen:
            seen.add(key)
            result.append(root)
    return tuple(result)


def candidate_library_paths() -> tuple[Path, ...]:
    """Return libmpv candidates with bundled files first.

    Packaged builds ignore environment/user overrides unless the manifest chose
    system mode or the user explicitly opted in. This avoids loading an
    unintended DLL before the verified bundled runtime.
    """
    result: list[Path] = []
    seen: set[str] = set()
    for root in _candidate_roots():
        for name in _library_names():
            candidate = root / name
            try:
                key = os.path.normcase(os.path.abspath(os.fspath(candidate)))
            except Exception:
                continue
            if key not in seen and candidate.is_file():
                seen.add(key)
                result.append(candidate)
    override = str(os.environ.get("SHANGBACKGROUND_LIBMPV", "") or "").strip()
    if override and _external_runtime_allowed():
        candidate = Path(os.path.expandvars(os.path.expanduser(override)))
        if candidate.is_file():
            key = os.path.normcase(os.path.abspath(os.fspath(candidate)))
            if key not in seen:
                result.append(candidate)
    return tuple(result)

def python_mpv_available() -> bool:
    """Compatibility helper retained for older plugins.

    The application no longer requires or imports ``python-mpv``.  Returning
    whether it happens to be installed keeps the historical diagnostic API
    harmless without making it part of runtime availability.
    """
    try:
        import importlib.util

        return importlib.util.find_spec("mpv") is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def resolve_libmpv_path() -> str | None:
    candidates = candidate_library_paths()
    if candidates:
        return os.fspath(candidates[0])
    if _external_runtime_allowed():
        for name in ("mpv", "libmpv"):
            try:
                found = ctypes.util.find_library(name)
            except Exception:
                found = None
            if found:
                return str(found)
    return None


def runtime_available() -> bool:
    """Return whether a libmpv candidate is discoverable.

    Loading is deferred to the dedicated player subprocess so a broken native
    runtime cannot pollute or crash the main GUI process.  The platform backend
    waits for mpv's IPC endpoint before accepting the child as ready.

    v1.4.4: In system mode, the internal libmpv player is not used (the
    external mpv.exe handles playback). However, we still return True if
    libmpv is discoverable so diagnostics can report its presence. The
    actual gating happens in _internal_libmpv_command() which checks
    video_runtime_mode() before spawning the internal player.
    """
    return bool(resolve_libmpv_path())


def _prepare_native_search_path(path: Path) -> None:
    parent = os.fspath(path.parent)
    if sys.platform.startswith("win"):
        # Preserve existing PATH ordering while making sibling FFmpeg/runtime
        # DLLs discoverable by libmpv.
        path_entries = os.environ.get("PATH", "").split(os.pathsep)
        if parent not in path_entries:
            os.environ["PATH"] = parent + os.pathsep + os.environ.get("PATH", "")
        add_dll_directory = getattr(os, "add_dll_directory", None)
        if add_dll_directory is not None:
            try:
                handle = add_dll_directory(parent)
            except OSError:
                handle = None
            if handle is not None:
                _DLL_DIRECTORY_HANDLES.append(handle)


def _configure_api(library: ctypes.CDLL) -> None:
    library.mpv_client_api_version.argtypes = []
    library.mpv_client_api_version.restype = ctypes.c_ulong
    library.mpv_create.argtypes = []
    library.mpv_create.restype = ctypes.c_void_p
    library.mpv_initialize.argtypes = [ctypes.c_void_p]
    library.mpv_initialize.restype = ctypes.c_int
    library.mpv_set_option_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    library.mpv_set_option_string.restype = ctypes.c_int
    library.mpv_command.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p)]
    library.mpv_command.restype = ctypes.c_int
    library.mpv_wait_event.argtypes = [ctypes.c_void_p, ctypes.c_double]
    library.mpv_wait_event.restype = ctypes.POINTER(_MpvEvent)
    library.mpv_error_string.argtypes = [ctypes.c_int]
    library.mpv_error_string.restype = ctypes.c_char_p
    library.mpv_terminate_destroy.argtypes = [ctypes.c_void_p]
    library.mpv_terminate_destroy.restype = None


def load_libmpv(path: str | os.PathLike[str] | None = None) -> tuple[ctypes.CDLL, str]:
    """Load libmpv and configure the subset of the stable client API we use."""
    resolved = os.fspath(path) if path else resolve_libmpv_path()
    if not resolved:
        raise LibMpvError("libmpv was not found")

    path_obj = Path(resolved)
    cache_key = os.path.normcase(os.path.abspath(resolved)) if path_obj.is_file() else resolved
    cached = _LOADED_LIBRARIES.get(cache_key)
    if cached is not None:
        return cached, resolved

    if path_obj.is_file():
        _prepare_native_search_path(path_obj)

    try:
        if sys.platform.startswith("win") and path_obj.is_file():
            # LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS
            try:
                library = ctypes.CDLL(resolved, winmode=0x00000100 | 0x00001000)
            except (OSError, TypeError):
                library = ctypes.CDLL(resolved)
        else:
            mode = getattr(ctypes, "RTLD_GLOBAL", 0) | getattr(os, "RTLD_NOW", 0)
            library = ctypes.CDLL(resolved, mode=mode)
    except OSError as exc:
        raise LibMpvError(f"failed to load libmpv: {exc}") from exc

    try:
        _configure_api(library)
        version = int(library.mpv_client_api_version())
    except (AttributeError, TypeError, ValueError) as exc:
        raise LibMpvError("the discovered library does not expose the libmpv client API") from exc
    if version <= 0:
        raise LibMpvError("libmpv reported an invalid client API version")

    _LOADED_LIBRARIES[cache_key] = library
    return library, resolved


def _error_text(library: ctypes.CDLL, status: int) -> str:
    try:
        value = library.mpv_error_string(int(status))
        return value.decode("utf-8", errors="replace") if value else f"error {status}"
    except Exception:
        return f"error {status}"


def _check(library: ctypes.CDLL, status: int, operation: str) -> None:
    if int(status) < 0:
        raise LibMpvError(f"{operation}: {_error_text(library, int(status))}")


def probe_libmpv(path: str | os.PathLike[str] | None = None) -> tuple[bool, str]:
    """Load the library and report its client API version for diagnostics."""
    try:
        library, resolved = load_libmpv(path)
        version = int(library.mpv_client_api_version())
        major = version >> 16
        minor = version & 0xFFFF
        return True, f"{resolved} | client API {major}.{minor}"
    except Exception as exc:
        return False, str(exc)


def _command(library: ctypes.CDLL, handle: int, *parts: str) -> None:
    encoded = [part.encode("utf-8") for part in parts]
    argv = (ctypes.c_char_p * (len(encoded) + 1))()
    for index, value in enumerate(encoded):
        argv[index] = value
    argv[len(encoded)] = None
    _check(library, library.mpv_command(handle, argv), "mpv command")


def run_libmpv_player(
    video_path: str,
    *,
    wid: str | int,
    ipc_path: str,
    muted: bool,
    volume: int,
) -> None:
    """Run a direct libmpv-backed wallpaper player until terminated."""
    target = os.path.abspath(os.path.expanduser(str(video_path or "")))
    if not os.path.isfile(target):
        raise SystemExit(2)

    try:
        library, _resolved = load_libmpv()
        handle = library.mpv_create()
        if not handle:
            raise LibMpvError("mpv_create returned NULL")

        clamped = max(0, min(100, int(volume)))
        options = {
            # Ignore user mpv.conf/scripts so wallpaper behavior remains
            # deterministic and startup avoids scanning script directories.
            "config": "no",
            "load-scripts": "no",
            "input-default-bindings": "no",
            "input-terminal": "no",
            "terminal": "no",
            "osc": "no",
            "osd-bar": "no",
            "wid": str(wid),
            "loop-file": "inf",
            "keep-open": "yes",
            "hwdec": "auto-safe",
            "border": "no",
            "panscan": "1.0",
            "keepaspect": "no",
            "keepaspect-window": "no",
            "audio-display": "no",
            "sub-auto": "no",
            "audio-file-auto": "no",
            "autoload-files": "no",
            "really-quiet": "yes",
            "volume": str(0 if muted else clamped),
            "mute": "yes" if muted else "no",
        }
        if ipc_path:
            options["input-ipc-server"] = str(ipc_path)

        for name, value in options.items():
            _check(
                library,
                library.mpv_set_option_string(
                    handle,
                    name.encode("utf-8"),
                    value.encode("utf-8"),
                ),
                f"set option {name}",
            )
        _check(library, library.mpv_initialize(handle), "mpv_initialize")
        _command(library, handle, "loadfile", target, "replace")
    except Exception as exc:
        try:
            if "handle" in locals() and handle:
                library.mpv_terminate_destroy(handle)
        except Exception:
            pass
        raise SystemExit(f"direct libmpv runtime unavailable: {exc}") from exc

    stopping = False

    def _stop(_signum=None, _frame=None) -> None:
        nonlocal stopping
        stopping = True

    old_handlers: list[tuple[int, object]] = []
    for sig_name in ("SIGTERM", "SIGINT"):
        try:
            sig = getattr(signal, sig_name)
            old_handlers.append((sig, signal.getsignal(sig)))
            signal.signal(sig, _stop)
        except Exception:
            pass

    try:
        while not stopping:
            event_ptr = library.mpv_wait_event(handle, 0.25)
            if event_ptr and int(event_ptr.contents.event_id) == MPV_EVENT_SHUTDOWN:
                break
    finally:
        for sig, previous in old_handlers:
            try:
                signal.signal(sig, previous)
            except Exception:
                pass
        try:
            library.mpv_terminate_destroy(handle)
        except Exception:
            pass
