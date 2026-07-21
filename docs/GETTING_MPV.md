# MPV / libmpv 运行时

## 程序实际怎样调用 MPV

Windows 和 Linux 的首选视频路径不是 `python-mpv`：

1. 平台视频后端启动当前应用的子进程；
2. 子进程参数包含 `--internal-libmpv-player`、目标窗口 ID 和随机 IPC 地址；
3. `src/main.py` 识别该内部参数并调用 `app.libmpv_runtime.run_libmpv_player()`；
4. `app.libmpv_runtime` 使用 `ctypes` 直接加载 libmpv，调用 `mpv_create`、`mpv_set_option_string`、`mpv_initialize`、`mpv_command(loadfile)` 和 `mpv_wait_event`；
5. GUI 通过 mpv JSON IPC 调整暂停、静音和音量；
6. 内部 libmpv 启动失败后，Windows/Linux 才尝试外部 `mpv`，Windows 最后还可尝试 VLC；
7. macOS 走 AVFoundation/AppKit，不依赖 libmpv。

这意味着 Windows 发布包需要的是完整、同架构的 libmpv DLL 运行时及其同目录依赖。仅有 `mpv.exe` 不能保证内部首选路径可用；`python-mpv` 包也不是运行依赖。

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
│  ├─ libmpv-2.dll / libmpv.so.*
│  ├─ 同目录依赖
│  ├─ 可选 mpv.exe / mpv
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

下载器限制 HTTPS 主机、压缩包大小、单成员大小、成员数量和解压总量，拒绝路径穿越与符号链接，并在元数据提供摘要时核对 SHA-256；发布者也可用 `--sha256` 提供独立固定值。安装后会验证主库、全部 DLL/EXE 的 PE 架构、目标 CPU 架构和运行时清单。

官方 release 页面提供的二进制资产属于 CI 构建，可能因构建选项不同而缺少某些功能。正式发布应固定版本、记录来源和 SHA-256，并在目标机器上验证硬件解码、常见编码、循环、IPC、中文路径和退出回收。

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
