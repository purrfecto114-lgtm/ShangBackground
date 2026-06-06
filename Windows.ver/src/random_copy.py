import os
import json
import shutil
import time
import logging
import random

# 日志默认不落盘，由主程序日志设置控制。
logger = logging.getLogger("ShangBackground.random_copy")
logger.addHandler(logging.NullHandler())

COPY_PREFIX = "(xxdz_random_copy)"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RANDOM_CONFIG_PATH = os.path.join(BASE_DIR, "random.json")

# 支持的图片扩展名（统一常量）
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.bmp', '.gif')
DEFAULT_WEIGHT = 100


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



def _folder_data(config, folder_abs):
    """兼容新版 folders 结构和旧版 {folder: {filename: value}} 结构。"""
    if isinstance(config, dict) and isinstance(config.get("folders"), dict):
        return config.get("folders", {}).get(folder_abs, {})
    return config.get(folder_abs, {}) if isinstance(config, dict) else {}


def get_original_image_paths(folder_path):
    """获取文件夹中的原始壁纸路径，不包含旧概率副本文件。"""
    folder_abs = os.path.abspath(folder_path)
    if not os.path.isdir(folder_abs):
        return []
    paths = []
    for name in sorted(os.listdir(folder_abs), key=str.lower):
        if name.startswith(COPY_PREFIX):
            continue
        path = os.path.join(folder_abs, name)
        if os.path.isfile(path) and name.lower().endswith(IMAGE_EXTENSIONS):
            paths.append(path)
    return paths


def get_probability_weights(folder_path):
    """读取当前文件夹的随机权重；未设置的图片由调用方按 DEFAULT_WEIGHT 处理。"""
    folder_abs = os.path.abspath(folder_path)
    config = _load_config()
    folder_data = _folder_data(config, folder_abs)
    weights = {}
    if isinstance(folder_data, dict):
        for filename, value in folder_data.items():
            try:
                weights[filename] = max(0.0, float(value))
            except Exception:
                continue
    return weights


def save_probability_weights(folder_path, weights_dict):
    """保存每张原始壁纸的随机权重，不再通过物理副本实现概率。"""
    folder_abs = os.path.abspath(folder_path)
    if not os.path.isdir(folder_abs):
        raise ValueError("壁纸文件夹无效")

    cleaned = {}
    originals = {os.path.basename(path) for path in get_original_image_paths(folder_abs)}
    for filename, weight in (weights_dict or {}).items():
        if filename not in originals:
            continue
        try:
            value = max(0.0, float(weight))
        except Exception:
            value = 0.0
        if value > 0:
            cleaned[filename] = round(value, 4)

    config = _load_config()
    # 迁移旧结构到新版 folders，同时保留其它未知字段。
    folders = config.get("folders") if isinstance(config, dict) else None
    if not isinstance(folders, dict):
        folders = {}
        if isinstance(config, dict):
            for key, value in list(config.items()):
                if isinstance(value, dict) and os.path.isabs(str(key)):
                    folders[str(key)] = value
        config = {"__version__": 2, "folders": folders}
    else:
        config["__version__"] = 2

    if cleaned:
        folders[folder_abs] = cleaned
    else:
        folders.pop(folder_abs, None)
    _save_config(config)
    # 新版使用内存权重，清理旧物理副本，避免文件夹膨胀和列表误读。
    cleanup_physical_only(folder_abs)


def weighted_choice(folder_path, current_path=""):
    """按 random.json 中的权重挑选一张原始壁纸。权重为 0 的图片不会被选中。"""
    originals = get_original_image_paths(folder_path)
    if not originals:
        return None

    current_abs = os.path.abspath(current_path) if current_path else ""
    candidates = [path for path in originals if os.path.abspath(path) != current_abs]
    if not candidates:
        candidates = originals

    weights_map = get_probability_weights(folder_path)
    weighted = []
    weights = []
    for path in candidates:
        filename = os.path.basename(path)
        weight = weights_map.get(filename, DEFAULT_WEIGHT)
        try:
            weight = float(weight)
        except Exception:
            weight = DEFAULT_WEIGHT
        if weight <= 0:
            continue
        weighted.append(path)
        weights.append(weight)

    if not weighted:
        return random.choice(candidates)
    return random.choices(weighted, weights=weights, k=1)[0]

def get_copy_count(folder_path, filename):
    """获取指定图片的旧副本数量 / 新权重值。"""
    folder_abs = os.path.abspath(folder_path)
    config = _load_config()
    folder_data = _folder_data(config, folder_abs)
    return folder_data.get(filename, 0) if isinstance(folder_data, dict) else 0


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
    folder_data = _folder_data(config, folder_abs)

    if not folder_data:
        log(f"restore_weights: 没有找到权重配置 for {folder_abs}")
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
    raise RuntimeError("随机概率图形设置已整合到 PySide6 主界面，请从新版界面调整随机顺序相关设置。")
