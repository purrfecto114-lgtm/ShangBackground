# ShangBackground build tools

This directory provides one release pipeline for PyInstaller and Nuitka. Both
backends consume the same feature manifest, architecture selection, dependency
policy, staging layout, validation gates, and atomic publication step.

## Release contract

A real build is publishable only when every stage succeeds:

1. select or create the project-local `.venv` (or an explicit
   `SHANGBACKGROUND_BUILD_PYTHON` interpreter);
2. install the selected profile requirements and run `pip check`;
3. verify host OS, Python architecture, backend versions, native build tools,
   and GUI runtime prerequisites;
4. compile into a fresh staging directory that cannot reuse an old artifact;
5. validate resources, feature manifest, native libraries, backend report, and
   the frozen executable itself;
6. start a real Qt widget with the target platform plugin (`xcb` under Xvfb on
   Linux), then atomically replace the last published release.

An interrupted or failed build discards staging and preserves the last validated
release. `--skip-validate` is accepted only for `--dry-run`; it cannot weaken a
publishable build.

## Platform rules

- PyInstaller and Nuitka release builds run on the target operating system.
  Cross-target commands are planning-only dry runs.
- The output architecture must match the selected build Python and any bundled
  MPV runtime.
- macOS releases use standalone `.app` bundles; macOS onefile builds are
  rejected.
- Onefile is a secondary distribution mode. Validate standalone first.
- Linux compatibility inherits the build host's glibc; build on the oldest
  distribution version that the release intends to support.

### Linux release host

The builder checks these before compilation:

- `ldd`, `xvfb-run`, `Xvfb`, and `xauth`;
- PyInstaller: `objdump` and `objcopy` (normally from `binutils`);
- Nuitka: a C11-capable GCC, Clang, or Zig compiler;
- a loadable PySide6 XCB platform plugin, including `libxcb-cursor.so.0`.

Typical Debian/Ubuntu runtime/build packages include `libxcb-cursor0`, `xvfb`,
`xauth`, and `binutils`. Fedora/RHEL commonly provides the cursor library as
`xcb-util-cursor`.

The frozen Linux acceptance test explicitly forces `QT_QPA_PLATFORM=xcb` and
removes host Python/Qt/library-path overrides. An `offscreen` or `minimal` Qt
startup therefore cannot masquerade as a successful desktop GUI build.

## HTML packaging rules

- HTML wallpaper uses **pywebview + the operating-system native WebView**.
- Qt QML/Qt Quick/QtWebEngine are deliberately excluded.
- PyInstaller standalone releases use an `_internal` support directory.
- The custom `hook-webview.py` collects the exact native backend and Python.NET
  chain on Windows while upstream hooks retain ownership of PySide6.
- Nuitka 4.1.3's pywebview plugin is disabled for HTML builds because it omits
  `webview.platforms.win32`; the builder includes the complete native backend
  chain explicitly.

## Commands

```bash
python build_tools/build.py --tool pyinstaller --profile full --mode standalone
python build_tools/build.py --tool nuitka --profile full --mode standalone
python build_tools/build.py --tool pyinstaller --target windows --skip-install --dry-run
python build_tools/build.py --tool nuitka --target windows --skip-install --dry-run
python build_tools/build.py self-test
python build_tools/build.py self-test --dynamic --dynamic-tool pyinstaller
```

`self-test` checks the platform-independent command contract. The dynamic mode
performs a real core-only frozen build and runs the same publication gates as a
release build; it is expected to fail rather than publish when the host is
missing a required native dependency.
