#!/usr/bin/env python3
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import stat
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def load(rel: str, name: str):
    path=ROOT/rel
    spec=importlib.util.spec_from_file_location(name,path)
    assert spec and spec.loader
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod


def main()->int:
    win=load('Windows.ver/src/platform_adapters/capabilities.py','caps_win')
    wc=win.probe_capabilities()
    assert wc['static_wallpaper']['state']=='supported'
    assert wc['video_wallpaper']['state']=='best_effort'
    assert 'undocumented' in str(wc['video_wallpaper']['limitations']).lower()

    lin=load('Linux.ver(beta)/src/platform_adapters/capabilities.py','caps_linux')
    fake=lambda name: f'/usr/bin/{name}' if name in {'gsettings','mpvpaper','mpv','xwinwrap'} else None
    gnome=lin.probe_capabilities({'XDG_SESSION_TYPE':'wayland','XDG_CURRENT_DESKTOP':'GNOME'},fake)
    assert gnome['static_wallpaper']['runtime_ready'] is True
    assert gnome['video_wallpaper']['state']=='unsupported'
    assert gnome['html_wallpaper']['state']=='unsupported'
    assert gnome['global_hotkeys']['state']=='unsupported'
    sway=lin.probe_capabilities({'XDG_SESSION_TYPE':'wayland','XDG_CURRENT_DESKTOP':'sway','SWAYSOCK':'/tmp/sway'},fake)
    assert sway['video_wallpaper']['state']=='best_effort' and sway['video_wallpaper']['runtime_ready'] is True
    x11=lin.probe_capabilities({'XDG_SESSION_TYPE':'x11','DISPLAY':':1'},fake)
    assert x11['video_wallpaper']['runtime_ready'] is True
    assert x11['html_wallpaper']['state']=='best_effort'

    mac=load('MacOS.ver(alpha)/src/platform_adapters/capabilities.py','caps_mac')
    mc=mac.probe_capabilities()
    assert mc['static_wallpaper']['state']=='supported'
    assert mc['video_wallpaper']['state']=='best_effort'
    assert mc['html_wallpaper']['state']=='best_effort'

    # Source-level guards for previously false-positive paths.
    linux_video=(ROOT/'Linux.ver(beta)/src/platform_adapters/video.py').read_text(encoding='utf-8')
    assert '"*", abs_video' in linux_video and '"ALL", abs_video' not in linux_video
    assert '_probe_executable' in linux_video and '_wayland_layer_shell_session' in linux_video
    assert not (ROOT/'Linux.ver(beta)/src/bin/mpv').exists()
    linux_html=(ROOT/'Linux.ver(beta)/src/platform_adapters/html_wallpaper.py').read_text(encoding='utf-8')
    assert 'SHANGBACKGROUND_ALLOW_UNSAFE_WAYLAND_HTML' in linux_html
    mac_video=(ROOT/'MacOS.ver(alpha)/src/platform_adapters/video.py').read_text(encoding='utf-8')
    assert '_resolve_mpv' not in mac_video
    mac_html=(ROOT/'MacOS.ver(alpha)/src/platform_adapters/run_html_wallpaper.py').read_text(encoding='utf-8')
    assert '_configure_macos_desktop_window' in mac_html
    assert 'CGWindowLevelForKey' in mac_html
    for tree in ('Windows.ver','MacOS.ver(alpha)'):
        html=(ROOT/tree/'src/platform_adapters/run_html_wallpaper.py').read_text(encoding='utf-8')
        assert 'QTWEBENGINE_DISABLE_SANDBOX", "1"' not in html

    # A present but non-runnable Linux backend must be rejected.
    video=load('Linux.ver(beta)/src/platform_adapters/video.py','linux_video_probe')
    with tempfile.TemporaryDirectory() as td:
        broken=Path(td)/'mpv'; broken.write_text('#!/bin/sh\necho missing-library >&2\nexit 127\n',encoding='utf-8'); broken.chmod(broken.stat().st_mode|stat.S_IXUSR)
        ok, detail=video._probe_executable(str(broken),'--version')
        assert ok is False and 'missing-library' in detail
    print('PASS platform feasibility guards')
    return 0

if __name__=='__main__': raise SystemExit(main())
