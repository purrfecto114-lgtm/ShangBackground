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

正式自动产物使用 Nuitka `full + standalone`，Windows/Linux 强制启用 UPX：

```bash
python build_tools/build.py \
  --tool nuitka \
  --target <host> \
  --profile full \
  --mode standalone \
  --mpv-runtime system \
  --arch <host-arch> \
  --upx
```

`full` 包含视频、HTML、Bing、全局热键、更新和字体。UPX 在 Windows/Linux 是发布门禁，macOS 因签名和 ABI 约束自动禁用。动态壁纸仍必须在对应桌面真机验收。

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

Windows 在二进制归档之外，还提供 Inno Setup 安装包（`-setup.exe`）。CI 在 Windows runner 安装 UPX 5.2.0 和 Inno Setup 7 x64，将已验证的 Nuitka full standalone 封装为安装包。

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

1. 先用 Nuitka + UPX 产出已验证的 full standalone 目录：

   ```bash
   python build_tools/build.py --tool nuitka --target windows \
     --profile full --mode standalone --mpv-runtime system --arch x86_64 --upx
   ```

2. 安装 [Inno Setup 7 x64](https://jrsoftware.org/isdl.php)，或设置 `SHANGBACKGROUND_ISCC` 指向其 `ISCC.exe`。

3. 调用 `installer` 子命令：

   ```bash
   python build_tools/build.py installer --tool nuitka \
     --target windows --profile full --arch x86_64 \
     --input dist-nuitka/windows/full-html-native-x86_64-mpv-system/standalone \
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

## 签名探测与验证

Windows 签名通过 `build_tools/signing.py` 提供结构化诊断，避免在缺失前置条件时伪造成功：

```bash
# 诊断当前环境（无 signtool/证书时报告 unsigned）
python build_tools/build.py signing check --json
python build_tools/build.py signing check --input dist-nuitka/windows/full-standalone/ShangBackground/ShangBackground.exe --json

# 尝试签名并验证（前置缺失时仍报告 unsigned，不删除未签名产物）
python build_tools/build.py signing sign --input dist-nuitka/windows/full-standalone/ShangBackground/ShangBackground.exe --certificate path/to/cert.pfx --json
```

底层接口 `build_tools.signing.sign_and_verify()` 返回 `SigningResult(status, reason)`：

- `unsigned`：缺少 `signtool`（`SHANGBACKGROUND_SIGNTOOL` 或 PATH 未找到）、缺少证书（`--certificate` 未提供）、或时间戳服务 URL 为空；`reason` 明确包含 `signtool`/`certificate`/`timestamp`，且不会调用签名工具、不删除产物。
- `signed`：签名命令与 `signtool verify /pa /all` 均返回 0，`reason` 为 `signed and verified`。
- `failed`：签名或验证命令返回非 0（例如签后 `verify` 失败），`reason` 包含 `sign failed` 或 `verify failed`。

验证已签名产物（工具与证书可用时）：

```bash
signtool verify /pa /all ShangBackground.exe
signtool verify /pa /all ShangBackground-*-setup.exe
```

当前环境无 `signtool`/证书时，诊断必须报告 `{"status": "unsigned", ...}`，结论措辞为“未签名（缺少 signtool/证书）”，不得写作“已签名”或“签名成功”。

## 发布验收表

发布结论必须分项陈述，不合并为单一“通过”：

| 验收项 | 需要证据 | 缺证据时的结论措辞 |
|---|---|---|
| 自动化测试 | `python -m pytest -q` 全绿、`python build_tools/build.py self-test` 通过 | 自动化行为已建立 / 自动化行为未验证 |
| 产物检查 | `dist-nuitka/<target>/.../standalone` 存在、`bin/mpv/mpv.exe` 与依赖清单、`SHA256SUMS.txt`、`build-features.json` 与哈希 | 发行产物已建立 / 发行产物未建立（缺 xxx） |
| Windows 桌面 smoke test | 真机执行：图片模式“选择视频”切换并播放、取消不改状态、托盘/桌面右键“全局设置”、冷启动不重复常驻、停止/退出清理子进程、进程路径指向安装目录 `mpv.exe` | 真实桌面可用性已建立 / 真实桌面可用性未验证（未在 Windows 真机执行） |
| 签名验证 | `python build_tools/build.py signing check --json` 输出 `status`；若 `signed` 需附 `signtool verify /pa /all` 对 `exe`、`setup.exe`、关键 DLL 的成功输出；若 `unsigned` 需附 `reason` 含缺失前置 | 签名已建立（已验证） / 未签名（缺少 signtool/证书） / 签名失败（verify 失败） |

`status=unsigned` 不得视为发布阻塞的伪造成功；未签名产物保留原位，便于审计，仅在签名成功后才视为可分发产物。

## 会话恢复验收

发布前在 Windows 真机至少执行：

1. 启动程序并切换到另一张静态或动态壁纸；
2. 连续点击两次“恢复启动前壁纸”，两次都不得报失败；
3. 再切换壁纸并退出程序，仍应恢复本次启动前的原始壁纸；
4. 正常退出成功后确认会话恢复文件被清理；恢复失败时应保留文件以便下次重试。
