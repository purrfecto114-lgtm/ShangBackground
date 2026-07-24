# 平台运行修复报告

- 基线：`ShangBackground-1.4.2-researched.zip`
- 检查日期：2026-07-24
- 范围：Linux 实际运行路径优先；Windows/macOS 做源码、接口与失败清理回归检查
- Workflow：仅审计并输出建议，**未修改** `.github/workflows/` 与 `.github/dependabot.yml`

## 1. 结论

本轮定位并修复了 Linux 上会直接影响真实启动/功能分流的运行时问题，同时对 Windows 与 macOS 的播放器失败路径增加了资源清理保护。新增 12 个回归测试后，全量测试为 **127 passed**。

Linux 的关键故障是会话识别不一致：能力探测与热键代码能够根据 `WAYLAND_DISPLAY` 判断 Wayland，但视频后端只读取 `XDG_SESSION_TYPE`。桌面启动器未导出 `XDG_SESSION_TYPE` 时，同一个进程会出现“探测认为 Wayland、启动却走 X11/xwinwrap”的矛盾。现已统一为共享会话识别模块。

> KDE Wayland 视频仍属于最佳努力支持，而不是可宣称的通用保证。上游 mpvpaper 明确面向 wlroots 合成器；KWin 真机上的层级、多屏、桌面图标共存仍需在目标 Plasma 版本复测。

## 2. 已实施修复

### 2.1 Linux 会话与 D-Bus 识别统一

新增：`src/platform_adapters/backends/linux/session.py`

统一判定顺序：

1. 有效的 `XDG_SESSION_TYPE=wayland|x11`
2. `WAYLAND_DISPLAY`
3. `DISPLAY`
4. 否则为 `unknown`

同时，用户会话总线不再只依赖 `DBUS_SESSION_BUS_ADDRESS`；若 `$XDG_RUNTIME_DIR/bus` 存在，也视为可连接。这样可避免 KDE/GNOME 自启动环境未继承总线地址时的误报。

接入文件：

- `src/platform_adapters/backends/linux/capabilities.py`
- `src/platform_adapters/backends/linux/hotkeys.py`
- `src/platform_adapters/backends/linux/video.py`
- `src/app/diagnostics.py`

### 2.2 Wayland 视频启动参数修复

文件：`src/platform_adapters/backends/linux/video.py`

- mpvpaper 全屏输出选择器从旧写法 `*` 改为当前文档中的 `ALL`。
- 新增环境变量 `SHANGBACKGROUND_MPVPAPER_OUTPUT`，允许指定 `DP-1`、`HDMI-A-1` 等单一输出。
- 启动时加入隔离参数：`no-config`、`load-scripts=no`、`autoload-files=no`、`sub-auto=no`、`audio-file-auto=no`、`no-osc`、`no-osd-bar`、`no-input-default-bindings`。
- 保留 mpv IPC、循环、音量及静音控制。

安全/兼容性影响：避免用户级 mpv 配置或脚本改变壁纸进程行为；不会影响 X11 的 `xwinwrap + mpv` 路径。

### 2.3 Wayland GlobalShortcuts Portal 防挂死

文件：`src/platform_adapters/backends/linux/portal_hotkeys.py`

- Portal Request 增加可配置超时，默认 45 秒。
- 超时后尝试调用 `org.freedesktop.portal.Request.Close`。
- 返回清晰错误，避免后台线程永久等待授权窗口或失效 Portal backend。

说明：Portal 绑定本身可能弹出由桌面环境提供的用户授权/配置窗口，这是协议正常行为，不应绕过。

### 2.4 Linux 依赖安装修复

文件：`src/app/backends/linux/dependencies.py`

修复两个真实运行问题：

1. 普通虚拟环境中 `pip install --user` 会被拒绝；现改为对当前解释器执行 `python -m pip install`，不附加 `--user`。
2. 虚拟环境默认与系统 Python 包隔离，过去仅安装发行版 `python3-pyside6` 等包后，当前 venv 仍可能无法导入。现对 Python 包安装到活动解释器；PyGObject 额外保留 GTK/WebKit 原生系统依赖安装。

### 2.5 诊断信息与缺依赖错误修复

文件：

- `src/app/diagnostics.py`
- `src/app/support.py`
- `src/app/config.py`

改动：

- Linux doctor 根据 Wayland/X11 检查真正对应的运行时：Wayland 检查 `dbus-next`、会话总线和 mpvpaper；X11 检查 `pynput` 与 xwinwrap。
- 原生 HTML 壁纸为可选 feature，缺失时改为 warning，不再使基础 GUI doctor 直接判定不健康。
- `apply_application_font()` 在没有 PySide6 时仍可导入并安全返回，避免错误信息被误导成 “support 模块缺少符号”。
- Wayland 热键依赖声明包含 `dbus-next`，并保留 `pynput` 供快捷键录制 UI 使用。

### 2.6 Windows 失败回滚

文件：`src/platform_adapters/backends/windows/video.py`

播放器已经成功拉起、但 PID/IPC 状态写盘失败时：

- 立即终止新启动的播放器；
- 删除不完整状态；
- 重新抛出原异常。

这避免 WorkerW 背后遗留无法由应用管理的 mpv/VLC 孤儿进程。WorkerW 获取、桌面右键及现有启动主路径未改写。

### 2.7 macOS 失败回滚

文件：`src/platform_adapters/backends/macos/video.py`

内部 AVPlayer 子进程启动即退出或启动异常时：

- 清空 `_CURRENT_PROC`；
- 删除 PID 状态和 IPC 标记；
- 回收已退出子进程。

避免下一次启动误判旧播放器仍运行。

## 3. 新增测试

文件：`tests/test_platform_runtime_regressions.py`

覆盖：

- 仅有 `WAYLAND_DISPLAY` 时的 Wayland 判定；
- `$XDG_RUNTIME_DIR/bus` 总线回退；
- KDE Wayland 能力 readiness；
- mpvpaper `ALL`、安全参数与单输出覆盖；
- Portal 超时与 Request.Close；
- venv 中禁止 `pip --user`；
- venv 依赖计划使用活动解释器；
- 无 Qt 时字体 helper 可导入；
- 三端视频公开 API 一致性；
- Windows 状态写入失败终止播放器；
- macOS 启动即退出清理状态。

## 4. 验证结果

最终执行命令：

```bash
python -m pytest -q
python -m compileall -q build_tools src tests .github/scripts
python build_tools/build.py self-test
```

构建计划 dry-run：

```bash
for target in windows linux macos; do
  for tool in pyinstaller nuitka; do
    python build_tools/build.py \
      --tool "$tool" --target "$target" \
      --profile lite --mode standalone \
      --mpv-runtime system --skip-install --dry-run
  done
done
```

结果：

- 全量 pytest：**127 passed**
- `compileall`：通过
- build self-test：通过
- Windows/Linux/macOS × PyInstaller/Nuitka 共 6 个计划：全部 dry-run 通过
- 三个平台覆盖下 `--doctor-json`：均能生成合法 JSON，不发生导入崩溃
- 沙箱缺少 PySide6/Pillow 时，GUI 启动以明确依赖错误退出，而非误报内部导入符号错误

## 5. 未能在本沙箱证明的项目

当前 Linux 沙箱无法取得 PySide6 包，也没有真实 KDE Plasma/KWin 会话，因此以下内容没有伪装成“已实测”：

- Qt 主窗口、托盘与首选项真实渲染；
- KDE Portal 授权窗口及真实快捷键激活信号；
- mpvpaper 在 KWin 下的桌面层级、桌面图标共存和多显示器行为；
- Windows WorkerW 真机嵌入；
- macOS AVFoundation/AppKit 真机播放及权限交互；
- 打包后的完整冻结产物启动。

建议在三端真机执行项目已有 `scripts/repro/`，并在 Workflow 优化时增加冻结产物冒烟。KDE Wayland 若 mpvpaper 无法在目标 KWin 工作，应明确降级到静态壁纸或 X11，而不是静默宣称成功。

## 6. 外部核验

抓取日期：2026-07-24。

- mpvpaper 官方：面向 wlroots；全输出参数为 `ALL`，并支持 `-o` 转发 mpv 参数和 IPC。  
  https://github.com/GhostNaN/mpvpaper
- XDG GlobalShortcuts Portal：会话式绑定、可能出现配置对话框、通过 Activated/Deactivated 信号通知，当前文档接口版本为 2。  
  https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html
- Python venv：虚拟环境默认隔离系统包；在 venv 中运行 pip 会自动安装到该环境。  
  https://docs.python.org/3/library/venv.html
