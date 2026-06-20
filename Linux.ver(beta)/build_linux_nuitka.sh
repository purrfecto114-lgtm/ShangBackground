#!/usr/bin/env bash
# Nuitka --mode=standalone (onedir equivalent) build for Linux.
# Uses UPX if available in PATH or via $UPX_DIR.
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install -r requirements-linux.txt
python3 -m pip install nuitka
rm -rf build dist ShangBackground-linux-x86_64.tar.gz

# Resolve UPX binary: prefer PATH, then $UPX_DIR/upx, finally project-local upx/upx
UPX_ARG=()
if command -v upx >/dev/null 2>&1; then
  UPX_ARG=(--upx-binary="$(command -v upx)")
elif [[ -n "${UPX_DIR:-}" && -x "${UPX_DIR}/upx" ]]; then
  UPX_ARG=(--upx-binary="${UPX_DIR}/upx")
elif [[ -x "upx/upx" ]]; then
  UPX_ARG=(--upx-binary="upx/upx")
fi

python3 -m nuitka \
  --mode=standalone \
  --assume-yes-for-downloads \
  --remove-output \
  --output-dir=dist \
  --output-filename=ShangBackground \
  --include-data-dir="src/img=img" \
  --include-data-dir="src/lang=lang" \
  --include-data-files="src/main_version_info.txt=main_version_info.txt" \
  --include-data-dir="fonts=fonts" \
  --include-package=app \
  --include-package=core \
  --include-package=services \
  --include-package=platform_adapters \
  --include-package=ui \
  --include-module=PySide6.QtSvg \
  --include-module=PySide6.QtSvgWidgets \
  --include-module=ui.probability_dialog \
  --include-module=ui.sidebar \
  --include-module=platform_adapters.video \
  --include-module=ui.dialog_style \
  --nofollow-import-to=tkinter \
  --nofollow-import-to=PyQt5 \
  --nofollow-import-to=PyQt6 \
  --nofollow-import-to=PySide2 \
  --nofollow-import-to=matplotlib \
  --nofollow-import-to=pandas \
  --nofollow-import-to=scipy \
  --nofollow-import-to=IPython \
  --nofollow-import-to=notebook \
  --nofollow-import-to=pytest \
  --enable-plugin=pyside6 \
  "${UPX_ARG[@]}" \
  "src/main.py"

# Nuitka emits dist/main.dist/main; rename for parity with the PyInstaller layout.
if [[ -d "dist/main.dist" ]]; then
  mv "dist/main.dist" "dist/ShangBackground"
fi
chmod +x "dist/ShangBackground/ShangBackground"
tar -C dist -czf ShangBackground-linux-x86_64.tar.gz ShangBackground

echo "Build output: dist/ShangBackground/"
echo "Archive: ShangBackground-linux-x86_64.tar.gz"
echo "Run: ./dist/ShangBackground/ShangBackground"
