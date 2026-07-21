# 项目结构

```text
src/
  app/                  Application Services、配置和启动组装
  core/                 兼容门面与核心运行入口
  ui/                   Qt Widgets 管理界面
  platform_adapters/    共享平台门面、native HTML/video helper
    backends/           windows/linux/macos 原生实现
  services/             网络与外部服务
build_tools/            稳定入口、Build Studio、PyInstaller hooks 与共享构建库
requirements/           平台与功能运行依赖
docs/                   架构、构建和平台说明
examples/html/           HTML 壁纸示例
```

HTML 壁纸不包含 `src/qml/` 或 Qt WebEngine helper；三端实现位于各平台的 `native_webview_desktop.py`。

缓存、`build-generated/`、构建目录、验证报告和 `.pyc` 不属于源码。`build-features.json` 仅在构建时生成并写入产物。内部测试树和旧维护工具不随发布源码包分发。

## Build tools 分层

```text
build_tools/
  build.py                 统一 CLI：builder、GUI、MPV 管理
  build_gui.py / .pyw      Build Studio 入口
  build_nuitka.py          Nuitka 兼容入口
  build_pyinstaller.py     PyInstaller 兼容入口
  _entry.py                入口路由，不包含构建策略
  buildlib/cli.py          共享参数分组与终端展示
  buildlib/gui.py          GUI，仅生成并执行公开 CLI 命令
  buildlib/plan.py         唯一构建计划
  buildlib/nuitka.py       Nuitka 参数映射
  buildlib/pyinstaller.py  PyInstaller 参数映射
  buildlib/mpv_runtime.py  MPV 运行时发现、验证和显式下载
  buildlib/runner.py       依赖安装、日志和进程树管理
```
