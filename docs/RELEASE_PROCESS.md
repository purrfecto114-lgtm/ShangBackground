# 发布与产物管理

## 自动化工作流

仓库使用以下 GitHub Actions：

| 工作流 | 触发条件 | 作用 |
|---|---|---|
| `.github/workflows/ci.yml` | push、Pull Request、手动 | Ruff、compileall、构建系统自检、pytest、多系统构建计划检查 |
| `.github/workflows/release.yml` | `main` 上 `src/app/version.py` 变更、手动 | 原生构建、归档、校验和、Tag、GitHub Release |
| `.github/workflows/codeql.yml` | push、Pull Request、每周、手动 | Python CodeQL 安全扫描 |
| `.github/workflows/dependency-review.yml` | Pull Request | 检查新增或升级依赖的已知漏洞 |

工作流默认只有 `contents: read`。只有最终发布 Job 获得 `contents: write`，用于创建 Tag 和 Release。

## 自动发布版本规则

`src/app/version.py` 中的 `APP_VERSION` 是版本号主来源，必须使用严格的 `major.minor.patch` 格式。发布前自动检查以下内容一致：

1. `src/app/version.py` 的 `APP_VERSION`；
2. `src/main_version_info.txt` 的 Windows 四段版本；
3. `README.md` 的版本徽章；
4. `python src/main.py --version` 的输出。

发布新版本时：

1. 同时更新上述静态版本位置；
2. 在本地运行 `python .github/scripts/release.py metadata`；
3. 运行 `python -m pytest -q` 和 `python build_tools/build.py self-test`；
4. 将变更合入 `main`。

当 `src/app/version.py` 的变更到达 `main` 后，Release 工作流会创建 `v<major.minor.patch>` Tag。若同名 Tag 已指向其他提交，发布会明确失败，不覆盖历史。若 Tag 已指向当前提交但 Release 尚未创建，可以安全重跑；若 Release 已存在，工作流会幂等跳过。

## 自动发布配置

正式自动产物使用 PyInstaller `lite + standalone`：

```bash
python build_tools/build.py \
  --tool pyinstaller \
  --target <host> \
  --profile lite \
  --mode standalone \
  --mpv-runtime system \
  --arch <host-arch>
```

`lite` 保留核心图片/幻灯片/颜色壁纸、Bing、全局热键、更新和字体功能；不默认包含视频与 HTML。原因是这两类功能依赖 MPV、系统 WebView、桌面嵌入、权限或目标系统图形会话，不能只靠托管 Runner 证明可发布。需要完整功能时，应在对应操作系统准备并验证原生运行时，再手动构建 `full` 产物。

自动发布的架构：

- Windows x86_64；
- Linux x86_64；
- macOS x86_64（Intel Runner）；
- macOS arm64（Apple Silicon Runner）。

## Release 资产

每个版本生成：

```text
ShangBackground-vX.Y.Z-windows-x86_64.zip
ShangBackground-vX.Y.Z-windows-x86_64-setup.exe
ShangBackground-vX.Y.Z-linux-x86_64.tar.gz
ShangBackground-vX.Y.Z-macos-x86_64.tar.gz
ShangBackground-vX.Y.Z-macos-arm64.tar.gz
ShangBackground-vX.Y.Z-source.zip
SHA256SUMS.txt
```

Windows 在二进制归档之外，还额外提供一个 Inno Setup 安装包（`-setup.exe`）。安装包内嵌用户许可协议（`packaging/windows/license.rtf`），用户必须在向导首页勾选"我接受协议"才能继续安装；安装包同时提供开始菜单、桌面（可选）与开机自启（可选）快捷方式，并写入 Add/Remove Programs 卸载入口。CI 在 Windows runner 上自动安装 Inno Setup 6 后调用 `python build_tools/build.py installer` 生成 `setup.exe`，与本机命令一致。

二进制归档保留 standalone 目录结构与 Unix 可执行权限。源码 ZIP 只含一个顶层目录，并排除 `.github/`、`tests/`、缓存、构建目录、站点资源和验证产物。工作流会重新解压源码包，然后执行：

```bash
python -m compileall -q build_tools src
python src/main.py --version
python build_tools/build.py self-test
# 三个平台、两种后端的 lite standalone dry-run
```

最终发布前，`.github/scripts/release.py checksums` 会验证六个预期归档（四个二进制包 + Windows setup.exe + 源码包）均存在且没有额外文件，然后生成排序稳定的 SHA-256 清单。

## 手动验证发布脚本

```bash
# 版本一致性
python .github/scripts/release.py metadata

# 创建并重新验证源码包
python .github/scripts/release.py source-archive --output-dir dist-release
python .github/scripts/release.py verify-source-archive \
  dist-release/ShangBackground-vX.Y.Z-source.zip

# 为一个已验证的本机构建创建归档
python .github/scripts/release.py package \
  --target linux \
  --arch x86_64 \
  --input dist-pyinstaller/linux/lite-x86_64/standalone \
  --output-dir dist-release
```

## 手动构建 Windows 安装包

CI 之外，开发者也可以在本机生成 `setup.exe`：

1. 先用 PyInstaller 产出已验证的 standalone 目录：

   ```bash
   python build_tools/build.py --tool pyinstaller --target windows \
     --profile lite --mode standalone --mpv-runtime system --arch x86_64
   ```

2. 安装 [Inno Setup 6](https://jrsoftware.org/isdl.php)，或设置 `SHANGBACKGROUND_ISCC` 环境变量指向 `ISCC.exe` 的绝对路径。

3. 调用 `installer` 子命令：

   ```bash
   python build_tools/build.py installer \
     --target windows --profile lite --arch x86_64 \
     --input dist-pyinstaller/windows/lite-x86_64/standalone \
     --output-dir dist-release
   ```

4. 产物为 `dist-release/ShangBackground-vX.Y.Z-windows-x86_64-setup.exe`。

`--dry-run` 只解析计划、校验产物布局、打印 ISCC 命令，不真正调用编译器，便于在 Linux/macOS 上预演。

## 原生验收边界

三端二进制必须由对应系统原生构建。异平台 dry-run 只能验证参数生成，不能替代以下真机验收：

- Windows Explorer/WorkerW 集成和恢复；
- Linux X11/Wayland 桌面差异；
- macOS 辅助功能权限、AppKit 与 Gatekeeper；
- 视频/HTML helper、GPU、系统 WebView 和 MPV；
- 代码签名、公证、杀毒软件与安装器行为。

当前工作流不执行代码签名或 Apple notarization，因为仓库中没有签名身份和密钥配置。后续加入签名时，应使用 GitHub Environments 和受保护 Secrets，并让签名 Job 独占相应权限。

## 会话恢复验收

发布前在 Windows 真机至少执行：

1. 启动程序并切换到另一张静态或动态壁纸；
2. 连续点击两次“恢复启动前壁纸”，两次都不得报失败；
3. 再切换壁纸并退出程序，仍应恢复本次启动前的原始壁纸；
4. 正常退出成功后确认会话恢复文件被清理；恢复失败时应保留文件以便下次重试。
