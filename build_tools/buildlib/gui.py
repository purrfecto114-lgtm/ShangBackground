"""Tk build launcher shared by ``build_gui.py`` and ``build.py --gui``."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
from typing import Any

from .constants import (
    PROJECT_ROOT, PYINSTALLER_CONTENTS_DIRECTORY, TARGETS, TOOLS, host_target, normalize_arch,
)
from .features import FEATURES, FEATURE_KEYS, default_features
from .runner import display_command

_LOG_LINE_LIMIT = 20_000
_LOG_TRIM_TO = 15_000


def _command(values: dict[str, object]) -> list[str]:
    """Translate GUI values to the public build CLI without hidden behavior."""
    command = [
        sys.executable,
        os.fspath(PROJECT_ROOT / "build_tools" / "build.py"),
        "--tool",
        str(values["tool"]),
        "--target",
        str(values["target"]),
        "--profile",
        str(values["profile"]),
        "--mode",
        str(values["mode"]),
        "--jobs",
        str(values["jobs"]),
        "--mpv-runtime",
        str(values["mpv_runtime"]),
        "--mpv-version",
        "auto",
        "--mpv-arch",
        str(values["arch"]),
        "--features",
        str(values["features"]),
    ]
    if str(values["tool"]) == "pyinstaller":
        command.extend(("--contents-directory", PYINSTALLER_CONTENTS_DIRECTORY))
    if values.get("skip_install"):
        command.append("--skip-install")
    if values.get("verbose_install"):
        command.append("--verbose-install")
    if values.get("dry_run"):
        command.append("--dry-run")
    return command


def _open_path(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", os.fspath(path)])
    else:
        subprocess.Popen(["xdg-open", os.fspath(path)])


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        process.terminate()


@dataclass(slots=True)
class _ProcessState:
    process: subprocess.Popen[str] | None = None
    stopping: bool = False


class BuildGuiApp:
    """Responsive launcher that delegates every build to the public CLI."""

    def __init__(self, root: Any) -> None:
        import tkinter as tk
        from tkinter import scrolledtext, ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self._scrolledtext = scrolledtext
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._state = _ProcessState()
        self._writes = 0
        self._advanced_visible = False

        self.root.title("ShangBackground Build Studio")
        self.root.geometry("1080x760")
        self.root.minsize(860, 620)
        self.root.option_add("*tearOff", False)
        self._configure_style()
        self._create_variables()
        self._build_layout()
        self._bind_events()
        self._sync_profile()
        self._sync_tool()
        self._refresh_preview()
        self.root.after(80, self._poll)

    def _configure_style(self) -> None:
        style = self.ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except self.tk.TclError:
            pass
        background = "#f4f6f9"
        surface = "#ffffff"
        accent = "#2563eb"
        text = "#172033"
        muted = "#5f6b7a"
        self.root.configure(background=background)
        style.configure("App.TFrame", background=background)
        style.configure("Surface.TFrame", background=surface)
        style.configure("Header.TFrame", background="#172033")
        style.configure("HeaderTitle.TLabel", background="#172033", foreground="#ffffff", font=("TkDefaultFont", 18, "bold"))
        style.configure("HeaderSub.TLabel", background="#172033", foreground="#cbd5e1", font=("TkDefaultFont", 10))
        style.configure("Card.TLabelframe", background=surface, borderwidth=1, relief="solid")
        style.configure("Card.TLabelframe.Label", background=surface, foreground=text, font=("TkDefaultFont", 10, "bold"))
        style.configure("Card.TLabel", background=surface, foreground=text)
        style.configure("Muted.TLabel", background=surface, foreground=muted)
        style.configure("Status.TLabel", background=background, foreground=muted)
        style.configure("Primary.TButton", font=("TkDefaultFont", 10, "bold"), padding=(18, 8), foreground="#ffffff", background=accent)
        style.map("Primary.TButton", background=[("active", "#1d4ed8"), ("disabled", "#9eb6e8")])
        style.configure("Action.TButton", padding=(12, 7))
        style.configure("Danger.TButton", padding=(12, 7))
        style.configure("Feature.TCheckbutton", background=surface, foreground=text)
        style.map("Feature.TCheckbutton", background=[("active", surface)])

    def _create_variables(self) -> None:
        tk = self.tk
        self.variables: dict[str, Any] = {
            "tool": tk.StringVar(value="pyinstaller"),
            "target": tk.StringVar(value=host_target()),
            "profile": tk.StringVar(value="full"),
            "mode": tk.StringVar(value="standalone"),
            "jobs": tk.StringVar(value="2"),
            "mpv_runtime": tk.StringVar(value="auto"),
            "arch": tk.StringVar(value=normalize_arch()),
            "skip_install": tk.BooleanVar(value=False),
            "verbose_install": tk.BooleanVar(value=False),
            "dry_run": tk.BooleanVar(value=False),
        }
        self.feature_vars = {
            key: tk.BooleanVar(value=key in default_features("full")) for key in FEATURE_KEYS
        }
        self.status = tk.StringVar(value="Ready")
        self.command_preview = tk.StringVar(value="")

    def _build_layout(self) -> None:
        ttk = self.ttk
        outer = ttk.Frame(self.root, style="App.TFrame")
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer, style="Header.TFrame", padding=(22, 16))
        header.pack(fill="x")
        ttk.Label(header, text="ShangBackground Build Studio", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Native HTML WebView · PyInstaller _internal · standalone-first release builds",
            style="HeaderSub.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        content = ttk.Frame(outer, style="App.TFrame", padding=(16, 14, 16, 12))
        content.pack(fill="both", expand=True)

        config = ttk.LabelFrame(content, text="Build configuration", style="Card.TLabelframe", padding=12)
        config.pack(fill="x")
        for column in range(5):
            config.columnconfigure(column, weight=1)

        host = host_target()
        mode_choices = ("standalone",) if host == "macos" else ("standalone", "onefile")
        fields = (
            ("Builder", "tool", TOOLS),
            ("Target", "target", TARGETS),
            ("Profile", "profile", ("full", "lite")),
            ("Output mode", "mode", mode_choices),
            ("Compiler jobs", "jobs", ("1", "2", "4")),
        )
        self.field_boxes: dict[str, Any] = {}
        for column, (label, key, choices) in enumerate(fields):
            ttk.Label(config, text=label, style="Muted.TLabel").grid(row=0, column=column, sticky="w", padx=(0, 10))
            box = ttk.Combobox(
                config,
                textvariable=self.variables[key],
                values=choices,
                state="readonly",
                width=15,
            )
            box.grid(row=1, column=column, sticky="ew", padx=(0, 10), pady=(4, 0))
            self.field_boxes[key] = box
        self.field_boxes["target"].configure(state="disabled")

        feature_card = ttk.LabelFrame(content, text="Included features", style="Card.TLabelframe", padding=12)
        feature_card.pack(fill="x", pady=(10, 0))
        for column in range(3):
            feature_card.columnconfigure(column, weight=1)
        for index, item in enumerate(FEATURES):
            check = ttk.Checkbutton(
                feature_card,
                text=item.label,
                variable=self.feature_vars[item.key],
                style="Feature.TCheckbutton",
                command=self._refresh_preview,
            )
            check.grid(row=index // 3, column=index % 3, sticky="w", padx=(0, 12), pady=3)

        advanced_header = ttk.Frame(content, style="App.TFrame")
        advanced_header.pack(fill="x", pady=(10, 0))
        self.advanced_toggle = ttk.Button(
            advanced_header,
            text="Show advanced options",
            style="Action.TButton",
            command=self._toggle_advanced,
        )
        self.advanced_toggle.pack(side="left")
        ttk.Label(
            advanced_header,
            text="Runtime selection and diagnostic controls",
            style="Status.TLabel",
        ).pack(side="left", padx=10)

        self.advanced = ttk.LabelFrame(content, text="Advanced options", style="Card.TLabelframe", padding=12)
        for column in range(6):
            self.advanced.columnconfigure(column, weight=1 if column in {1, 3} else 0)
        arch_choices = ("x86_64", "arm64") if host == "macos" else ("x86_64", "arm64", "x86")
        mpv_choices = ("auto", "system") if host == "macos" else ("auto", "bundled", "system")
        ttk.Label(self.advanced, text="MPV runtime", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            self.advanced,
            textvariable=self.variables["mpv_runtime"],
            values=mpv_choices,
            state="readonly",
            width=14,
        ).grid(row=0, column=1, sticky="ew", padx=(6, 18))
        ttk.Label(self.advanced, text="Architecture", style="Muted.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Combobox(
            self.advanced,
            textvariable=self.variables["arch"],
            values=arch_choices,
            state="readonly",
            width=14,
        ).grid(row=0, column=3, sticky="ew", padx=(6, 18))
        ttk.Checkbutton(
            self.advanced,
            text="Skip dependency install",
            variable=self.variables["skip_install"],
            command=self._refresh_preview,
        ).grid(row=0, column=4, sticky="w", padx=(0, 12))
        ttk.Checkbutton(
            self.advanced,
            text="Full pip log",
            variable=self.variables["verbose_install"],
            command=self._refresh_preview,
        ).grid(row=0, column=5, sticky="w")
        ttk.Checkbutton(
            self.advanced,
            text="Dry-run only",
            variable=self.variables["dry_run"],
            command=self._refresh_preview,
        ).grid(row=1, column=4, sticky="w", pady=(8, 0))

        self.preview_card = ttk.LabelFrame(
            content, text="Command preview", style="Card.TLabelframe", padding=(12, 8)
        )
        self.preview_card.pack(fill="x", pady=(10, 0))
        entry = self.ttk.Entry(self.preview_card, textvariable=self.command_preview, state="readonly")
        entry.pack(fill="x")

        actions = ttk.Frame(content, style="App.TFrame")
        actions.pack(fill="x", pady=(10, 8))
        self.start_button = ttk.Button(actions, text="Start build", style="Primary.TButton", command=self._start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="Stop", style="Danger.TButton", state="disabled", command=self._stop)
        self.stop_button.pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Open output", style="Action.TButton", command=self._open_output).pack(side="left", padx=(18, 0))
        ttk.Button(actions, text="Open logs", style="Action.TButton", command=self._open_logs).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Clear log", style="Action.TButton", command=self._clear_log).pack(side="left", padx=(8, 0))
        ttk.Label(actions, textvariable=self.status, style="Status.TLabel").pack(side="right")

        log_card = ttk.LabelFrame(content, text="Build output", style="Card.TLabelframe", padding=8)
        log_card.pack(fill="both", expand=True)
        self.output = self._scrolledtext.ScrolledText(
            log_card,
            wrap="word",
            font=("TkFixedFont", 9),
            background="#111827",
            foreground="#dbeafe",
            insertbackground="#ffffff",
            selectbackground="#334155",
            relief="flat",
            padx=10,
            pady=8,
        )
        self.output.pack(fill="both", expand=True)

    def _bind_events(self) -> None:
        for key, variable in self.variables.items():
            if key in {"skip_install", "verbose_install", "dry_run"}:
                continue
            variable.trace_add("write", self._on_value_changed)
        self.variables["profile"].trace_add("write", self._sync_profile)
        self.variables["tool"].trace_add("write", self._sync_tool)
        self.root.bind("<Control-Return>", lambda _event: self._start())
        self.root.bind("<Escape>", lambda _event: self._stop())
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _on_value_changed(self, *_args: object) -> None:
        self._refresh_preview()

    def _selected_values(self) -> dict[str, object]:
        values = {key: variable.get() for key, variable in self.variables.items()}
        values["features"] = ",".join(
            key for key in FEATURE_KEYS if self.feature_vars[key].get()
        ) or "none"
        return values

    def _refresh_preview(self) -> None:
        try:
            self.command_preview.set(display_command(_command(self._selected_values())))
        except Exception as exc:
            self.command_preview.set(f"Unable to build command: {exc}")

    def _toggle_advanced(self) -> None:
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            self.advanced.pack(fill="x", pady=(8, 0), before=self.preview_card)
            self.advanced_toggle.configure(text="Hide advanced options")
        else:
            self.advanced.pack_forget()
            self.advanced_toggle.configure(text="Show advanced options")

    def _append(self, text: str) -> None:
        self.output.insert("end", text)
        self._writes += 1
        if self._writes % 200 == 0:
            try:
                line_count = int(self.output.index("end-1c").split(".", 1)[0])
                if line_count > _LOG_LINE_LIMIT:
                    self.output.delete("1.0", f"{line_count - _LOG_TRIM_TO}.0")
            except (ValueError, self.tk.TclError):
                pass
        self.output.see("end")

    def _worker(self, command: list[str]) -> None:
        code = 1
        try:
            kwargs: dict[str, object] = {
                "cwd": PROJECT_ROOT,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "bufsize": 1,
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                kwargs["start_new_session"] = True
            process = subprocess.Popen(command, **kwargs)  # type: ignore[arg-type]
            self._state.process = process
            if process.stdout is None:
                raise RuntimeError("build process stdout pipe was not created")
            for line in process.stdout:
                self._events.put(("line", line))
            code = process.wait()
        except Exception as exc:
            self._events.put(("line", f"\nBuild launcher error: {type(exc).__name__}: {exc}\n"))
        finally:
            self._state.process = None
            self._state.stopping = False
            self._events.put(("done", code))

    def _start(self) -> None:
        if self._state.process is not None or str(self.start_button.cget("state")) == "disabled":
            return
        command = _command(self._selected_values())
        self._append("\n" + "─" * 76 + "\n")
        self._append("$ " + display_command(command) + "\n\n")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("Starting…")
        threading.Thread(target=self._worker, args=(command,), daemon=True).start()

    def _stop(self) -> None:
        process = self._state.process
        if process is None or self._state.stopping:
            return
        self._state.stopping = True
        self.status.set("Stopping process tree…")
        _terminate_process_tree(process)

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "line":
                    text = str(payload)
                    self._append(text)
                    stripped = text.strip()
                    if stripped.startswith("◆ ") or stripped.startswith("> "):
                        self.status.set(stripped[2:])
                    elif stripped.startswith("[stage]"):
                        self.status.set(stripped.removeprefix("[stage]").strip())
                elif kind == "done":
                    code = payload if isinstance(payload, int) else int(str(payload))
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.status.set("Build completed" if code == 0 else f"Build failed · exit {code}")
                    self._append(f"\nCommand finished with exit code {code}.\n")
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _sync_profile(self, *_args: object) -> None:
        defaults = default_features(self.variables["profile"].get())
        for key, variable in self.feature_vars.items():
            variable.set(key in defaults)
        self._refresh_preview()

    def _sync_tool(self, *_args: object) -> None:
        jobs_box = self.field_boxes.get("jobs")
        if jobs_box is not None:
            state = "readonly" if self.variables["tool"].get() == "nuitka" else "disabled"
            jobs_box.configure(state=state)
        self._refresh_preview()

    def _open_output(self) -> None:
        self._open_directory(PROJECT_ROOT / f"dist-{self.variables['tool'].get()}" / self.variables["target"].get())

    def _open_logs(self) -> None:
        self._open_directory(PROJECT_ROOT / "build-logs" / self.variables["target"].get())

    def _open_directory(self, path: Path) -> None:
        try:
            _open_path(path)
        except OSError as exc:
            self.status.set(f"Unable to open directory: {exc}")

    def _clear_log(self) -> None:
        self.output.delete("1.0", "end")
        self._writes = 0

    def _close(self) -> None:
        process = self._state.process
        if process is not None:
            _terminate_process_tree(process)
        self.root.destroy()


def create_app(root: Any) -> BuildGuiApp:
    """Create the GUI without entering the event loop; useful for smoke tests."""
    return BuildGuiApp(root)


def main() -> int:
    import tkinter as tk

    root = tk.Tk()
    create_app(root)
    root.mainloop()
    return 0
