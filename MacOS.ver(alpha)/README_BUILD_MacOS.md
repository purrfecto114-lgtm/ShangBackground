# macOS 适配版说明

## 运行源码

```bash
cd "MacOS适配版"
python3 -m pip install -r requirements-macos.txt
python3 src/main.py
```

首次切换壁纸时，macOS 可能要求允许 Terminal / Python / 打包后的 App 控制 “System Events”。请在：

`系统设置 → 隐私与安全性 → 自动化 / 辅助功能`

允许对应程序。

## PyInstaller --onedir 打包命令

```bash
python3 -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --windowed \
  --contents-directory "." \
  --name "ShangBackground" \
  --icon "src/img/LOGO.icns" \
  --paths "src" \
  --add-data "src/img:img" \
  --add-data "src/lang:lang" \
  --add-data "src/settings.json:." \
  --add-data "fonts:fonts" \
  --collect-all PySide6 \
  --hidden-import PySide6.QtSvg \
  --hidden-import PySide6.QtXml \
  "src/main.py"
```

也可以直接执行：

```bash
chmod +x build_macos_onedir.sh
./build_macos_onedir.sh
open dist/ShangBackground.app
```

## macOS 专项改动

- 壁纸设置使用 `osascript` 操作 System Events 的 every desktop。
- 开机自启动写入 `~/Library/LaunchAgents/com.xxdz.shangbackground.plist`。
- 配置文件写入 `~/Library/Application Support/ShangBackground/settings.json`，避免写入 App 包或安装目录。
- 图标使用 `src/img/LOGO.icns` 打包，运行时资源使用 `src/img/LOGO.png`。
- UPX 不建议用于 macOS：会影响/破坏 codesign 校验，Apple Silicon 上尤其不适合。
