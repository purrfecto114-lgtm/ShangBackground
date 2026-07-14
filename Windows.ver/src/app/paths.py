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
# Bundled-binaries directory.  Place the platform-appropriate mpv binary here
# (``mpv.exe`` on Windows, ``mpv`` on Linux/macOS) to embed it into the
# packaged app so the video wallpaper mode works without requiring the user
# to install mpv system-wide.  In source runs, this resolves to
# ``<platform>/src/bin``; in Nuitka onefile/standalone runs it resolves to
# ``<runtime-resource-root>/bin`` thanks to ``--include-data-dir=src/bin=bin``.
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
    # broke for paths containing parentheses (e.g. ``Linux.ver(beta)``),
    # spaces, or non-ASCII characters. ``QSvgRenderer`` does NOT decode
    # percent-encoded ``%28``/``%29`` sequences — it treats the entire URL
    # string (including the ``file://`` prefix and the percent-encoding) as a
    # literal file path. This produced errors such as:
    #
    #     qt.svg: Cannot open file 'file:///home/.../Linux.ver%28beta%29/.../spin_arrow_up_dark.svg'
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

def bundled_bin_path(name: str) -> str | None:
    """Return the absolute path of a bundled binary if present, else None.

    Looks inside ``BIN_DIR`` (``<resource_root>/bin``).  On Windows the
    expected name is e.g. ``mpv.exe``; on Linux/macOS just ``mpv``.  Caller
    passes the platform-appropriate filename; we just verify the file exists.
    """
    try:
        candidate = BIN_DIR / name
        if candidate.is_file():
            return os.fspath(candidate)
    except Exception:
        pass
    return None


def mpv_bundled_exe() -> str | None:
    """Return the bundled mpv executable path, or None when not bundled.

    On Windows looks for ``bin/mpv.exe``; on Linux/macOS looks for ``bin/mpv``.
    The build scripts copy the platform-appropriate mpv binary into ``bin/``
    before invoking Nuitka/PyInstaller, and ``--include-data-dir=src/bin=bin``
    surfaces it at runtime under ``RESOURCE_ROOT/bin``.
    """
    name = "mpv.exe" if sys.platform.startswith("win") else "mpv"
    return bundled_bin_path(name)


# ---------------------------------------------------------------------------
# v1.4.6: mpv 用户安装目录 (自动下载位置)
# 联网考证结论:
#   - %LOCALAPPDATA%\<App>\bin\mpv\ 是 Windows 惯例 (per-user, 无需 admin,
#     不被 pip reinstall 清空). 证据: superuser.com/questions/1445143
#   - macOS: ~/Library/Application Support/<App>/bin/mpv/
#   - Linux: ~/.local/share/<App>/bin/mpv/ (XDG_DATA_HOME)
# ---------------------------------------------------------------------------

def mpv_user_install_dir() -> str:
    """返回 mpv 用户安装目录 (自动下载位置). 目录会被创建.

    v1.4.6 新增: 之前 mpv 只能手动放到 bin/, 现在支持自动下载到此目录.
    """
    try:
        install_root = APP_DATA_DIR / "bin" / "mpv"
        install_root.mkdir(parents=True, exist_ok=True)
        return os.fspath(install_root)
    except Exception:
        # 兜底: 临时目录
        import tempfile
        fallback = Path(tempfile.gettempdir()) / "shangbackground-mpv"
        fallback.mkdir(parents=True, exist_ok=True)
        return os.fspath(fallback)


def mpv_user_install_exe() -> str | None:
    """返回用户安装目录下的 mpv 可执行文件路径 (若存在), 否则 None."""
    name = "mpv.exe" if sys.platform.startswith("win") else "mpv"
    candidate = Path(mpv_user_install_dir()) / name
    if candidate.is_file():
        return os.fspath(candidate)
    return None


def mpv_version_info_path() -> str:
    """返回 mpv-version.json 路径 (记录已下载 mpv 的 url/sha256/date)."""
    return os.fspath(Path(mpv_user_install_dir()) / "mpv-version.json")


def resolve_mpv_path() -> str | None:
    """统一 mpv 路径解析顺序 (v1.4.6):

    1. 用户安装目录 (自动下载) — mpv_user_install_exe()
    2. 打包内置 — mpv_bundled_exe()
    3. 系统 PATH — shutil.which()
    4. 返回 None (调用方再走 registry / 常见目录兜底)

    优先用户安装目录是为了让自动下载的版本覆盖可能过时的系统 mpv.
    """
    # 1. 用户安装目录
    user_exe = mpv_user_install_exe()
    if user_exe:
        return user_exe
    # 2. 打包内置
    bundled = mpv_bundled_exe()
    if bundled:
        return bundled
    # 3. 系统 PATH
    try:
        import shutil
        name = "mpv.exe" if sys.platform.startswith("win") else "mpv"
        found = shutil.which(name)
        if found:
            return found
    except Exception:
        pass
    return None

