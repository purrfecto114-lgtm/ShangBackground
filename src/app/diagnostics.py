"""Headless runtime diagnostics used by ``shangbackground --doctor``."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.build_features import is_feature_enabled
from app.config import APP_VERSION, PLATFORM_ID
from app.paths import IMAGE_DIR, LANG_DIR, RESOURCE_ROOT, resolve_mpv_path, user_data_dir
from app.storage import load_json_object


@dataclass(slots=True)
class DiagnosticCheck:
    name: str
    status: str
    detail: str
    required: bool = False
    # v1.6.1: actionable next step shown for fail/warn checks — both in the
    # human-readable report and in the --doctor-json payload, so a user who
    # hits a missing dependency knows what to run instead of just "FAIL".
    hint: str = ""


@dataclass(slots=True)
class DiagnosticReport:
    app_version: str
    platform: str
    python: str
    resource_root: str
    data_dir: str
    checks: list[DiagnosticCheck]

    @property
    def healthy(self) -> bool:
        return not any(check.required and check.status == "fail" for check in self.checks)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["healthy"] = self.healthy
        return data


def _module_check(
    module: str,
    label: str,
    *,
    required: bool,
    load: bool = False,
    install: str = "",
    missing_hint: str = "",
) -> DiagnosticCheck:
    try:
        if load:
            importlib.import_module(module)
            available = True
        else:
            available = importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, OSError, RuntimeError, ValueError):
        available = False
    hint = ""
    if not available:
        if install:
            hint = f"python -m pip install {install}"
        elif missing_hint:
            hint = missing_hint
        elif required:
            hint = "see README 依赖说明 for installation options"
    return DiagnosticCheck(
        name=label,
        status="pass" if available else ("fail" if required else "warn"),
        detail=f"{module}: {'available' if available else 'missing'}",
        required=required,
        hint=hint,
    )


def _command_check(commands: tuple[str, ...], label: str, *, required: bool = False) -> DiagnosticCheck:
    found = [command for command in commands if shutil.which(command)]
    hint = ""
    if not found:
        hint = (
            "install via your system package manager, then re-run --doctor"
            if required
            else "optional — the app automatically falls back to another wallpaper backend when this command is missing"
        )
    return DiagnosticCheck(
        name=label,
        status="pass" if found else ("fail" if required else "warn"),
        detail=("found: " + ", ".join(found)) if found else "not found: " + ", ".join(commands),
        required=required,
        hint=hint,
    )


def _wayland_video_check(capability: dict) -> DiagnosticCheck:
    """KDE_SUPPORT_PLAN 任务 5：doctor 与 capabilities.py 统一口径。

    消费 probe_capabilities() 的 "video_wallpaper" 子表，不再自行以
    mpvpaper 命令存在性重判（旧口径在 KDE Wayland 装了 mpvpaper 时显示
    pass，与 v1.6.2 能力声明“KDE 不默认 ready”分裂）。

    状态映射：runtime_ready → pass；其余 → warn（可选项不 fail）。
    detail 携带能力判定的 backend；hint 按 state/backend 区分：KDE 有
    mpvpaper（实验开关指引）/ KDE 无后端 / wlroots 未装 mpvpaper（安装
    指引）/ 其它（GNOME 等无受支持的视频壁纸层）。
    """
    if not capability:
        # 异常防御：能力子表缺失/异常时如实降级，不渲染为 pass。
        return DiagnosticCheck(
            "Wayland video embedding",
            "warn",
            "capability probe unavailable",
            required=False,
            hint="能力判定暂时不可用，请重跑 --doctor",
        )
    backend = str(capability.get("backend") or "")
    state = str(capability.get("state") or "")
    if capability.get("runtime_ready"):
        return DiagnosticCheck(
            "Wayland video embedding",
            "pass",
            f"backend: {backend}" if backend else "runtime ready",
            required=False,
        )
    detail = f"backend: {backend}" if backend else f"state: {state or 'unknown'}"
    if "mpvpaper layer-shell" in backend:
        # wlroots 会话但未装 mpvpaper：装上即可 ready。
        hint = "安装 mpvpaper 后重跑 --doctor"
    elif state == "best_effort":
        # KDE/KWin 有 mpvpaper：backend 自带 experimental/untested 措辞。
        hint = "KDE/KWin 未真机验证：如需实验请设置 SHANGBACKGROUND_ALLOW_MPVPAPER=1（可能黑屏或无首帧）"
    elif "no supported KDE/KWin" in backend:
        hint = "KDE 会话当前无受支持的视频壁纸后端（mpvpaper 面向 wlroots 系）"
    else:
        hint = "当前 Wayland 桌面无受支持的视频壁纸层（GNOME 需桌面扩展）"
    return DiagnosticCheck("Wayland video embedding", "warn", detail, required=False, hint=hint)


def _directory_check(path: Path, label: str, *, required: bool = True) -> DiagnosticCheck:
    ok = path.is_dir()
    return DiagnosticCheck(
        name=label,
        status="pass" if ok else ("fail" if required else "warn"),
        detail=os.fspath(path),
        required=required,
    )


def _writable_directory_check(path: Path) -> DiagnosticCheck:
    try:
        path.mkdir(parents=True, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".doctor-", dir=os.fspath(path))
        os.close(fd)
        Path(probe).unlink(missing_ok=True)
    except OSError as exc:
        return DiagnosticCheck("user-data-writable", "fail", str(exc), required=True)
    return DiagnosticCheck("user-data-writable", "pass", os.fspath(path), required=True)


def _config_check(data_dir: Path) -> DiagnosticCheck:
    primary = data_dir / "settings.json"
    backup = data_dir / "settings.json.bak"
    if not primary.exists() and not backup.exists():
        return DiagnosticCheck("configuration", "pass", "not created yet")
    errors: list[str] = []
    for candidate in (primary, backup):
        if not candidate.is_file():
            continue
        try:
            load_json_object(candidate)
            recovered = candidate == backup and primary.exists()
            return DiagnosticCheck(
                "configuration",
                "warn" if recovered else "pass",
                f"readable: {candidate.name}" + (" (backup recovery available)" if recovered else ""),
            )
        except Exception as exc:
            errors.append(f"{candidate.name}: {exc}")
    return DiagnosticCheck("configuration", "fail", "; ".join(errors), required=True)


def collect_diagnostics() -> DiagnosticReport:
    data_dir = Path(user_data_dir("ShangBackground"))
    checks: list[DiagnosticCheck] = [
        DiagnosticCheck(
            "python-version",
            "pass" if sys.version_info >= (3, 10) else "fail",
            sys.version.split()[0],
            required=True,
        ),
        _directory_check(Path(RESOURCE_ROOT), "resource-root"),
        _directory_check(Path(IMAGE_DIR), "image-resources"),
        _directory_check(Path(LANG_DIR), "language-resources"),
        _writable_directory_check(data_dir),
        _config_check(data_dir),
        _module_check("PIL", "Pillow", required=True, install="pillow"),
        _module_check("PySide6", "PySide6 Essentials", required=True, install="PySide6-Essentials"),
        _module_check("psutil", "process safety", required=False, install="psutil"),
    ]
    if is_feature_enabled("html"):
        from platform_adapters.native_html_runner import dependency_probe

        html_probe = dependency_probe(load_runtime=True)
        detail_parts: list[str] = []
        raw_missing_html = html_probe.get("missing_modules", [])
        missing_html = (
            [str(item) for item in raw_missing_html]
            if isinstance(raw_missing_html, (list, tuple, set, frozenset))
            else []
        )
        if missing_html:
            detail_parts.append("missing: " + ", ".join(missing_html))
        import_errors = html_probe.get("runtime_import_errors", {})
        if isinstance(import_errors, dict):
            detail_parts.extend(f"{module}: {error}" for module, error in import_errors.items())
        for key in ("desktop_backend_error", "environment_error"):
            value = str(html_probe.get(key, "") or "")
            if value:
                detail_parts.append(value)
        healthy_html = bool(html_probe.get("healthy"))
        checks.append(
            DiagnosticCheck(
                "native-html-wallpaper",
                "pass" if healthy_html else "warn",
                "; ".join(detail_parts) if detail_parts else "available, importable, and platform-ready",
                required=False,
            )
        )

    try:
        from app.libmpv_runtime import resolve_libmpv_path
        from app.build_features import video_runtime_mode

        _video_mode = video_runtime_mode()
        libmpv_path = resolve_libmpv_path()
        # v1.4.4: Only probe (load) libmpv in bundled mode. In system mode,
        # loading libmpv into the GUI process is wasteful and can cause
        # DLL conflicts; the external mpv.exe handles playback.
        if libmpv_path and _video_mode not in ("system", "disabled"):
            from app.libmpv_runtime import probe_libmpv

            libmpv_ok, libmpv_detail = probe_libmpv(libmpv_path)
        else:
            libmpv_ok = False
            libmpv_detail = (
                f"skipped (mode={_video_mode})" if _video_mode in ("system", "disabled") else "libmpv not found"
            )
    except Exception as exc:
        libmpv_path = None
        libmpv_ok, libmpv_detail = False, str(exc)

    resolved_mpv = resolve_mpv_path()
    system_mpv = shutil.which("mpv")
    bundled_mpv = bool(resolved_mpv and resolved_mpv != system_mpv)
    if libmpv_ok:
        video_detail = f"direct libmpv: {libmpv_detail}"
    elif bundled_mpv:
        video_detail = f"bundled/user mpv executable: {resolved_mpv}"
    elif system_mpv:
        video_detail = f"system mpv: {system_mpv}"
    elif libmpv_path:
        video_detail = f"libmpv found but failed to load: {libmpv_detail}"
    else:
        video_detail = "libmpv/mpv not found"
    checks.append(
        DiagnosticCheck(
            "video-backend",
            "pass" if libmpv_ok or bundled_mpv or system_mpv else "warn",
            video_detail,
        )
    )

    if PLATFORM_ID == "windows":
        checks.append(
            DiagnosticCheck(
                "Windows COM wallpaper",
                "pass",
                "built-in ctypes + ole32 IDesktopWallpaper",
            )
        )
    elif PLATFORM_ID == "linux":
        # v1.6.1: shared layers must not import platform backends directly;
        # the session facade dispatches to the Linux backend's canonical
        # fallback chain while staying import-safe on every platform.
        from platform_adapters.session import (
            detect_session_type,
            kde_wallpaper_restore_scope,
            linux_video_wallpaper_capability,
            session_bus_available,
        )

        session = detect_session_type()
        checks.append(
            _command_check(
                (
                    "plasma-apply-wallpaperimage",
                    "qdbus6",
                    "qdbus",
                    "gsettings",
                    "xfconf-query",
                    "pcmanfm",
                    "feh",
                    "nitrogen",
                ),
                "static wallpaper backend",
            )
        )
        # v1.6.2 审查必须修复项 2：KDE 恢复范围降级声明——非图片插件
        # （slideshow/color/第三方）不会被退出恢复还原，必须在诊断中
        # 显式告知用户，而不是静默丢失后让人意外。
        # v1.6.3（schema=3）口径更新：退出恢复现在会重选原插件并完整恢复
        # org.kde.image 的 Image/FillMode；非图片插件的内部配置（轮播列表、
        # 颜色等）仍无法捕获，会回落插件默认值——如实降级告知。
        scope = kde_wallpaper_restore_scope()
        if scope.get("applicable"):
            if not scope.get("reachable"):
                checks.append(
                    DiagnosticCheck(
                        "kde-wallpaper-restore",
                        "warn",
                        f"无法读取 Plasma 壁纸插件状态：{scope.get('detail', 'unknown')}",
                    )
                )
            elif scope.get("restorable"):
                checks.append(
                    DiagnosticCheck(
                        "kde-wallpaper-restore",
                        "pass",
                        f"恢复范围：本地静态图片（{scope.get('detail', '')}）",
                    )
                )
            else:
                checks.append(
                    DiagnosticCheck(
                        "kde-wallpaper-restore",
                        "warn",
                        f"当前 Plasma 壁纸插件包含非图片插件：{scope.get('detail', '')}。"
                        "退出恢复会重选原插件并恢复 org.kde.image 的图片/填充模式",
                        hint="v1.6.3 schema=3：插件本身会被恢复，但 slideshow/color/"
                        "第三方插件的内部配置（轮播列表、颜色等）不被保存，"
                        "会回落插件默认值；如需完整保留请在退出前自行记录插件设置",
                    )
                )
        if session == "wayland":
            module = _module_check("dbus_next", "Wayland global shortcuts", required=False, install="dbus-next")
            if module.status == "pass" and not session_bus_available():
                module.status = "warn"
                module.detail += "; no D-Bus session bus endpoint detected"
            checks.append(module)
            # v1.6.3（KDE_SUPPORT_PLAN 任务 5）：消费统一能力判定，不再以
            # mpvpaper 命令存在性自行重判（KDE Wayland 曾因此误显示 pass）。
            checks.append(_wayland_video_check(linux_video_wallpaper_capability()))
        elif session == "x11":
            checks.append(_module_check("pynput", "X11 global hotkeys", required=False, install="pynput"))
            checks.append(_command_check(("xwinwrap",), "X11 desktop video embedding"))
        else:
            checks.append(
                DiagnosticCheck(
                    "graphical-session",
                    "warn",
                    "neither XDG_SESSION_TYPE, WAYLAND_DISPLAY nor DISPLAY identifies a GUI session",
                )
            )
    else:
        checks.extend(
            [
                _module_check("pynput", "global hotkeys", required=False, install="pynput"),
                _module_check(
                    "AppKit",
                    "macOS AppKit",
                    required=False,
                    missing_hint="part of the macOS system Python (python.org or Homebrew builds include it)",
                ),
                _module_check(
                    "Quartz",
                    "macOS Quartz",
                    required=False,
                    missing_hint="part of the macOS system Python (python.org or Homebrew builds include it)",
                ),
            ]
        )

    return DiagnosticReport(
        app_version=APP_VERSION,
        platform=PLATFORM_ID,
        python=sys.version.split()[0],
        resource_root=os.fspath(RESOURCE_ROOT),
        data_dir=os.fspath(data_dir),
        checks=checks,
    )


def render_human(report: DiagnosticReport) -> str:
    icons = {"pass": "OK", "warn": "WARN", "fail": "FAIL"}
    lines = [
        f"ShangBackground {report.app_version} diagnostics",
        f"platform={report.platform} python={report.python}",
        f"resources={report.resource_root}",
        f"data={report.data_dir}",
        "",
    ]
    for check in report.checks:
        lines.append(f"[{icons.get(check.status, check.status.upper())}] {check.name}: {check.detail}")
        if check.hint and check.status in {"fail", "warn"}:
            lines.append(f"        hint: {check.hint}")
    lines.append("")
    lines.append("result=healthy" if report.healthy else "result=missing required components")
    return "\n".join(lines)


def main(*, json_output: bool = False) -> int:
    report = collect_diagnostics()
    if json_output:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_human(report))
    return 0 if report.healthy else 2
