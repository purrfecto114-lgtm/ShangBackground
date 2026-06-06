#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install -r requirements-linux.txt
rm -rf build dist ShangBackground-linux-x86_64.tar.gz

PYI_ARGS=(
  --noconfirm
  --clean
  --onedir
  --contents-directory "."
  --name "ShangBackground"
  --paths "src"
  --add-data "src/img:img"
  --add-data "src/lang:lang"
  --add-data "src/settings.json:."
  --add-data "fonts:fonts"
  --hidden-import "PySide6.QtSvg"
  --hidden-import "PySide6.QtSvgWidgets"
  --exclude-module "tkinter"
  --exclude-module "PyQt5"
  --exclude-module "PyQt6"
  --exclude-module "PySide2"
  --exclude-module "matplotlib"
  --exclude-module "pandas"
  --exclude-module "scipy"
  --exclude-module "IPython"
  --exclude-module "notebook"
  --exclude-module "pytest"
)

if command -v upx >/dev/null 2>&1; then
  PYI_ARGS+=(--upx-dir "$(dirname "$(command -v upx)")")
elif [[ -n "${UPX_DIR:-}" ]]; then
  PYI_ARGS+=(--upx-dir "$UPX_DIR")
fi

python3 -m PyInstaller "${PYI_ARGS[@]}" "src/main.py"

chmod +x "dist/ShangBackground/ShangBackground"
tar -C dist -czf ShangBackground-linux-x86_64.tar.gz ShangBackground

echo "Build output: dist/ShangBackground/"
echo "Archive: ShangBackground-linux-x86_64.tar.gz"
echo "Run: ./dist/ShangBackground/ShangBackground"
