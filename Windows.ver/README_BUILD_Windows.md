# Windows.ver 构建说明

建议在 Windows 10/11 + Python 3.10/3.11 虚拟环境中构建。

```bat
cd Windows.ver
python -m pip install -r requirements-windows.txt
build_windows_onedir.bat
.\dist\ShangBackground\ShangBackground.exe
```

完整 onedir 命令：

```bat
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onedir ^
  --windowed ^
  --contents-directory "." ^
  --name "ShangBackground" ^
  --icon "src\img\LOGO.ico" ^
  --version-file "src\main_version_info.txt" ^
  --paths "src" ^
  --add-data "src\img;img" ^
  --add-data "src\lang;lang" ^
  --add-data "src\settings.json;." ^
  --add-data "fonts;fonts" ^
  --collect-all PySide6 ^
  --hidden-import PySide6.QtSvg ^
  --hidden-import PySide6.QtXml ^
  --upx-exclude "Qt6*.dll" ^
  --upx-exclude "PySide6\*.pyd" ^
  --upx-exclude "shiboken6\*.pyd" ^
  --upx-exclude "python*.dll" ^
  "src\main.pyw"
```

说明：

- Windows 分支保留 Windows 右键菜单、注册表、自启动、管理员权限重启等逻辑。
- 此命令不加 `--noupx`；UPX 在 PATH 中时 PyInstaller 会自动使用。
- 已排除 Qt/Python 核心二进制，减少 UPX 导致 Qt 插件损坏的风险。
- 如果构建产物启动异常，请运行 `build_windows_onedir_noupx.bat` 重打包。
