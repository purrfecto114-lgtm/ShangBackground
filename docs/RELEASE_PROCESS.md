# 发布与产物管理

## 发布原则

三端二进制必须由对应系统原生构建和验收。异平台 dry-run 只能验证参数生成，不能替代签名、权限、系统 WebView、桌面层、退出恢复或 GPU/帧率实测。

## 运行包

```bash
python build_tools/build.py --tool pyinstaller --profile full --mode standalone
python build_tools/build.py --tool nuitka --profile full --mode standalone
```

先验证 standalone，再评估 onefile。运行包不应包含源码缓存、开发文档、内部验证材料或构建脚本。

## 源码归档约束

源码 ZIP 应当：

1. 只含一个顶层目录；
2. 排除缓存、构建目录、`build-generated/`、验证产物和 `BUILD-INFO.json`；
3. 不包含内部测试树与旧维护目录；
4. 保留 `build_tools/`、平台 requirements 和许可证文件；
5. 在重新解压后再次执行 AST、编码、导入契约和构建 dry-run；
6. 单独提供 SHA-256、补丁和验证报告。

清理时删除 `__pycache__/`、`.pytest_cache/`、`build-*/`、`dist-*/`、`build-generated/` 与临时日志。不要删除 `.venv`，除非准备重建环境。

## 会话恢复验收

发布前在 Windows 真机至少执行：

1. 启动程序并切换到另一张静态或动态壁纸；
2. 连续点击两次“恢复启动前壁纸”，两次都不得报失败；
3. 再切换壁纸并退出程序，仍应恢复本次启动前的原始壁纸；
4. 正常退出成功后确认会话恢复文件被清理；恢复失败时应保留文件以便下次重试。

## 构建工具验收

除真实 standalone 构建外，检查三个帮助入口、GUI 命令预览、开始/停止、产物目录和日志目录按钮。兼容入口生成的命令应与统一入口一致。
