# -*- coding: utf-8 -*-
"""把一张 AI 生成 / 手绘的角色立绘，转换成游戏需要的玩家贴图。

适用场景
    你不需要会画画，也不需要画 6 张方向帧。只要有一张“正面全身、背景干净”的
    角色图，这个脚本会自动完成：抠背景 → 裁切 → 缩放 → 脚底对齐 → 生成左右
    两套 idle/walk 帧。

用法
    python tools/art/import_player.py 角色.png
    python tools/art/import_player.py 角色.png --hurt 受击.png
    python tools/art/import_player.py 角色.png --bg green     # 绿幕
    python tools/art/import_player.py 角色.png --bg white     # 白底
    python tools/art/import_player.py 角色.png --bg auto      # 取四角平均色（默认）
    python tools/art/import_player.py 角色.png --bg none      # 已有透明通道

输出（覆盖 assets/sprites/player/ 下同名文件）
    player_e_idle.png / walk0 / walk1
    player_w_idle.png / walk0 / walk1   （e 的水平镜像）
    player_hurt.png                     （--hurt 提供则用提供的，否则复制 idle）

约定
    - 输出 125×135 RGBA，脚底基线在图片底边；
    - walk0/walk1 由 idle 做 1.5% / 3% 的“纵向压缩 + 脚底固定”得到，
      是低成本的呼吸/走路起伏；要真正的迈腿，请用 VRoid/Blender 出 3 帧。
"""

from __future__ import annotations

import argparse
import sys
from collections import deque
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "assets" / "sprites" / "player"

TARGET_W, TARGET_H = 125, 135
INNER_MAX_W, INNER_MAX_H = 123, 133
SQUASH = (1.0, 0.985, 0.97)  # idle / walk0 / walk1

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GREEN = (0, 255, 0)


def parse_color(text):
    text = text.lower()
    if text == "white":
        return WHITE
    if text == "black":
        return BLACK
    if text == "green":
        return GREEN
    if text.startswith("#") and len(text) == 7:
        return tuple(int(text[i : i + 2], 16) for i in (1, 3, 5))
    raise argparse.ArgumentTypeError("颜色只支持 white/black/green/#RRGGBB")


def remove_background(img, color, tolerance=38.0):
    """从四边泛洪抠背景（只删与边缘连通的背景），不会误伤白色围裙/浅色皮肤。

    全局颜色阈值会把接近白色的皮肤/围裙也变透明；泛洪只处理“从画布边缘能走到”
    的背景区域，角色内部的浅色像素不受影响。最后 alpha 做 1px 腐蚀，去掉白边。
    """
    img = img.convert("RGBA")
    pixels = img.load()
    width, height = img.size
    near = bytearray(width * height)
    limit = tolerance * tolerance
    for y in range(height):
        row = y * width
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            dr, dg, db = r - color[0], g - color[1], b - color[2]
            if dr * dr + dg * dg + db * db <= limit:
                near[row + x] = 1

    visited = bytearray(width * height)
    queue = deque()

    def push(x, y):
        index = y * width + x
        if near[index] and not visited[index]:
            visited[index] = 1
            queue.append((x, y))

    for x in range(width):
        push(x, 0)
        push(x, height - 1)
    for y in range(height):
        push(0, y)
        push(width - 1, y)

    while queue:
        x, y = queue.popleft()
        r, g, b, _a = pixels[x, y]
        pixels[x, y] = (r, g, b, 0)
        if x > 0:
            push(x - 1, y)
        if x + 1 < width:
            push(x + 1, y)
        if y > 0:
            push(x, y - 1)
        if y + 1 < height:
            push(x, y + 1)

    # 1px 腐蚀：缩掉背景与角色之间那圈白边/绿边
    alpha = img.getchannel("A").filter(ImageFilter.MinFilter(3))
    img.putalpha(alpha)

    # 绿幕去溢色：只压“绿色明显压过红/蓝”的像素，白围裙、蓝发、皮肤不受影响
    pixels = img.load()
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            clean_g = min(g, max(r, b))
            if clean_g != g:
                pixels[x, y] = (r, clean_g, b, a)
    return img


def corner_color(img):
    img = img.convert("RGB")
    width, height = img.size
    points = [(2, 2), (width - 3, 2), (2, height - 3), (width - 3, height - 3)]
    colors = [img.getpixel(point) for point in points]
    return tuple(sum(channel) // len(colors) for channel in zip(*colors))


def trim(img):
    bbox = img.getchannel("A").point(lambda value: 255 if value > 8 else 0).getbbox()
    if bbox is None:
        raise SystemExit("抠图后没有任何不透明像素：检查 --bg 和原图背景色是否匹配。")
    return img.crop(bbox)


def fit_and_align(img):
    """等比缩放到 125×135 内，水平居中、脚底贴底边。"""
    img = trim(img)
    width, height = img.size
    scale = min(INNER_MAX_W / width, INNER_MAX_H / height)
    new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    img = img.resize(new_size, Image.LANCZOS)
    canvas = Image.new("RGBA", (TARGET_W, TARGET_H), (0, 0, 0, 0))
    x = (TARGET_W - new_size[0]) // 2
    y = TARGET_H - new_size[1]
    canvas.alpha_composite(img, (x, y))
    return canvas


def remove_gray_shadow(img, low=25, high=160, spread=14):
    """去掉低饱和度的灰色地面阴影（即梦常见），不影响白色围裙/深蓝衣服。"""
    pixels = img.load()
    width, height = img.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            if max(r, g, b) - min(r, g, b) <= spread and low <= (r + g + b) / 3.0 <= high:
                pixels[x, y] = (r, g, b, 0)
    return img


def keep_largest_component(img, min_ratio=0.02, min_area=60):
    """保留足够大的不透明连通块，去掉速度线、星星、水印残留等漂浮物。

    不直接“只留最大块”，避免把与身体分离的鞋子/发梢误删。
    """
    from collections import deque

    width, height = img.size
    pixels = img.load()
    seen = bytearray(width * height)
    components = []
    for y in range(height):
        for x in range(width):
            index = y * width + x
            if seen[index] or pixels[x, y][3] <= 10:
                continue
            queue = deque([(x, y)])
            seen[index] = 1
            component = []
            while queue:
                cx, cy = queue.popleft()
                component.append((cx, cy))
                for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if 0 <= nx < width and 0 <= ny < height:
                        ni = ny * width + nx
                        if not seen[ni] and pixels[nx, ny][3] > 10:
                            seen[ni] = 1
                            queue.append((nx, ny))
            components.append(component)
    if not components:
        return img
    biggest = max(len(component) for component in components)
    threshold = max(min_area, int(biggest * min_ratio))
    keep = set()
    for component in components:
        if len(component) >= threshold:
            keep.update(component)
    mask = Image.new("L", (width, height), 0)
    mask_pixels = mask.load()
    for x, y in keep:
        mask_pixels[x, y] = 255
    img.putalpha(Image.composite(img.getchannel("A"), Image.new("L", (width, height), 0), mask))
    return img


def remove_top_orphans(img, max_y=24, max_area=400):
    """去掉完全位于头顶上方、面积很小的漂浮物（速度线、星星、水印残留）。"""
    from collections import deque

    width, height = img.size
    pixels = img.load()
    seen = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            index = y * width + x
            if seen[index] or pixels[x, y][3] <= 10:
                continue
            queue = deque([(x, y)])
            seen[index] = 1
            component = []
            while queue:
                cx, cy = queue.popleft()
                component.append((cx, cy))
                for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if 0 <= nx < width and 0 <= ny < height:
                        ni = ny * width + nx
                        if not seen[ni] and pixels[nx, ny][3] > 10:
                            seen[ni] = 1
                            queue.append((nx, ny))
            bottom = max(cy for _, cy in component)
            if bottom < max_y and len(component) <= max_area:
                for px, py in component:
                    r, g, b, _a = pixels[px, py]
                    pixels[px, py] = (r, g, b, 0)
    return img


def clean_sprite(img):
    return remove_top_orphans(keep_largest_component(remove_gray_shadow(img)))



    return keep_largest_component(remove_gray_shadow(img))


def squash_from_bottom(img, factor):
    """纵向压缩但脚底固定，做出走路的轻微起伏。"""
    width, height = img.size
    new_height = max(1, round(height * factor))
    shrunk = img.resize((width, new_height), Image.LANCZOS)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.alpha_composite(shrunk, (0, height - new_height))
    return canvas


def make_frames(source, bg, tolerance, crop_bottom=0, clean=False):
    img = Image.open(source)
    if crop_bottom > 0:
        width, height = img.size
        img = img.crop((0, 0, width, max(1, height - crop_bottom)))
    if bg == "none":
        if img.mode != "RGBA":
            raise SystemExit("--bg none 需要图片自带透明通道。")
        cut = img.convert("RGBA")
    else:
        color = corner_color(img) if bg == "auto" else parse_color(bg)
        cut = remove_background(img, color, tolerance)
    idle = fit_and_align(cut)
    if clean:
        idle = clean_sprite(idle)
    frames = [squash_from_bottom(idle, factor) for factor in SQUASH]
    return frames


def save_set(out_dir, source, bg, tolerance, hurt_source=None, crop_bottom=0, clean=False):
    frames = make_frames(source, bg, tolerance, crop_bottom, clean)
    names = ("idle", "walk0", "walk1")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in zip(names, frames):
        frame.save(out_dir / ("player_e_%s.png" % name))
        ImageOps.mirror(frame).save(out_dir / ("player_w_%s.png" % name))
        print("  player_e_%s.png / player_w_%s.png" % (name, name))

    if hurt_source:
        hurt = make_frames(hurt_source, bg, tolerance, crop_bottom, clean)[0]
        hurt.save(out_dir / "player_hurt.png")
        print("  player_hurt.png（来自 %s）" % hurt_source)
    else:
        frames[0].save(out_dir / "player_hurt.png")
        print("  player_hurt.png（未提供 --hurt，暂用 idle 代替）")


def main(argv=None):
    parser = argparse.ArgumentParser(description="把一张正面角色图转换成游戏玩家贴图")
    parser.add_argument("source", help="正面全身角色图（PNG/JPG/WebP）")
    parser.add_argument("--hurt", default=None, help="可选的受击表情图")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    parser.add_argument("--bg", default="auto", help="auto/white/black/green/#RRGGBB/none")
    parser.add_argument("--tolerance", type=float, default=38.0, help="背景色容差（0-255）")
    parser.add_argument("--crop-bottom", type=int, default=0, help="导入前裁掉底部 N 像素（去即梦水印）")
    parser.add_argument("--clean", action="store_true", help="去掉灰色地面阴影与漂浮速度线（适合即梦图）")
    args = parser.parse_args(argv)

    source = Path(args.source)
    if not source.is_file():
        raise SystemExit("找不到输入图片：%s" % source)
    print("导入 %s → %s" % (source, args.out))
    save_set(Path(args.out), source, args.bg, args.tolerance, args.hurt, args.crop_bottom, args.clean)
    print("完成。运行 python game.py 查看效果。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
