# ShangBackground 1.4.2

## 修复

- 修正打包版本号同步，统一升级到 1.4.2。
- 修复 Windows 静态壁纸首次切换动画在冻结版本中偶发需要先进入 HTML 壁纸模式的问题。
  - 保留 IDesktopWallpaper 原生过渡路径。
  - 避免 COM 设置成功后继续触发旧版 SPI 刷新覆盖 Explorer 过渡状态。
- 清理过期构建缓存文件和 Python `__pycache__` 产物。

## 构建工具

- 更新 build_tools 文档。
- 清理旧构建说明和无效临时文件。
