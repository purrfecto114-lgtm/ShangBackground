# Bundling mpv into ShangBackground

This document explains how to embed the **mpv** media player binary into the
ShangBackground application package, so video wallpaper mode works in
packaged builds (Nuitka onefile/standalone, PyInstaller onedir, Inno Setup
installer) without requiring the user to install mpv system-wide.

## Background

ShangBackground's video wallpaper mode relies on an external player process
to render looping video behind desktop icons.  On each platform the lookup
order is:

| Platform | Preferred backend | Fallback backend |
|----------|-------------------|------------------|
| Windows  | mpv (embeds into WorkerW window via `--wid`) | VLC (`--video-wallpaper`) |
| Linux    | X11: `xwinwrap` + mpv; Wayland: `mpvpaper` | — |
| macOS    | AVFoundation (native, via pyobjc) | mpv (when AVFoundation missing) |

Before this change, every platform relied on `shutil.which("mpv")` plus
platform-specific registry / common-dir lookups.  Packaged builds therefore
required the user to install mpv separately, which is a friction point for
non-technical users.  v1.4.2 introduces a `bin/` directory inside each
platform source tree; placing the platform-appropriate mpv binary there
makes the build self-contained.

## How to bundle mpv

### Windows

1. Download the latest **mpv x86_64** build from
   https://sourceforge.net/projects/mpv-player-windows/files/64bit/
   (look for `mpv-x86_64-*.7z`).
2. Extract the archive and copy `mpv.exe` to:
   ```
   Windows.ver/src/bin/mpv.exe
   ```
3. Run any of the build scripts:
   - `Windows.ver/build_windows_nuitka.bat` (Nuitka standalone)
   - `Windows.ver/build_windows_nuitka_onefile.bat` (Nuitka onefile)
   - `Windows.ver/build_windows_onedir.bat` (PyInstaller onedir)
   - `python scripts/build_nuitka.py --platform windows`
4. Verify the binary ended up in the dist:
   - Nuitka standalone: `build/nuitka/windows/ShangBackground.dist/bin/mpv.exe`
   - Nuitka onefile: embedded inside `ShangBackground.exe` (extracted to a
     temp dir at runtime)
   - PyInstaller: `dist/ShangBackground/bin/mpv.exe`

The Inno Setup installer (`scripts/shangbackground.iss`) uses
`recursesubdirs` to pull in the entire `.dist` folder, so `bin/mpv.exe` is
included automatically.

### Linux

1. Install mpv via your distro's package manager:
   - Debian/Ubuntu: `sudo apt install mpv`
   - Fedora: `sudo dnf install mpv`
   - Arch: `sudo pacman -S mpv`
2. Copy the binary into the source tree:
   ```bash
   cp $(command -v mpv) "Linux.ver(beta)/src/bin/mpv"
   chmod +x "Linux.ver(beta)/src/bin/mpv"
   ```
3. Run a build script:
   - `Linux.ver(beta)/build_linux_nuitka.sh` (Nuitka standalone)
   - `Linux.ver(beta)/build_linux_onedir.sh` (PyInstaller onedir)
   - `python scripts/build_nuitka.py --platform linux`

**Note**: bundling a distro-packaged mpv may pull in shared library
dependencies that aren't present on the target system.  For maximum
portability, build a statically-linked mpv from source
(https://github.com/mpv-player/mpv#building-from-source) or use
[mpv-appimage](https://github.com/mpv-player/mpv/wiki/AppImage).  When in
doubt, leave `bin/` empty and let the app fall back to the user's system
mpv.

**Wayland note**: mpvpaper is a separate binary that is NOT auto-bundled.
Wayland users still need to install mpvpaper system-wide.

### macOS

1. Install mpv via Homebrew:
   ```bash
   brew install mpv
   ```
2. Copy the binary into the source tree:
   ```bash
   cp $(brew --prefix)/bin/mpv "MacOS.ver(alpha)/src/bin/mpv"
   chmod +x "MacOS.ver(alpha)/src/bin/mpv"
   ```
3. Run a build script:
   - `MacOS.ver(alpha)/build_macos_onedir.sh` (PyInstaller onedir)
   - `python scripts/build_nuitka.py --platform macos`

**Note**: on macOS the default video backend is AVFoundation (native,
via pyobjc).  Bundled mpv is only used as a **fallback** when AVFoundation
dependencies are missing.  If you want to force mpv as the primary backend,
you would need to modify `MacOS.ver(alpha)/src/platform_adapters/video.py`
`start_video_wallpaper()` to prefer `_resolve_mpv()` over the AVPlayer path.

## How the resolution works at runtime

All three platforms share the same lookup order via
`app.paths.mpv_bundled_exe()`:

```python
def mpv_bundled_exe() -> str | None:
    name = "mpv.exe" if sys.platform.startswith("win") else "mpv"
    return bundled_bin_path(name)  # looks in RESOURCE_ROOT / "bin"
```

`RESOURCE_ROOT` is resolved by `app.paths` to:
- **Source runs**: `<platform>/src/`
- **Nuitka onefile**: the temporary extraction directory
- **Nuitka standalone / PyInstaller onedir**: the dist directory
- **Inno Setup install**: `{app}/` (where `bin/mpv.exe` lands)

The platform `video.py` then calls (in order):
1. `mpv_bundled_exe()` — bundled binary, preferred
2. `shutil.which("mpv")` — system PATH
3. **(Windows only)** Registry `App Paths` / `open-command` entries
4. **(Windows only)** Common install dirs (Program Files, scoop, etc.)
5. **(Windows only)** VLC as a final fallback

## Licensing

mpv is licensed under **GPL-2.0-or-later** (with LGPL-2.1-or-later portions
for some libraries).  Bundling mpv with this application triggers GPL
distribution obligations:

1. You must include the mpv source code or a written offer to supply it.
2. You must include the mpv LICENSE file alongside the binary.
3. The combined work becomes GPL-licensed.

If you do not want to redistribute mpv under GPL terms, simply leave the
`bin/` directory empty — the app will fall back to the system-installed
mpv or VLC automatically, and no GPL obligations are triggered.

For more details see https://github.com/mpv-player/mpv/blob/master/COPYING
and https://www.gnu.org/licenses/gpl-2.0.html.

## Verifying the bundle works

After building, verify the bundled mpv is reachable:

```python
# Run from inside the packaged dist directory
python -c "from app.paths import mpv_bundled_exe; print(mpv_bundled_exe())"
# Expected: /path/to/dist/bin/mpv(.exe)
```

Or just launch the app, switch to "视频" mode, pick a video file, and click
"启动".  The status bar will show "mpv 启动后立即退出" if the bundled
binary is broken (e.g. missing DLL dependencies on Windows), or
"未找到 mpv" if the binary isn't where the app expects it.

## Troubleshooting

**Windows**: `mpv.exe` won't run after extraction.  This is usually missing
Visual C++ Runtime.  Either bundle `vcruntime140.dll` alongside `mpv.exe`,
or use the static-linked mpv build from
https://sourceforge.net/projects/mpv-player-windows/files/64bit/.

**Linux**: bundled `mpv` fails with `error while loading shared libraries`.
The binary was built against shared libs that aren't on the target system.
Either:
- Build a static mpv from source, or
- Use `ldd mpv` to identify missing libs and bundle them alongside, or
- Leave `bin/` empty and rely on the system mpv.

**macOS**: bundled `mpv` shows "cannot be opened because the developer
cannot be verified".  Either:
- Codesign the binary: `codesign --force --sign - bin/mpv`, or
- Tell users to `xattr -dr com.apple.quarantine /Applications/ShangBackground.app`,
  or
- Notarize the entire `.app` bundle (which transitively covers `bin/mpv`).
