from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlparse


def _run_args(args: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """Run a command without a shell and return (returncode, stdout, stderr).

    Always decodes subprocess output as UTF-8.  On systems whose locale is not
    UTF-8 (e.g. ``C`` or ``POSIX``), ``text=True`` would default to ASCII and
    either crash or silently mangle wallpaper paths containing CJK characters,
    producing visible stutter when the slideshow advances to a Chinese-named
    image.  Pinning the encoding keeps behaviour deterministic across distros.
    """
    try:
        result = subprocess.run(
            args,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return -1, "", str(exc)


def _ensure_existing_file(path: str) -> str:
    """Return a direct absolute Unicode path and fail early."""
    try:
        raw = os.fspath(path)
    except (TypeError, ValueError, OSError) as exc:
        raise FileNotFoundError(f"Invalid wallpaper path: {path!r}") from exc
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    abs_path = os.path.abspath(os.path.expanduser(str(raw)))
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"Wallpaper file does not exist: {abs_path}")
    return abs_path


def _file_uri(path: str) -> str:
    return Path(_ensure_existing_file(path)).as_uri()


def _path_from_uri(value: str) -> str:
    value = (value or "").strip().strip("'\"")
    if value.startswith("file://"):
        parsed = urlparse(value)
        return unquote(parsed.path)
    return value


def _same_existing_file(left: str, right: str) -> bool:
    left_path = _valid_wallpaper_path_from_value(left)
    right_path = _valid_wallpaper_path_from_value(right)
    if not left_path or not right_path:
        return False
    try:
        return os.path.samefile(left_path, right_path)
    except OSError:
        return os.path.abspath(left_path) == os.path.abspath(right_path)


def get_screen_size(root=None):
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            screen = app.primaryScreen()
            if screen is not None:
                geo = screen.geometry()
                return geo.width(), geo.height()
    except Exception:
        pass
    try:
        rc, out, _ = _run_args(["xrandr", "--current"], timeout=5)
        if rc == 0 and out:
            import re
            for line in out.splitlines():
                if "*" in line:
                    match = re.search(r"(\d+)x(\d+)", line)
                    if match:
                        return int(match.group(1)), int(match.group(2))
    except Exception:
        pass
    try:
        if root is not None:
            return root.winfo_screenwidth(), root.winfo_screenheight()
    except Exception:
        pass
    return 1920, 1080


def _desktop_session_tokens() -> str:
    values = [
        os.environ.get("XDG_CURRENT_DESKTOP", ""),
        os.environ.get("DESKTOP_SESSION", ""),
        os.environ.get("KDE_FULL_SESSION", ""),
        os.environ.get("WAYLAND_DISPLAY", ""),
    ]
    return " ".join(values).lower()


def _is_kde_session() -> bool:
    tokens = _desktop_session_tokens()
    return "kde" in tokens or "plasma" in tokens


def _is_xfce_session() -> bool:
    return "xfce" in _desktop_session_tokens()


def _is_lxde_session() -> bool:
    tokens = _desktop_session_tokens()
    return "lxde" in tokens or "lxqt" in tokens


def _is_gsettings_desktop_session() -> bool:
    tokens = _desktop_session_tokens()
    return any(name in tokens for name in ("gnome", "cinnamon", "mate", "budgie", "unity", "deepin", "pantheon"))


_LINUX_FIT_MODE = "填充"


def _normalize_linux_fit_mode(value: object) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "填充": "填充", "fill": "填充", "zoom": "填充", "crop": "填充",
        "适应": "适应", "fit": "适应", "scaled": "适应", "scale": "适应",
        "拉伸": "拉伸", "stretch": "拉伸", "stretched": "拉伸",
        "居中": "居中", "center": "居中", "centered": "居中",
        "平铺": "平铺", "tile": "平铺", "tiled": "平铺", "wallpaper": "平铺",
    }
    return aliases.get(text, "填充")


def _valid_wallpaper_path_from_value(value: str) -> str:
    path = _path_from_uri(value)
    path = os.path.abspath(os.path.expanduser(path)) if path else ""
    return path if path and os.path.isfile(path) else ""


def _summarize_command_output(value: str, *, max_lines: int = 3, max_chars: int = 240) -> str:
    """Keep desktop-command diagnostics readable in the GUI log."""
    text = (value or "").strip()
    if not text:
        return "empty"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "empty"
    picked: list[str] = []
    for line in lines:
        lower = line.lower()
        if lower.startswith(("value:", "plugin:", "image:", "wallpaperplugin:", "fillmode:", "previewimage:")) or "error" in lower or "unknown" in lower:
            picked.append(line)
        if len(picked) >= max_lines:
            break
    if not picked:
        picked = lines[:max_lines]
    summary = " | ".join(picked)
    if len(summary) > max_chars:
        summary = summary[: max_chars - 1].rstrip() + "…"
    return summary


def _qdbus_commands() -> list[str]:
    """Return installed Qt DBus frontends in the order KDE 6/5 usually needs."""
    return [cmd for cmd in ("qdbus6", "qdbus") if shutil.which(cmd)]


def _run_plasma_script(script: str, *, timeout: int = 10, allow_dbus_send: bool = False) -> tuple[bool, str, str]:
    """Run a Plasma shell script and return (success, stdout, diagnostics)."""
    errors: list[str] = []
    for qdbus in _qdbus_commands():
        rc, out, err = _run_args([qdbus, "org.kde.plasmashell", "/PlasmaShell", "org.kde.PlasmaShell.evaluateScript", script], timeout=timeout)
        if rc == 0:
            return True, out, ""
        errors.append(f"{qdbus}: {err or out or 'no output'}")
    if allow_dbus_send and shutil.which("dbus-send"):
        rc, out, err = _run_args([
            "dbus-send", "--session", "--dest=org.kde.plasmashell", "--type=method_call",
            "/PlasmaShell", "org.kde.PlasmaShell.evaluateScript", f"string:{script}",
        ], timeout=timeout)
        if rc == 0:
            return True, out, ""
        errors.append(f"dbus-send: {err or out or 'no output'}")
    if not errors:
        errors.append("qdbus6/qdbus: command not found")
    return False, "", " | ".join(errors)


def _kde_read_wallpaper_values() -> tuple[bool, list[str], str]:
    """Return raw local/URI wallpaper values from every KDE desktop containment."""
    script = r'''
var allDesktops = desktops();
for (var i = 0; i < allDesktops.length; i++) {
    var d = allDesktops[i];
    var plugin = d.wallpaperPlugin || "org.kde.image";
    print("PLUGIN:" + plugin);
    var groups = [
        ["Wallpaper", plugin, "General"],
        ["Wallpaper", "org.kde.image", "General"],
        ["Wallpaper", "image"],
        ["Wallpaper"]
    ];
    var keys = ["Image", "wallpaper", "PreviewImage"];
    for (var g = 0; g < groups.length; g++) {
        d.currentConfigGroup = groups[g];
        for (var k = 0; k < keys.length; k++) {
            var value = d.readConfig(keys[k], "");
            if (value) {
                print("VALUE:" + value);
            }
        }
    }
}
'''
    ok, out, detail = _run_plasma_script(script, timeout=10)
    if not ok:
        return False, [], detail
    values: list[str] = []
    for line in out.splitlines():
        text = line.strip()
        if text.startswith("VALUE:"):
            raw_value = text.split(":", 1)[1].strip()
        else:
            key, sep, raw_value = text.partition(":")
            if not sep or key.strip().lower() not in {"image", "wallpaper", "previewimage"}:
                continue
            raw_value = raw_value.strip()
        if raw_value and raw_value.lower() not in {"null", "none", "undefined"}:
            values.append(raw_value)
    # Empty Image is a valid Plasma state: the user may currently use a solid
    # color, slideshow/provider, or a wallpaper plugin without a concrete file.
    # Treat it as “no restorable static image” instead of a command failure so
    # startup capture and preview polling do not spam the log.
    if not values:
        return True, [], out
    return True, values, out


def _get_kde_wallpaper() -> tuple[bool, str]:
    """Read current KDE Plasma static image wallpaper via plasmashell scripting.

    Returns ``(True, "")`` when Plasma is reachable but the current wallpaper
    is not a local static image. That is not a fatal error: startup restoration
    simply has no safe file to restore.
    """
    ok, values, detail = _kde_read_wallpaper_values()
    if ok:
        for value in values:
            path = _valid_wallpaper_path_from_value(value)
            if path:
                return True, path
        return True, ""
    return False, detail or "KDE wallpaper query produced no result"


def _kde_set_script(image_value: str, *, fill_mode: int | None = None) -> str:
    fill_line = ""
    if fill_mode is not None:
        fill_line = f"\n    d.writeConfig(\"FillMode\", {int(fill_mode)});"
    return """
var allDesktops = desktops();
for (var i = 0; i < allDesktops.length; i++) {
    var d = allDesktops[i];
    d.wallpaperPlugin = "org.kde.image";
    d.currentConfigGroup = Array("Wallpaper", "org.kde.image", "General");
    d.writeConfig("Image", %s);%s
    d.reloadConfig();
}
print("SHANGBACKGROUND_KDE_SET_DONE:" + allDesktops.length);
""" % (json.dumps(image_value), fill_line)


def _verify_kde_wallpaper(abs_path: str, *, timeout: float = 0.5) -> tuple[bool, str]:
    """Best-effort read-back verification. Does NOT block the caller.

    v1.4.8: On Plasma 6, readConfig("Image") often returns empty even after a
    successful writeConfig. This function is now a non-blocking best-effort
    check — it tries one quick read-back (150ms delay) and returns whatever
    it gets. The caller should NOT fail the wallpaper set based on this
    function returning (False, "") — the return code of
    plasma-apply-wallpaperimage / evaluateScript is the source of truth.
    """
    time.sleep(0.15)
    ok, detail = _get_kde_wallpaper()
    if ok and _same_existing_file(detail, abs_path):
        return True, detail
    return False, detail


def _set_kde_wallpaper(path: str, *, fill_mode: int | None = None) -> tuple[bool, str]:
    """Set a real static KDE/Plasma wallpaper.

    v1.4.8: On Plasma 6, ``readConfig("Image")`` after ``writeConfig`` often
    returns empty because the wallpaper config is stored in a different
    containment/plugin path than what ``_kde_read_wallpaper_values`` probes.
    This caused every wallpaper set to fail with "read-back was empty" even
    though the wallpaper was actually applied.

    Fix: trust the return code of ``plasma-apply-wallpaperimage`` and
    ``evaluateScript``. Do the read-back as a best-effort verification, but
    do NOT fail the entire operation when read-back is empty — the wallpaper
    is visible on screen regardless.
    """
    abs_path = _ensure_existing_file(path)
    uri = _file_uri(abs_path)
    errors: list[str] = []

    # plasma-apply-wallpaperimage is the canonical Plasma 6 command.
    if shutil.which("plasma-apply-wallpaperimage"):
        rc, out, err = _run_args(["plasma-apply-wallpaperimage", abs_path], timeout=8)
        if rc == 0:
            # Best-effort read-back — do NOT fail if empty. The wallpaper is set.
            verified, current = _verify_kde_wallpaper(abs_path)
            if verified:
                return True, current
            # Read-back empty or mismatched — but rc=0 means success on Plasma 6.
            # Trust the return code and return the path we just set.
            return True, abs_path
        elif err or out:
            errors.append(f"plasma-apply-wallpaperimage: {err or out}")

    # Fallback: Plasma scripting via qdbus6 evaluateScript.
    for image_value in (uri, abs_path):
        script = _kde_set_script(image_value, fill_mode=fill_mode)
        ok, out, detail = _run_plasma_script(script, timeout=4, allow_dbus_send=False)
        if not ok:
            errors.append(detail)
            continue
        # evaluateScript succeeded (output contains SHANGBACKGROUND_KDE_SET_DONE).
        # Trust it — do NOT fail on empty read-back.
        if "SHANGBACKGROUND_KDE_SET_DONE" in (out or ""):
            verified, current = _verify_kde_wallpaper(abs_path)
            if verified:
                return True, current
            return True, abs_path
        errors.append(f"evaluateScript({image_value!r}) output={_summarize_command_output(out)}")

    return False, " | ".join(errors[-6:]) or "KDE wallpaper command could not be executed"


def _get_gnome_wallpaper() -> tuple[bool, str]:
    if not shutil.which("gsettings"):
        return False, "gsettings: command not found"
    errors: list[str] = []
    for key in ("picture-uri", "picture-uri-dark"):
        rc, out, err = _run_args(["gsettings", "get", "org.gnome.desktop.background", key])
        if rc == 0 and out:
            path = _valid_wallpaper_path_from_value(out)
            if path:
                return True, path
            errors.append(f"gsettings {key}: not an existing file ({out})")
        elif err or out:
            errors.append(f"gsettings {key}: {err or out}")
    return False, " | ".join(errors[-3:]) or "gsettings returned no wallpaper path"


def _set_gnome_wallpaper(path: str) -> tuple[bool, str]:
    abs_path = _ensure_existing_file(path)
    if not shutil.which("gsettings"):
        return False, "gsettings: command not found"
    uri = _file_uri(abs_path)
    errors: list[str] = []
    for key in ("picture-uri", "picture-uri-dark"):
        rc, out, err = _run_args(["gsettings", "set", "org.gnome.desktop.background", key, uri])
        if rc != 0 and (err or out):
            errors.append(f"gsettings {key}: {err or out}")
    ok, current = _get_gnome_wallpaper()
    if ok and _same_existing_file(current, abs_path):
        return True, current
    errors.append("gsettings did not verify; read-back=" + (current or "empty"))
    return False, " | ".join(errors[-4:])


def _xfce_wallpaper_properties() -> list[str]:
    if not shutil.which("xfconf-query"):
        return []
    rc, out, _err = _run_args(["xfconf-query", "-c", "xfce4-desktop", "-l"], timeout=10)
    props: list[str] = []
    if rc == 0 and out:
        for line in out.splitlines():
            prop = line.strip()
            if prop.startswith("/backdrop/") and prop.endswith(("/last-image", "/image-path")):
                props.append(prop)
    fallback = [
        "/backdrop/screen0/monitor0/image-path",
        "/backdrop/screen0/monitor0/workspace0/last-image",
        "/backdrop/screen0/monitordefault/workspace0/last-image",
    ]
    for prop in fallback:
        if prop not in props:
            props.append(prop)
    return props


def _get_xfce_wallpaper() -> tuple[bool, str]:
    if not shutil.which("xfconf-query"):
        return False, "xfconf-query: command not found"
    errors: list[str] = []
    for prop in _xfce_wallpaper_properties():
        rc, out, err = _run_args(["xfconf-query", "-c", "xfce4-desktop", "-p", prop], timeout=10)
        if rc == 0 and out:
            path = _valid_wallpaper_path_from_value(out)
            if path:
                return True, path
            errors.append(f"xfconf-query {prop}: not an existing file ({out})")
        elif err or out:
            errors.append(f"xfconf-query {prop}: {err or out}")
    return False, " | ".join(errors[-5:]) or "xfconf-query returned no wallpaper path"


def _xfce_image_style_properties() -> list[str]:
    """Return existing Xfce image-style keys for every monitor/workspace."""
    props: list[str] = []
    if shutil.which("xfconf-query"):
        rc, out, _err = _run_args(["xfconf-query", "-c", "xfce4-desktop", "-l"], timeout=10)
        if rc == 0 and out:
            for line in out.splitlines():
                prop = line.strip()
                if prop.startswith("/backdrop/") and prop.endswith("/image-style"):
                    props.append(prop)
    # Xfce stores the path and image-style next to each other. Deriving from
    # the discovered path keys also covers installations where image-style has
    # not been explicitly materialised yet.
    for wallpaper_prop in _xfce_wallpaper_properties():
        if wallpaper_prop.endswith("/last-image"):
            candidate = wallpaper_prop[: -len("/last-image")] + "/image-style"
        elif wallpaper_prop.endswith("/image-path"):
            candidate = wallpaper_prop[: -len("/image-path")] + "/image-style"
        else:
            continue
        if candidate not in props:
            props.append(candidate)
    return props


def _xfce_image_style_value(fit_mode: str) -> int:
    # xfdesktop enum order: None=0, Centered=1, Tiled=2, Stretched=3,
    # Scaled=4 (letterbox), Zoomed=5 (crop-to-fill).
    return {"居中": 1, "平铺": 2, "拉伸": 3, "适应": 4, "填充": 5}.get(
        _normalize_linux_fit_mode(fit_mode), 5
    )


def _set_xfce_fit_mode(fit_mode: str) -> tuple[bool, str]:
    if not shutil.which("xfconf-query"):
        return False, "xfconf-query: command not found"
    value = _xfce_image_style_value(fit_mode)
    changed = 0
    errors: list[str] = []
    for prop in _xfce_image_style_properties():
        rc, out, err = _run_args(
            ["xfconf-query", "-c", "xfce4-desktop", "-p", prop, "-s", str(value)], timeout=10
        )
        if rc != 0:
            rc, out, err = _run_args(
                ["xfconf-query", "-c", "xfce4-desktop", "-p", prop, "-n", "-t", "int", "-s", str(value)],
                timeout=10,
            )
        if rc == 0:
            changed += 1
        elif err or out:
            errors.append(f"xfconf-query {prop}: {err or out}")
    return (changed > 0), (f"updated {changed} Xfce image-style value(s)" if changed else " | ".join(errors[-5:]))


def _set_xfce_wallpaper(path: str) -> tuple[bool, str]:
    abs_path = _ensure_existing_file(path)
    if not shutil.which("xfconf-query"):
        return False, "xfconf-query: command not found"
    # Apply ratio first so the new image is never briefly rendered with the
    # previous persistent Xfce style.
    _set_xfce_fit_mode(_LINUX_FIT_MODE)
    errors: list[str] = []
    success_count = 0
    for prop in _xfce_wallpaper_properties():
        rc, out, err = _run_args(["xfconf-query", "-c", "xfce4-desktop", "-p", prop, "-s", abs_path], timeout=10)
        if rc != 0:
            rc, out, err = _run_args(["xfconf-query", "-c", "xfce4-desktop", "-p", prop, "-n", "-t", "string", "-s", abs_path], timeout=10)
        if rc == 0:
            success_count += 1
        elif err or out:
            errors.append(f"xfconf-query {prop}: {err or out}")
    if success_count:
        ok, current = _get_xfce_wallpaper()
        if ok and _same_existing_file(current, abs_path):
            return True, current
        errors.append("xfconf-query did not verify; read-back=" + (current or "empty"))
    return False, " | ".join(errors[-5:]) or "xfconf-query could not update any wallpaper property"


def _pcmanfm_mode(executable: str, fit_mode: str) -> str:
    mode = _normalize_linux_fit_mode(fit_mode)
    if Path(executable).name == "pcmanfm-qt":
        # PCManFM-Qt exposes no crop/zoom-fill mode in its documented CLI.
        return {"填充": "fit", "适应": "fit", "拉伸": "stretch", "居中": "center", "平铺": "tile"}[mode]
    return {"填充": "crop", "适应": "fit", "拉伸": "stretch", "居中": "center", "平铺": "tile"}[mode]


def _set_pcmanfm_wallpaper(path: str) -> tuple[bool, str]:
    abs_path = _ensure_existing_file(path)
    executable = shutil.which("pcmanfm") or shutil.which("pcmanfm-qt")
    if not executable:
        return False, "pcmanfm/pcmanfm-qt: command not found"
    mode = _pcmanfm_mode(executable, _LINUX_FIT_MODE)
    rc, out, err = _run_args([executable, f"--set-wallpaper={abs_path}", f"--wallpaper-mode={mode}"], timeout=10)
    if rc == 0:
        return True, out
    return False, f"{Path(executable).name}: {err or out or 'no output'}"


def _set_feh_or_nitrogen_wallpaper(path: str) -> tuple[bool, str]:
    abs_path = _ensure_existing_file(path)
    mode = _normalize_linux_fit_mode(_LINUX_FIT_MODE)
    feh_option = {
        "填充": "--bg-fill", "适应": "--bg-max", "拉伸": "--bg-scale",
        "居中": "--bg-center", "平铺": "--bg-tile",
    }[mode]
    nitrogen_option = {
        "填充": "--set-zoom-fill", "适应": "--set-scaled", "拉伸": "--set-auto",
        "居中": "--set-centered", "平铺": "--set-tiled",
    }[mode]
    errors: list[str] = []
    for cmd in (["feh", feh_option, abs_path], ["nitrogen", nitrogen_option, "--save", abs_path]):
        if not shutil.which(cmd[0]):
            continue
        rc, out, err = _run_args(cmd, timeout=10)
        if rc == 0:
            return True, out
        errors.append(f"{cmd[0]}: {err or out or 'no output'}")
    if not errors:
        errors.append("feh/nitrogen: command not found")
    return False, " | ".join(errors)


def _set_linux_wallpaper(path: str) -> None:
    abs_path = _ensure_existing_file(path)
    errors: list[str] = []

    # Route by the active desktop first. Merely having gsettings installed does
    # not mean it owns the desktop (it is commonly present in Xfce/Openbox too);
    # accepting its successful dconf write there creates a false-positive where
    # ShangBackground reports success but the visible wallpaper never changes.
    if _is_kde_session():
        primary_setters = (
            lambda value: _set_kde_wallpaper(value, fill_mode=_kde_fit_mode_value(_LINUX_FIT_MODE)),
        )
    elif _is_xfce_session():
        primary_setters = (_set_xfce_wallpaper,)
    elif _is_lxde_session():
        primary_setters = (_set_pcmanfm_wallpaper,)
    elif _is_gsettings_desktop_session():
        primary_setters = (_set_gnome_wallpaper,)
    else:
        primary_setters = ()

    attempted: set[object] = set()
    for setter in primary_setters:
        attempted.add(setter)
        ok, detail = setter(abs_path)
        if ok:
            return
        errors.append(detail)

    # Unknown/minimal X11 sessions use explicit wallpaper setters. For a known
    # desktop, these are fallbacks only after its own backend has failed.
    fallback_setters = (_set_pcmanfm_wallpaper, _set_feh_or_nitrogen_wallpaper)
    if not _is_kde_session() and not _is_xfce_session() and not _is_lxde_session() and shutil.which("xfconf-query"):
        fallback_setters = (_set_xfce_wallpaper,) + fallback_setters
    if not _is_kde_session() and not _is_xfce_session() and not _is_lxde_session() and shutil.which("gsettings") and _is_gsettings_desktop_session():
        fallback_setters = (_set_gnome_wallpaper,) + fallback_setters
    for setter in fallback_setters:
        if setter in attempted:
            continue
        ok, detail = setter(abs_path)
        if ok:
            return
        errors.append(detail)

    raise RuntimeError(
        "Cannot set wallpaper on Linux. KDE/Plasma requires qdbus6 or qdbus from plasma-workspace/qttools; "
        "GNOME-family desktops require gsettings; XFCE requires xfconf-query; LXDE/minimal sessions can use pcmanfm, feh, or nitrogen. "
        + " | ".join(errors[-8:])
    )


def refresh_shell_ui() -> None:
    """No-op shell repaint hook for non-Windows platforms."""
    return


def set_wallpaper_platform(path: str) -> None:
    _set_linux_wallpaper(path)


def get_current_wallpaper_platform() -> str:
    errors: list[str] = []
    if _is_kde_session():
        getters = (_get_kde_wallpaper,)
    elif _is_xfce_session():
        getters = (_get_xfce_wallpaper,)
    elif _is_gsettings_desktop_session():
        getters = (_get_gnome_wallpaper,)
    else:
        # PCManFM/feh/nitrogen do not expose one portable authoritative query.
        # Probe only concrete desktop backends; do not return an unrelated dconf
        # value simply because gsettings happens to be installed.
        getters = tuple(
            getter for available, getter in (
                (shutil.which("xfconf-query"), _get_xfce_wallpaper),
                (shutil.which("gsettings") and _is_gsettings_desktop_session(), _get_gnome_wallpaper),
            ) if available
        )
    for getter in getters:
        ok, detail = getter()
        if ok:
            return detail
        errors.append(detail)
    raise RuntimeError("无法读取当前 Linux 壁纸: " + (" | ".join(errors[-6:]) or "当前桌面没有可查询的壁纸后端"))


def _kde_fit_mode_value(fit_mode: str) -> int:
    # Plasma's image wallpaper stores a numeric FillMode. The default cropped
    # full-screen mode observed in Plasma is 2; the remaining values mirror the
    # order used by the image wallpaper plugin for scale/stretch/tile/center.
    style_map_kde = {
        "拉伸": 0,
        "适应": 1,
        "填充": 2,
        "平铺": 3,
        "居中": 4,
    }
    return style_map_kde.get(fit_mode, 2)


def _set_kde_fit_mode(fit_mode: str) -> tuple[bool, str]:
    if not _is_kde_session():
        return False, "not a KDE Plasma session"
    if not _qdbus_commands():
        return False, "qdbus: command not found"
    fill_mode = _kde_fit_mode_value(fit_mode)
    script = """
var allDesktops = desktops();
for (var i = 0; i < allDesktops.length; i++) {
    var d = allDesktops[i];
    var plugin = d.wallpaperPlugin || "org.kde.image";
    d.currentConfigGroup = Array("Wallpaper", plugin, "General");
    d.writeConfig("FillMode", %d);
    d.reloadConfig();
}
""" % int(fill_mode)
    ok, output, diagnostics = _run_plasma_script(script, timeout=5)
    detail = output or diagnostics or ("KDE FillMode updated" if ok else "KDE FillMode update failed")
    return bool(ok), detail


def configure_fit_mode(fit_mode, winreg_module=None, log=None):
    """Apply the requested ratio to the active Linux desktop backend.

    The mode is also retained for stateless setters (PCManFM/feh/nitrogen), so
    factory reset and the next wallpaper change cannot silently reuse an old
    hard-coded scale option.
    """
    del winreg_module
    global _LINUX_FIT_MODE
    _LINUX_FIT_MODE = _normalize_linux_fit_mode(fit_mode)
    try:
        style_map_linux = {
            "填充": "zoom",
            "适应": "scaled",
            "拉伸": "stretched",
            "居中": "centered",
            "平铺": "wallpaper",
        }
        option = style_map_linux[_LINUX_FIT_MODE]
        applied = False
        diagnostics: list[str] = []
        if _is_kde_session():
            applied, detail = _set_kde_fit_mode(_LINUX_FIT_MODE)
            if detail:
                diagnostics.append(detail)
        elif _is_xfce_session():
            applied, detail = _set_xfce_fit_mode(_LINUX_FIT_MODE)
            if detail:
                diagnostics.append(detail)
        elif _is_gsettings_desktop_session() and shutil.which("gsettings"):
            rc, out, err = _run_args(
                ["gsettings", "set", "org.gnome.desktop.background", "picture-options", option]
            )
            applied = rc == 0
            if not applied:
                diagnostics.append(err or out or f"gsettings exit {rc}")
        elif _is_lxde_session():
            executable = shutil.which("pcmanfm") or shutil.which("pcmanfm-qt")
            if executable:
                pcman_mode = _pcmanfm_mode(executable, _LINUX_FIT_MODE)
                rc, out, err = _run_args([executable, f"--wallpaper-mode={pcman_mode}"], timeout=10)
                applied = rc == 0
                if not applied:
                    diagnostics.append(err or out or f"{Path(executable).name} exit {rc}")
        if log:
            if applied:
                log(f"Linux 壁纸缩放模式已设置为：{_LINUX_FIT_MODE}")
            elif diagnostics:
                log("Linux 壁纸缩放模式未由当前桌面后端确认：" + " | ".join(diagnostics[-3:]))
            else:
                log(f"Linux 当前桌面没有可持久化的比例后端；将在下次设置壁纸时使用：{_LINUX_FIT_MODE}")
    except Exception as exc:
        if log:
            log(f"Linux fit mode config failed: {exc}")


# ── Bug 5 fix: desktop foreground detection ──────────────────────────────
# Used by MainWindow._is_desktop_foreground() to implement the
# "桌面失焦时暂停" video policy and the HTML wallpaper auto-pause feature.
# Previously this returned True unconditionally on Linux/macOS, silently
# disabling both features.

# Cache the desktop window-class names per session to avoid spawning a
# subprocess on every call (the video focus policy polls every ~1s).
_DESKTOP_WM_CLASSES = frozenset({
    "plasmashell", "plasma-shell",  # KDE Plasma
    "gnome-shell", "gjs", "gnome-shell-extension",  # GNOME
    "xfdesktop",  # XFCE
    "pcmanfm-qt", "pcmanfm",  # LXQt / LXDE
    "mate-desktop", "caja",  # MATE
    "dde-desktop", "deepin-desktop",  # Deepin
    "nemo-desktop",  # Cinnamon
    "budgie-panel",  # Budgie
})
_LAST_FOREGROUND_CACHE: tuple[bool, float] = (True, 0.0)
_FOREGROUND_CACHE_TTL = 0.8  # seconds — avoid spawning xdotool more than ~1x/sec


def is_desktop_foreground() -> bool:
    """Return True when the desktop shell is the active foreground surface.

    Bug 5 fix: implements actual detection for Linux X11 (xdotool + xprop)
    and Linux Wayland (gdbus/qdbus), instead of always returning True.

    - X11: ``xdotool getactivewindow`` → ``xprop -id <wid> WM_CLASS`` →
      check if the WM_CLASS matches a known desktop shell (plasmashell,
      gnome-shell, xfdesktop, etc.).  Also accept windows with the
      ``_NET_WM_WINDOW_TYPE_DESKTOP`` atom.
    - Wayland: no portable foreground-window API.  Try GNOME Shell Eval
      (gdbus) and KWin (qdbus) scripts; fall back to True (conservative)
      if both fail, but log once.
    - Cached for 0.8s to avoid spawning a subprocess on every video-focus
      policy tick (~1s interval).
    """
    global _LAST_FOREGROUND_CACHE
    import time as _time
    now = _time.monotonic()
    cached_val, cached_at = _LAST_FOREGROUND_CACHE
    if now - cached_at < _FOREGROUND_CACHE_TTL:
        return cached_val

    result = _detect_desktop_foreground_uncached()
    _LAST_FOREGROUND_CACHE = (result, now)
    return result


def _detect_desktop_foreground_uncached() -> bool:
    """Spawn the appropriate subprocess(es) to detect the foreground window."""
    import os as _os
    import sys as _sys
    is_wayland = (
        _sys.platform.startswith("linux")
        and (
            _os.environ.get("WAYLAND_DISPLAY")
            or _os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        )
    )
    if is_wayland:
        return _detect_desktop_foreground_wayland()
    return _detect_desktop_foreground_x11()


def _detect_desktop_foreground_x11() -> bool:
    """X11: use xdotool + xprop to identify the foreground window's WM_CLASS."""
    if not shutil.which("xdotool") or not shutil.which("xprop"):
        # Tools not available — conservatively return True (don't pause video).
        return True
    try:
        rc, out, _err = _run_args(["xdotool", "getactivewindow"], timeout=2)
        if rc != 0 or not out.strip():
            return True
        wid = out.strip()
        # Check WM_CLASS first — fastest path.
        rc, out, _err = _run_args(
            ["xprop", "-id", wid, "WM_CLASS", "_NET_WM_WINDOW_TYPE"],
            timeout=2,
        )
        if rc != 0:
            return True
        text = out or ""
        # Look for known desktop shell WM_CLASS values (case-insensitive).
        for cls in _DESKTOP_WM_CLASSES:
            if cls in text.lower():
                return True
        # Also accept windows explicitly typed as desktop.
        if "_NET_WM_WINDOW_TYPE_DESKTOP" in text:
            return True
        return False
    except Exception:
        return True


def _detect_desktop_foreground_wayland() -> bool:
    """Wayland: try GNOME Shell Eval and KWin scripting; fall back to True."""
    # Try GNOME Shell first.
    if shutil.which("gdbus"):
        try:
            # GNOME Shell Eval runs JavaScript in the shell process.
            # Returns "true" if any window on the active workspace has
            # window_type == DESKTOP.
            script = (
                "global.workspace_manager.get_active_workspace()"
                ".list_windows().some(w => w.window_type == Meta.WindowType.DESKTOP)"
            )
            rc, out, _err = _run_args(
                [
                    "gdbus", "call", "--session",
                    "--dest", "org.gnome.Shell",
                    "--object-path", "/org/gnome/Shell",
                    "--method", "org.gnome.Shell.Eval",
                    script,
                ],
                timeout=3,
            )
            if rc == 0 and out:
                # Output format: ('true', '"<js_result>"')  or  ('false', '')
                # The second element is the JS expression's value as a string.
                if "'true'" in out and "true" in out.split(",", 1)[-1].lower():
                    return True
                if "'true'" in out and "false" in out.split(",", 1)[-1].lower():
                    return False
        except Exception:
            pass
    # Try KWin scripting.
    if shutil.which("qdbus") or shutil.which("qdbus6"):
        qdbus = "qdbus6" if shutil.which("qdbus6") else "qdbus"
        try:
            # KWin's queryWindowInfo requires user interaction; instead use
            # the ScriptConsole via org.kde.kwin.Scripting.  This is complex;
            # fall back to True (conservative) for now.
            # A simpler heuristic: check if plasmashell is in the list of
            # running processes — not perfect but better than always-True.
            rc, out, _err = _run_args(
                [qdbus, "org.kde.KWin", "/KWin", "org.kde.KWin.queryWindowInfo"],
                timeout=3,
            )
            # queryWindowInfo usually requires interaction; if it fails, fall back.
            if rc == 0 and out:
                text = out.lower()
                for cls in _DESKTOP_WM_CLASSES:
                    if cls in text:
                        return True
                return False
        except Exception:
            pass
    # Fallback: conservative True (don't pause video on Wayland where we
    # can't reliably detect the desktop).  The video focus policy is opt-in
    # so this only affects users who explicitly enabled "桌面失焦时暂停".
    return True
