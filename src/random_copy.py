import os
import json
import shutil
import time
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk
import logging

# 日志默认不落盘；主程序日志由 legacy_tk_main.log 按用户设置统一控制。
logger = logging.getLogger("ShangBackground.random_copy")
logger.addHandler(logging.NullHandler())

COPY_PREFIX = "(xxdz_random_copy)"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RANDOM_CONFIG_PATH = os.path.join(BASE_DIR, "random.json")

# 支持的图片扩展名（统一常量）
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.bmp')


def log(msg):
    """带时间戳的日志输出函数。"""
    timestamp = time.strftime("[%H:%M:%S]")
    print(f"{timestamp} {msg}")
    logger.info(msg)


def _load_config():
    """加载随机概率配置文件。"""
    if os.path.exists(RANDOM_CONFIG_PATH):
        try:
            with open(RANDOM_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_config(data):
    """保存随机概率配置文件。"""
    try:
        with open(RANDOM_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"保存随机配置失败: {e}")


def get_copy_count(folder_path, filename):
    """获取指定图片的副本数量（权重值）。"""
    folder_abs = os.path.abspath(folder_path)
    config = _load_config()
    folder_data = config.get(folder_abs, {})
    return folder_data.get(filename, 0)


def _delete_copies(folder_abs, filename=None):
    """删除指定文件夹中的副本文件。

    参数:
        folder_abs: 文件夹绝对路径
        filename: 如果指定，只删除该图片的副本；否则删除所有副本

    返回:
        删除的文件数量
    """
    if not os.path.isdir(folder_abs):
        return 0
    deleted = 0
    for f in os.listdir(folder_abs):
        if not f.startswith(COPY_PREFIX):
            continue
        if filename is not None and not f.endswith(filename):
            continue
        try:
            os.remove(os.path.join(folder_abs, f))
            deleted += 1
        except OSError:
            pass
    return deleted


def _create_copies(folder_abs, filename, copy_count):
    """为指定图片创建副本文件。

    参数:
        folder_abs: 文件夹绝对路径
        filename: 原始图片文件名
        copy_count: 需要创建的副本数量

    返回:
        实际创建的副本数量
    """
    original_path = os.path.join(folder_abs, filename)
    if not os.path.exists(original_path):
        return 0
    created = 0
    for i in range(1, copy_count + 1):
        copy_name = f"{COPY_PREFIX}_{i}_{filename}"
        copy_path = os.path.join(folder_abs, copy_name)
        try:
            shutil.copy2(original_path, copy_path)
            created += 1
        except Exception as e:
            log(f"复制失败 {copy_name}: {e}")
    return created


def _apply_copy_count(folder_path, filename, copy_count):
    """实际执行文件复制/删除（不保存配置）。

    参数:
        folder_path: 文件夹路径
        filename: 原始图片文件名
        copy_count: 目标副本数量

    返回:
        操作是否成功
    """
    folder_abs = os.path.abspath(folder_path)
    log(f"_apply_copy_count: folder={folder_abs}, filename={filename}, copy_count={copy_count}")

    if not os.path.isdir(folder_abs):
        return False
    original_path = os.path.join(folder_abs, filename)
    if not os.path.exists(original_path):
        log(f"_apply_copy_count: 原图不存在 {original_path}")
        return False

    copy_count = max(0, int(copy_count))

    # 删除该图片的所有旧副本
    deleted = _delete_copies(folder_abs, filename)
    log(f"_apply_copy_count: 删除 {deleted} 个旧副本")

    # 创建新副本
    created = _create_copies(folder_abs, filename, copy_count)
    log(f"_apply_copy_count: 创建 {created} 个副本 (目标: {copy_count})")
    return True


def save_all_changes(folder_path, changes_dict):
    """批量保存所有更改。

    参数:
        folder_path: 文件夹路径
        changes_dict: {filename: copy_count} 字典
    """
    folder_abs = os.path.abspath(folder_path)
    log(f"save_all_changes: folder={folder_abs}, changes={changes_dict}")

    for filename, copy_count in changes_dict.items():
        _apply_copy_count(folder_abs, filename, copy_count)

    # 更新配置文件
    config = _load_config()

    if not changes_dict:
        if folder_abs in config:
            del config[folder_abs]
    else:
        if folder_abs not in config:
            config[folder_abs] = {}
        for filename, copy_count in changes_dict.items():
            if copy_count == 0:
                config[folder_abs].pop(filename, None)
            else:
                config[folder_abs][filename] = copy_count
        # 如果文件夹配置为空，删除该条目
        if not config[folder_abs]:
            del config[folder_abs]

    _save_config(config)
    log("save_all_changes: 保存完成")


def cleanup_folder(folder_path):
    """删除指定文件夹下所有副本文件，并清空配置。"""
    folder_abs = os.path.abspath(folder_path)
    deleted = _delete_copies(folder_abs)
    log(f"cleanup_folder: 删除了 {deleted} 个副本文件")

    config = _load_config()
    if folder_abs in config:
        del config[folder_abs]
        _save_config(config)


def cleanup_physical_only(folder_path):
    """仅删除指定文件夹下所有副本文件，不清空配置文件（保留权重）。"""
    folder_abs = os.path.abspath(folder_path)
    deleted = _delete_copies(folder_abs)
    log(f"cleanup_physical_only: 删除了 {deleted} 个副本文件，配置未改动")


def restore_weights(folder_path):
    """根据 random.json 中的配置恢复副本文件。"""
    folder_abs = os.path.abspath(folder_path)
    log(f"restore_weights: folder={folder_abs}")

    if not os.path.isdir(folder_abs):
        log(f"restore_weights: 文件夹无效 {folder_abs}")
        return

    config = _load_config()
    folder_data = config.get(folder_abs, {})

    if not folder_data:
        log(f"restore_weights: 没有找到权重配置 for {folder_abs}")
        gui_root = globals().get("root")
        if gui_root is not None:
            try:
                gui_root.after(0, lambda: messagebox.showinfo(
                    "提示喵",
                    "未找到随机概率配置，请先打开「设置随机概率」并保存",
                    parent=gui_root
                ))
            except Exception:
                pass
        return

    log(f"restore_weights: 开始恢复 {len(folder_data)} 个图片的副本")

    for filename, copy_count in folder_data.items():
        original_path = os.path.join(folder_abs, filename)
        if not os.path.exists(original_path):
            log(f"restore_weights: 原图不存在 {filename}")
            continue

        # 删除该图片已有的所有副本，然后创建新副本
        _delete_copies(folder_abs, filename)
        created = _create_copies(folder_abs, filename, copy_count)
        log(f"restore_weights: {filename} 创建 {created} 个副本 (目标: {copy_count})")

    log("restore_weights: 恢复完成")


def get_all_images_with_copies(folder_path):
    """获取文件夹中所有图片文件路径（包括副本）。"""
    folder_abs = os.path.abspath(folder_path)
    if not os.path.isdir(folder_abs):
        return []
    all_files = os.listdir(folder_abs)
    return [os.path.join(folder_abs, f) for f in all_files
            if f.lower().endswith(IMAGE_EXTENSIONS)]


def open_random_probability_window(parent, folder):
    """打开随机概率设置窗口。

    参数:
        parent: 父窗口
        folder: 壁纸文件夹路径
    """
    if not folder or not os.path.isdir(folder):
        messagebox.showwarning("提示喵", "请先设置幻灯片文件夹", parent=parent)
        return

    all_files = os.listdir(folder)
    original_images = [f for f in all_files
                       if f.lower().endswith(IMAGE_EXTENSIONS) and not f.startswith(COPY_PREFIX)]

    if not original_images:
        messagebox.showinfo("提示喵", "文件夹中没有图片", parent=parent)
        return

    win = tk.Toplevel(parent)
    win.title("随机概率设置")
    win.geometry("995x505")
    win.minsize(500, 400)
    icon_path = os.path.join(BASE_DIR, "img", "LOGO.ico")
    if os.path.exists(icon_path):
        try:
            win.iconbitmap(icon_path)
        except Exception:
            pass

    main_frame = ttk.Frame(win, padding=10)
    main_frame.pack(fill="both", expand=True)

    # 存储每个图片的当前显示值和原始值
    items = {}
    pending_changes = {}  # {filename: new_value}
    has_unsaved_changes = False

    def mark_unsaved():
        nonlocal has_unsaved_changes
        has_unsaved_changes = True

    def on_image_click(filename, event=None):
        """高亮当前选中的图片。"""
        for fname, data in items.items():
            data['frame'].config(highlightbackground='#ffffff', highlightcolor='#ffffff', highlightthickness=0)
        current_data = items[filename]
        current_data['frame'].config(highlightbackground='#12F2D8', highlightcolor='#12F2D8', highlightthickness=2)

    def create_thumbnail(img_path):
        """创建缩略图，使用 with 语句确保资源释放。"""
        try:
            with Image.open(img_path) as img:
                img.thumbnail((140, 100), Image.Resampling.LANCZOS)
                return ImageTk.PhotoImage(img)
        except Exception:
            return None

    # 顶部按钮栏（背景色 #CCFCF2）
    top_frame = tk.Frame(main_frame, bg='#CCFCF2')
    top_frame.pack(fill="x", pady=(0, 10))

    def delete_all():
        nonlocal pending_changes, has_unsaved_changes
        if messagebox.askyesno("确认？", f"删除文件夹「{folder}」中所有识别文件（副本）？\n注意：此操作立即生效，无法撤销喵",
                                  parent=win):
            cleanup_folder(folder)
            pending_changes = {}
            has_unsaved_changes = False
            win.destroy()
            open_random_probability_window(parent, folder)

    btn_frame = tk.Frame(top_frame, bg='#CCFCF2')
    btn_frame.pack(side="left")
    ttk.Button(btn_frame, text="一键删除所有识别文件", command=delete_all).pack(side="left")
    tip_label = tk.Label(btn_frame, text="  ⓘ 此功能可以提供您喜欢的某个壁纸被随机到概率，也会在壁纸文件夹下创建识别文件，当然您可以随时恢复到最初状态",
                         bg='#CCFCF2', fg='#666666', font=("微软雅黑", 8))
    tip_label.pack(side="left", padx=(10, 0))

    def save_changes():
        nonlocal has_unsaved_changes, pending_changes
        if pending_changes:
            save_all_changes(folder, pending_changes.copy())
            for filename, new_val in pending_changes.items():
                items[filename]['original_value'] = new_val
                items[filename]['weight_label'].config(text=f"喜欢附加值: {new_val}")
            pending_changes = {}
            has_unsaved_changes = False
            win.destroy()

    def close_window():
        nonlocal has_unsaved_changes
        if has_unsaved_changes:
            result = messagebox.askyesnocancel("未保存的更改",
                                                  "随机概率已修改，您是否保存更改啊？",
                                                  parent=win)
            if result is True:
                save_changes()
            elif result is False:
                win.destroy()
        else:
            win.destroy()

    ttk.Button(top_frame, text="保存", command=save_changes).pack(side="right", padx=(5, 0))
    ttk.Button(top_frame, text="取消", command=close_window).pack(side="right")

    # 滚动区域
    canvas = tk.Canvas(main_frame, bg='#ffffff', highlightthickness=0)
    scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
    scrollable_frame = tk.Frame(canvas, bg='#ffffff')
    scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    # 创建图片网格
    all_img_frames = []
    for filename in original_images:
        full_path = os.path.join(folder, filename)
        photo = create_thumbnail(full_path)

        img_frame = tk.Frame(scrollable_frame, relief="flat", borderwidth=0, bg='#ffffff')

        if photo:
            img_label = tk.Label(img_frame, image=photo, cursor="hand2")
            img_label.image = photo
            img_label.pack(pady=5)
        else:
            img_label = tk.Label(img_frame, text="加载失败", bg="#ddd", width=20, height=8)
            img_label.pack(pady=5)

        name_label = ttk.Label(img_frame, text=filename, wraplength=130)
        name_label.pack()

        current_count = get_copy_count(folder, filename)
        weight_label = ttk.Label(img_frame, text=f"喜欢附加值: {current_count}")
        weight_label.pack(pady=(2, 0))

        # 滑块框架
        slider_frame = ttk.Frame(img_frame)
        var = tk.IntVar(value=current_count)

        scale = ttk.Scale(slider_frame, from_=0, to=20, orient="horizontal",
                          variable=var, length=120)
        scale.pack(side="left", padx=5)

        spinbox = ttk.Spinbox(slider_frame, from_=0, to=999, width=4, textvariable=var)
        spinbox.pack(side="left", padx=2)

        current_filename = filename
        current_var = var
        current_scale = scale
        current_spinbox = spinbox

        def on_slide(val, fn=current_filename, v=current_var, sb=current_spinbox):
            int_val = int(float(val))
            v.set(int_val)
            sb.delete(0, tk.END)
            sb.insert(0, str(int_val))
            pending_changes[fn] = int_val
            mark_unsaved()

        def on_spinbox_change(*args, fn=current_filename, v=current_var, sc=current_scale, sb=current_spinbox):
            try:
                val = v.get()
                val = max(0, min(999, val))
                v.set(val)
                sc.set(val)
                sb.delete(0, tk.END)
                sb.insert(0, str(val))
                pending_changes[fn] = val
                mark_unsaved()
            except Exception:
                pass

        scale.config(command=on_slide)
        var.trace_add("write", on_spinbox_change)

        slider_frame.pack(fill="x", pady=(5, 0))

        items[filename] = {
            'frame': img_frame,
            'slider_frame': slider_frame,
            'var': var,
            'scale': scale,
            'weight_label': weight_label,
            'original_value': current_count,
            'display_value': current_count
        }

        img_label.bind("<Button-1>", lambda e, fn=filename: on_image_click(fn))
        name_label.bind("<Button-1>", lambda e, fn=filename: on_image_click(fn))
        weight_label.bind("<Button-1>", lambda e, fn=filename: on_image_click(fn))

        all_img_frames.append(img_frame)

    # 动态布局函数
    def relayout(event=None):
        canvas_width = canvas.winfo_width()
        if canvas_width < 100:
            canvas_width = 800
        min_item_width = 170
        cols = max(1, canvas_width // min_item_width)

        for img_frame in all_img_frames:
            img_frame.grid_forget()

        for i, img_frame in enumerate(all_img_frames):
            row = i // cols
            col = i % cols
            img_frame.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")

        for c in range(cols):
            scrollable_frame.grid_columnconfigure(c, weight=1)

    for img_frame in all_img_frames:
        img_frame.pack_forget()

    win.update_idletasks()
    relayout()
    canvas.bind("<Configure>", relayout)

    def on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas.bind("<MouseWheel>", on_mousewheel)
    scrollable_frame.bind("<MouseWheel>", on_mousewheel)

    win.protocol("WM_DELETE_WINDOW", close_window)
    win.transient(parent)
    win.grab_set()
    win.wait_window()
