# 开发环境与日常流程

## 安装与启动

```bash
python -m venv .venv
# 激活虚拟环境后，选择当前平台：
python -m pip install -r requirements/windows-full.txt
python -m pip install -r requirements/linux-full.txt
python -m pip install -r requirements/macos-full.txt
python src/main.py
```

三个平台依赖文件只能选择当前系统对应的一项。HTML、视频和热键的系统级依赖见各 requirements 文件注释与 [`GETTING_MPV.md`](GETTING_MPV.md)。

## 构建入口

```bash
python build_tools/build.py --gui
python build_tools/build.py --tool pyinstaller --profile full --mode standalone --dry-run --skip-install
python build_tools/build.py --tool nuitka --profile full --mode standalone --dry-run --skip-install
```

构建实现只维护在 `build_tools/buildlib/`；`build.py`、`build_gui.py[w]`、`build_nuitka.py` 和 `build_pyinstaller.py` 只是稳定入口。修改构建参数时同时更新共享解析器、GUI 命令预览和 `docs/BUILD_SYSTEM.md`，不要在兼容入口中复制逻辑。生成目录不应提交或混入源码归档。

## 修改规则

- 保持 Qt Widgets 管理界面，不向主界面嵌入网页控件；
- 新业务使用 Service、Policy 或 Controller，新平台代码进入对应 Backend；
- 配置通过 `ConfigRepository`/`app.storage`，历史与收藏通过 `WallpaperLibrary`；
- 动态壁纸、会话恢复、IPC 和幻灯片状态使用 `RuntimeState`；启动前壁纸快照在整个应用会话内保持只读，只有最终退出成功后才消费；
- 壁纸状态变更经过共享操作锁；
- 三端原生能力和二进制必须在目标系统验证；异平台只允许构建命令 dry-run。

正式入口在创建 UI 前调用 `core.engine.initialize_application()`。Qt Designer 只用于独立表单；生成代码与业务逻辑保持分离。

> 本发布源码包不包含内部测试树和仓库维护脚本。完整验证记录作为独立交付物保存。
