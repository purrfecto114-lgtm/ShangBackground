#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install -r requirements-macos.txt
rm -rf build dist

PYI_ARGS=(
  --noconfirm
  --clean
  --onedir
  --windowed
  --contents-directory "."
  --name "ShangBackground"
  --paths "src"
  --add-data "src/img:img"
  --add-data "src/lang:lang"
  --add-data "src/main_version_info.txt:."
  --add-data "fonts:fonts"
  --hidden-import "PySide6.QtSvg"
  --hidden-import "PySide6.QtSvgWidgets"
  --hidden-import "ui.probability_dialog"
  --hidden-import "ui.sidebar"
  --hidden-import "platform_adapters.video"
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
  --exclude-module "PySide6.QtWebEngineCore"
  --exclude-module "PySide6.QtWebEngineWidgets"
  --exclude-module "PySide6.QtQuick"
  --exclude-module "PySide6.QtQml"
  --exclude-module "PySide6.QtMultimedia"
  --exclude-module "PySide6.QtPdf"
  --exclude-module "PySide6.QtSql"
)

if [[ -f "src/img/LOGO.icns" ]]; then
  PYI_ARGS+=(--icon "src/img/LOGO.icns")
fi
if command -v upx >/dev/null 2>&1; then
  PYI_ARGS+=(--upx-dir "$(dirname "$(command -v upx)")")
elif [[ -n "${UPX_DIR:-}" ]]; then
  PYI_ARGS+=(--upx-dir "$UPX_DIR")
fi

python3 -m PyInstaller "${PYI_ARGS[@]}" "src/main.py"

echo "Build output: dist/ShangBackground.app"
echo "Run: open dist/ShangBackground.app"
