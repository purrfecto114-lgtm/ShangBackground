# ShangBackground build tools

The folder is a deterministic front end for PyInstaller and Nuitka. Both
backends consume the same feature manifest and MPV selection.

## HTML packaging rules

- HTML wallpaper uses **pywebview + the operating-system native WebView**.
- Qt QML/Qt Quick/QtWebEngine are deliberately excluded.
- PyInstaller standalone release builds default to an `_internal` support directory.
- The custom `hook-webview.py` collects the exact native backend and Python.NET
  chain on Windows while upstream PyInstaller hooks retain ownership of PySide6.
- Nuitka 4.1.3's pywebview plugin is disabled for HTML builds because it omits
  `webview.platforms.win32`; the builder explicitly includes the complete native
  backend chain instead.

## Commands

```bash
python build_tools/build.py --tool pyinstaller --profile full --mode standalone
python build_tools/build.py --tool nuitka --profile full --mode standalone
python build_tools/build.py --tool pyinstaller --target windows --skip-install --dry-run
python build_tools/build.py --tool nuitka --target windows --skip-install --dry-run
python build_tools/build.py self-test
```

After a real build, structure validation checks the executable, feature manifest,
resources, and the PyInstaller `_internal` directory. Runtime acceptance still
needs a clean target machine and the application's `--doctor-json` / native HTML
self-test.
