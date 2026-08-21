# MPV / libmpv 运行时

## 程序实际怎样调用 MPV

Windows 的首选视频路径不是 `python-mpv`，也不再把完整主程序当作首选播放器进程：

1. Windows 优先启动已验证的 `mpv.exe`，传入 WorkerW 的 `--wid`；
2. 使用 `--no-config` 隔离用户 mpv 配置，并通过 `--input-ipc-server` 的 JSON IPC 调整暂停、静音和音量；
3. 旧的 libmpv-only bundle 仍可回退到 `--internal-libmpv-player` 兼容路径；
4. Linux 保留各桌面环境/Wayland 的平台适配路径；
5. macOS 走 AVFoundation/AppKit，不依赖 libmpv。

这意味着 **v1.5.0 新 Windows 发布包必须包含完整、同架构的 `mpv.exe` 运行时**（及其同目录 DLL 依赖）。运行时代码仍兼容旧安装遗留的 libmpv-only payload，但构建器不再接受它作为新发布包输入，避免再次生成“启动完整 ShangBackground 子进程承载 libmpv”的 bundle。`python-mpv` 包不是运行依赖。

## 本地文件优先

构建选择器先检查候选目录的直接文件，避免因递归搜索误选另一平台或架构的嵌套运行时：

```text
src/bin/mpv/<target>/<arch>/
src/bin/mpv/<target>/
src/bin/<target>/<arch>/
src/bin/<target>/
src/bin/mpv/
src/bin/
bin/ 下的对应结构
```

随后才读取托管版本：

```text
src/bin/mpv/<target>/<arch>/
├─ ACTIVE
├─ <runtime-id>/
│  ├─ mpv.exe                  # Windows v1.5.0 新包必需
│  ├─ *.dll                    # Windows 同目录依赖（如有）
│  ├─ libmpv.so.* / mpv       # Linux 本地运行时（按构建策略）
│  ├─ licenses/
│  └─ runtime.json
```

## Windows 下载与验证

下载是显式操作，普通构建不会在后台获取原生二进制：

```powershell
python build_tools/build.py mpv download --target windows --arch x86_64 --channel stable
# 发布构建可进一步固定已独立取得的归档摘要：
python build_tools/build.py mpv download --target windows --arch x86_64 --channel stable --sha256 <64位SHA-256>
python build_tools/build.py mpv list --target windows --arch x86_64
python build_tools/build.py mpv verify --target windows --arch x86_64 --version auto
python build_tools/build.py mpv activate --target windows --arch x86_64 <runtime-id>
python build_tools/build.py mpv prune --target windows --arch x86_64 --keep 2
```

`stable`（默认）读取 mpv 官方 GitHub 的 latest stable release；当前稳定发布页明确同时提供 CI 构建的二进制资产。`development` 则读取 `git-release` prerelease，属于最新 master 的未测试开发构建，只应显式选用。部分官方 MinGW/CI artifact 的外层 ZIP 里还会再放一个 MPV ZIP；下载器兼容**一层、受限的 MPV 嵌套 ZIP**，同时把外层与内层展开量计入同一个安全预算。它还限制 HTTPS 主机、压缩包大小、单成员大小、成员数量和解压总量，拒绝路径穿越与符号链接，并在元数据提供摘要时核对 SHA-256；发布者也可用 `--sha256` 提供独立固定值。安装后会验证 `mpv.exe`、全部 DLL/EXE 的 PE 架构、目标 CPU 架构和运行时清单。

官方稳定 release 页说明其中二进制资产同样由 CI 生成，且构建选项可能不覆盖 mpv 的全部功能（例如编码能力）。正式发布应固定稳定版本、记录来源和 SHA-256，并在目标机器上验证硬件解码、常见编码、循环、IPC、中文路径和退出回收。

## 各平台构建策略

### Windows

`full + video + --mpv-runtime auto` 或 `bundled` 的真实构建必须找到完整、已验证的本地运行时；否则立即停止并给出准备命令。构建后文件位于：

```text
<application>/bin/mpv/
```

### Linux

`auto` 先使用本地 libmpv；未找到时记录为 `system`，由目标发行版提供 libmpv/mpv。跨发行版发布时不能把本机 `.so` 无条件复制到其他系统。

### macOS

使用 AVFoundation/AppKit。`--mpv-runtime bundled` 会被拒绝，避免产生与真实运行路径不一致的包。

## 构建示例

```bash
# Windows 自包含视频包（需先准备运行时）
python build_tools/build.py --tool nuitka --target windows --profile full \
  --features video,html,bing,hotkeys,updates,fonts \
  --mpv-runtime auto --mpv-arch x86_64 --mode standalone

# Linux 使用系统 libmpv
python build_tools/build.py --tool pyinstaller --target linux --profile full \
  --features video --mpv-runtime system --mode standalone

# macOS 原生视频
python build_tools/build.py --tool pyinstaller --target macos --profile full \
  --features video --mpv-runtime auto --mode standalone
```
