Optional versioned native runtimes live here. Source archives do not ship third-party binaries.

Recommended Windows workflow:
  python build_tools/build.py mpv download --channel stable
  python build_tools/build.py mpv list
  python build_tools/build.py mpv verify --version auto

Selection order:
  1. Flat local payloads already present under src/bin/mpv/<target>/, src/bin/mpv/,
     src/bin/<target>/ or src/bin/.
  2. Managed runtimes below src/bin/mpv/<target>/<arch>/<runtime-id>/.
  3. Linux target-system libmpv or macOS AVFoundation when no local payload is selected.

Windows builds never download native code implicitly. Run the explicit ``mpv download``
command above before choosing bundled/auto for a full video build.

Managed layout:
  src/bin/mpv/<target>/<arch>/<runtime-id>/
  src/bin/mpv/<target>/<arch>/ACTIVE

A selected build copies only one runtime to the packaged path:
  bin/mpv/

Examples:
  src/bin/mpv/windows/x86_64/v0.41.0/mpv.exe
  src/bin/mpv/windows/arm64/v0.41.0/mpv.exe
  src/bin/mpv/linux/x86_64/local/libmpv.so.2

Flat local payloads are a supported explicit override and are checked before version metadata.
macOS uses the native AVFoundation runner by default and ignores libmpv payloads.

Windows prefers the bundled mpv executable + JSON IPC; a libmpv-only payload remains a compatibility fallback. python-mpv is not required.
Verify origin, architecture, licenses, dependencies, and SHA-256 before distribution.
