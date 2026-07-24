# 阶段 4：测试报告

本阶段目标：用静态、单元、构建 dry-run 与可执行冒烟证明改动不破坏现有功能；无法在沙箱完成的 GUI/KDE 项目必须明确标注。

## 环境

- 日期：2026-07-24（Europe/Helsinki）
- 沙箱：Linux container，Python 3.13.5
- 图形条件：有 Xvfb，但当前 Python 环境没有 PySide6；无真实 KDE Plasma/KWin 会话
- 可选依赖：`dbus-next` 未安装；内部 pip mirror 未提供 `dbus-next`/`ruff`，系统 apt 安装尝试超时/无可用索引

## 结果摘要

| 项目 | 结果 | 证据 |
|---|---:|---|
| 全量 pytest | ✅ | 115 passed / 19.66s |
| 指定质量门禁 | ✅ | 24 passed / 0.13s |
| 新增专项测试 | ✅ | mpv、i18n、gzip、KDE capability、Portal mock、64 feature 组合 |
| compileall | ✅ | build_tools/src/tests/.github scripts |
| Build self-test | ✅ | staging、HTML backend、QtWebEngine 排除、lite plan、Windows installer invariants |
| PyInstaller full/all dry-run | ✅ | 包含 `app.mpv_backend`、Linux portal、`dbus_next` 与资源目录 |
| Nuitka full/all dry-run | ✅ | 同上，使用 `--include-module`，未启动 compiler |
| CLI version | ✅ | `1.4.2` |
| Wayland 缺 portal 依赖降级 | ✅ | `refresh(...) == False`，无异常/崩溃 |
| Shell 脚本语法 | ✅ | KDE repro 与截图脚本 `bash -n` 通过 |
| Qt offscreen GUI | ⚠️ 未执行 | `ModuleNotFoundError: No module named 'PySide6'` |
| 真机 KDE Wayland 视频/Portal | ⚠️ 未执行 | 需要 KWin、mpvpaper、xdg-desktop-portal-kde 与用户 consent |
| Ruff | ⚠️ 未执行 | 工具不可安装；以 compileall、pytest、`git diff --check` 补充 |

## pytest 原始输出

```text
........................................................................ [ 62%]
...........................................                              [100%]
115 passed in 19.66s
```

指定质量门禁：

```text
........................                                                 [100%]
24 passed in 0.13s
```

执行命令：

```bash
python -m pytest -q
python -m pytest -q \
  tests/test_build_feature_matrix.py \
  tests/test_build_pipeline.py \
  tests/test_build_runtime_gate.py \
  tests/test_static_mode_transaction.py
```

## 新增测试覆盖

- `tests/test_mpv_backend.py`：legacy module → `MpvBackend` 生命周期、暂停、音量 clamp、缺失能力降级。
- `tests/test_i18n_runtime.py`：zh/en 切换、事件、gzip magic 恢复、错误 JSON 回退、`t()` 签名行为。
- `tests/test_linux_wayland_backends.py`：KDE Wayland layer-shell/Portal capability、GNOME 不误报、热键 mock、XDG trigger 转换。
- `tests/test_bundle_resources.py`：普通 JSON 通过、gzip 伪装 `.json` 构建失败。
- `tests/test_build_feature_matrix.py`：6 features 的 64 个组合，manifest 与 include/exclude 无交集。

## 构建 dry-run

PyInstaller：

```bash
python build_tools/build.py --tool pyinstaller --target linux \
  --profile full --features all --mpv-runtime system --dry-run
```

关键证据：命令包含 `--hidden-import app.mpv_backend`、`platform_adapters.backends.linux.portal_hotkeys`、`dbus_next`，并复制 `src/lang`；dry-run 未启动 compiler。

Nuitka：

```bash
python build_tools/build.py --tool nuitka --target linux \
  --profile full --features all --mpv-runtime system --dry-run
```

关键证据：命令包含相同模块的 `--include-module`，并保持 QtWebEngine/QML/Quick 排除。

## 冒烟原始输出

```text
compileall: PASS
Build-tool self-test passed.
Checked syntax, staging output, native HTML backend chains, Qt WebEngine exclusion,
lite build plans, and Windows installer invariants.
1.4.2
shell syntax: PASS
wayland_hotkeys_without_dbus_next= False
```

Qt 尝试：

```text
Traceback (most recent call last):
  File "<stdin>", line 1, in <module>
ModuleNotFoundError: No module named 'PySide6'
```

## 真机 KDE 复现

```bash
# KDE Wayland 会话中
python -m pip install -r requirements/linux.txt -r requirements/hotkeys.txt
sudo <发行版包管理器> install mpvpaper xdg-desktop-portal xdg-desktop-portal-kde
VIDEO=/absolute/path/demo.mp4 ./scripts/repro/kde_dynamic_check.sh
./scripts/screenshots/capture_all.sh
```

验收：

1. capability 中 KDE static ready，video 为 mpvpaper/KWin best-effort，hotkeys 为 Portal ready。
2. Portal 显示系统授权 UI；批准后 `Ctrl+Alt+N` 产生 activation。
3. 视频位于图标后方，桌面右键可用，多屏行为符合预期；停止后 mpvpaper/socket 无残留。
4. 语言中文→英文同窗口即时重绘，控件状态保留且没有重复 signal。

## 沙箱限制声明

本报告没有把 mock capability、Xvfb 存在或占位图片描述成真实 KDE 验证。KWin layer-shell、Portal consent UI、系统托盘、桌面图标层级与多显示器只能在真实 Plasma 会话中判定。

本阶段产出：静态与构建门禁全绿，动态边界有可执行脚本与明确验收条件。
