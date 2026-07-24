# Screenshot Evidence

沙箱没有可用的 PySide6 包源，也没有真实 KDE compositor，因此此目录中的 `placeholder-*.png` 和 `.txt` 是**明确标注的证据占位**，不是伪造的运行截图。

真机采集：

```bash
./scripts/screenshots/capture_all.sh
```

动态检查：

```bash
VIDEO=/absolute/path/demo.mp4 ./scripts/repro/kde_dynamic_check.sh
```

判定标准：

1. mpv 设置页能看到 runtime/后端与错误降级信息。
2. 中文切英文后同一窗口文案即时变化，不需重启，且没有重复控件/信号。
3. KDE Wayland 与 X11 视频均位于桌面背景层，图标和右键仍可用；退出后无残留进程。
4. Build GUI feature 勾选与 manifest/产物提示一致。
5. 托盘、单实例与 XDG autostart 在 KDE 中可发现且行为一致。
