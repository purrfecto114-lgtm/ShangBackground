@echo off
setlocal
cd /d "%~dp0"

python -m pip install -r requirements-windows.txt || exit /b 1
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

set "UPX_ARG=--noupx"
if /I not "%SHANG_NO_UPX%"=="1" if exist "upx\upx.exe" set UPX_ARG=--upx-dir "upx"

python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onedir ^
  --windowed ^
  --contents-directory "." ^
  --name "ShangBackground" ^
  --paths "src" ^
  --icon "src\img\LOGO.ico" ^
  --version-file "src\main_version_info.txt" ^
  --add-data "src\img;img" ^
  --add-data "src\lang;lang" ^
  --add-data "src\main_version_info.txt;." ^
  --add-data "fonts;fonts" ^
  --hidden-import "PySide6.QtSvg" ^
  --hidden-import "PySide6.QtSvgWidgets" ^
  --hidden-import "ui.probability_dialog" ^
  --hidden-import "ui.sidebar" ^
  --hidden-import "platform_adapters.video" ^
  --exclude-module "tkinter" ^
  --exclude-module "PyQt5" ^
  --exclude-module "PyQt6" ^
  --exclude-module "PySide2" ^
  --exclude-module "matplotlib" ^
  --exclude-module "pandas" ^
  --exclude-module "scipy" ^
  --exclude-module "IPython" ^
  --exclude-module "notebook" ^
  --exclude-module "pytest" ^
  %UPX_ARG% ^
  "src\main.pyw" || exit /b 1

echo Build output: dist\ShangBackground\ShangBackground.exe
endlocal
