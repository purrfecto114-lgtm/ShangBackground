# ShangBackground macOS 适配版

这是从 Windows PySide6 版迁移出的 macOS 独立目录。主要入口：

- 源码运行：`python3 src/main.py`
- 打包脚本：`./build_macos_onedir.sh`
- 打包说明：`README_BUILD_MacOS.md`

主要改动：英文 CN/EN 语言切换、托盘“关于”等价到 GUI 精灵图关于窗口、macOS osascript 壁纸切换、LaunchAgents 自启动、用户目录配置文件、PyInstaller onedir 资源路径适配。
## 源码分类

统一目录说明见项目根目录 `PROJECT_STRUCTURE.md`。所有 SVG、图片和语言资源由 `src/app/paths.py` 统一定位；新增模块请按 `app / core / services / platform_adapters / ui` 分类。

