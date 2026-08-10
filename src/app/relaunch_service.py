"""Cross-platform relaunch orchestration separated from the runtime engine."""
from __future__ import annotations

from collections.abc import Callable, Sequence
import ctypes
import os
import subprocess
import sys
from typing import Any

LogCallback = Callable[..., None]


class RelaunchService:
    """Persist session state, release guards, and start the replacement process."""

    _SKIP_FLAGS = {
        "--previous", "--next", "--random", "--show", "--hide",
        "--jump-to-wallpaper", "--from-context-menu", "--sync-context-on-start",
        "--inherit-session-wallpaper", "--context-menu-dispatched-child",
        "--quit", "--wait-for-exit", "--internal-video-player", "--muted",
    }
    _SKIP_VALUE_FLAGS = {
        "--set-wallpaper",
        "--relaunch-wait-pid",
        "--relaunch-wait-created-at",
    }

    def __init__(
        self,
        *,
        is_windows: bool,
        is_frozen: Callable[[], bool],
        executable_path: Callable[[], str],
        base_dir: Callable[[], str],
        capture_session: Callable[[], Any],
        persist_session: Callable[[], Any],
        release_guard: Callable[[], Any],
        cleanup_tray: Callable[[], Any],
        recover_guard: Callable[[], Any],
        log: LogCallback,
        argv: Callable[[], Sequence[str]] = lambda: tuple(sys.argv),
        python_executable: Callable[[], str] = lambda: sys.executable,
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self._is_windows = bool(is_windows)
        self._is_frozen = is_frozen
        self._executable_path = executable_path
        self._base_dir = base_dir
        self._capture_session = capture_session
        self._persist_session = persist_session
        self._release_guard = release_guard
        self._cleanup_tray = cleanup_tray
        self._recover_guard = recover_guard
        self._log = log
        self._argv = argv
        self._python_executable = python_executable
        self._popen = popen

    def is_windows_admin(self) -> bool:
        if not self._is_windows:
            return False
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception as exc:
            self._log(f"管理员权限检测失败: {exc}")
            return False

    def restart(self, extra_args: Sequence[str] | None = None) -> bool:
        try:
            if self._is_windows:
                executable, base_args = self._windows_command_parts()
                relaunch_args = [*base_args, *self.filtered_restart_args(extra_args)]
                workdir = os.path.dirname(executable) or os.getcwd()
            else:
                executable, base_args = self._command_parts()
                relaunch_args = [*base_args, *self.filtered_restart_args(extra_args)]
                workdir = os.path.abspath(str(self._base_dir() or os.getcwd()))
            self._capture_session()
            self._persist_session()
            relaunch_args.extend(self._handoff_wait_args())
            # Launch the replacement *before* irreversible cleanup.  The child
            # waits for this exact parent process to exit before attempting the
            # singleton lock, so a spawn/UAC failure leaves the current GUI fully
            # alive instead of trying to reconstruct an already-finalized exit.
            self._popen([executable, *relaunch_args], cwd=workdir, close_fds=True)
            self._release_guard()
            self._cleanup_tray()
            self._log(f"已请求普通重启: exe={executable}; args={relaunch_args}; cwd={workdir}")
            return True
        except Exception as exc:
            self._log(f"普通重启失败: {exc}", level="ERROR", exc_info=exc)
            return False

    def restart_as_admin(self, extra_args: Sequence[str] | None = None) -> bool:
        if not self._is_windows or self.is_windows_admin():
            return self.restart(extra_args)
        try:
            executable, base_args = self._windows_command_parts()
            relaunch_args = [*base_args, *self.filtered_restart_args(extra_args)]
            workdir = os.path.dirname(executable) or os.getcwd()
            self._log(f"准备管理员重启: exe={executable}; args={relaunch_args}; cwd={workdir}")
            self._capture_session()
            self._persist_session()
            relaunch_args.extend(self._handoff_wait_args())
            ok, detail = self._shell_execute_runas(executable, relaunch_args, workdir)
            if not ok:
                # Nothing irreversible has happened yet.  UAC cancellation or
                # ShellExecute failure simply returns control to the live GUI.
                self._log(f"提权重启失败: {detail}", level="ERROR")
                return False
            self._release_guard()
            self._cleanup_tray()
            self._log(f"已请求管理员权限重启: {detail}")
            return True
        except Exception as exc:
            self._log(f"提权重启异常: {exc}", level="ERROR", exc_info=exc)
            return False

    def _handoff_wait_args(self) -> list[str]:
        """Arguments that make the replacement wait for this process to exit."""
        pid = int(os.getpid())
        args = ["--relaunch-wait-pid", str(pid)]
        try:
            import psutil
            created_at = float(psutil.Process(pid).create_time())
        except Exception:
            created_at = 0.0
        if created_at > 0:
            args.extend(["--relaunch-wait-created-at", f"{created_at:.6f}"])
        return args

    def filtered_restart_args(self, extra_args: Sequence[str] | None = None) -> list[str]:
        def _filter(raw_args: Sequence[str]) -> list[str]:
            result: list[str] = []
            skip_next = False
            for raw in raw_args:
                arg = str(raw)
                if skip_next:
                    skip_next = False
                    continue
                if arg in self._SKIP_FLAGS:
                    continue
                if arg in self._SKIP_VALUE_FLAGS:
                    skip_next = True
                    continue
                if any(arg.startswith(flag + "=") for flag in self._SKIP_VALUE_FLAGS):
                    continue
                result.append(arg)
            return result

        current = _filter(list(self._argv())[1:])
        requested = _filter([str(arg) for arg in (extra_args or ())])
        for arg in requested:
            if arg not in current:
                current.append(arg)
        if "--inherit-session-wallpaper" not in current:
            current.append("--inherit-session-wallpaper")
        return current

    def _safe_recover(self) -> None:
        try:
            self._recover_guard()
        except Exception as exc:
            self._log(f"恢复单实例与 IPC 守卫失败: {exc}", level="ERROR", exc_info=exc)

    def _command_parts(self) -> tuple[str, list[str]]:
        argv = list(self._argv())
        if self._is_frozen():
            candidates = [self._executable_path(), self._python_executable(), argv[0] if argv else ""]
            for candidate in candidates:
                candidate = os.path.abspath(os.path.expanduser(str(candidate or "")))
                if candidate and os.path.isfile(candidate):
                    return candidate, []
            return os.path.abspath(self._executable_path()), []
        entry = os.path.abspath(argv[0]) if argv else os.path.join(self._base_dir(), "main.py")
        return self._python_executable(), [entry]

    def _windows_command_parts(self) -> tuple[str, list[str]]:
        if self._is_frozen():
            return self._command_parts()
        python_executable = self._python_executable()
        pythonw = os.path.join(os.path.dirname(python_executable), "pythonw.exe")
        executable = pythonw if os.path.exists(pythonw) else python_executable
        argv = list(self._argv())
        entry = os.path.abspath(argv[0]) if argv else os.path.join(self._base_dir(), "main.pyw")
        return os.path.abspath(executable), [entry]

    @staticmethod
    def _format_windows_error(code: Any) -> str:
        try:
            code = int(code or 0)
        except Exception:
            code = 0
        if not code:
            return "unknown error"
        try:
            return ctypes.FormatError(code).strip()
        except Exception:
            return f"Win32 error {code}"

    def _shell_execute_runas(self, executable: str, args: list[str], workdir: str) -> tuple[bool, str]:
        params = subprocess.list2cmdline([str(arg) for arg in args])
        executable = os.path.abspath(os.path.expanduser(str(executable)))
        workdir = os.path.abspath(os.path.expanduser(str(workdir or os.path.dirname(executable))))
        if not os.path.isfile(executable):
            return False, f"executable not found: {executable}"
        try:
            from ctypes import wintypes
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

            class SHELLEXECUTEINFOW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG),
                    ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE),
                ]

            info = SHELLEXECUTEINFOW()
            info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
            info.fMask = 0x00000040
            info.lpVerb = "runas"
            info.lpFile = executable
            info.lpParameters = params
            info.lpDirectory = workdir
            info.nShow = 1
            shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
            shell32.ShellExecuteExW.restype = wintypes.BOOL
            if bool(shell32.ShellExecuteExW(ctypes.byref(info))):
                if info.hProcess:
                    kernel32.CloseHandle(info.hProcess)
                return True, "ShellExecuteExW succeeded"
            err = ctypes.get_last_error()
            return False, f"ShellExecuteExW failed: {err} {self._format_windows_error(err)}"
        except Exception as exc:
            self._log(f"ShellExecuteExW unavailable, falling back to ShellExecuteW: {exc}", level="WARNING")
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, workdir, 1)
            value = int(ret or 0)
            if value > 32:
                return True, f"ShellExecuteW succeeded: {value}"
            err = int(ctypes.windll.kernel32.GetLastError() or 0)
            return False, f"ShellExecuteW failed: return={value}; {err} {self._format_windows_error(err)}"
        except Exception as exc:
            return False, f"ShellExecuteW exception: {exc}"


def cleanup_tray_icon(icon: Any, *, is_windows: bool, sleep: Callable[[float], Any]) -> None:
    """Hide and stop a legacy tray object, then ask Explorer to refresh its tray."""
    if icon is not None:
        try:
            icon.visible = False
        except Exception:
            pass
        try:
            icon.stop()
        except Exception:
            pass
    if is_windows:
        try:
            user32 = ctypes.windll.user32
            tray = user32.FindWindowW("Shell_TrayWnd", None)
            notify = user32.FindWindowExW(tray, None, "TrayNotifyWnd", None) if tray else None
            toolbar = user32.FindWindowExW(notify, None, "ToolbarWindow32", None) if notify else None
            if toolbar:
                user32.SendMessageW(toolbar, 0x0200, 0, 0)
        except Exception:
            pass
    try:
        sleep(0.08)
    except Exception:
        pass
