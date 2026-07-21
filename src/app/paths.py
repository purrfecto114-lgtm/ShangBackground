"""Centralized runtime path resolution for source and packaged builds.

The project is expected to be packaged with Nuitka later.  Nuitka onefile and
standalone builds intentionally separate two locations:

* the permanent executable location (``sys.argv[0]`` / ``sys.executable``), used
  for relaunch, startup and context-menu commands;
* the runtime extraction/distribution location (``__file__`` /
  ``__compiled__.containing_dir``), where data files included with Nuitka live.

Keep all read-only bundled resources (``img``, ``lang``) resolved from the
runtime resource root, and all user-writable files resolved from the per-user
data directory below.
"""
from __future__ import annotations

import os
import platform
import sys
import tempfile
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
_SOURCE_ROOT = _PACKAGE_ROOT.parent


def _compiled_value():
    """Return Nuitka's ``__compiled__`` marker when available."""
    try:
        marker = globals().get("__compiled__")
        if marker is not None:
            return marker
    except Exception:
        pass
    try:
        main_module = sys.modules.get("__main__")
        return getattr(main_module, "__compiled__", None)
    except Exception:
        return None


def is_nuitka_compiled() -> bool:
    """Return True when running from a Nuitka-compiled program/module."""
    return _compiled_value() is not None


def _looks_like_bundled_executable() -> bool:
    """Best-effort packaged-runtime check independent of Python-specific markers.

    Nuitka normally exposes ``__compiled__`` and PyInstaller exposes
    ``sys.frozen``. Some relaunch/helper paths can hide those markers from
    imported modules while ``sys.executable`` still points at the app binary.
    Treat a non-Python executable as packaged only when argv[0] is not a loose
    .py/.pyw script, so normal source runs remain developer runs.
    """
    try:
        exe = _safe_resolve(Path(sys.executable or ""))
        if not exe.is_file():
            return False
        if exe.stem.lower().startswith(("python", "pypy")):
            return False
        arg0 = _safe_resolve(Path(sys.argv[0])) if sys.argv else Path("")
        if arg0.suffix.lower() in (".py", ".pyw"):
            return False
        if sys.platform.startswith("win"):
            return exe.suffix.lower() == ".exe"
        if sys.platform == "darwin":
            exe_text = str(exe).replace("\\", "/")
            return ".app/" in exe_text or exe.name.lower() != "python"
        return True
    except Exception:
        return False


def is_packaged_runtime() -> bool:
    """Return True for PyInstaller/cx_Freeze-style or Nuitka packaged runs."""
    return bool(getattr(sys, "frozen", False) or is_nuitka_compiled() or _looks_like_bundled_executable())


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except Exception:
        try:
            return Path(os.path.abspath(os.fspath(path)))
        except Exception:
            return path


def _existing_file(path: str | os.PathLike[str] | None) -> Path | None:
    if not path:
        return None
    try:
        candidate = _safe_resolve(Path(os.fspath(path)).expanduser())
        return candidate if candidate.is_file() else None
    except Exception:
        return None


def compiled_containing_dir() -> Path | None:
    """Return Nuitka's runtime containing directory when exposed.

    In standalone mode this is usually the ``*.dist`` directory.  In onefile
    mode this is the temporary/permanent unpack directory that also contains
    data files included by ``--include-data-*``.
    """
    compiled = _compiled_value()
    containing_dir = getattr(compiled, "containing_dir", None) if compiled is not None else None
    if containing_dir:
        try:
            return _safe_resolve(Path(os.fspath(containing_dir)))
        except Exception:
            return Path(os.fspath(containing_dir))
    return None


def app_executable_path() -> str:
    """Return the stable executable/interpreter path for relaunch commands.

    Nuitka onefile uses ``__file__`` for the extraction location, but commands
    that must survive after the current process exits need the original EXE.
    Prefer Nuitka's ``original_argv0`` when present, then ``sys.argv[0]``, then
    ``sys.executable``.
    """
    compiled = _compiled_value()
    candidates: list[object] = []
    if compiled is not None:
        candidates.append(getattr(compiled, "original_argv0", None))
    if is_packaged_runtime() and sys.argv:
        candidates.append(sys.argv[0])
    candidates.append(sys.executable)
    if sys.argv:
        candidates.append(sys.argv[0])
    for raw in candidates:
        found = _existing_file(raw)
        if found is not None:
            return os.fspath(found)
    try:
        return os.fspath(_safe_resolve(Path(sys.executable)))
    except Exception:
        return os.fspath(_safe_resolve(Path(sys.argv[0] if sys.argv else ".")))


def executable_dir() -> Path:
    """Return the directory containing the stable executable/interpreter."""
    return Path(app_executable_path()).parent


def _append_root_variants(roots: list[Path], root: Path | None) -> None:
    if root is None:
        return
    try:
        root = _safe_resolve(root)
    except Exception:
        pass
    for candidate in (root, root / "src"):
        if candidate not in roots:
            roots.append(candidate)


def _candidate_roots() -> list[Path]:
    """Return possible resource roots, ordered by runtime relevance."""
    roots: list[Path] = []
    bundled = getattr(sys, "_MEIPASS", None)

    if is_packaged_runtime():
        # Packaged mode: data files live beside/inside the unpacked runtime, not
        # necessarily beside the original executable.  Search those locations
        # before falling back to the source-tree path.
        if bundled:
            _append_root_variants(roots, Path(bundled))
        _append_root_variants(roots, compiled_containing_dir())
        _append_root_variants(roots, _SOURCE_ROOT)
        _append_root_variants(roots, executable_dir())
    else:
        _append_root_variants(roots, _SOURCE_ROOT)
        if bundled:
            _append_root_variants(roots, Path(bundled))

    unique: list[Path] = []
    for root in roots:
        root = _safe_resolve(root)
        if root not in unique:
            unique.append(root)
    return unique


def _is_resource_root(root: Path) -> bool:
    return (root / "img").is_dir() and (root / "lang").is_dir()


def _select_resource_root() -> Path:
    candidates = _candidate_roots()
    for root in candidates:
        if _is_resource_root(root):
            return root
    return candidates[0] if candidates else _SOURCE_ROOT


RESOURCE_ROOT = _select_resource_root()
IMAGE_DIR = RESOURCE_ROOT / "img"
LANG_DIR = RESOURCE_ROOT / "lang"
TRANSLATIONS_DIR = RESOURCE_ROOT / "translations"
PROJECT_ROOT = _SOURCE_ROOT.parent
# Bundled native runtimes.  Source builds keep versioned MPV payloads under
# ``src/bin/mpv/<platform>/<arch>/<runtime-id>/``.  The build backend selects
# exactly one verified version and maps it to ``<runtime-root>/bin/mpv`` so the
# packaged application does not carry every downloaded version.
BIN_DIR = RESOURCE_ROOT / "bin"


def resource_path(*parts: str | os.PathLike[str]) -> str:
    return os.fspath(RESOURCE_ROOT.joinpath(*map(os.fspath, parts)))


def image_path(name: str) -> str:
    return os.fspath(IMAGE_DIR / name)


def qss_url_path(path: str | os.PathLike[str], *, cache_buster: str | None = None) -> str:
    """Return a Qt StyleSheet-safe path for ``url(...)``.

    The path is normalized to forward slashes and returned as a raw absolute
    POSIX path (no ``file://`` scheme, no percent-encoding). Qt's QSS
    ``url("...")`` parser accepts such absolute paths directly when they are
    double-quoted, and ``QSvgRenderer`` can open them as-is.

    Background (Bug 3 — SVG re-reading fails):
      The old implementation returned a percent-encoded POSIX path WITHOUT the
      ``file://`` scheme.  Qt's QSS parser accepted this on the first
      ``setStyleSheet()`` call but its internal ``QSvgRenderer`` cache then
      keyed off the raw URL string; on the second rebuild (theme switch) a
      stale renderer was returned, producing ``qt.svg: Cannot open file ...``
      warnings and missing checkbox/spin indicators.

    Background (Bug 9 — file:// scheme regression):
      A later fix wrapped the path in a ``file://`` URL with percent-encoding.
      This worked for ASCII-only paths but broke for paths containing ``()``,
      spaces, or non-ASCII characters, because ``QSvgRenderer`` does NOT
      decode percent-encoding and treats the entire URL string as a literal
      file path.

    Background (Bug 10 — current behavior):
      We now return the raw absolute POSIX path. Qt's QSS ``url("...")``
      parser handles this correctly on POSIX (``/home/...``) and Windows
      (``C:/Users/...``), and ``QSvgRenderer`` can open the path directly.
      Theme-switch cache invalidation works because dark/light SVGs have
      different filenames, so the path string changes between rebuilds.

    Args:
        path: Filesystem path (str or PathLike).
        cache_buster: Optional short signature (e.g. ``"dark"`` or a hash) that
            gets appended as ``?v=<sig>`` to force Qt to re-read the SVG when
            the theme changes.  When None, no query string is appended (cached
            renderers are reused, matching Qt's default behavior).

    Returns:
        An absolute POSIX path string suitable for QSS ``url("...")``.
    """
    # Bug 9 fix (regression of Bug 3): ``QUrl.fromLocalFile(...).toString()``
    # uses ``PrettyDecoded`` formatting by default, which **omits the empty
    # authority component** when the host is empty.  On Windows this produced
    # ``file:/D:/path`` (single slash) instead of ``file:///D:/path`` (three
    # slashes).  Qt's QSS parser then failed to recognise the URL as a local
    # file URL, treated it as a relative path, and prepended the application's
    # current working directory, producing errors such as:
    #
    #     qt.svg: Cannot open file 'D:/Microsoft VS Code/file:/D:/.../spin_arrow_up_dark.svg'
    #
    # We now build the URL string by hand so the result is independent of Qt's
    # URL formatting options.  Three slashes after ``file:`` are guaranteed on
    # both POSIX (absolute path already starts with ``/``) and Windows (we
    # prepend a slash before the drive letter).
    #
    # ── Bug 10 fix (regression of Bug 9): The ``file://`` + percent-encoded
    # path approach worked for paths containing only ASCII characters, but
    # broke for project paths containing parentheses,
    # spaces, or non-ASCII characters. ``QSvgRenderer`` does NOT decode
    # percent-encoded ``%28``/``%29`` sequences — it treats the entire URL
    # string (including the ``file://`` prefix and the percent-encoding) as a
    # literal file path. This produced errors such as:
    #
    #     qt.svg: Cannot open file 'file:///home/.../My%20Project%20(beta)/.../spin_arrow_up_dark.svg'
    #
    # and caused spinbox arrows + checkbox indicators to go missing whenever
    # the install path contained ``()``, spaces, or Unicode characters.
    #
    # The fix: return the raw absolute POSIX path WITHOUT the ``file://``
    # scheme and WITHOUT percent-encoding. Qt's QSS ``url("...")`` parser
    # accepts absolute paths directly when they are double-quoted (which the
    # call sites already do). This works on POSIX (``/home/...``), Windows
    # (``C:/Users/...``), and paths with ``()``, spaces, or Unicode.
    posix_path = os.path.abspath(os.fspath(path)).replace("\\", "/")
    # On Windows the absolute path looks like ``C:/Users/...``; Qt's QSS
    # parser handles this directly when the URL is double-quoted. We do NOT
    # prepend a leading slash and do NOT percent-encode — both were the
    # source of the ``file://`` regressions above.
    # If a cache-buster is requested, append a query string. Note: this only
    # works for QSS url() references that Qt resolves through its URL parser;
    # QSvgRenderer's direct file loader does not understand query strings,
    # so cache-busting is best-effort and mainly relevant when the QSS path
    # changes between theme rebuilds (which it does, because dark/light SVGs
    # have different filenames).
    if cache_buster:
        return f"{posix_path}?v={cache_buster}"
    return posix_path


def image_qss_url(name: str, *, cache_buster: str | None = None) -> str:
    return qss_url_path(image_path(name), cache_buster=cache_buster)


def language_path(name: str) -> str:
    return os.fspath(LANG_DIR / name)


def font_directories() -> tuple[Path, ...]:
    candidates = [RESOURCE_ROOT / "fonts", PROJECT_ROOT / "fonts", executable_dir() / "fonts"]
    result: list[Path] = []
    for candidate in candidates:
        candidate = _safe_resolve(candidate)
        if candidate not in result:
            result.append(candidate)
    return tuple(result)


def entry_script_path() -> str:
    """Return the runtime entry target used by startup/source launchers."""
    if is_packaged_runtime():
        return app_executable_path()
    return os.fspath(RESOURCE_ROOT / "main.py")

# ---------------------------------------------------------------------------
# Per-user writable storage paths
# ---------------------------------------------------------------------------
# Keep this module independent from app.config to avoid circular imports.
DEFAULT_APP_NAME = "ShangBackground"


def _ensure_directory(path: Path) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except Exception:
        fallback = Path(tempfile.gettempdir()) / DEFAULT_APP_NAME
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def user_data_dir(app_name: str = DEFAULT_APP_NAME) -> str:
    """Return the single per-user writable directory for config and runtime state.

    This replaces scattered LOCALAPPDATA/XDG/Library path snippets in the main
    process, HTML-wallpaper parent adapter and HTML-wallpaper child process so
    all of them read/write the same settings and runtime-control files.
    """
    name = str(app_name or DEFAULT_APP_NAME).strip() or DEFAULT_APP_NAME
    if sys.platform.startswith("win"):
        root = (
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        )
        path = Path(root) / name
    elif sys.platform == "darwin":
        path = Path(os.path.expanduser("~/Library/Application Support")) / name
    else:
        root = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
        path = Path(root) / name.lower()
    return os.fspath(_ensure_directory(path))


APP_DATA_DIR = Path(user_data_dir(DEFAULT_APP_NAME))


def app_data_path(*parts: str | os.PathLike[str]) -> str:
    return os.fspath(APP_DATA_DIR.joinpath(*map(os.fspath, parts)))


def config_path(name: str = "settings.json") -> str:
    return app_data_path(name)


# ---------------------------------------------------------------------------
# Bundled binaries (mpv, etc.)
# ---------------------------------------------------------------------------

def _runtime_platform_id() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _runtime_arch_id() -> str:
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


def _versioned_mpv_roots() -> tuple[Path, ...]:
    """Return packaged, active source, then inactive versioned MPV roots."""
    roots: list[Path] = []
    # A selected build runtime is always flattened to this stable packaged path.
    packaged = BIN_DIR / "mpv"
    if packaged.is_dir():
        roots.append(packaged)

    source_root = BIN_DIR / "mpv" / _runtime_platform_id() / _runtime_arch_id()
    if source_root.is_dir():
        active = ""
        try:
            active = source_root.joinpath("ACTIVE").read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            pass
        if active:
            active_root = source_root / active
            if active_root.is_dir():
                roots.append(active_root)
        for candidate in sorted(source_root.iterdir(), key=lambda item: item.name.lower()):
            if candidate.is_dir() and candidate not in roots:
                roots.append(candidate)
    return tuple(roots)


def bundled_bin_path(name: str) -> str | None:
    """Return a bundled executable/library path from the selected MPV runtime.

    Packaged builds search ``bin/mpv`` first.  Source runs then follow the
    ``ACTIVE`` marker under the platform/architecture/version hierarchy, while
    retaining the historical ``src/bin/<platform>`` and flat ``src/bin``
    layouts as migration fallbacks.
    """
    try:
        candidates = [root / name for root in _versioned_mpv_roots()]
        candidates.extend((BIN_DIR / _runtime_platform_id() / name, BIN_DIR / name))
        for candidate in candidates:
            if candidate.is_file():
                return os.fspath(candidate)
    except Exception:
        pass
    return None


def mpv_bundled_exe() -> str | None:
    """Return the bundled mpv executable path, or None when not bundled.

    The selected build runtime is copied into ``bin/mpv``.  In source runs the
    active runtime is resolved from ``bin/mpv/<platform>/<arch>/<runtime-id>``.
    """
    name = "mpv.exe" if sys.platform.startswith("win") else "mpv"
    return bundled_bin_path(name)


# ---------------------------------------------------------------------------
# Per-user optional media runtime directory. This is intentionally unmanaged:
# installers or users may place a verified target-platform mpv/libmpv payload
# here without requiring administrator privileges.
# ---------------------------------------------------------------------------

def mpv_user_install_path(*, create: bool = False) -> Path:
    """Return the optional per-user MPV directory without probing side effects."""
    root = APP_DATA_DIR / "bin" / "mpv"
    if create:
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            import tempfile
            root = Path(tempfile.gettempdir()) / "shangbackground-mpv"
            root.mkdir(parents=True, exist_ok=True)
    return root


def mpv_user_install_dir() -> str:
    """Compatibility API for installers that intentionally create the directory."""
    return os.fspath(mpv_user_install_path(create=True))


def mpv_user_install_exe() -> str | None:
    """Return a user-installed MPV executable without creating directories."""
    name = "mpv.exe" if sys.platform.startswith("win") else "mpv"
    candidate = mpv_user_install_path(create=False) / name
    return os.fspath(candidate) if candidate.is_file() else None

def external_media_runtime_allowed() -> bool:
    """Whether a packaged build may execute user/PATH media runtimes.

    Source runs are developer-controlled. Packaged builds only opt into external
    executables when the build manifest selected ``system`` mode or the user
    explicitly enables the escape hatch. This prevents an otherwise
    self-contained build from unexpectedly running a same-named binary from
    PATH, a registry command, or a writable user directory after bundled MPV
    fails.
    """
    if not is_packaged_runtime():
        return True
    if os.environ.get("SHANGBACKGROUND_ALLOW_EXTERNAL_MPV") == "1":
        return True
    try:
        from app.build_features import video_runtime_mode

        return video_runtime_mode() == "system"
    except Exception:
        return False


def resolve_mpv_path() -> str | None:
    """Resolve an external MPV executable with packaged-runtime hardening.

    Bundled files always win. Packaged builds only consult user/PATH locations
    when their build manifest selected system mode or the user explicitly opts
    into external runtimes.
    """
    bundled = mpv_bundled_exe()
    if bundled:
        return bundled
    if not external_media_runtime_allowed():
        return None
    user_exe = mpv_user_install_exe()
    if user_exe:
        return user_exe
    try:
        import shutil
        name = "mpv.exe" if sys.platform.startswith("win") else "mpv"
        return shutil.which(name)
    except Exception:
        return None

