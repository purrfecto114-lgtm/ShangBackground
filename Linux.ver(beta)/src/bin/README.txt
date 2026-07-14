Linux 视频后端说明
==================

本目录不再附带单一的动态链接 mpv ELF。此类文件依赖特定发行版的
FFmpeg/libplacebo/mujs 等 SONAME，换一台系统即可能无法启动。

X11：请通过发行版包管理器安装 mpv 与 xwinwrap。
Wayland：仅兼容 layer-shell 的合成器可使用 mpvpaper；GNOME/KDE
Wayland 需要专用桌面扩展/插件后端，当前版本会明确拒绝假成功。

如要随应用分发，请提供按架构构建且依赖闭包完整的 AppImage/运行时，
并让 platform_adapters.video._probe_executable() 验证后再启用。
