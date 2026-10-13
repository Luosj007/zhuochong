# -*- coding: utf-8 -*-
"""
桌宠素材管理 + 抠图工具。

用法:
    python make_assets.py              # 列出所有图片, 选编号抠图
    python make_assets.py 0            # 抠第 0 张图片
    python make_assets.py hln.jpg      # 按文件名抠图
    python make_assets.py pets/hln.jpg # 完整路径

支持格式: jpg / jpeg / png / bmp / webp
抠图后生成 assets/pet.png, 重启 desktop_pet.py 即可生效。

抠图策略(自动选择):
    1. 纯色背景(纯黑/纯白等): 泛洪填充标记背景, 角色内部暗部不受影响
    2. 否则优先 rembg AI 抠图(需 pip install rembg onnxruntime)
    3. rembg 不可用时退回本地颜色分割
"""
import os
import sys
import glob

import numpy as np
from PIL import Image

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PETS_DIR = os.path.join(SCRIPT_DIR, "pets")
ASSETS_DIR = os.path.join(SCRIPT_DIR, "assets")
OUT = os.path.join(ASSETS_DIR, "pet.png")
IMG_EXTS = ("*.jpg", "*.jpeg", "*.png", "*.bmp", "*.webp")

# 全局: 供主程序和列表模式共享
_last_used = ""


def find_source():
    """
    命令行指定文件名/编号 > 交互式选择 > 自动选最新图片。
    返回 (路径, 文件名)。
    """
    if not os.path.isdir(PETS_DIR):
        os.makedirs(PETS_DIR, exist_ok=True)

    files = sorted(glob.glob(os.path.join(PETS_DIR, "*.*")))
    # 只保留支持的图片格式(不区分大小写)
    exts_lower = {e.lstrip("*.").lower() for e in IMG_EXTS}
    pics = [f for f in files if os.path.splitext(f)[1].lstrip(".").lower() in exts_lower]
    pics.sort(key=lambda f: os.path.basename(f).lower())

    if not pics:
        print("文件夹 %s/ 里没有支持的图片(jpg/jpeg/png/bmp/webp)" % os.path.basename(PETS_DIR))
        sys.exit(1)

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        # 尝试按索引选
        try:
            idx = int(arg)
            if 0 <= idx < len(pics):
                return pics[idx], os.path.basename(pics[idx])
            print("索引 %d 超出范围(0~%d)" % (idx, len(pics) - 1))
            sys.exit(1)
        except ValueError:
            pass
        # 按文件名选(支持相对 pets/ 下的路径)
        candidate = os.path.join(PETS_DIR, arg) if not os.path.isabs(arg) else arg
        if os.path.isfile(candidate):
            return candidate, os.path.basename(candidate)
        # 模糊匹配: 不区分大小写+扩展名
        for f in pics:
            if arg.lower() in os.path.basename(f).lower():
                return f, os.path.basename(f)
        print("找不到匹配的图片:", arg)
        print("可用图片: %s" % ", ".join(sorted(os.path.basename(f) for f in pics)))
        sys.exit(1)

    if len(pics) == 1:
        return pics[0], os.path.basename(pics[0])

    # 交互式列表 + 选最新
    print("\n═══ 桌宠图片素材 ═══════════════════════════════╗")
    for i, f in enumerate(pics):
        sz = os.path.getsize(f)
        if sz < 1024:
            s = "%d B" % sz
        elif sz < 1024 * 1024:
            s = "%.1f KB" % (sz / 1024)
        else:
            s = "%.1f MB" % (sz / 1024 / 1024)
        print("  [%d] %-32s %s" % (i, os.path.basename(f), s))
    print("═══════════════════════════════════════════════╝")
    print("最新图片: %s" % os.path.basename(pics[-1]))

    try:
        inp = input("\n请输入图片编号或名称 (直接回车选最新): ").strip()
    except (EOFError, KeyboardInterrupt):
        inp = ""
    if inp == "":
        return pics[-1], os.path.basename(pics[-1])

    # 复用上面的逻辑
    sys.argv = [sys.argv[0], inp]
    return find_source()


def _bg_color(rgb):
    """检测四角是否为同一纯色, 返回该颜色或 None。"""
    h, w = rgb.shape[:2]
    corners = np.stack([rgb[4, 4], rgb[4, w - 5],
                        rgb[h - 5, 4], rgb[h - 5, w - 5]]).astype(np.float32)
    c = corners.mean(axis=0)
    if corners.std(axis=0).max() > 12:
        return None
    return c


def _flood_background(rgb, bg, tolerance=12):
    """从图片四边向内做 BFS 泛洪填充, 标记与背景色连通的区域。"""
    from collections import deque
    h, w = rgb.shape[:2]
    bg_f = bg.astype(np.float32)
    diff = rgb.astype(np.float32) - bg_f
    dist = np.sqrt((diff ** 2).sum(axis=2))

    is_bg = np.zeros((h, w), dtype=bool)
    visited = np.zeros((h, w), dtype=bool)
    q = deque()

    def _try(y, x):
        if not visited[y, x] and dist[y, x] <= tolerance:
            visited[y, x] = True
            is_bg[y, x] = True
            q.append((y, x))

    for x in range(w):
        _try(0, x); _try(h - 1, x)
    for y in range(h):
        _try(y, 0); _try(y, w - 1)

    while q:
        cy, cx = q.popleft()
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            ny, nx = cy + dy, cx + dx
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] \
                    and dist[ny, nx] <= tolerance:
                visited[ny, nx] = True
                is_bg[ny, nx] = True
                q.append((ny, nx))

    return is_bg


def cutout_solid_bg(img):
    """纯色背景抠图: 泛洪填充标记背景, 角色内部暗部不受影响。"""
    rgb = np.asarray(img.convert("RGB"))
    bg = _bg_color(rgb)
    if bg is None:
        return None, False

    is_bg = _flood_background(rgb, bg, tolerance=12)
    diff = rgb.astype(np.float32) - bg
    color_dist = np.sqrt((diff ** 2).sum(axis=2))

    alpha = np.where(is_bg, 0.0,
                     np.clip((color_dist - 3.0) / 6.0, 0.0, 1.0) * 255.0)
    a8 = alpha.astype(np.uint8)
    cut = Image.fromarray(np.dstack([rgb, a8]), "RGBA")
    return cut, True


def cutout_rembg(img):
    from rembg import remove, new_session
    return remove(img, session=new_session("u2net"))


def cutout_color(img):
    """本地颜色分割: 高饱和暖色前景 vs 低饱和冷灰背景。"""
    from scipy import ndimage
    from skimage import morphology
    arr = np.asarray(img.convert("RGB")).astype(np.float32) / 255.0
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    mx = arr.max(axis=2)
    mn = arr.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    warm = (r > b + 0.02) & (g > b - 0.02)
    score = sat * 0.65 + warm.astype(np.float32) * 0.35
    fg = score > 0.18
    fg = morphology.binary_opening(fg, morphology.disk(3))
    fg = morphology.remove_small_objects(fg, min_size=2000)
    fg = ndimage.binary_closing(fg, structure=np.ones((15, 15)))
    fg = ndimage.binary_fill_holes(fg)
    lab, n = ndimage.label(fg)
    if n > 1:
        sizes = ndimage.sum(fg, lab, range(1, n + 1))
        fg = lab == (int(np.argmax(sizes)) + 1)
    dist = ndimage.distance_transform_edt(fg)
    alpha = np.clip(dist / 4.0, 0, 1)
    a8 = (alpha * 255).astype(np.uint8)
    out = np.dstack([np.asarray(img.convert("RGB")), a8])
    return Image.fromarray(out, "RGBA")


def main():
    src_path, src_name = find_source()
    print("\n源图片:", src_name)
    print("路径:", src_path)
    os.makedirs(ASSETS_DIR, exist_ok=True)
    src = Image.open(src_path).convert("RGBA")

    method = "?"
    cut = None
    cut, ok = cutout_solid_bg(src)
    if ok:
        method = "solid-bg-flood"
    else:
        try:
            print("尝试 rembg AI 抠图...")
            cut = cutout_rembg(src)
            method = "rembg"
        except Exception as e:
            print("rembg 不可用(%s), 退回本地颜色分割" % type(e).__name__)
            cut = cutout_color(src)
            method = "color"

    # 去杂点: 只保留最大连通块
    try:
        from scipy import ndimage
        a = np.asarray(cut)[:, :, 3]
        keep = a >= 128
        lab, n = ndimage.label(keep)
        if n > 1:
            sizes = ndimage.sum(keep, lab, range(1, n + 1))
            keep = lab == (int(np.argmax(sizes)) + 1)
            a = np.where(keep, a, 0)
            cut = Image.fromarray(np.dstack([np.asarray(cut)[:, :, :3], a]),
                                   "RGBA")
    except Exception:
        pass

    bbox = cut.getbbox()
    if bbox:
        pad = 6
        bbox = (max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                min(cut.width, bbox[2] + pad), min(cut.height, bbox[3] + pad))
        cut = cut.crop(bbox)
    cut.save(OUT)
    print("\nOK[%s]: %s %s" % (method, OUT, cut.size))

    # 预览图: 拼在蓝色背景上检查边缘
    prev = Image.new("RGBA", cut.size, (30, 120, 220, 255))
    prev.alpha_composite(cut)
    prev.convert("RGB").save(os.path.join(ASSETS_DIR, "preview.png"))

    print("\n 接下来:")
    print("  1. 打开 %s 检查抠图效果" % os.path.join(ASSETS_DIR, "preview.png"))
    print("  2. 右键退出旧桌宠, 再启动 desktop_pet.py")
    print("  3. 想换别的图片? 重复运行本脚本选不同编号即可")


if __name__ == "__main__":
    main()
