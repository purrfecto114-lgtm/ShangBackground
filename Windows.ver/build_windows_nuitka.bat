@echo off
setlocal
cd /d "%~dp0"

python -m pip install -r requirements-windows.txt || exit /b 1
python -m pip install nuitka || exit /b 1
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

python -m nuitka --mode=standalone --windows-console-mode=disable --assume-yes-for-downloads --remove-output --output-dir=dist --output-filename=ShangBackground.exe --windows-icon-from-ico="src\img\LOGO.ico" --windows-file-version=1.3.6.0 --windows-product-version=1.3.6.0 --windows-company-name="XXDZ工作室" --windows-file-description="上一个桌面背景" --windows-product-name="上一个桌面背景" --windows-legal-copyright="Copyright (C) 小小电子xxdz" --include-data-dir="src\img=img" --include-data-dir="src\lang=lang" --include-data-files="src\main_version_info.txt=main_version_info.txt" --include-data-dir="fonts=fonts" --include-package=app --include-package=core --include-package=services --include-package=platform_adapters --include-package=ui --include-module=PySide6.QtSvg --include-module=PySide6.QtSvgWidgets --include-module=ui.probability_dialog --include-module=ui.sidebar --include-module=platform_adapters.video --include-module=ui.dialog_style --nofollow-import-to=tkinter --nofollow-import-to=PyQt5 --nofollow-import-to=PyQt6 --nofollow-import-to=PySide2 --nofollow-import-to=matplotlib --nofollow-import-to=pandas --nofollow-import-to=scipy --nofollow-import-to=IPython --nofollow-import-to=notebook --nofollow-import-to=pytest --enable-plugin=pyside6 --upx-binary="upx\upx.exe" "src\main.pyw" || exit /b 1

REM Nuitka emits dist\main.pyw.dist\ShangBackground.exe; rename for parity with the PyInstaller layout.
if exist "dist\main.pyw.dist" ren "dist\main.pyw.dist" "ShangBackground"

echo Build output: dist\ShangBackground\ShangBackground.exe
endlocal
