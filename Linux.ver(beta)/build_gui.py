#!/usr/bin/env python3
"""Small GUI launcher for the local platform's Nuitka build script."""
from __future__ import annotations

import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading

EXPECTED_PLATFORM = "linux"
PLATFORM_LABEL = "Linux"
PROJECT_DIR = Path(__file__).resolve().parent
BUILD_SCRIPT = PROJECT_DIR / "build_nuitka.py"


def current_host() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def build_command(
    profile: str = "full",
    mode: str = "standalone",
    jobs: int = 2,
    *,
    install_dependencies: bool = True,
    windows_console_mode: str = "disable",
    upx: bool = False,
    upx_binary: str = "",
) -> list[str]:
    """Build a command that can only invoke this platform tree's local script."""
    if profile not in {"lite", "full"}:
        raise ValueError(f"Unsupported profile: {profile}")
    if mode not in {"standalone", "onefile"}:
        raise ValueError(f"Unsupported mode: {mode}")
    if int(jobs) not in {1, 2, 4}:
        raise ValueError("Jobs must be 1, 2, or 4")
    command = [
        sys.executable,
        str(BUILD_SCRIPT),
        "--profile",
        profile,
        "--mode",
        mode,
        "--jobs",
        str(int(jobs)),
    ]
    if not install_dependencies:
        command.append("--skip-install")
    if EXPECTED_PLATFORM == "windows":
        if windows_console_mode not in {"disable", "force"}:
            raise ValueError("Windows console mode must be disable or force")
        command.extend(["--windows-console-mode", windows_console_mode])
    if upx:
        command.append("--upx")
        if upx_binary:
            command.extend(["--upx-binary", upx_binary])
    return command


def open_path(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class BuildLauncher:
    def __init__(self, root, tk, ttk, scrolledtext, messagebox):
        self.root = root
        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.process: subprocess.Popen[str] | None = None
        self.output_queue: queue.Queue[str | None] = queue.Queue()

        root.title(f"ShangBackground Build - {PLATFORM_LABEL}")
        root.geometry("760x520")
        root.minsize(680, 440)

        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)

        settings = ttk.LabelFrame(outer, text="Build options", padding=10)
        settings.pack(fill="x")

        self.profile = tk.StringVar(value="full")
        self.mode = tk.StringVar(value="standalone")
        self.jobs = tk.StringVar(value="2")
        self.install_dependencies = tk.BooleanVar(value=True)
        self.console_mode = tk.StringVar(value="disable")

        ttk.Label(settings, text="Profile").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=4)
        ttk.Combobox(settings, textvariable=self.profile, values=("full", "lite"), state="readonly", width=14).grid(row=0, column=1, sticky="w", pady=4)
        ttk.Label(settings, text="Mode").grid(row=0, column=2, sticky="w", padx=(18, 6), pady=4)
        ttk.Combobox(settings, textvariable=self.mode, values=("standalone", "onefile"), state="readonly", width=14).grid(row=0, column=3, sticky="w", pady=4)
        ttk.Label(settings, text="Jobs").grid(row=0, column=4, sticky="w", padx=(18, 6), pady=4)
        ttk.Combobox(settings, textvariable=self.jobs, values=("1", "2", "4"), state="readonly", width=6).grid(row=0, column=5, sticky="w", pady=4)

        ttk.Checkbutton(settings, text="Install/update build dependencies", variable=self.install_dependencies).grid(row=1, column=0, columnspan=3, sticky="w", pady=4)
        if EXPECTED_PLATFORM == "windows":
            ttk.Label(settings, text="Program console").grid(row=1, column=3, sticky="e", padx=(0, 6), pady=4)
            ttk.Combobox(
                settings,
                textvariable=self.console_mode,
                values=("disable", "force"),
                state="readonly",
                width=14,
            ).grid(row=1, column=4, columnspan=2, sticky="w", pady=4)

        # UPX compression option row
        self.upx_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(settings, text="Compress with UPX", variable=self.upx_enabled).grid(row=2, column=0, sticky="w", pady=4)
        ttk.Label(settings, text="UPX path").grid(row=2, column=1, sticky="e", padx=(4, 6), pady=4)
        import shutil as _shutil
        _default_upx = _shutil.which("upx") or _shutil.which("upx-ucl") or ""
        self.upx_binary = tk.StringVar(value=_default_upx)
        ttk.Entry(settings, textvariable=self.upx_binary, width=32).grid(row=2, column=2, columnspan=3, sticky="we", pady=4)
        ttk.Button(settings, text="Browse...", command=self._browse_upx).grid(row=2, column=5, sticky="w", padx=(4, 0), pady=4)

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(10, 8))
        self.start_button = ttk.Button(buttons, text="Start build", command=self.start_build)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop_build, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Open output", command=lambda: open_path(PROJECT_DIR / "dist-nuitka")).pack(side="left", padx=(18, 0))
        ttk.Button(buttons, text="Open logs", command=lambda: open_path(PROJECT_DIR / "build-logs")).pack(side="left", padx=(8, 0))

        self.status = tk.StringVar(value=f"Ready - local {PLATFORM_LABEL} source only")
        ttk.Label(outer, textvariable=self.status).pack(fill="x", pady=(0, 6))
        self.output = scrolledtext.ScrolledText(outer, wrap="word", height=18, font=("TkFixedFont", 9))
        self.output.pack(fill="both", expand=True)
        self.output.configure(state="disabled")

        if current_host() != EXPECTED_PLATFORM:
            self.start_button.configure(state="disabled")
            self.status.set(f"Host mismatch: this launcher only builds {PLATFORM_LABEL} on {PLATFORM_LABEL}")

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(80, self.poll_output)

    def _browse_upx(self) -> None:
        initial = self.upx_binary.get().strip()
        initial_dir = None
        if initial:
            from pathlib import Path as _P
            p = _P(initial).parent
            if p.is_dir():
                initial_dir = str(p)
        filetypes = [("UPX executable", "*.exe *.upx"), ("All files", "*.*")] if os.name == "nt" else [("All files", "*")]
        path = self.tk.filedialog.askopenfilename(title="Select UPX executable", initialdir=initial_dir, filetypes=filetypes)
        if path:
            self.upx_binary.set(path)

    def append_output(self, text: str) -> None:
        self.output.configure(state="normal")
        self.output.insert("end", text)
        self.output.see("end")
        self.output.configure(state="disabled")

    def start_build(self) -> None:
        if self.process is not None:
            return
        if current_host() != EXPECTED_PLATFORM:
            self.messagebox.showerror("Cannot build", f"This launcher only builds {PLATFORM_LABEL} on {PLATFORM_LABEL}.")
            return
        try:
            command = build_command(
                self.profile.get(),
                self.mode.get(),
                int(self.jobs.get()),
                install_dependencies=bool(self.install_dependencies.get()),
                windows_console_mode=self.console_mode.get(),
                upx=bool(self.upx_enabled.get()),
                upx_binary=self.upx_binary.get().strip(),
            )
        except (TypeError, ValueError) as exc:
            self.messagebox.showerror("Invalid options", str(exc))
            return

        self.append_output("\n$ " + subprocess.list2cmdline(command) + "\n\n")
        creationflags = 0
        popen_kwargs: dict[str, object] = {}
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True
        try:
            self.process = subprocess.Popen(
                command,
                cwd=PROJECT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
                **popen_kwargs,
            )
        except Exception as exc:
            self.process = None
            self.messagebox.showerror("Build failed to start", str(exc))
            return

        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("Building...")
        threading.Thread(target=self.read_output, daemon=True).start()

    def read_output(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                self.output_queue.put(line)
            return_code = process.wait()
            self.output_queue.put(f"\nBuild finished with exit code {return_code}.\n")
        except Exception as exc:
            self.output_queue.put(f"\nOutput reader error: {exc}\n")
        finally:
            self.output_queue.put(None)

    def poll_output(self) -> None:
        while True:
            try:
                item = self.output_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                process = self.process
                code = None if process is None else process.poll()
                self.process = None
                self.start_button.configure(state="normal" if current_host() == EXPECTED_PLATFORM else "disabled")
                self.stop_button.configure(state="disabled")
                self.status.set("Build completed" if code == 0 else f"Build stopped or failed (exit {code})")
            else:
                self.append_output(item)
        self.root.after(80, self.poll_output)

    def stop_build(self) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        self.status.set("Stopping build...")
        try:
            if os.name == "nt":
                process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass

    def on_close(self) -> None:
        if self.process is not None and self.process.poll() is None:
            if not self.messagebox.askyesno("Build is running", "Stop the current build and close?"):
                return
            self.stop_build()
        self.root.destroy()


def main() -> int:
    if not BUILD_SCRIPT.is_file():
        print(f"Missing build script: {BUILD_SCRIPT}", file=sys.stderr)
        return 2
    try:
        import tkinter as tk
        from tkinter import messagebox, scrolledtext, ttk
    except ImportError as exc:
        print("Tkinter is required for the build GUI. Install the platform's Python Tk package.", file=sys.stderr)
        print(exc, file=sys.stderr)
        return 2
    root = tk.Tk()
    BuildLauncher(root, tk, ttk, scrolledtext, messagebox)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
