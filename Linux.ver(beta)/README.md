# ShangBackground Linux.ver(beta)

这是从 Windows PySide6 版迁移出的 Linux 独立目录，优先适配 Debian / Ubuntu / GNOME，同时加入 KDE、XFCE、feh、nitrogen 的壁纸后端兜底。

- 源码运行：`python3 src/main.py`
- 打包脚本：`./build_linux_onedir.sh`
- 打包说明：`README_BUILD_Linux.md`

主要改动：英文 CN/EN 语言切换、托盘“关于”等价到 GUI 精灵图关于窗口、XDG autostart、用户目录配置文件、PyInstaller onedir 资源路径适配。
## 源码分类

统一目录说明见项目根目录 `PROJECT_STRUCTURE.md`。所有 SVG、图片和语言资源由 `src/app/paths.py` 统一定位；新增模块请按 `app / core / services / platform_adapters / ui` 分类。

