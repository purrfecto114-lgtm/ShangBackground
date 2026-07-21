"""Tk build launcher shared by ``build_gui.py``, ``build_gui.pyw``, and ``build.py --gui``.

Refactored into independent, composable components:
- **Panels**: ConfigPanel, FeaturePanel, AdvancedPanel, CommandPreview, LogPanel, ActionBar
- **Services**: BuildWorker, PresetManager, ThemeManager, ToolTip
- **State**: AppState — a central observable dataclass that decouples widgets from each other.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
from tkinter import messagebox
from typing import Any

from .constants import (
    ARCHES,
    MODES,
    MPV_MODES,
    PROFILES,
    PROJECT_ROOT,
    PYINSTALLER_CONTENTS_DIRECTORY,
    TARGETS,
    TOOLS,
    host_target,
    normalize_arch,
    python_executable,
)
from .features import FEATURES, FEATURE_KEYS, default_features
from .runner import display_command

# ── Constants ──────────────────────────────────────────────────────────────────

_LOG_LINE_LIMIT = 20_000
_LOG_TRIM_TO = 15_000
_PRESET_DIR = PROJECT_ROOT / "build_tools" / "presets"
_HISTORY_FILE = PROJECT_ROOT / "build-logs" / ".build_history.json"
_MAX_HISTORY = 20

# ── Helpers ────────────────────────────────────────────────────────────────────


def _command(values: dict[str, object]) -> list[str]:
    """Translate GUI values to the public build CLI."""
    command = [
        python_executable(),
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
        "--arch",
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
    if not path.is_dir():
        raise FileNotFoundError(f"Directory does not exist yet: {path}")
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", os.fspath(path)])
    else:
        subprocess.Popen(["xdg-open", os.fspath(path)])


def _terminate_process_tree(process: subprocess.Popen[str], *, grace_seconds: float = 2.0) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        try:
            process.wait(timeout=max(grace_seconds, 1.0))
        except (subprocess.TimeoutExpired, OSError):
            process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        process.terminate()
    try:
        process.wait(timeout=grace_seconds)
        return
    except (subprocess.TimeoutExpired, OSError):
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        process.kill()
    try:
        process.wait(timeout=2)
    except (subprocess.TimeoutExpired, OSError):
        pass


# ── ToolTip ────────────────────────────────────────────────────────────────────


class ToolTip:
    """Lightweight tooltip for ttk widgets."""

    def __init__(self, widget: Any, text: str, *, delay_ms: int = 500) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._tip: Any = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._hide)
        widget.bind("<ButtonPress>", self._hide)

    def _schedule(self, _event: object) -> None:
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _show(self) -> None:
        if self._tip is not None:
            return
        import tkinter as tk

        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        x = self.widget.winfo_rootx() + 6
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            self._tip,
            text=self.text,
            justify="left",
            background="#ffffe0",
            foreground="#333333",
            relief="solid",
            borderwidth=1,
            font=("TkDefaultFont", 9),
            wraplength=320,
            padx=6,
            pady=3,
        )
        label.pack()

    def _hide(self, _event: object = None) -> None:
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


# ── ThemeManager ───────────────────────────────────────────────────────────────


@dataclass(slots=True)
class ThemeColors:
    name: str
    background: str
    surface: str
    header_bg: str
    header_title: str
    header_sub: str
    accent: str
    accent_hover: str
    accent_disabled: str
    text: str
    muted: str
    log_bg: str
    log_fg: str
    log_select: str


LIGHT_THEME = ThemeColors(
    name="light",
    background="#f4f6f9",
    surface="#ffffff",
    header_bg="#172033",
    header_title="#ffffff",
    header_sub="#cbd5e1",
    accent="#2563eb",
    accent_hover="#1d4ed8",
    accent_disabled="#9eb6e8",
    text="#172033",
    muted="#5f6b7a",
    log_bg="#111827",
    log_fg="#dbeafe",
    log_select="#334155",
)

DARK_THEME = ThemeColors(
    name="dark",
    background="#0f172a",
    surface="#1e293b",
    header_bg="#0b1324",
    header_title="#e2e8f0",
    header_sub="#94a3b8",
    accent="#3b82f6",
    accent_hover="#2563eb",
    accent_disabled="#1e3a5f",
    text="#e2e8f0",
    muted="#94a3b8",
    log_bg="#020617",
    log_fg="#93c5fd",
    log_select="#1e293b",
)


class ThemeManager:
    """Apply a ThemeColors palette to the ttk style and root background."""

    def __init__(self, root: Any, ttk: Any) -> None:
        self.root = root
        self.ttk = ttk
        self.style = ttk.Style(root)
        self._current: ThemeColors | None = None

    def apply(self, theme: ThemeColors) -> None:
        self._current = theme
        try:
            self.style.theme_use("clam")
        except Exception:
            pass
        self.root.configure(background=theme.background)

        self.style.configure("App.TFrame", background=theme.background)
        self.style.configure("Surface.TFrame", background=theme.surface)
        self.style.configure("Header.TFrame", background=theme.header_bg)
        self.style.configure(
            "HeaderTitle.TLabel",
            background=theme.header_bg,
            foreground=theme.header_title,
            font=("TkDefaultFont", 18, "bold"),
        )
        self.style.configure(
            "HeaderSub.TLabel",
            background=theme.header_bg,
            foreground=theme.header_sub,
            font=("TkDefaultFont", 10),
        )
        self.style.configure(
            "Card.TLabelframe",
            background=theme.surface,
            borderwidth=1,
            relief="solid",
        )
        self.style.configure(
            "Card.TLabelframe.Label",
            background=theme.surface,
            foreground=theme.text,
            font=("TkDefaultFont", 10, "bold"),
        )
        self.style.configure("Card.TLabel", background=theme.surface, foreground=theme.text)
        self.style.configure("Muted.TLabel", background=theme.surface, foreground=theme.muted)
        self.style.configure("Status.TLabel", background=theme.background, foreground=theme.muted)
        self.style.configure(
            "Primary.TButton",
            font=("TkDefaultFont", 10, "bold"),
            padding=(18, 8),
            foreground=theme.header_title,
            background=theme.accent,
        )
        self.style.map(
            "Primary.TButton",
            background=[
                ("active", theme.accent_hover),
                ("disabled", theme.accent_disabled),
            ],
        )
        self.style.configure("Action.TButton", padding=(12, 7))
        self.style.configure("Danger.TButton", padding=(12, 7))
        self.style.configure("Feature.TCheckbutton", background=theme.surface, foreground=theme.text)
        self.style.map("Feature.TCheckbutton", background=[("active", theme.surface)])
        self.style.configure("Small.TButton", padding=(4, 2))

    @property
    def colors(self) -> ThemeColors:
        assert self._current is not None
        return self._current


# ── AppState ───────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class AppState:
    """Central observable state. Widgets bind to on_change callbacks."""

    tool: str = "pyinstaller"
    target: str = field(default_factory=host_target)
    profile: str = "full"
    mode: str = "standalone"
    jobs: str = "2"
    mpv_runtime: str = "auto"
    arch: str = field(default_factory=normalize_arch)
    skip_install: bool = False
    verbose_install: bool = False
    dry_run: bool = False
    features: dict[str, bool] = field(default_factory=dict)
    status: str = "Ready"
    command_preview: str = ""
    theme_dark: bool = False

    _callbacks: dict[str, list[Callable[[AppState], None]]] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.features = {key: key in default_features(self.profile) for key in FEATURE_KEYS}

    def on_change(self, field_name: str, callback: Callable[[AppState], None]) -> None:
        self._callbacks.setdefault(field_name, []).append(callback)

    def set(self, **kwargs: object) -> None:
        changed: set[str] = set()
        for key, value in kwargs.items():
            if getattr(self, key) != value:
                object.__setattr__(self, key, value)
                changed.add(key)
        for field_name in changed:
            for cb in self._callbacks.get(field_name, ()):
                cb(self)

    def feature_string(self) -> str:
        return ",".join(k for k in FEATURE_KEYS if self.features.get(k, False)) or "none"

    def snapshot(self) -> dict[str, object]:
        """Serialize state suitable for preset save."""
        return {
            "tool": self.tool,
            "target": self.target,
            "profile": self.profile,
            "mode": self.mode,
            "jobs": self.jobs,
            "mpv_runtime": self.mpv_runtime,
            "arch": self.arch,
            "skip_install": self.skip_install,
            "verbose_install": self.verbose_install,
            "dry_run": self.dry_run,
            "features": {k: v for k, v in self.features.items()},
            "theme_dark": self.theme_dark,
        }

    def restore_snapshot(self, data: dict[str, object]) -> None:
        if not isinstance(data, dict):
            raise ValueError("Preset root must be a JSON object.")
        updates: dict[str, object] = {}
        choices: dict[str, tuple[str, ...]] = {
            "tool": TOOLS,
            "target": TARGETS,
            "profile": PROFILES,
            "mode": MODES,
            "jobs": ("1", "2", "4"),
            "mpv_runtime": MPV_MODES,
            "arch": ARCHES,
        }
        for key, allowed in choices.items():
            if key not in data:
                continue
            value = data[key]
            if not isinstance(value, str) or value not in allowed:
                raise ValueError(f"Preset field {key!r} has an unsupported value: {value!r}")
            updates[key] = value
        for key in ("skip_install", "verbose_install", "dry_run", "theme_dark"):
            if key not in data:
                continue
            value = data[key]
            if not isinstance(value, bool):
                raise ValueError(f"Preset field {key!r} must be a boolean")
            updates[key] = value
        if "features" in data:
            value = data["features"]
            if not isinstance(value, dict):
                raise ValueError("Preset field 'features' must be a JSON object")
            unknown = sorted(set(map(str, value)) - set(FEATURE_KEYS))
            if unknown:
                raise ValueError("Preset contains unknown feature keys: " + ", ".join(unknown))
            if any(not isinstance(enabled, bool) for enabled in value.values()):
                raise ValueError("Preset feature values must be booleans")
            updates["features"] = {key: bool(value.get(key, False)) for key in FEATURE_KEYS}
        if updates:
            self.set(**updates)


# ── PresetManager ──────────────────────────────────────────────────────────────


@dataclass(slots=True)
class PresetManager:
    directory: Path = _PRESET_DIR

    def list_presets(self) -> list[str]:
        self.directory.mkdir(parents=True, exist_ok=True)
        return sorted(p.stem for p in self.directory.glob("*.json") if p.is_file())

    @staticmethod
    def _safe_name(name: str) -> str:
        value = str(name).strip()
        if not value or value in {".", ".."} or Path(value).name != value:
            raise ValueError("Preset name must be a single file-safe name.")
        if any(character in value for character in '<>:"/\\|?*'):
            raise ValueError("Preset name contains characters that are not allowed in filenames.")
        return value

    def save(self, name: str, data: dict[str, object]) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{self._safe_name(name)}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        return path

    def load(self, name: str) -> dict[str, object]:
        path = self.directory / f"{self._safe_name(name)}.json"
        if not path.is_file():
            raise FileNotFoundError(f"Preset not found: {name}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Preset root must be a JSON object: {name}")
        return dict(payload)

    def delete(self, name: str) -> None:
        path = self.directory / f"{self._safe_name(name)}.json"
        if path.is_file():
            path.unlink()


# ── BuildHistory ───────────────────────────────────────────────────────────────


@dataclass(slots=True)
class BuildHistory:
    path: Path = _HISTORY_FILE

    def entries(self) -> list[dict[str, object]]:
        if not self.path.is_file():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(payload, list):
            return []
        return [dict(item) for item in payload if isinstance(item, dict)]

    def record(self, entry: dict[str, object]) -> None:
        records = self.entries()
        records.insert(0, dict(entry))
        if len(records) > _MAX_HISTORY:
            records = records[:_MAX_HISTORY]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


# ── BuildWorker ────────────────────────────────────────────────────────────────


class BuildWorker:
    """Manages a background build subprocess with streamed output via a queue."""

    def __init__(self, events: queue.Queue[tuple[str, object]]) -> None:
        self._events = events
        self._process: subprocess.Popen[str] | None = None
        self._stopping = False
        self._launching = False
        self._state_lock = threading.Lock()
        self._started_at: float | None = None

    @property
    def running(self) -> bool:
        with self._state_lock:
            process = self._process
            return self._launching or (process is not None and process.poll() is None)

    @property
    def elapsed(self) -> float:
        if self._started_at is None:
            return 0.0
        return time.monotonic() - self._started_at

    def start(self, command: list[str]) -> None:
        with self._state_lock:
            process = self._process
            if self._launching or (process is not None and process.poll() is None):
                raise RuntimeError("A build is already in progress.")
            self._launching = True
            self._stopping = False
        self._events.put(("status", "Starting build…"))

        def _run() -> None:
            code = 1
            process: subprocess.Popen[str] | None = None
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
                self._started_at = time.monotonic()
                process = subprocess.Popen(command, **kwargs)  # type: ignore[arg-type]
                with self._state_lock:
                    self._process = process
                    self._launching = False
                    should_stop = self._stopping
                if should_stop:
                    _terminate_process_tree(process)
                if process.stdout is None:
                    raise RuntimeError("build process stdout pipe was not created")
                try:
                    for line in process.stdout:
                        self._events.put(("line", line))
                except (OSError, ValueError) as exc:
                    self._events.put(("line", f"\nI/O error reading build output: {exc}\n"))
                code = process.wait()
            except Exception as exc:
                self._events.put(("line", f"\nBuild launcher error: {type(exc).__name__}: {exc}\n"))
                if process is not None and process.poll() is None:
                    _terminate_process_tree(process)
                    try:
                        code = process.wait(timeout=5)
                    except (subprocess.TimeoutExpired, OSError):
                        code = -1
            finally:
                with self._state_lock:
                    self._process = None
                    self._launching = False
                    self._stopping = False
                self._events.put(("done", code))

        threading.Thread(target=_run, daemon=True).start()

    def stop(self) -> bool:
        """Request process-tree termination, including the launch race window."""
        with self._state_lock:
            if self._stopping:
                return False
            process = self._process
            if not self._launching and (process is None or process.poll() is not None):
                return False
            self._stopping = True
        if process is not None:
            _terminate_process_tree(process)
        return True


# ── ConfigPanel ────────────────────────────────────────────────────────────────


class ConfigPanel:
    """Build configuration: tool, target, profile, mode, jobs."""

    def __init__(self, parent: Any, state: AppState, ttk: Any, tk: Any, theme: ThemeManager) -> None:
        self.state = state
        self.ttk = ttk
        self.tk = tk
        self.theme = theme
        self._boxes: dict[str, Any] = {}
        self._suppress = False
        self._card: Any = None
        self._build(parent)

    def _build(self, parent: Any) -> None:
        card = self.ttk.LabelFrame(parent, text="Build configuration", style="Card.TLabelframe", padding=12)
        card.pack(fill="x")
        self._card = card
        for column in range(5):
            card.columnconfigure(column, weight=1)

        host = host_target()
        mode_choices = ("standalone",) if host == "macos" else ("standalone", "onefile")
        fields = (
            ("Builder", "tool", TOOLS, "Selects the packaging backend for the build."),
            ("Target", "target", TARGETS, "The operating system the build is compiled for."),
            ("Profile", "profile", ("full", "lite"), "Full: all features. Lite: core features only."),
            ("Output mode", "mode", mode_choices, "Standalone: directory with _internal. Onefile: single executable."),
            ("Compiler jobs", "jobs", ("1", "2", "4"), "Number of parallel compilation jobs (Nuitka only)."),
        )
        for column, (label, key, choices, tip) in enumerate(fields):
            lbl = self.ttk.Label(card, text=label, style="Muted.TLabel")
            lbl.grid(row=0, column=column, sticky="w", padx=(0, 10))
            box = self.ttk.Combobox(
                card,
                values=choices,
                state="readonly",
                width=15,
            )
            box.grid(row=1, column=column, sticky="ew", padx=(0, 10), pady=(4, 0))
            box.bind("<<ComboboxSelected>>", lambda _e, k=key, b=box: self._on_select(k, b))
            ToolTip(box, tip)
            self._boxes[key] = box
        self._boxes["target"].configure(state="disabled")
        self._sync()

    def _on_select(self, key: str, box: Any) -> None:
        if self._suppress:
            return
        self.state.set(**{key: box.get()})

    def _sync(self) -> None:
        """Re-read state and update widgets, suppressing feedback loops."""
        self._suppress = True
        try:
            for key, box in self._boxes.items():
                val = getattr(self.state, key, "")
                if box.get() != str(val):
                    box.set(str(val))
            jobs_box = self._boxes.get("jobs")
            if jobs_box is not None:
                jobs_box.configure(state="readonly" if self.state.tool == "nuitka" else "disabled")
        finally:
            self._suppress = False

    def refresh(self) -> None:
        self._sync()

    def bind_state(self) -> None:
        self.state.on_change("tool", lambda _s: self.refresh())


# ── FeaturePanel ───────────────────────────────────────────────────────────────


class FeaturePanel:
    """Checkbox grid for feature selection."""

    def __init__(self, parent: Any, state: AppState, ttk: Any, tk: Any, theme: ThemeManager) -> None:
        self.state = state
        self.ttk = ttk
        self.tk = tk
        self.theme = theme
        self._vars: dict[str, Any] = {}
        self._suppress = False
        self._build(parent)

    def _build(self, parent: Any) -> None:
        card = self.ttk.LabelFrame(parent, text="Included features", style="Card.TLabelframe", padding=12)
        card.pack(fill="x", pady=(10, 0))
        for column in range(3):
            card.columnconfigure(column, weight=1)
        tips: dict[str, str] = {
            "video": "Enable video wallpaper via libmpv native player.",
            "html": "Enable HTML wallpaper via the operating-system native WebView.",
            "bing": "Include Bing wallpaper service integration.",
            "hotkeys": "Global keyboard shortcuts for wallpaper control.",
            "updates": "Periodic update checks.",
            "fonts": "Bundle application fonts.",
        }
        for index, item in enumerate(FEATURES):
            var = self.tk.BooleanVar(value=self.state.features.get(item.key, False))
            self._vars[item.key] = var
            check = self.ttk.Checkbutton(
                card,
                text=item.label,
                variable=var,
                style="Feature.TCheckbutton",
                command=lambda k=item.key: self._toggle(k),
            )
            check.grid(row=index // 3, column=index % 3, sticky="w", padx=(0, 12), pady=3)
            ToolTip(check, tips.get(item.key, ""))

    def _toggle(self, key: str) -> None:
        if self._suppress:
            return
        features = dict(self.state.features)
        features[key] = bool(self._vars[key].get())
        self.state.set(features=features)

    def _sync(self) -> None:
        self._suppress = True
        try:
            for key, var in self._vars.items():
                var.set(self.state.features.get(key, False))
        finally:
            self._suppress = False

    def refresh(self) -> None:
        self._sync()

    def bind_state(self) -> None:
        self.state.on_change("features", lambda _s: self.refresh())
        self.state.on_change("profile", lambda _s: self._on_profile_changed())

    def _on_profile_changed(self) -> None:
        defaults = default_features(self.state.profile)
        features = {key: key in defaults for key in FEATURE_KEYS}
        self.state.set(features=features)


# ── AdvancedPanel ──────────────────────────────────────────────────────────────


class AdvancedPanel:
    """MPV runtime, architecture, and diagnostic flags."""

    def __init__(self, parent: Any, state: AppState, ttk: Any, tk: Any, theme: ThemeManager) -> None:
        self.state = state
        self.ttk = ttk
        self.tk = tk
        self.theme = theme
        self._widgets: dict[str, Any] = {}
        self._suppress = False
        self._card: Any = None
        self._build(parent)

    def _build(self, parent: Any) -> None:
        host = host_target()
        arch_choices = ("x86_64", "arm64") if host == "macos" else ("x86_64", "arm64", "x86")
        mpv_choices = ("auto", "system") if host == "macos" else ("auto", "bundled", "system")

        self._card = self.ttk.LabelFrame(parent, text="Advanced options", style="Card.TLabelframe", padding=12)
        for column in range(6):
            self._card.columnconfigure(column, weight=1 if column in {1, 3} else 0)

        self.ttk.Label(self._card, text="MPV runtime", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        mpv_box = self.ttk.Combobox(self._card, values=mpv_choices, state="readonly", width=14)
        mpv_box.grid(row=0, column=1, sticky="ew", padx=(6, 18))
        mpv_box.bind("<<ComboboxSelected>>", lambda _e: self.state.set(mpv_runtime=mpv_box.get()))
        ToolTip(mpv_box, "How libmpv is sourced: auto-discovers, bundled with the app, or from the system.")
        self._widgets["mpv_runtime"] = mpv_box

        self.ttk.Label(self._card, text="Architecture", style="Muted.TLabel").grid(row=0, column=2, sticky="w")
        arch_box = self.ttk.Combobox(self._card, values=arch_choices, state="readonly", width=14)
        arch_box.grid(row=0, column=3, sticky="ew", padx=(6, 18))
        arch_box.bind("<<ComboboxSelected>>", lambda _e: self.state.set(arch=arch_box.get()))
        ToolTip(
            arch_box,
            "Output CPU architecture. It must match the Python interpreter used for the build; bundled MPV must match it too.",
        )
        self._widgets["arch"] = arch_box

        for col, key, label in ((4, "skip_install", "Skip dependency install"), (5, "verbose_install", "Full pip log")):
            var = self.tk.BooleanVar(value=getattr(self.state, key, False))
            self._widgets[key] = var
            cb = self.ttk.Checkbutton(
                self._card,
                text=label,
                variable=var,
                command=lambda k=key, v=var: self.state.set(**{k: bool(v.get())}),
            )
            cb.grid(row=0, column=col, sticky="w", padx=(0, 12))
            tips = {
                "skip_install": "Skip pip install step.",
                "verbose_install": "Show verbose pip output during install.",
            }
            ToolTip(cb, tips.get(key, ""))

        dry_var = self.tk.BooleanVar(value=self.state.dry_run)
        self._widgets["dry_run"] = dry_var
        dry_cb = self.ttk.Checkbutton(
            self._card,
            text="Dry-run only",
            variable=dry_var,
            command=lambda: self.state.set(dry_run=bool(dry_var.get())),
        )
        dry_cb.grid(row=1, column=4, sticky="w", pady=(8, 0))
        ToolTip(dry_cb, "Plan the build without running the compiler.")

    def refresh(self) -> None:
        self._suppress = True
        try:
            if "mpv_runtime" in self._widgets:
                self._widgets["mpv_runtime"].set(self.state.mpv_runtime)
            if "arch" in self._widgets:
                self._widgets["arch"].set(self.state.arch)
            for key in ("skip_install", "verbose_install", "dry_run"):
                if key in self._widgets:
                    self._widgets[key].set(getattr(self.state, key, False))
        finally:
            self._suppress = False


# ── CommandPreview ─────────────────────────────────────────────────────────────


class CommandPreview:
    """Read-only entry showing the generated CLI command."""

    def __init__(self, parent: Any, state: AppState, ttk: Any, tk: Any) -> None:
        self.state = state
        self.ttk = ttk
        self.tk = tk
        self._var: Any = None
        self._card: Any = None
        self._build(parent)

    def _build(self, parent: Any) -> None:
        card = self.ttk.LabelFrame(parent, text="Command preview", style="Card.TLabelframe", padding=(12, 8))
        card.pack(fill="x", pady=(10, 0))
        self._card = card
        self._var = self.tk.StringVar(value="")
        entry = self.ttk.Entry(card, textvariable=self._var, state="readonly")
        entry.pack(fill="x")

    def _do_refresh(self) -> None:
        try:
            cmd = _command(
                {
                    "tool": self.state.tool,
                    "target": self.state.target,
                    "profile": self.state.profile,
                    "mode": self.state.mode,
                    "jobs": self.state.jobs,
                    "mpv_runtime": self.state.mpv_runtime,
                    "arch": self.state.arch,
                    "features": self.state.feature_string(),
                    "skip_install": self.state.skip_install,
                    "verbose_install": self.state.verbose_install,
                    "dry_run": self.state.dry_run,
                }
            )
            self.state.set(command_preview=display_command(cmd))
        except Exception as exc:
            self.state.set(command_preview=f"Unable to build command: {exc}")

    def refresh(self) -> None:
        if self._var is not None:
            self._var.set(self.state.command_preview)


# ── LogPanel ───────────────────────────────────────────────────────────────────


class LogPanel:
    """Scrolled build output log with line trimming."""

    def __init__(self, parent: Any, theme: ThemeManager, scrolledtext: Any) -> None:
        self.theme = theme
        self._scrolledtext = scrolledtext
        self._writes = 0
        self._widget: Any = None
        self._card: Any = None
        self._build(parent)

    def _build(self, parent: Any) -> None:
        card = self.theme.ttk.LabelFrame(parent, text="Build output", style="Card.TLabelframe", padding=8)
        self._card = card
        colors = self.theme.colors
        self._widget = self._scrolledtext.ScrolledText(
            card,
            wrap="word",
            font=("TkFixedFont", 9),
            background=colors.log_bg,
            foreground=colors.log_fg,
            insertbackground="#ffffff",
            selectbackground=colors.log_select,
            relief="flat",
            padx=10,
            pady=8,
        )
        self._widget.pack(fill="both", expand=True)

    def append(self, text: str) -> None:
        self._widget.insert("end", text)
        self._writes += 1
        if self._writes % 200 == 0:
            try:
                line_count = int(self._widget.index("end-1c").split(".", 1)[0])
                if line_count > _LOG_LINE_LIMIT:
                    self._widget.delete("1.0", f"{line_count - _LOG_TRIM_TO}.0")
            except Exception:
                pass
        try:
            self._widget.see("end")
        except Exception:
            pass

    def clear(self) -> None:
        self._widget.delete("1.0", "end")
        self._writes = 0

    def update_theme(self) -> None:
        colors = self.theme.colors
        try:
            self._widget.configure(
                background=colors.log_bg,
                foreground=colors.log_fg,
                selectbackground=colors.log_select,
            )
        except Exception:
            pass


# ── ActionBar ──────────────────────────────────────────────────────────────────


class ActionBar:
    """Start, stop, open output, open logs, clear log, plus preset and theme controls."""

    def __init__(
        self,
        parent: Any,
        state: AppState,
        ttk: Any,
        tk: Any,
        theme: ThemeManager,
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
        on_open_output: Callable[[], None],
        on_open_logs: Callable[[], None],
        on_clear: Callable[[], None],
        on_save_preset: Callable[[], None],
        on_load_preset: Callable[[], None],
        on_toggle_theme: Callable[[], None],
    ) -> None:
        self.state = state
        self.ttk = ttk
        self.tk = tk
        self.theme = theme
        self._status_var: Any = None
        self._start_btn: Any = None
        self._stop_btn: Any = None
        self._on_start_cb = on_start
        self._on_stop_cb = on_stop
        self._build(parent, on_open_output, on_open_logs, on_clear, on_save_preset, on_load_preset, on_toggle_theme)

    def _build(
        self,
        parent: Any,
        on_open_output: Callable[[], None],
        on_open_logs: Callable[[], None],
        on_clear: Callable[[], None],
        on_save_preset: Callable[[], None],
        on_load_preset: Callable[[], None],
        on_toggle_theme: Callable[[], None],
    ) -> None:
        frame = self.ttk.Frame(parent, style="App.TFrame")
        frame.pack(fill="x", pady=(10, 8))
        frame.columnconfigure(0, weight=1)

        primary = self.ttk.Frame(frame, style="App.TFrame")
        primary.grid(row=0, column=0, sticky="ew")
        secondary = self.ttk.Frame(frame, style="App.TFrame")
        secondary.grid(row=1, column=0, sticky="ew", pady=(7, 0))

        self._start_btn = self.ttk.Button(primary, text="Start build", style="Primary.TButton", command=self._on_start_cb)
        self._start_btn.pack(side="left")
        ToolTip(self._start_btn, "Start the build process (Ctrl+Return).")

        self._stop_btn = self.ttk.Button(
            primary, text="Stop", style="Danger.TButton", state="disabled", command=self._on_stop_cb
        )
        self._stop_btn.pack(side="left", padx=(8, 0))
        ToolTip(self._stop_btn, "Cancel the running build (Escape).")

        self.ttk.Button(primary, text="Open output", style="Action.TButton", command=on_open_output).pack(
            side="left", padx=(18, 0)
        )
        self.ttk.Button(primary, text="Open logs", style="Action.TButton", command=on_open_logs).pack(
            side="left", padx=(8, 0)
        )
        self.ttk.Button(primary, text="Clear log", style="Action.TButton", command=on_clear).pack(
            side="left", padx=(8, 0)
        )

        # Preset controls
        self.ttk.Button(secondary, text="Save preset", style="Small.TButton", command=on_save_preset).pack(
            side="left", padx=(18, 0)
        )
        self.ttk.Button(secondary, text="Load preset", style="Small.TButton", command=on_load_preset).pack(
            side="left", padx=(4, 0)
        )

        # Theme toggle
        self.ttk.Button(secondary, text="Toggle theme", style="Small.TButton", command=on_toggle_theme).pack(
            side="left", padx=(18, 0)
        )

        # Status label
        self._status_var = self.tk.StringVar(value="Ready")
        status_label = self.ttk.Label(secondary, textvariable=self._status_var, style="Status.TLabel", anchor="e")
        status_label.pack(side="right", fill="x", expand=True, padx=(12, 0))
        self.state.on_change("status", lambda s: self._status_var.set(s.status))

    def set_running(self, running: bool) -> None:
        self._start_btn.configure(state="disabled" if running else "normal")
        self._stop_btn.configure(state="normal" if running else "disabled")


# ── PresetDialog ───────────────────────────────────────────────────────────────


class PresetDialog:
    """Simple modal to save/load/delete presets."""

    @staticmethod
    def save_dialog(parent: Any, preset_manager: PresetManager, state: AppState, tk: Any) -> str | None:
        presets = preset_manager.list_presets()
        dialog = tk.Toplevel(parent)
        dialog.title("Save Preset")
        dialog.geometry("360x200")
        dialog.transient(parent)
        dialog.grab_set()
        dialog.resizable(False, False)

        tk.Label(dialog, text="Preset name:", font=("TkDefaultFont", 10)).pack(pady=(16, 4))
        name_var = tk.StringVar(value="")
        entry = tk.Entry(dialog, textvariable=name_var, width=30, font=("TkFixedFont", 10))
        entry.pack(pady=(0, 6))
        entry.focus_set()

        if presets:
            tk.Label(dialog, text="Click to select existing:", font=("TkDefaultFont", 9), fg="#5f6b7a").pack()
            lb = tk.Listbox(dialog, height=3, font=("TkFixedFont", 9))
            lb.pack(fill="x", padx=16)
            for name in presets:
                lb.insert("end", name)
            lb.bind(
                "<<ListboxSelect>>",
                lambda _e: (name_var.set(lb.get(lb.curselection()[0]))) if lb.curselection() else None,
            )

        result: list[str | None] = [None]

        def _save() -> None:
            name = name_var.get().strip()
            if not name:
                messagebox.showwarning("Invalid name", "Please enter a preset name.", parent=dialog)
                return
            result[0] = name
            dialog.destroy()

        def _cancel() -> None:
            dialog.destroy()

        btn_frame = tk.Frame(dialog)
        btn_frame.pack(pady=(8, 0))
        tk.Button(btn_frame, text="Save", command=_save, width=10).pack(side="left", padx=(0, 6))
        tk.Button(btn_frame, text="Cancel", command=_cancel, width=10).pack(side="left")
        entry.bind("<Return>", lambda _e: _save())
        entry.bind("<Escape>", lambda _e: _cancel())

        dialog.wait_window()
        return result[0]

    @staticmethod
    def load_dialog(parent: Any, preset_manager: PresetManager, tk: Any) -> str | None:
        presets = preset_manager.list_presets()
        if not presets:
            messagebox.showinfo("No presets", "No saved presets found.", parent=parent)
            return None

        dialog = tk.Toplevel(parent)
        dialog.title("Load Preset")
        dialog.geometry("360x220")
        dialog.transient(parent)
        dialog.grab_set()
        dialog.resizable(False, False)

        tk.Label(dialog, text="Select a preset:", font=("TkDefaultFont", 10)).pack(pady=(16, 6))
        lb = tk.Listbox(dialog, height=6, font=("TkFixedFont", 10))
        lb.pack(fill="x", padx=16)
        for name in presets:
            lb.insert("end", name)
        lb.select_set(0)
        lb.focus_set()

        result: list[str | None] = [None]

        def _load() -> None:
            sel = lb.curselection()
            if not sel:
                return
            result[0] = lb.get(sel[0])
            dialog.destroy()

        def _delete() -> None:
            sel = lb.curselection()
            if not sel:
                return
            name = lb.get(sel[0])
            if messagebox.askyesno("Delete preset", f"Delete preset '{name}'?", parent=dialog):
                preset_manager.delete(name)
                dialog.destroy()
                result[0] = "__reload__"

        def _cancel() -> None:
            dialog.destroy()

        btn_frame = tk.Frame(dialog)
        btn_frame.pack(pady=(8, 0))
        tk.Button(btn_frame, text="Load", command=_load, width=10).pack(side="left", padx=(0, 6))
        tk.Button(btn_frame, text="Delete", command=_delete, width=10).pack(side="left", padx=(0, 6))
        tk.Button(btn_frame, text="Cancel", command=_cancel, width=10).pack(side="left")
        lb.bind("<Double-Button-1>", lambda _e: _load())
        lb.bind("<Return>", lambda _e: _load())
        lb.bind("<Escape>", lambda _e: _cancel())

        dialog.wait_window()
        return result[0]


# ── BuildGuiApp ────────────────────────────────────────────────────────────────


class BuildGuiApp:
    """Orchestrator that wires panels, state, worker, and preset management together."""

    _PREVIEW_DEBOUNCE_MS = 150

    def __init__(self, root: Any) -> None:
        import tkinter as tk
        from tkinter import scrolledtext, ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self._scrolledtext = scrolledtext

        # State
        self._state = AppState()

        # Services
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._worker = BuildWorker(self._events)
        self._theme_mgr = ThemeManager(root, ttk)
        self._presets = PresetManager()
        self._history = BuildHistory()
        self._active_build_record: dict[str, object] | None = None

        # Debounce tracking
        self._preview_after_id: str | None = None
        self._adv_visible = False

        # Apply initial theme
        self._theme_mgr.apply(LIGHT_THEME)

        # Build UI
        self.root.title("ShangBackground Build Studio")
        self.root.geometry("1120x800")
        self.root.minsize(900, 640)
        self.root.option_add("*tearOff", False)
        self._build_layout()

        # Bind keyboard shortcuts
        self.root.bind("<Control-Return>", lambda _event: self._start())
        self.root.bind("<Escape>", lambda _event: self._stop())
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        # Initial sync + preview
        self._refresh_preview()
        self._sync_all()

        # Start polling
        self.root.after(80, self._poll)

    # ── Layout ────────────────────────────────────────────────────────────

    def _build_layout(self) -> None:
        ttk = self.ttk

        outer = ttk.Frame(self.root, style="App.TFrame")
        outer.pack(fill="both", expand=True)

        # Header
        header = ttk.Frame(outer, style="Header.TFrame", padding=(22, 16))
        header.pack(fill="x")
        ttk.Label(header, text="ShangBackground Build Studio", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Native HTML WebView · PyInstaller _internal · standalone-first release builds",
            style="HeaderSub.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        # Scrollable content area
        canvas = self.tk.Canvas(outer, highlightthickness=0, background=self._theme_mgr.colors.background)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self._content_frame = ttk.Frame(canvas, style="App.TFrame", padding=(16, 14, 16, 12))
        content_window = canvas.create_window((0, 0), window=self._content_frame, anchor="nw")

        def _sync_scrollregion(_event: Any = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_content_width(event: Any) -> None:
            # A Canvas window does not automatically grow with the canvas.
            # Keep the embedded content exactly viewport-wide so maximize,
            # DPI scaling, and manual resize cannot leave stale geometry.
            canvas.itemconfigure(content_window, width=max(1, int(event.width)))

        self._content_frame.bind("<Configure>", _sync_scrollregion)
        canvas.bind("<Configure>", _sync_content_width, add="+")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Config panel
        self._config_panel = ConfigPanel(self._content_frame, self._state, ttk, self.tk, self._theme_mgr)
        self._config_panel.bind_state()

        # Feature panel
        self._feature_panel = FeaturePanel(self._content_frame, self._state, ttk, self.tk, self._theme_mgr)
        self._feature_panel.bind_state()

        # Advanced toggle + panel
        adv_header = ttk.Frame(self._content_frame, style="App.TFrame")
        adv_header.pack(fill="x", pady=(10, 0))
        self._adv_toggle = ttk.Button(
            adv_header,
            text="Show advanced options",
            style="Action.TButton",
            command=self._toggle_advanced,
        )
        self._adv_toggle.pack(side="left")
        ttk.Label(adv_header, text="Runtime selection and diagnostic controls", style="Status.TLabel").pack(
            side="left", padx=10
        )

        self._advanced_panel = AdvancedPanel(self._content_frame, self._state, ttk, self.tk, self._theme_mgr)

        # Command preview
        self._command_preview = CommandPreview(self._content_frame, self._state, ttk, self.tk)

        # Content area: PanedWindow for action bar + log
        paned = self.tk.PanedWindow(
            self._content_frame, orient="vertical", bg=self._theme_mgr.colors.background, sashwidth=4
        )
        paned.pack(fill="both", expand=True, pady=(10, 0))

        # Action bar
        actions_frame = ttk.Frame(paned, style="App.TFrame")
        self._action_bar = ActionBar(
            actions_frame,
            self._state,
            ttk,
            self.tk,
            self._theme_mgr,
            on_start=self._start,
            on_stop=self._stop,
            on_open_output=self._open_output,
            on_open_logs=self._open_logs,
            on_clear=self._clear_log,
            on_save_preset=self._save_preset,
            on_load_preset=self._load_preset,
            on_toggle_theme=self._toggle_theme,
        )

        # Log panel
        self._log_panel = LogPanel(paned, self._theme_mgr, self._scrolledtext)

        paned.add(actions_frame, minsize=50, height=50)
        paned.add(self._log_panel._card, minsize=120)

        # Canvas + scrollbar layout
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Mousewheel scrolling on canvas when hovered
        def _on_enter_canvas(_e: Any) -> None:
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _on_leave_canvas(_e: Any) -> None:
            canvas.unbind_all("<MouseWheel>")

        def _on_mousewheel(event: Any) -> None:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind("<Enter>", _on_enter_canvas)
        canvas.bind("<Leave>", _on_leave_canvas)

    # ── Actions ───────────────────────────────────────────────────────────

    def _start(self) -> None:
        if self._worker.running:
            return
        cmd = _command(
            {
                "tool": self._state.tool,
                "target": self._state.target,
                "profile": self._state.profile,
                "mode": self._state.mode,
                "jobs": self._state.jobs,
                "mpv_runtime": self._state.mpv_runtime,
                "arch": self._state.arch,
                "features": self._state.feature_string(),
                "skip_install": self._state.skip_install,
                "verbose_install": self._state.verbose_install,
                "dry_run": self._state.dry_run,
            }
        )
        self._active_build_record = {
            "tool": self._state.tool,
            "target": self._state.target,
            "profile": self._state.profile,
            "mode": self._state.mode,
            "arch": self._state.arch,
            "features": self._state.feature_string(),
            "dry_run": self._state.dry_run,
            "command": display_command(cmd),
        }
        self._log_panel.append("\n" + "─" * 76 + "\n")
        self._log_panel.append("$ " + display_command(cmd) + "\n\n")
        self._action_bar.set_running(True)
        self._state.set(status="Starting build…")
        try:
            self._worker.start(cmd)
        except Exception as exc:
            self._active_build_record = None
            self._log_panel.append(f"\nFailed to start build worker: {exc}\n")
            self._action_bar.set_running(False)
            self._state.set(status=f"Error: {exc}")

    def _stop(self) -> None:
        if not self._worker.running:
            return
        if not messagebox.askyesno(
            "Cancel build",
            "A build is in progress. Stop it?\n\nThe staging output will be discarded; the last validated release is preserved.",
            parent=self.root,
        ):
            return
        self._state.set(status="Stopping build process…")
        self._worker.stop()

    def _open_output(self) -> None:
        self._open_directory(PROJECT_ROOT / f"dist-{self._state.tool}" / self._state.target)

    def _open_logs(self) -> None:
        self._open_directory(PROJECT_ROOT / "build-logs" / self._state.target)

    def _open_directory(self, path: Path) -> None:
        try:
            _open_path(path)
        except OSError as exc:
            self._state.set(status=f"Unable to open directory: {exc}")

    def _clear_log(self) -> None:
        self._log_panel.clear()

    def _toggle_advanced(self) -> None:
        self._adv_visible = not self._adv_visible
        if self._adv_visible:
            self._advanced_panel._card.pack(
                fill="x",
                pady=(8, 0),
                before=self._command_preview._card,
            )
            self._adv_toggle.configure(text="Hide advanced options")
        else:
            self._advanced_panel._card.pack_forget()
            self._adv_toggle.configure(text="Show advanced options")

    def _toggle_theme(self) -> None:
        dark = not self._state.theme_dark
        self._state.set(theme_dark=dark)
        theme = DARK_THEME if dark else LIGHT_THEME
        self._theme_mgr.apply(theme)
        self._log_panel.update_theme()
        # Update canvas background
        for child in self.root.winfo_children():
            if isinstance(child, self.tk.Canvas):
                child.configure(background=theme.background)

    def _save_preset(self) -> None:
        name = PresetDialog.save_dialog(self.root, self._presets, self._state, self.tk)
        if name:
            try:
                self._presets.save(name, self._state.snapshot())
                self._state.set(status=f"Preset '{name}' saved.")
            except (OSError, ValueError) as exc:
                messagebox.showerror("Save failed", str(exc), parent=self.root)

    def _load_preset(self) -> None:
        name = PresetDialog.load_dialog(self.root, self._presets, self.tk)
        if not name or name == "__reload__":
            return
        try:
            data = self._presets.load(name)
            self._state.restore_snapshot(data)
            # Release builders are native-host only. A preset copied from a
            # different OS must not silently turn the disabled target field
            # into a cross-build request.
            normalized = {"target": host_target()}
            if host_target() == "macos" and self._state.mode == "onefile":
                normalized["mode"] = "standalone"
            self._state.set(**normalized)
            self._sync_all()
            self._refresh_preview()
            self._state.set(status=f"Preset '{name}' loaded.")
        except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
            messagebox.showerror("Load failed", str(exc), parent=self.root)

    # ── Preview ───────────────────────────────────────────────────────────

    def _refresh_preview(self) -> None:
        """Debounced command preview refresh."""
        if self._preview_after_id is not None:
            self.root.after_cancel(self._preview_after_id)
        self._preview_after_id = self.root.after(self._PREVIEW_DEBOUNCE_MS, self._do_refresh_preview)

    def _do_refresh_preview(self) -> None:
        self._preview_after_id = None
        self._command_preview._do_refresh()
        self._command_preview.refresh()

    def _sync_all(self) -> None:
        """Push state to all panels."""
        self._config_panel.refresh()
        self._feature_panel.refresh()
        self._advanced_panel.refresh()
        self._command_preview.refresh()
        theme = DARK_THEME if self._state.theme_dark else LIGHT_THEME
        self._theme_mgr.apply(theme)
        self._log_panel.update_theme()

    # ── Polling ───────────────────────────────────────────────────────────

    def _poll(self) -> None:
        try:
            self._poll_events()
        except Exception as exc:
            message = f"Build event processing error: {type(exc).__name__}: {exc}"
            self._state.set(status=message)
            self._log_panel.append("\n" + message + "\n")
        finally:
            try:
                self.root.after(80, self._poll)
            except self.tk.TclError:
                pass

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "line":
                    text = str(payload)
                    self._log_panel.append(text)
                    stripped = text.strip()
                    if stripped.startswith("◆ ") or stripped.startswith("> "):
                        self._state.set(status=stripped[2:])
                    elif stripped.startswith("[stage]"):
                        self._state.set(status=stripped.removeprefix("[stage]").strip())
                elif kind == "done":
                    code = payload if isinstance(payload, int) else int(str(payload))
                    elapsed = self._worker.elapsed
                    self._action_bar.set_running(False)
                    status_text = "Build completed" if code == 0 else f"Build failed · exit {code}"
                    self._state.set(status=f"{status_text} [{elapsed:.1f}s]")
                    self._log_panel.append(f"\nCommand finished with exit code {code}. [{elapsed:.1f}s]\n")
                    # Record the immutable selection that launched this
                    # process. Controls/presets may have changed while it ran.
                    record = dict(self._active_build_record or {})
                    record.update(
                        {
                            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "exit_code": code,
                            "elapsed_s": round(elapsed, 1),
                        }
                    )
                    try:
                        self._history.record(record)
                    except OSError as exc:
                        self._log_panel.append(f"\nBuild history was not saved: {exc}\n")
                    self._active_build_record = None
                elif kind == "status":
                    self._state.set(status=str(payload))
        except queue.Empty:
            pass

    # ── Window close ──────────────────────────────────────────────────────

    def _close(self) -> None:
        if self._worker.running:
            if not messagebox.askyesno(
                "Build still running",
                "A build is in progress. Closing the window will forcefully terminate it. Continue?",
                parent=self.root,
            ):
                return
            self._state.set(status="Terminating build process…")
            self._worker.stop()
        self.root.destroy()


# ── Public API ─────────────────────────────────────────────────────────────────


def create_app(root: Any) -> BuildGuiApp:
    """Create the GUI without entering the event loop; useful for smoke tests."""
    return BuildGuiApp(root)


def main() -> int:
    import tkinter as tk

    root = tk.Tk()
    create_app(root)
    root.mainloop()
    return 0
