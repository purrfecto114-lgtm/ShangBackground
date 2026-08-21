# 构建系统

当前构建目录按“共享构建计划 + 后端适配器 + 构建后校验”组织。Nuitka 与 PyInstaller 共用功能清单、MPV 选择器、依赖安装器、资源决策和日志执行器；各后端只负责表达自己的命令行与产物结构。

## 入口

```bash
python build_tools/build.py --tool pyinstaller --profile full --mode standalone
python build_tools/build.py --tool nuitka --profile full --mode standalone
python build_tools/build.py --gui
```

兼容入口仍可用：

```bash
python build_tools/build_pyinstaller.py ...
python build_tools/build_nuitka.py ...
python build_tools/build_gui.py
```

实际构建只允许在目标操作系统上执行。异平台只可用 `--dry-run` 检查命令，不代表原生编译通过。

## 交互界面

CLI 使用标准库 `argparse` 的分组帮助，显示默认值，并在执行前打印稳定的构建计划摘要。所有入口最终都调用 `build_tools/buildlib/`，兼容脚本不维护第二套参数。

GUI 只是 CLI 的可视化前端：

- 命令预览始终显示将要执行的完整命令；
- 高级选项默认折叠；
- 实时日志有行数上限，完整日志仍写入 `build-logs/`；
- Stop 会终止完整编译进程树；
- `Ctrl+Enter` 开始，`Esc` 停止。

```bash
python build_tools/build.py --tool pyinstaller --help
python build_tools/build.py --tool nuitka --help
python build_tools/build.py mpv --help
```

## 功能选择

核心图片、幻灯片、纯色和渐变始终存在。可选功能：

```text
video, html, bing, hotkeys, updates, fonts
```

```bash
python build_tools/build.py --list-features
python build_tools/build.py --tool pyinstaller --features video,bing
python build_tools/build.py --tool nuitka --exclude-features html,updates
```

`full` 默认启用全部可选功能；`lite` 默认关闭视频和 HTML。每个实际构建都会生成 `build-generated/<tool>/<target>/<variant>/build-features.json`，冻结程序只按该清单开放功能；清单缺失或损坏时，打包程序退化为 core-only，而不是猜测功能存在。

## MPV 选择

构建器按以下顺序选择 Windows/Linux 视频运行时：

1. 检查 `src/bin` 或根目录 `bin` 下与目标平台匹配的直接文件；
2. 检查 `src/bin/mpv/<target>/<arch>/ACTIVE` 和已安装版本；
3. Linux 的 `auto` 可退回目标系统 libmpv；
4. Windows 的实际 `full + video + auto/bundled` 必须找到已验证的完整运行时；
5. macOS 使用 AVFoundation，不打包 MPV。

普通构建不会联网下载原生代码。Windows 运行时必须显式准备：

```bash
python build_tools/build.py mpv download --target windows --arch x86_64 --channel stable
python build_tools/build.py mpv verify --target windows --arch x86_64
python build_tools/build.py mpv list --target windows --arch x86_64
```

## Nuitka 原生文件

Nuitka 使用构建时临时包和 `--user-package-configuration-file`：

- Windows v1.5+ 运行时以 `mpv.exe` 为必需入口，并收集同目录 DLL；
- 旧 libmpv-only payload 仅作为运行时兼容路径，不再作为 Windows 新包输入；
- Linux 本地 bundled 模式仍可收集 libmpv 及同目录依赖；
- `data-files` 收集许可证和运行时元数据；
- 统一放入产物 `bin/mpv/`。

HTML 使用 pywebview 的系统原生后端，不使用 Qt WebEngine/QML。Nuitka 4.1.3 的 pywebview 插件在 Windows 过滤平台模块时漏掉了 `webview.platforms.win32`，而 `winforms` 会直接导入该模块。因此 HTML 构建会禁用该插件，并显式加入目标平台的完整后端链；Nuitka 自带 package configuration 仍负责收集 pywebview 的 JS、原生 DLL 和 WebView2 组件。构建后会读取 `compilation-report.xml`，确认目标平台模块确实进入编译图。

## PyInstaller 原生文件

PyInstaller 对 MPV 原生库和可执行文件使用 `--add-binary`，许可证/JSON/配置使用 `--add-data`。动态 Python 模块使用 `--hidden-import`；不排除 `distutils`、`setuptools`、`pip` 或 `wheel`，避免和 hook 依赖分析冲突。

standalone/onedir 固定使用 PyInstaller 6 的标准 `_internal` 内容目录，产物结构为 `ShangBackground/ShangBackground.exe` 加 `ShangBackground/_internal/`。不支持 `--contents-directory .` 的旧式扁平目录。项目只保留 `hook-webview.py` 和 Pillow 补充 hook，不再覆盖 PyInstaller 官方的 PySide6 QtCore/QtGui/QtNetwork hooks。

## 输出与日志

```text
dist-nuitka/<target>/<variant>/<mode>/
dist-pyinstaller/<target>/<variant>/<mode>/
build-generated/<tool>/<target>/<variant>/
build-logs/<target>/<timestamp>.log
build-logs/<target>/latest.log
```

GUI 和 CLI 停止构建时会回收整个编译进程组，防止只结束 Python 启动器而遗留 C 编译器或子构建进程。GUI 的实时文本只保留最近约 15,000–20,000 行，完整构建输出仍写入日志文件，避免长时间 Nuitka 编译耗尽界面内存。PyInstaller 直接读取已经验证的 MPV 源 payload，不再先复制一份到 `build-generated`；Nuitka 仅为 package configuration 保留一次必要的临时 staging。

## 可重复性与已知边界

- 构建工具版本固定在 `build_tools/requirements/`；部分应用运行依赖仍使用版本范围且没有 hashes，因此当前不是完全可重复、可离线证明的供应链构建。正式发布应在干净虚拟环境中生成并审计锁定清单。
- `mpv download` 只在显式调用时联网，并可用 `--sha256 <64位摘要>` 固定下载归档。GitHub 元数据自带摘要时也会核对。
- 项目默认使用 `standalone`。截至 2026-07，Nuitka 上游仍有 Linux + PySide6 6.11.1 的 onefile 问题报告；未在目标机验证前不要把 onefile 当作正式发布模式。
- Nuitka/PyInstaller 都不是跨平台原生编译器；异平台 dry-run 只检查本项目生成的命令和资源映射。

## 发布前最低验证

```bash
python -m compileall -q build_tools src
python build_tools/build.py self-test
python build_tools/build.py --tool nuitka --target <host> --skip-install --dry-run
python build_tools/build.py --tool pyinstaller --target <host> --skip-install --dry-run
# PyInstaller 命令预览中应出现：--contents-directory _internal
```

随后必须在干净的目标系统完成真实 standalone 构建、启动/退出、视频和 HTML helper、中文路径、签名及依赖缺失测试。onefile 应在 standalone 验收后再评估。
