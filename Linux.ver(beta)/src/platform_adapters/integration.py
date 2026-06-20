from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlparse


def _run_args(args: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """Run a command without a shell and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return -1, "", str(exc)


def _ensure_existing_file(path: str) -> str:
    """Return an absolute path and fail early with a useful error."""
    abs_path = str(Path(path).expanduser().resolve())
    if not Path(abs_path).is_file():
        raise FileNotFoundError(f"Wallpaper file does not exist: {abs_path}")
    return abs_path


def _file_uri(path: str) -> str:
    return Path(path).expanduser().resolve().as_uri()


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


def _verify_kde_wallpaper(abs_path: str, *, timeout: float = 2.5) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    last_detail = ""
    while True:
        ok, detail = _get_kde_wallpaper()
        last_detail = detail
        if ok and _same_existing_file(detail, abs_path):
            return True, detail
        if time.monotonic() >= deadline:
            break
        time.sleep(0.15)
    return False, last_detail


def _set_kde_wallpaper(path: str, *, fill_mode: int | None = None) -> tuple[bool, str]:
    """Set a real static KDE/Plasma wallpaper and verify that Plasma read it back."""
    abs_path = _ensure_existing_file(path)
    uri = _file_uri(abs_path)
    errors: list[str] = []

    # This helper is convenient when it works, but Plasma 6 installations can
    # return 0 while not applying anything, so success is never trusted without
    # a read-back verification.
    if shutil.which("plasma-apply-wallpaperimage"):
        rc, out, err = _run_args(["plasma-apply-wallpaperimage", abs_path], timeout=10)
        if rc == 0:
            verified, current = _verify_kde_wallpaper(abs_path)
            if verified:
                return True, current
            errors.append("plasma-apply-wallpaperimage: command returned success but read-back was " + (current or "empty"))
        elif err or out:
            errors.append(f"plasma-apply-wallpaperimage: {err or out}")

    # Official Plasma scripting uses file:// URIs in the org.kde.image General/Image key.
    for image_value in (uri, abs_path):
        script = _kde_set_script(image_value, fill_mode=fill_mode)
        ok, out, detail = _run_plasma_script(script, timeout=10, allow_dbus_send=True)
        if not ok:
            errors.append(detail)
            continue
        verified, current = _verify_kde_wallpaper(abs_path)
        if verified:
            return True, current
        errors.append(f"evaluateScript({image_value!r}) did not verify; output={_summarize_command_output(out)}, read-back={current or 'empty'}")

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


def _set_xfce_wallpaper(path: str) -> tuple[bool, str]:
    abs_path = _ensure_existing_file(path)
    if not shutil.which("xfconf-query"):
        return False, "xfconf-query: command not found"
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


def _set_pcmanfm_wallpaper(path: str) -> tuple[bool, str]:
    abs_path = _ensure_existing_file(path)
    if not shutil.which("pcmanfm"):
        return False, "pcmanfm: command not found"
    rc, out, err = _run_args(["pcmanfm", f"--set-wallpaper={abs_path}", "--wallpaper-mode=fit"], timeout=10)
    if rc == 0:
        return True, out
    return False, f"pcmanfm: {err or out or 'no output'}"


def _set_feh_or_nitrogen_wallpaper(path: str) -> tuple[bool, str]:
    abs_path = _ensure_existing_file(path)
    errors: list[str] = []
    for cmd in (["feh", "--bg-scale", abs_path], ["nitrogen", "--set-scaled", abs_path]):
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

    if _is_kde_session():
        ok, detail = _set_kde_wallpaper(abs_path)
        if ok:
            return
        errors.append("KDE/Plasma: " + detail)
    else:
        ok, detail = _set_gnome_wallpaper(abs_path)
        if ok:
            return
        errors.append(detail)

        ok, detail = _set_xfce_wallpaper(abs_path)
        if ok:
            return
        errors.append(detail)

    # Cross-desktop fallbacks are still useful in LXDE/openbox/minimal sessions,
    # but KDE is intentionally not considered successful unless the KDE backend
    # verified the target image.
    for setter in (_set_pcmanfm_wallpaper, _set_feh_or_nitrogen_wallpaper):
        ok, detail = setter(abs_path)
        if ok:
            return
        errors.append(detail)

    raise RuntimeError(
        "Cannot set wallpaper on Linux. KDE/Plasma requires qdbus6 or qdbus from plasma-workspace/qttools; "
        "GNOME requires gsettings; XFCE requires xfconf-query; LXDE/minimal sessions can use pcmanfm, feh, or nitrogen. "
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
        ok, detail = _get_kde_wallpaper()
        if ok:
            return detail
        # KDE command/backend failures are real diagnostics; an empty string is
        # handled inside _get_kde_wallpaper as a supported non-static wallpaper.
        errors.append(f"KDE/Plasma: {detail}")
    else:
        ok, detail = _get_gnome_wallpaper()
        if ok:
            return detail
        errors.append(detail)

        ok, detail = _get_xfce_wallpaper()
        if ok:
            return detail
        errors.append(detail)

    raise RuntimeError("无法读取当前 Linux 壁纸: " + " | ".join(errors[-6:]))


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


def _set_kde_fit_mode(fit_mode: str) -> None:
    if not _is_kde_session() or not _qdbus_commands():
        return
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
    _run_plasma_script(script, timeout=5)


def configure_fit_mode(fit_mode, winreg_module=None, log=None):
    """Apply supported desktop-environment scaling while preserving the shared API."""
    del winreg_module
    try:
        style_map_linux = {
            "填充": "zoom",
            "适应": "scaled",
            "拉伸": "stretched",
            "居中": "centered",
            "平铺": "wallpaper",
        }
        option = style_map_linux.get(fit_mode, "zoom")
        if _is_kde_session():
            _set_kde_fit_mode(fit_mode)
        elif shutil.which("gsettings"):
            _run_args(["gsettings", "set", "org.gnome.desktop.background", "picture-options", option])
    except Exception as exc:
        if log:
            log(f"Linux fit mode config failed: {exc}")
