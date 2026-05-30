"""
.py - 丝滑转场动画模块


通过 Tkinter 全屏覆盖窗口播放壁纸切换动画，
支持渐显混合(fade)、(slide)、(scan)三种效果。

性能说明：
  - 有 numpy 时：所有效果均通过 numpy 向量化操作实时计算，30 fps 无压力。
  - 无 numpy 时：回退 PIL Image 操作，使用预生成帧缓存避免卡顿。

兼容性：

  因此无论调用方是 Tkinter 还是 PySide6，都不会发生 QtRootShim 冲突。

预期导入方式（已在 legacy_tk_main.py 中使用）:
    
"""

from __future__ import annotations

import threading

try:
    from PIL import Image, ImageTk
except ImportError:  # pragma: no cover
    Image = None
    ImageTk = None

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


# ---------------------------------------------------------------------------
# 内部图像工具
# ---------------------------------------------------------------------------

def _resize_to_fit(img: Image.Image, fit_mode: str, target_size: tuple) -> Image.Image:
    """按适应模式缩放图片到目标尺寸，返回 RGB 模式 PIL Image。"""
    target_w, target_h = target_size
    rgb = img.convert("RGB")
    w, h = rgb.size

    if fit_mode == "填充" or fit_mode == "fill":
        ratio = max(target_w / w, target_h / h)
        nw = int(w * ratio)
        nh = int(h * ratio)
        resized = rgb.resize((nw, nh), Image.Resampling.LANCZOS)
        left = (nw - target_w) // 2
        top = (nh - target_h) // 2
        return resized.crop((left, top, left + target_w, top + target_h))

    if fit_mode == "适应" or fit_mode == "fit":
        ratio = min(target_w / w, target_h / h)
        nw = int(w * ratio)
        nh = int(h * ratio)
        resized = rgb.resize((nw, nh), Image.Resampling.LANCZOS)
        result = Image.new("RGB", target_size, (0, 0, 0))
        result.paste(resized, ((target_w - nw) // 2, (target_h - nh) // 2))
        return result

    if fit_mode == "拉伸" or fit_mode == "stretch":
        return rgb.resize(target_size, Image.Resampling.LANCZOS)

    if fit_mode == "居中" or fit_mode == "center":
        result = Image.new("RGB", target_size, (0, 0, 0))
        result.paste(rgb, ((target_w - w) // 2, (target_h - h) // 2))
        return result

    if fit_mode == "平铺" or fit_mode == "tile":
        result = Image.new("RGB", target_size)
        for x in range(0, target_w, w):
            for y in range(0, target_h, h):
                result.paste(rgb, (x, y))
        return result

    # fallback
    return rgb.resize(target_size, Image.Resampling.LANCZOS)


def _get_screen_size():
    """获取真实屏幕分辨率（包括任务栏区域）。

    优先使用 ctypes 读取 Windows 真实屏幕尺寸，
    fallback 返回常见分辨率。
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        pass
    return 1920, 1080


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------


    """丝滑壁纸转场动画控制器。

    构造参数:
        current_path : str  — 当前壁纸路径
        target_path  : str  — 目标壁纸路径
        duration     : float — 动画总时长（秒）
        on_complete  : callable — 动画完成回调（无参数）
        master       : 任意对象 — 仅用作 after() 调度源；
                       如果传入了有效的 Tkinter 根窗口则优先使用其 after()，
                       否则使用内部自建 Tk 根窗口。
        fit_mode     : str — 适应模式（填充/适应/拉伸/居中/平铺）
        effect       : str — 动画效果（fade/slide/scan）
        direction    : str — 方向（left/right/up/down）
    """

    _FPS = 30  # 目标帧率

    def __init__(
        self,
        current_path: str,
        target_path: str,
        duration: float,
        on_complete,
        master=None,
        fit_mode: str = "填充",
        effect: str = "fade",
        direction: str = "right",
    ):
        if Image is None:
            raise ImportError("PIL (Pillow) 未安装，无法使用丝滑转场")

        self.current_path = current_path
        self.target_path = target_path
        self.duration = max(0.1, duration)
        self.on_complete = on_complete
        self._external_master = master
        self.fit_mode = fit_mode if fit_mode in ("填充", "适应", "拉伸", "居中", "平铺") else "填充"
        self.effect = effect if effect in ("fade", "slide", "scan") else "fade"
        self.direction = direction if direction in ("left", "right", "up", "down") else "right"

        # 运行时状态
        self._running = False
        self._window = None
        self._canvas = None
        self._photo_ref = None  # 保留当前 PhotoImage 引用防止 GC
        # 内部 Tk 根窗口（自建，不依赖 master）
        self._tk_root = None

        # 预计算帧缓存（仅用于 PIL-only 回退路径）
        self._frames_cache = []

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def start(self):
        """开始播放转场动画（可在任意线程调用，自动调度到 Tk 主线程）。"""
        import tkinter as tk
        # 如果尚未创建内部根窗口，立即创建
        if self._tk_root is None:
            try:
                self._tk_root = tk.Tk()
                self._tk_root.withdraw()  # 隐藏主窗口
            except Exception as exc:
                raise RuntimeError(f"无法初始化 Tk 根窗口: {exc}") from exc

        # 确保 Tk 主循环已进入（否则 after 不会触发）
        # 通过检查当前线程 + 调度
        if not _is_tk_main_thread(self._tk_root):
            self._tk_root.after(0, self._start_impl)
            return

        self._start_impl()

    def _start_impl(self):
        """实际初始化（保证在 Tk 主线程中执行）。"""
        if self._running:
            return
        self._running = True

        # 1. 获取屏幕尺寸
        self._sw, self._sh = _get_screen_size()

        # 2. 加载并缩放两张图片
        try:
            cur_img = Image.open(self.current_path).convert("RGB")
            tgt_img = Image.open(self.target_path).convert("RGB")
        except Exception:
            self._finish()
            return
        target_size = (self._sw, self._sh)
        self._cur_pil = _resize_to_fit(cur_img, self.fit_mode, target_size)
        self._tgt_pil = _resize_to_fit(tgt_img, self.fit_mode, target_size)

        # 3. 预转为 numpy 数组（加速实时帧生成）
        if HAS_NUMPY:
            self._cur_arr = np.array(self._cur_pil)
            self._tgt_arr = np.array(self._tgt_pil)
        else:
            self._cur_arr = self._cur_pil
            self._tgt_arr = self._tgt_pil

        # 4. 计算帧参数
        total_frames = max(1, int(self.duration * self._FPS))
        self._total = total_frames
        self._interval_ms = int((self.duration / total_frames) * 1000)
        self._idx = 0

        # 5. 无 numpy 时预生成帧缓存
        if not HAS_NUMPY:
            self._precompute_frames()

        # 6. 创建全屏覆盖窗口（使用内部 _tk_root）
        import tkinter as tk
        self._window = tk.Toplevel(self._tk_root)
        self._window.title("")
        try:
            self._window.attributes("-fullscreen", True)
        except Exception:
            self._window.geometry(f"{self._sw}x{self._sh}+0+0")
        self._window.attributes("-topmost", True)
        self._window.lift()
        self._window.focus_force()

        # 退出途径
        self._window.protocol("WM_DELETE_WINDOW", self._finish)
        self._window.bind("<Escape>", lambda e: self._finish())
        self._window.bind("<Button-1>", lambda e: self._finish())

        from tkinter import Canvas as _Canvas
        self._canvas = _Canvas(
            self._window,
            width=self._sw, height=self._sh,
            highlightthickness=0, bd=0,
        )
        self._canvas.pack()

        # 7. 启动动画（显示第 0 帧）
        self._tick()

    # ------------------------------------------------------------------
    # 帧生成（有 numpy → 实时；无 numpy → 使用缓存）
    # ------------------------------------------------------------------

    def _tick(self):
        """动画滴答：从缓存或实时生成取一帧并显示。"""
        if not self._running or self._window is None:
            self._finish()
            return

        frame = None
        if self._frames_cache:
            # PIL-only：从预生成缓存取
            if self._idx < len(self._frames_cache):
                frame = self._frames_cache[self._idx]
        else:
            # 有 numpy：实时生成
            t = min(self._idx / self._total, 1.0) if self._total > 0 else 1.0
            frame = self._generate_frame(t)

        if frame is not None:
            try:
                photo = ImageTk.PhotoImage(frame)
                self._canvas.delete("all")
                self._canvas.create_image(0, 0, anchor="nw", image=photo)
                self._photo_ref = photo
            except Exception:
                pass

        self._idx += 1

        if self._idx <= self._total:
            self._tk_root.after(self._interval_ms, self._tick)
        else:
            self._finish()

    def _precompute_frames(self):
        """无 numpy 时预生成所有帧 PIL Image（回退路径）。"""
        self._frames_cache = []
        total = self._total
        if total <= 0:
            return
        for i in range(total + 1):
            t = min(i / total, 1.0) if total > 0 else 1.0
            frame = self._generate_frame(t)
            self._frames_cache.append(frame)

    # -- 实时帧生成（numpy 向量化） --

    def _generate_frame(self, t: float) -> Image.Image:
        """根据效果实时生成一帧。"""
        if self.effect == "fade":
            return self._gen_fade(t)
        if self.effect == "slide":
            return self._gen_slide(t)
        if self.effect == "scan":
            return self._gen_scan(t)
        return self._gen_fade(t)

    def _gen_fade(self, t: float) -> Image.Image:
        """ — numpy 浮点混合或 PIL blend。"""
        if HAS_NUMPY:
            blended = (
                self._cur_arr.astype(np.float32) * (1.0 - t)
                + self._tgt_arr.astype(np.float32) * t
            ).astype(np.uint8)
            return Image.fromarray(blended, "RGB")
        return Image.blend(self._cur_pil, self._tgt_pil, t)

    def _gen_slide(self, t: float) -> Image.Image:
        """ — numpy 数组切片（远快于 PIL crop+paste）。"""
        w, h = self._sw, self._sh
        if HAS_NUMPY:
            result = np.empty((h, w, 3), dtype=np.uint8)
            ca, ta = self._cur_arr, self._tgt_arr
            if self.direction == "right":
                sp = int(w * (1.0 - t))
                if sp > 0:
                    result[:, :sp] = ca[:, :sp]
                if sp < w:
                    result[:, sp:] = ta[:, sp:]
            elif self.direction == "left":
                sp = int(w * t)
                if sp > 0:
                    result[:, :sp] = ta[:, :sp]
                if sp < w:
                    result[:, sp:] = ca[:, sp:]
            elif self.direction == "up":
                sp = int(h * t)
                if sp > 0:
                    result[:sp] = ta[:sp]
                if sp < h:
                    result[sp:] = ca[sp:]
            elif self.direction == "down":
                sp = int(h * (1.0 - t))
                if sp > 0:
                    result[:sp] = ca[:sp]
                if sp < h:
                    result[sp:] = ta[sp:]
            return Image.fromarray(result, "RGB")
        # PIL fallback
        return self._slide_frame_pil(t)

    def _gen_scan(self, t: float) -> Image.Image:
        """扫描 — numpy 数组切片。"""
        w, h = self._sw, self._sh
        if HAS_NUMPY:
            result = np.empty((h, w, 3), dtype=np.uint8)
            ca, ta = self._cur_arr, self._tgt_arr
            if self.direction == "right":
                sp = int(w * t)
                if sp > 0:
                    result[:, :sp] = ta[:, :sp]
                if sp < w:
                    result[:, sp:] = ca[:, sp:]
            elif self.direction == "left":
                sp = int(w * (1.0 - t))
                if sp > 0:
                    result[:, :sp] = ca[:, :sp]
                if sp < w:
                    result[:, sp:] = ta[:, sp:]
            elif self.direction == "down":
                sp = int(h * t)
                if sp > 0:
                    result[:sp] = ta[:sp]
                if sp < h:
                    result[sp:] = ca[sp:]
            elif self.direction == "up":
                sp = int(h * (1.0 - t))
                if sp > 0:
                    result[:sp] = ca[:sp]
                if sp < h:
                    result[sp:] = ta[sp:]
            return Image.fromarray(result, "RGB")
        # PIL fallback
        return self._scan_frame_pil(t)

    # -- PIL-only 回退（slide / scan） --

    def _slide_frame_pil(self, t: float) -> Image.Image:
        """放映机效果 — PIL 实现（仅无 numpy 时进入）。"""
        w, h = self._sw, self._sh
        result = Image.new("RGB", (w, h))
        cur, tgt = self._cur_pil, self._tgt_pil
        if self.direction == "right":
            sp = int(w * (1.0 - t))
            if sp > 0:
                result.paste(cur.crop((0, 0, sp, h)), (0, 0))
            if sp < w:
                result.paste(tgt.crop((sp, 0, w, h)), (sp, 0))
        elif self.direction == "left":
            sp = int(w * t)
            if sp > 0:
                result.paste(tgt.crop((0, 0, sp, h)), (0, 0))
            if sp < w:
                result.paste(cur.crop((sp, 0, w, h)), (sp, 0))
        elif self.direction == "up":
            sp = int(h * t)
            if sp > 0:
                result.paste(tgt.crop((0, 0, w, sp)), (0, 0))
            if sp < h:
                result.paste(cur.crop((0, sp, w, h)), (0, sp))
        elif self.direction == "down":
            sp = int(h * (1.0 - t))
            if sp > 0:
                result.paste(cur.crop((0, 0, w, sp)), (0, 0))
            if sp < h:
                result.paste(tgt.crop((0, sp, w, h)), (0, sp))
        return result

    def _scan_frame_pil(self, t: float) -> Image.Image:
        """扫描效果 — PIL 实现（仅无 numpy 时进入）。"""
        w, h = self._sw, self._sh
        result = Image.new("RGB", (w, h))
        cur, tgt = self._cur_pil, self._tgt_pil
        if self.direction == "right":
            sp = int(w * t)
            if sp > 0:
                result.paste(tgt.crop((0, 0, sp, h)), (0, 0))
            if sp < w:
                result.paste(cur.crop((sp, 0, w, h)), (sp, 0))
        elif self.direction == "left":
            sp = int(w * (1.0 - t))
            if sp > 0:
                result.paste(cur.crop((0, 0, sp, h)), (0, 0))
            if sp < w:
                result.paste(tgt.crop((sp, 0, w, h)), (sp, 0))
        elif self.direction == "down":
            sp = int(h * t)
            if sp > 0:
                result.paste(tgt.crop((0, 0, w, sp)), (0, 0))
            if sp < h:
                result.paste(cur.crop((0, sp, w, h)), (0, sp))
        elif self.direction == "up":
            sp = int(h * (1.0 - t))
            if sp > 0:
                result.paste(cur.crop((0, 0, w, sp)), (0, 0))
            if sp < h:
                result.paste(tgt.crop((0, sp, w, h)), (0, sp))
        return result

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def _finish(self):
        """停止动画，清理窗口和内部 Tk 根窗口，触发回调。"""
        self._running = False
        self._photo_ref = None
        self._frames_cache.clear()

        # 销毁覆盖窗口
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None
        self._canvas = None

        # 销毁内部 Tk 根窗口
        if self._tk_root is not None:
            try:
                self._tk_root.destroy()
            except Exception:
                pass
            self._tk_root = None

        # 触发完成回调
        if self.on_complete is not None:
            cb = self.on_complete
            self.on_complete = None
            try:
                cb()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _is_tk_main_thread(root) -> bool:
    """判断当前线程是否为 Tk 窗口的主线程。

    在 'main' 线程且 Tk 已初始化时返回 True。
    """
    if threading.current_thread() is not threading.main_thread():
        return False
    try:
        root.tk.call("info", "command", ".", )
    except Exception:
        return False
    return True
