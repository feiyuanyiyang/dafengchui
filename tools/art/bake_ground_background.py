# -*- coding: utf-8 -*-
"""烘焙游戏内“地面”背景候选（伪 3D，俯视/斜俯视；不改游戏代码）。

两张 1200×840 的整屏地面：
    bg_ground_courtyard.png   明亮庭院：苔藓地 + 蜿蜒石板小径
    bg_ground_meadow.png      明亮草原：草地、草簇、野花

思路：画面就是角色脚下的一块地。用石缝投影、石块倒角、草丛投影、
水洼反光和柔光/暗角制造“俯视 + 一点厚度”的伪 3D，而不是风景远景。
顶部/底部有很轻的压暗，保证白色 HUD 与底部提示文字在亮地面上仍可读。

    python tools/art/bake_ground_background.py
    python tools/art/bake_ground_background.py --only meadow
"""

from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "art_preview"

W, H = 1200, 840
SS = 2
W2, H2 = W * SS, H * SS


# =============================================================================
# 基础
# =============================================================================
def _to_image(arr):
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGB")


def _fbm(w, h, seed, base=4, octaves=5, persistence=0.55):
    """多倍频值噪声，返回 0..1 的 float32 数组。"""
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w), dtype=np.float32)
    amplitude = 1.0
    total = 0.0
    for octave in range(octaves):
        gw = max(2, int(base * (2 ** octave)))
        gh = max(2, int(base * (2 ** octave) * h / w))
        grid = rng.random((gh, gw)).astype(np.float32)
        layer = Image.fromarray((grid * 255).astype(np.uint8)).resize(
            (w, h), Image.BICUBIC
        )
        out += amplitude * (np.asarray(layer, dtype=np.float32) / 255.0)
        total += amplitude
        amplitude *= persistence
    out /= total
    out -= out.min()
    return out / max(float(out.max()), 1e-6)


def _vertical_gradient(stops, w=W2, h=H2):
    ys = np.linspace(0.0, 1.0, h)
    positions = np.array([stop[0] for stop in stops], dtype=np.float32)
    colors = np.array([stop[1] for stop in stops], dtype=np.float32)
    out = np.empty((h, w, 3), dtype=np.float32)
    for channel in range(3):
        out[:, :, channel] = np.interp(
            ys, positions, colors[:, channel]
        )[:, None]
    return out


def _add_glow(base, cx, cy, rx, ry, color, strength, power=2.0):
    h, w = base.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    d = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
    falloff = np.exp(-(d ** (power * 0.5)) * 2.0)
    return base + falloff[:, :, None] * np.array(color, dtype=np.float32) * strength


def _radial_mask(w, h, cx, cy, radius, feather=0.18, wobble=0.0, seed=0):
    yy, xx = np.mgrid[0:h, 0:w]
    dx = xx - cx
    dy = yy - cy
    theta = np.arctan2(dy, dx)
    rng = np.random.default_rng(seed)
    if wobble:
        radius_map = radius * (
            1.0
            + wobble * np.sin(3 * theta + rng.uniform(0, math.tau))
            + wobble * 0.65 * np.sin(7 * theta + rng.uniform(0, math.tau))
        )
    else:
        radius_map = radius
    dist = np.hypot(dx, dy) / radius_map
    mask = np.clip((1.0 - dist) / feather, 0.0, 1.0)
    return mask * mask * (3.0 - 2.0 * mask)


def _finish(img2x, seed, grain=1.8, vignette=0.46, lift=-1.0, contrast=1.02,
            grade=None, top_shade=0.0, bottom_shade=0.0):
    img = img2x.resize((W, H), Image.LANCZOS)
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    arr = (arr - 128.0) * contrast + 128.0 + lift
    if grade is not None:
        arr = grade(arr)

    rng = np.random.default_rng(seed)
    arr += rng.normal(0.0, grain, (H, W, 1)).astype(np.float32)

    # 顶部柔和压暗，给白色 HUD 文字留出对比（指数渐变，不出现硬边）
    if top_shade:
        ys = np.linspace(0.0, 1.0, H, dtype=np.float32)[:, None]
        shade = np.exp(-ys / 0.13)
        arr *= (1.0 - top_shade * shade)[:, :, None]
    # 底部轻压暗，保证最下方的操作提示也能看清
    if bottom_shade:
        ys = np.linspace(0.0, 1.0, H, dtype=np.float32)[:, None]
        shade = np.exp(-(1.0 - ys) / 0.055)
        arr *= (1.0 - bottom_shade * shade)[:, :, None]

    yy, xx = np.mgrid[0:H, 0:W]
    nx = (xx + 0.5) / W - 0.5
    ny = (yy + 0.5) / H - 0.5
    d = np.hypot(nx / 0.62, ny / 0.58)
    t = np.clip((d - 0.40) / 0.80, 0.0, 1.0)
    v = t * t * (3.0 - 2.0 * t)
    arr *= (1.0 - vignette * v)[:, :, None]

    return _to_image(arr)


def _save(img, filename, out_dir):
    path = Path(out_dir) / filename
    img.save(path, optimize=True)
    print("  -> %s  (%d×%d, %.1f KB)" % (
        path, img.width, img.height, path.stat().st_size / 1024.0))


def _chamfer_rect(x1, y1, x2, y2, cut):
    """切角矩形：比尖角更像被踩磨过的石板，也更容易做出倒角。"""
    return [
        (x1 + cut, y1), (x2 - cut, y1),
        (x2, y1 + cut), (x2, y2 - cut),
        (x2 - cut, y2), (x1 + cut, y2),
        (x1, y2 - cut), (x1, y1 + cut),
    ]


# =============================================================================
# 候选 1：深夜庭院石板地
# =============================================================================
def courtyard_floor(seed=4401):
    w, h = W2, H2
    rng = random.Random(seed)

    # 石缝底色：先铺一层偏冷的湿泥/苔藓底
    base = _vertical_gradient([
        (0.00, (88, 84, 73)),
        (0.45, (102, 98, 86)),
        (0.78, (98, 94, 83)),
        (1.00, (86, 83, 72)),
    ], w, h)

    stone_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bevel_light = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bevel_dark = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    stone_mask = Image.new("L", (w, h), 0)
    sd = ImageDraw.Draw(stone_layer)
    shd = ImageDraw.Draw(shadow_layer)
    bld = ImageDraw.Draw(bevel_light)
    bdd = ImageDraw.Draw(bevel_dark)
    smd = ImageDraw.Draw(stone_mask)

    rows = []
    y = -0.08 * h
    while y < h * 1.06:
        row_h = rng.uniform(0.075, 0.11) * h
        rows.append((y, row_h))
        y += row_h

    for row_index, (row_y, row_h) in enumerate(rows):
        # 轻微的“近大远小”：上方的石板略小、下方略大，给俯视画面一点纵深
        persp = 0.90 + 0.22 * min(1.0, max(0.0, row_y / h))
        row_hh = row_h * persp
        x = -rng.uniform(0.02, 0.18) * w
        while x < w * 1.06:
            stone_w = rng.uniform(0.06, 0.115) * w * persp
            gap = rng.uniform(2.4, 4.2) * SS * persp
            # 8% 的格子空出来，变成苔藓/碎石，打散“砌墙”的规则感
            if rng.random() < 0.08:
                x += stone_w
                continue
            jitter = 5.0 * SS
            x1 = x + gap
            y1 = row_y + gap
            x2 = x + stone_w - gap
            y2 = row_y + row_hh * rng.uniform(0.80, 1.0) - gap
            if x2 - x1 < 30 * SS or y2 - y1 < 24 * SS:
                x += stone_w
                continue
            cut = rng.uniform(4, 10) * SS
            poly = [
                (px + rng.uniform(-jitter, jitter),
                 py + rng.uniform(-jitter, jitter))
                for px, py in _chamfer_rect(x1, y1, x2, y2, cut)
            ]
            # 阴影：统一向下偏一点，制造“石块浮起”的厚度
            shadow_poly = [(px + 4.0 * SS * persp, py + 5.5 * SS * persp)
                           for px, py in poly]
            shd.polygon(shadow_poly, fill=(58, 52, 42, 95))

            shade = rng.uniform(0.72, 1.08)
            tint = rng.choice([
                (136, 130, 116), (122, 118, 107), (148, 140, 124),
                (112, 109, 100), (140, 132, 116),
            ])
            warm = rng.uniform(-3, 4)
            stone_color = (
                int(max(0, min(255, tint[0] * shade + warm))),
                int(max(0, min(255, tint[1] * shade + warm * 0.5))),
                int(max(0, min(255, tint[2] * shade))),
                255,
            )
            sd.polygon(poly, fill=stone_color)
            smd.polygon(poly, fill=255)

            # 倒角：上/左受光，下/右压暗（沿切角多边形的每条边）
            cx = sum(p[0] for p in poly) / len(poly)
            cy = sum(p[1] for p in poly) / len(poly)
            for index in range(len(poly)):
                p1 = poly[index]
                p2 = poly[(index + 1) % len(poly)]
                mx = (p1[0] + p2[0]) / 2.0
                my = (p1[1] + p2[1]) / 2.0
                if (mx - cx) + (my - cy) < 0:
                    bld.line([p1, p2], fill=(226, 220, 202, 95),
                             width=max(1, int(1.8 * SS)))
                else:
                    bdd.line([p1, p2], fill=(32, 28, 22, 135),
                             width=max(1, int(1.8 * SS)))

            # 少量裂纹 / 水渍
            if rng.random() < 0.28:
                start = (x1 + rng.uniform(0.15, 0.7) * (x2 - x1), y1 + 4 * SS)
                end = (start[0] + rng.uniform(-40, 40) * SS,
                       start[1] + rng.uniform(20, 60) * SS)
                mid = ((start[0] + end[0]) / 2 + rng.uniform(-12, 12) * SS,
                       (start[1] + end[1]) / 2)
                bdd.line([start, mid, end], fill=(48, 40, 32, 45),
                         width=max(1, int(1.0 * SS)), joint="curve")

            x += stone_w

    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(2.2 * SS))
    stone_layer = stone_layer.filter(ImageFilter.GaussianBlur(0.6 * SS))
    bevel_light = bevel_light.filter(ImageFilter.GaussianBlur(0.5 * SS))
    bevel_dark = bevel_dark.filter(ImageFilter.GaussianBlur(0.5 * SS))

    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8)).convert("RGBA")
    img = Image.alpha_composite(img, shadow_layer)
    img = Image.alpha_composite(img, stone_layer)
    img = Image.alpha_composite(img, bevel_light)
    img = Image.alpha_composite(img, bevel_dark)

    # 石头表面色差：用 fBm 轻微调制
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    texture = _fbm(w, h, seed + 3, base=6, octaves=5)
    texture = (texture - 0.5)[:, :, None]
    mask_arr = np.asarray(stone_mask, dtype=np.float32)[:, :, None] / 255.0
    arr *= 1.0 + texture * 0.14 * mask_arr

    # 石缝里的苔藓
    moss = _fbm(w, h, seed + 7, base=20, octaves=4)
    moss = np.clip((moss - 0.50) * 3.2, 0, 1)
    moss *= 1.0 - mask_arr[:, :, 0]
    arr = arr + moss[:, :, None] * np.array((88, 102, 64), dtype=np.float32) * 0.14

    # 石块之间的环境遮蔽：缝隙更暗
    mask_blur = np.asarray(
        stone_mask.filter(ImageFilter.GaussianBlur(7 * SS)), dtype=np.float32
    )[:, :, None] / 255.0
    arr *= 0.80 + 0.20 * mask_blur

    # 苔藓团：零散趴在石缝和石块边缘
    moss_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    md = ImageDraw.Draw(moss_layer)
    mrng = random.Random(seed + 13)
    for _ in range(12):
        mx = mrng.uniform(0, w)
        my = mrng.uniform(0, h)
        for _ in range(mrng.randint(2, 4)):
            rx = mrng.uniform(3, 10) * SS
            ry = rx * mrng.uniform(0.35, 0.7)
            px = mx + mrng.uniform(-12, 12) * SS
            py = my + mrng.uniform(-8, 8) * SS
            md.ellipse([px - rx, py - ry, px + rx, py + ry],
                       fill=(98, 108, 70, mrng.randint(14, 34)))
    moss_layer = moss_layer.filter(ImageFilter.GaussianBlur(2.2 * SS))

    # 白天的柔光：左上偏暖、右下偏亮，不再用月夜冷光
    arr = _add_glow(arr, 0.28 * w, 0.20 * h, 0.48 * w, 0.38 * h,
                    (255, 244, 214), 0.055)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGB")
    img = Image.alpha_composite(img.convert("RGBA"), moss_layer)

    # 石缝里钻出的几丛杂草，进一步打散规则感
    weed_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    wd = ImageDraw.Draw(weed_layer)
    wrng = random.Random(seed + 21)
    for _ in range(10):
        wx = wrng.uniform(0, w)
        wy = wrng.uniform(0, h)
        wd.ellipse([wx - 9 * SS, wy - 2 * SS, wx + 9 * SS, wy + 4 * SS],
                   fill=(48, 42, 30, 32))
        for _blade_index in range(wrng.randint(4, 7)):
            angle = -math.pi / 2 + wrng.uniform(-1.1, 1.1)
            length = wrng.uniform(8, 18) * SS
            x2 = wx + math.cos(angle) * length
            y2 = wy + math.sin(angle) * length * 0.75
            wd.line([(wx, wy), (x2, y2)],
                    fill=(78, 100, 58, wrng.randint(70, 110)),
                    width=wrng.choice([1, 1, 2]))
    weed_layer = weed_layer.filter(ImageFilter.GaussianBlur(0.7 * SS))
    img = Image.alpha_composite(img, weed_layer)

    # 白天不画夜景水洼，改成石面上很淡的阳光反光
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    arr = _add_glow(arr, 0.32 * w, 0.34 * h, 0.30 * w, 0.20 * h,
                    (255, 248, 222), 0.045)
    arr = _add_glow(arr, 0.74 * w, 0.72 * h, 0.28 * w, 0.20 * h,
                    (255, 242, 206), 0.035)

    # 树影：白天的软阴影，落在地面左侧
    leaf_shadow = Image.new("L", (w, h), 0)
    lsd = ImageDraw.Draw(leaf_shadow)
    lrng = random.Random(seed + 11)
    clusters = [(0.12, 0.22, 0.26), (0.26, 0.36, 0.22), (0.08, 0.58, 0.20),
                (0.44, 0.14, 0.16), (0.62, 0.88, 0.15)]
    for cxr, cyr, spread in clusters:
        cx, cy = cxr * w, cyr * h
        for _ in range(26):
            angle = lrng.uniform(0, math.tau)
            dist = abs(lrng.gauss(0, spread)) * 0.28 * w
            x = cx + math.cos(angle) * dist
            y = cy + math.sin(angle) * dist * 0.8
            r = lrng.uniform(4, 15) * SS
            lsd.ellipse([x - r * 1.5, y - r * 0.6, x + r * 1.5, y + r * 0.6],
                        fill=255)
    leaf_shadow = leaf_shadow.filter(ImageFilter.GaussianBlur(7 * SS))
    shadow_arr = np.asarray(leaf_shadow, dtype=np.float32)[:, :, None] / 255.0
    arr *= 1.0 - 0.30 * shadow_arr

    # 画面外树冠投下的长阴影（几条软曲线），让地面有方向光
    branch_shadow = Image.new("L", (w, h), 0)
    bsd = ImageDraw.Draw(branch_shadow)
    brng = random.Random(seed + 15)
    for _ in range(4):
        start = (-0.05 * w, brng.uniform(0.18, 0.75) * h)
        end = (brng.uniform(0.55, 1.02) * w, brng.uniform(0.35, 1.0) * h)
        ctrl = ((start[0] + end[0]) * 0.45, (start[1] + end[1]) * 0.35)
        points = [
            (
                (1 - t) ** 2 * start[0] + 2 * (1 - t) * t * ctrl[0] + t ** 2 * end[0],
                (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * ctrl[1] + t ** 2 * end[1],
            )
            for t in [i / 16.0 for i in range(17)]
        ]
        bsd.line(points, fill=255, width=int(brng.uniform(18, 38) * SS),
                 joint="curve")
    branch_shadow = branch_shadow.filter(ImageFilter.GaussianBlur(16 * SS))
    branch_arr = np.asarray(branch_shadow, dtype=np.float32)[:, :, None] / 255.0
    arr *= 1.0 - 0.10 * branch_arr
    # 阴影之外再补一层暖阳光，让“树影/光斑”形成明暗节奏
    arr += (1.0 - shadow_arr) * np.array((255, 246, 214), dtype=np.float32) * 0.03
    img = _to_image(arr)

    # 少量落叶
    leaf_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(leaf_layer)
    lrng = random.Random(seed + 17)
    for _ in range(18):
        x = lrng.choice([lrng.uniform(0.0, 0.30), lrng.uniform(0.72, 1.0)]) * w
        y = lrng.uniform(0.1, 0.95) * h
        length = lrng.uniform(12, 22) * SS
        angle = lrng.uniform(-1.2, 1.2)
        points = [
            (x - length / 2, y), (x, y - length * 0.24),
            (x + length / 2, y), (x, y + length * 0.24),
        ]
        ca, sa = math.cos(angle), math.sin(angle)
        rotated = [
            (x + (px - x) * ca - (py - y) * sa,
             y + (px - x) * sa + (py - y) * ca)
            for px, py in points
        ]
        ld.polygon(rotated, fill=(138, 112, 68, 115))
    leaf_layer = leaf_layer.filter(ImageFilter.GaussianBlur(1.2 * SS))
    img = Image.alpha_composite(img.convert("RGBA"), leaf_layer)
    return _finish(img, seed, grain=1.2, vignette=0.30, lift=1.0,
                   contrast=1.02, top_shade=0.40)


# =============================================================================
# 候选 2：水彩夜色湿地（已弃用，保留生成逻辑供以后重做；不在 BUILDERS 中）
# =============================================================================
def watercolor_floor(seed=5502):
    w, h = W2, H2
    rng = random.Random(seed)

    base = _vertical_gradient([
        (0.00, (12, 20, 28)),
        (0.35, (15, 32, 36)),
        (0.66, (18, 40, 42)),
        (0.86, (13, 29, 33)),
        (1.00, (8, 19, 24)),
    ], w, h)

    # 大块湿画法色斑
    arr = base.copy()
    for index, (cx, cy, rx, ry, color, strength) in enumerate((
        (0.20, 0.24, 0.30, 0.22, (40, 82, 88), 0.28),
        (0.78, 0.30, 0.28, 0.24, (48, 70, 104), 0.25),
        (0.38, 0.68, 0.34, 0.24, (34, 80, 72), 0.23),
        (0.80, 0.76, 0.30, 0.22, (28, 62, 68), 0.25),
        (0.55, 0.92, 0.40, 0.18, (20, 46, 50), 0.26),
    )):
        arr = _add_glow(arr, cx * w, cy * h, rx * w, ry * h, color,
                        strength, power=1.5)
    img = _to_image(arr).convert("RGBA")

    bloom = Image.new("RGBA", (120, 84), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bloom)
    brng = random.Random(seed + 7)
    palette = [(40, 84, 88), (48, 66, 104), (32, 72, 66), (58, 102, 90),
               (26, 54, 60)]
    for _ in range(72):
        cx, cy = brng.uniform(0, 120), brng.uniform(0, 84)
        rx, ry = brng.uniform(8, 30), brng.uniform(5, 22)
        bd.ellipse([cx - rx, cy - ry, cx + rx, cy + ry],
                   fill=palette[brng.randrange(len(palette))]
                   + (brng.randint(12, 32),))
    bloom = bloom.resize((w, h), Image.BICUBIC)
    bloom = bloom.filter(ImageFilter.GaussianBlur(18 * SS))
    bloom = bloom.filter(ImageFilter.UnsharpMask(radius=6 * SS, percent=80,
                                                 threshold=0))
    img = Image.alpha_composite(img, bloom)

    # 干后的水痕边：几圈很淡的颜料边缘，增强“水彩”而不是“模糊照片”
    edge_layer = Image.new("RGBA", (120, 84), (0, 0, 0, 0))
    ed = ImageDraw.Draw(edge_layer)
    erng = random.Random(seed + 9)
    for _ in range(18):
        cx, cy = erng.uniform(0, 120), erng.uniform(0, 84)
        rw, rh = erng.uniform(8, 26), erng.uniform(5, 18)
        points = []
        for step in range(12):
            angle = step / 12.0 * math.tau
            wobble = erng.uniform(0.82, 1.18)
            points.append((cx + math.cos(angle) * rw * wobble,
                           cy + math.sin(angle) * rh * wobble))
        ed.line(points + [points[0]],
                fill=(10, 28, 34, erng.randint(30, 70)), width=2)
    edge_layer = edge_layer.resize((w, h), Image.BICUBIC)
    edge_layer = edge_layer.filter(ImageFilter.GaussianBlur(5 * SS))
    img = Image.alpha_composite(img, edge_layer)

    # 湿石板：很淡的石缝结构，给水彩地面一个“可踩”的尺度
    slab_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(slab_layer)
    srng = random.Random(seed + 31)
    slab_y = -0.06 * h
    while slab_y < h * 1.04:
        slab_h = srng.uniform(0.145, 0.195) * h
        slab_x = -srng.uniform(0.0, 0.10) * w
        while slab_x < w * 1.04:
            slab_w = srng.uniform(0.17, 0.27) * w
            gap = srng.uniform(6, 9) * SS
            poly = _chamfer_rect(
                slab_x + gap, slab_y + gap,
                slab_x + slab_w - gap, slab_y + slab_h - gap,
                srng.uniform(6, 14) * SS,
            )
            sdraw.polygon(poly, fill=(38, 68, 72, srng.randint(20, 38)))
            sdraw.line(poly + [poly[0]], fill=(8, 20, 24, srng.randint(28, 48)),
                       width=max(1, int(1.8 * SS)))
            slab_x += slab_w
        slab_y += slab_h
    slab_layer = slab_layer.filter(ImageFilter.GaussianBlur(2.4 * SS))
    img = Image.alpha_composite(img, slab_layer)

    # 几片落在湿地上的花瓣（很淡的暖色，只放边缘）
    petal_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pd = ImageDraw.Draw(petal_layer)
    prng = random.Random(seed + 33)
    for _ in range(14):
        edge = prng.choice([0, 1])
        cx = prng.uniform(0.0, 0.26) * w if edge == 0 else prng.uniform(0.74, 1.0) * w
        cy = prng.uniform(0.05, 0.95) * h
        for _petal in range(prng.randint(1, 3)):
            x = cx + prng.uniform(-14, 14) * SS
            y = cy + prng.uniform(-12, 12) * SS
            rx = prng.uniform(3, 6) * SS
            angle = prng.uniform(-1.2, 1.2)
            ca, sa = math.cos(angle), math.sin(angle)
            points = [
                (x - rx, y), (x, y - rx * 0.45),
                (x + rx, y), (x, y + rx * 0.45),
            ]
            rotated = [
                (x + (px - x) * ca - (py - y) * sa,
                 y + (px - x) * sa + (py - y) * ca)
                for px, py in points
            ]
            pd.polygon(rotated, fill=(150, 122, 110, prng.randint(26, 46)))
            pd.line([(x, y), (x + rx * 0.6, y + rx * 0.2)],
                    fill=(180, 156, 138, prng.randint(20, 36)), width=1)
    petal_layer = petal_layer.filter(ImageFilter.GaussianBlur(0.7 * SS))
    img = Image.alpha_composite(img, petal_layer)

    # 纸纹：低频纸浆 + 细纤维
    low = _fbm(w, h, seed + 11, base=5, octaves=5)
    low = (low - 0.5)[:, :, None]
    granulation = _fbm(w, h, seed + 12, base=26, octaves=4)
    granulation = (granulation - 0.5)[:, :, None]
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    arr += low * 10.0 + granulation * 8.0
    fiber_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fiber_layer)
    frng = random.Random(seed + 13)
    for _ in range(320):
        x = frng.uniform(0, w)
        y = frng.uniform(0, h)
        length = frng.uniform(6, 28) * SS
        angle = frng.uniform(-0.18, 0.18)
        fd.line(
            [(x, y), (x + math.cos(angle) * length,
                      y + math.sin(angle) * length)],
            fill=(176, 196, 188, frng.randint(5, 12)), width=1,
        )
    fiber_layer = fiber_layer.filter(ImageFilter.GaussianBlur(0.4))
    img = Image.alpha_composite(_to_image(arr).convert("RGBA"), fiber_layer)

    # 湿地水洼 + 冷光反射
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    arr = _add_glow(arr, 0.62 * w, 0.30 * h, 0.30 * w, 0.20 * h,
                    (70, 108, 116), 0.10)
    img = _to_image(arr).convert("RGBA")

    puddle_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for index, (px, py, prx, pry, alpha) in enumerate((
        (0.30, 0.56, 0.15, 0.055, 78),
        (0.68, 0.42, 0.10, 0.040, 64),
        (0.55, 0.80, 0.18, 0.060, 54),
    )):
        mask = _radial_mask(w, h, px * w, py * h, prx * w, feather=0.70,
                            wobble=0.25, seed=seed + 20 + index)
        yy, xx = np.mgrid[0:h, 0:w]
        squash = np.exp(-(((yy - py * h) / (pry * h)) ** 2) * 1.7)
        a = (mask * squash * alpha).astype(np.uint8)
        layer = np.zeros((h, w, 4), dtype=np.uint8)
        layer[:, :, 0] = 92
        layer[:, :, 1] = 122
        layer[:, :, 2] = 126
        layer[:, :, 3] = a
        puddle_layer = Image.alpha_composite(
            puddle_layer, Image.fromarray(layer, mode="RGBA")
        )
    puddle_layer = puddle_layer.filter(ImageFilter.GaussianBlur(4 * SS))
    img = Image.alpha_composite(img, puddle_layer)

    # 水洼里的反光
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    arr = _add_glow(arr, 0.30 * w, 0.552 * h, 0.11 * w, 0.020 * h,
                    (186, 206, 200), 0.20)
    arr = _add_glow(arr, 0.68 * w, 0.412 * h, 0.075 * w, 0.014 * h,
                    (168, 190, 188), 0.15)

    # 叶影（边缘）
    leaf_shadow = Image.new("L", (w, h), 0)
    lsd = ImageDraw.Draw(leaf_shadow)
    lrng = random.Random(seed + 23)
    for cxr, cyr, spread in ((0.10, 0.16, 0.18), (0.90, 0.22, 0.16),
                             (0.16, 0.86, 0.16), (0.88, 0.78, 0.17)):
        cx, cy = cxr * w, cyr * h
        for _ in range(22):
            angle = lrng.uniform(0, math.tau)
            dist = abs(lrng.gauss(0, spread)) * 0.26 * w
            x = cx + math.cos(angle) * dist
            y = cy + math.sin(angle) * dist
            r = lrng.uniform(5, 16) * SS
            lsd.ellipse([x - r * 1.6, y - r * 0.55, x + r * 1.6, y + r * 0.55],
                        fill=255)
    leaf_shadow = leaf_shadow.filter(ImageFilter.GaussianBlur(9 * SS))
    shadow = np.asarray(leaf_shadow, dtype=np.float32)[:, :, None] / 255.0
    arr *= 1.0 - 0.28 * shadow

    # 右下一点暖色，破掉全冷调
    arr = _add_glow(arr, 0.88 * w, 0.90 * h, 0.28 * w, 0.20 * h,
                    (206, 166, 112), 0.075)
    img = _to_image(arr)

    # 少量颜料颗粒
    speck = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    spd = ImageDraw.Draw(speck)
    srng = random.Random(seed + 29)
    for _ in range(140):
        x = srng.uniform(0, w)
        y = srng.uniform(0, h)
        r = srng.uniform(0.5, 1.6) * SS
        a = srng.randint(6, 20)
        spd.ellipse([x - r, y - r, x + r, y + r],
                    fill=(180, 204, 196, a))
    speck = speck.filter(ImageFilter.GaussianBlur(0.5))
    img = Image.alpha_composite(img.convert("RGBA"), speck)
    return _finish(
        img, seed, grain=2.6, vignette=0.44, lift=2.0, contrast=1.03,
        grade=_watercolor_ground_grade,
    )


def _watercolor_ground_grade(arr):
    gray = arr.mean(axis=2, keepdims=True)
    arr = gray + (arr - gray) * 1.14
    luminance = arr.mean(axis=2, keepdims=True)
    shadow = np.clip((80.0 - luminance) / 80.0, 0.0, 1.0)
    arr = arr + shadow * np.array((1.0, 6.0, 8.0), dtype=np.float32)
    return arr


# =============================================================================
# 候选 3：翠绿草原地面
# =============================================================================
def meadow_floor(seed=6603):
    w, h = W2, H2
    rng = random.Random(seed)

    # 底色：明亮的草坪绿，留出足够对比给角色和障碍物
    arr = _vertical_gradient([
        (0.00, (62, 104, 54)),
        (0.22, (78, 124, 64)),
        (0.52, (92, 142, 72)),
        (0.78, (80, 126, 64)),
        (1.00, (64, 104, 54)),
    ], w, h)
    arr = _add_glow(arr, 0.46 * w, 0.42 * h, 0.55 * w, 0.50 * h,
                    (132, 174, 92), 0.16)
    img = _to_image(arr).convert("RGBA")

    # 大块深浅：像被云影扫过的草场
    patch = _fbm(w, h, seed + 3, base=5, octaves=5)
    patch2 = _fbm(w, h, seed + 4, base=11, octaves=4)
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    arr += (patch - 0.5)[:, :, None] * np.array((18, 28, 14), dtype=np.float32)
    arr += (patch2 - 0.5)[:, :, None] * np.array((10, 16, 8), dtype=np.float32)
    img = _to_image(arr).convert("RGBA")

    # 细草：大量短笔画，近处更长更亮
    fine = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(fine)
    grng = random.Random(seed + 7)
    for _cluster in range(500):
        cx = grng.uniform(-0.02, 1.02) * w
        cy = grng.uniform(-0.02, 1.02) * h
        persp = 0.72 + 0.45 * min(1.0, max(0.0, cy / h))
        for _ in range(grng.randint(8, 18)):
            x = cx + grng.uniform(-24, 24) * SS
            y = cy + grng.uniform(-16, 16) * SS
            length = grng.uniform(4, 9) * SS * persp
            lean = grng.uniform(2, 7) * SS
            shade = grng.randint(88, 140)
            a = grng.randint(55, 115)
            gd.line([(x, y), (x + lean, y - length)],
                    fill=(int(shade * 0.58), shade + 28,
                          int(shade * 0.50), a), width=2)
    fine = fine.filter(ImageFilter.GaussianBlur(0.3))
    img = Image.alpha_composite(img, fine)

    # 粗草：稍长、更清楚的草叶，制造“能踩的厚度”
    coarse = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    cd = ImageDraw.Draw(coarse)
    crng = random.Random(seed + 9)
    for _cluster in range(220):
        cx = crng.uniform(-0.02, 1.02) * w
        cy = crng.uniform(-0.02, 1.02) * h
        persp = 0.75 + 0.45 * min(1.0, max(0.0, cy / h))
        for _ in range(crng.randint(4, 9)):
            x = cx + crng.uniform(-28, 28) * SS
            y = cy + crng.uniform(-18, 18) * SS
            length = crng.uniform(9, 20) * SS * persp
            lean = crng.uniform(3, 11) * SS
            shade = crng.randint(80, 132)
            a = crng.randint(85, 150)
            cd.line([(x, y), (x + lean, y - length)],
                    fill=(int(shade * 0.55), shade + 30,
                          int(shade * 0.48), a),
                    width=crng.choice([2, 2, 3]))
            if crng.random() < 0.30:
                cd.line([(x + lean, y - length),
                         (x + lean + crng.uniform(-2, 5) * SS,
                          y - length - 3 * SS)],
                        fill=(shade + 38, shade + 62, shade + 22, 140),
                        width=2)
    coarse = coarse.filter(ImageFilter.GaussianBlur(0.4))
    img = Image.alpha_composite(img, coarse)

    # 俯视草簇：放射状草叶 + 很浅的投影，撒在画面里
    tuft_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    td = ImageDraw.Draw(tuft_layer)
    trng = random.Random(seed + 11)
    for _ in range(16):
        cx = trng.uniform(-0.02, 1.02) * w
        cy = trng.uniform(-0.02, 1.02) * h
        persp = 0.72 + 0.45 * min(1.0, max(0.0, cy / h))
        blades = trng.randint(10, 17)
        radius = trng.uniform(13, 22) * SS * persp
        td.ellipse([cx - radius * 1.1, cy - radius * 0.35,
                    cx + radius * 1.1, cy + radius * 0.45],
                   fill=(52, 88, 46, 45))
        for _index in range(blades):
            angle = -math.pi / 2 + trng.uniform(-1.35, 1.35)
            length = trng.uniform(18, 38) * SS * persp
            x2 = cx + math.cos(angle) * length
            y2 = cy + math.sin(angle) * length * 0.72
            td.line([(cx, cy), (x2, y2)],
                    fill=(48, 96, 48, trng.randint(160, 215)),
                    width=trng.choice([2, 2, 3]))
        for _tip in range(trng.randint(4, 7)):
            angle = -math.pi / 2 + trng.uniform(-1.3, 1.3)
            length = trng.uniform(22, 42) * SS * persp
            x2 = cx + math.cos(angle) * length
            y2 = cy + math.sin(angle) * length * 0.72
            td.line([(x2 - 2 * SS, y2 + 3 * SS), (x2, y2)],
                    fill=(176, 210, 138, trng.randint(120, 170)), width=2)
    tuft_layer = tuft_layer.filter(ImageFilter.GaussianBlur(0.6 * SS))
    img = Image.alpha_composite(img, tuft_layer)

    # 一条被踩出来的草径：颜色略浅、草更短（保持很淡）
    trail = Image.new("L", (w, h), 0)
    tdraw = ImageDraw.Draw(trail)
    points = []
    for step in range(41):
        t = step / 40.0
        x = (0.04 + t * 0.94) * w
        y = (0.16 + t * 0.72 + 0.055 * math.sin(t * math.pi * 2.4)) * h
        points.append((x, y))
    tdraw.line(points, fill=255, width=int(0.060 * w), joint="curve")
    trail = trail.filter(ImageFilter.GaussianBlur(26 * SS))
    trail_arr = np.asarray(trail, dtype=np.float32)[:, :, None] / 255.0
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    arr += trail_arr * np.array((5, 9, 4), dtype=np.float32)
    img = _to_image(arr).convert("RGBA")

    # 小野花：草丛边缘和草径旁，近处大、远处小
    flower_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    fd = ImageDraw.Draw(flower_layer)
    frng = random.Random(seed + 17)
    for _ in range(34):
        cx = frng.choice([frng.uniform(0.02, 0.34),
                          frng.uniform(0.66, 0.98)]) * w
        cy = frng.uniform(0.04, 0.96) * h
        persp = 0.72 + 0.45 * min(1.0, max(0.0, cy / h))
        for _flower in range(frng.randint(2, 5)):
            x = cx + frng.uniform(-18, 18) * SS
            y = cy + frng.uniform(-14, 14) * SS
            r = frng.uniform(1.4, 2.6) * SS * persp
            color = frng.choice([(250, 246, 222), (236, 244, 204),
                                 (255, 228, 176)])
            for petal in range(5):
                angle = petal / 5.0 * math.tau
                px = x + math.cos(angle) * r
                py = y + math.sin(angle) * r * 0.85
                fd.ellipse([px - r * 0.60, py - r * 0.55,
                            px + r * 0.60, py + r * 0.55],
                           fill=color + (frng.randint(150, 200),))
            fd.ellipse([x - 0.6 * SS * persp, y - 0.6 * SS * persp,
                        x + 0.6 * SS * persp, y + 0.6 * SS * persp],
                       fill=(255, 244, 186, 190))
    flower_layer = flower_layer.filter(ImageFilter.GaussianBlur(0.35))
    img = Image.alpha_composite(img, flower_layer)

    # 云影：大块软暗斑，给地面一点“天气感”
    cloud = _fbm(w, h, seed + 23, base=4, octaves=3)
    cloud = np.clip((cloud - 0.48) * 2.2, 0, 1)[:, :, None]
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    arr *= 1.0 - 0.12 * cloud
    arr = _add_glow(arr, 0.24 * w, 0.22 * h, 0.30 * w, 0.25 * h,
                    (196, 214, 138), 0.08)

    # 顶部轻微压暗，保证 HUD 文字在浅草地上也清楚
    yy = np.arange(h, dtype=np.float32)[:, None]
    arr *= (1.0 - 0.10 * np.clip((0.12 * h - yy) / (0.12 * h), 0, 1))[:, :, None]
    img = _to_image(arr)
    return _finish(
        img, seed, grain=1.2, vignette=0.0, lift=2.0, contrast=1.01,
        grade=_meadow_grade, top_shade=0.0, bottom_shade=0.0,
    )


def _meadow_grade(arr):
    gray = arr.mean(axis=2, keepdims=True)
    arr = gray + (arr - gray) * 1.12
    luminance = arr.mean(axis=2, keepdims=True)
    highlight = np.clip((luminance - 55.0) / 100.0, 0.0, 1.0)
    arr = arr + highlight * np.array((4.0, 3.0, 0.0), dtype=np.float32)
    return arr


# =============================================================================
# 候选 1b：庭院苔藓 + 石板小径（替代满屏砖墙的方案）
# =============================================================================
def courtyard_path_floor(seed=4401):
    w, h = W2, H2
    rng = random.Random(seed)

    # 苔藓/泥土底：明亮但不过饱和
    arr = _vertical_gradient([
        (0.00, (100, 120, 72)),
        (0.38, (120, 146, 86)),
        (0.72, (112, 136, 80)),
        (1.00, (98, 118, 70)),
    ], w, h)
    arr = _add_glow(arr, 0.28 * w, 0.24 * h, 0.55 * w, 0.45 * h,
                    (212, 224, 150), 0.13)
    soil = _fbm(w, h, seed + 3, base=5, octaves=5)
    soil = np.clip((soil - 0.56) * 3.0, 0.0, 1.0)[:, :, None]
    arr = arr * (1.0 - 0.42 * soil) + soil * np.array(
        (112, 96, 70), dtype=np.float32
    ) * 0.42
    img = _to_image(arr).convert("RGBA")

    # 苔藓/短草纹理
    moss = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    md = ImageDraw.Draw(moss)
    mrng = random.Random(seed + 7)
    for _ in range(1600):
        x = mrng.uniform(0, w)
        y = mrng.uniform(0, h)
        length = mrng.uniform(2.5, 9) * SS
        lean = mrng.uniform(1, 6) * SS
        md.line([(x, y), (x + lean, y - length)],
                fill=(mrng.randint(78, 128), mrng.randint(112, 166),
                      mrng.randint(48, 84), mrng.randint(30, 85)), width=1)
    moss = moss.filter(ImageFilter.GaussianBlur(0.35))
    img = Image.alpha_composite(img, moss)

    # 石板小径：上窄下宽，形成俯视纵深
    stone_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bevel_light = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bevel_dark = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    stone_mask = Image.new("L", (w, h), 0)
    sd = ImageDraw.Draw(stone_layer)
    shd = ImageDraw.Draw(shadow_layer)
    bld = ImageDraw.Draw(bevel_light)
    bdd = ImageDraw.Draw(bevel_dark)
    smd = ImageDraw.Draw(stone_mask)

    row_y = -0.06 * h
    while row_y < h * 1.05:
        t = min(1.0, max(0.0, row_y / h))
        row_h = (0.052 + 0.055 * t) * h
        center = (0.25 + 0.67 * t + 0.040 * math.sin(t * 3.4 + 0.5)) * w
        width = (0.28 + 0.10 * t) * w
        left = center - width / 2
        right = center + width / 2
        x = left
        while x < right:
            stone_w = (0.062 + 0.050 * rng.random()) * w * (0.85 + 0.32 * t)
            if x + stone_w > right:
                stone_w = right - x
            gap = rng.uniform(2.0, 4.2) * SS
            x1 = x + gap
            y1 = row_y + gap
            x2 = x + stone_w - gap
            y2 = row_y + row_h * rng.uniform(0.82, 0.98) - gap
            if x2 - x1 > 24 * SS and y2 - y1 > 18 * SS:
                cut = rng.uniform(4, 10) * SS
                poly = [
                    (px + rng.uniform(-4 * SS, 4 * SS),
                     py + rng.uniform(-4 * SS, 4 * SS))
                    for px, py in _chamfer_rect(x1, y1, x2, y2, cut)
                ]
                shadow_poly = [(px + 3.5 * SS, py + 4.5 * SS) for px, py in poly]
                shd.polygon(shadow_poly, fill=(52, 44, 32, 140))

                shade = rng.uniform(0.80, 1.06)
                tint = rng.choice([
                    (172, 164, 144), (160, 152, 134), (180, 170, 148),
                    (150, 146, 132), (174, 164, 144),
                ])
                sd.polygon(poly, fill=(
                    int(min(255, tint[0] * shade)),
                    int(min(255, tint[1] * shade)),
                    int(min(255, tint[2] * shade)),
                    255,
                ))
                smd.polygon(poly, fill=255)

                cx = sum(p[0] for p in poly) / len(poly)
                cy = sum(p[1] for p in poly) / len(poly)
                for index in range(len(poly)):
                    p1 = poly[index]
                    p2 = poly[(index + 1) % len(poly)]
                    mx = (p1[0] + p2[0]) / 2.0
                    my = (p1[1] + p2[1]) / 2.0
                    if (mx - cx) + (my - cy) < 0:
                        bld.line([p1, p2], fill=(246, 240, 220, 105),
                                 width=max(1, int(1.7 * SS)))
                    else:
                        bdd.line([p1, p2], fill=(34, 30, 22, 150),
                                 width=max(1, int(1.7 * SS)))
            x += stone_w
        row_y += row_h * 0.94

    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(2.4 * SS))
    stone_layer = stone_layer.filter(ImageFilter.GaussianBlur(0.5 * SS))
    bevel_light = bevel_light.filter(ImageFilter.GaussianBlur(0.5 * SS))
    bevel_dark = bevel_dark.filter(ImageFilter.GaussianBlur(0.5 * SS))
    img = Image.alpha_composite(img, shadow_layer)
    img = Image.alpha_composite(img, stone_layer)
    img = Image.alpha_composite(img, bevel_light)
    img = Image.alpha_composite(img, bevel_dark)

    # 石头表面轻微色差
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    texture = _fbm(w, h, seed + 11, base=8, octaves=4)
    texture = (texture - 0.5)[:, :, None]
    mask_arr = np.asarray(stone_mask, dtype=np.float32)[:, :, None] / 255.0
    arr *= 1.0 + texture * 0.12 * mask_arr
    arr *= 0.94 + 0.06 * mask_arr
    img = _to_image(arr).convert("RGBA")

    # 苔藓从两侧爬过石径边缘
    creep = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    cd = ImageDraw.Draw(creep)
    crng = random.Random(seed + 15)
    for _ in range(220):
        t = crng.uniform(0.0, 1.0)
        row_h = (0.052 + 0.055 * t) * h
        center = (0.25 + 0.67 * t + 0.040 * math.sin(t * 3.4 + 0.5)) * w
        width = (0.28 + 0.10 * t) * w
        side = crng.choice([-1, 1])
        cx = center + side * width / 2 + crng.uniform(-8, 8) * SS
        cy = t * h + crng.uniform(-row_h, row_h)
        rx = crng.uniform(5, 18) * SS
        ry = rx * crng.uniform(0.4, 0.8)
        cd.ellipse([cx - rx, cy - ry, cx + rx, cy + ry],
                   fill=(crng.randint(72, 108), crng.randint(104, 138),
                         crng.randint(46, 72), crng.randint(40, 100)))
    creep = creep.filter(ImageFilter.GaussianBlur(2.2 * SS))
    img = Image.alpha_composite(img, creep)

    # 苔藓地上的草簇与小野花，让路两侧不是一块平绿
    detail = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dd = ImageDraw.Draw(detail)
    drng = random.Random(seed + 16)
    placed = 0
    attempts = 0
    while placed < 34 and attempts < 400:
        attempts += 1
        x = drng.uniform(0, w)
        y = drng.uniform(0, h)
        t = y / h
        center = (0.25 + 0.67 * t + 0.040 * math.sin(t * 3.4 + 0.5)) * w
        width = (0.28 + 0.10 * t) * w
        if abs(x - center) < width / 2 + 6 * SS:
            continue
        placed += 1
        dd.ellipse([x - 8 * SS, y - 2 * SS, x + 8 * SS, y + 4 * SS],
                   fill=(58, 74, 40, 40))
        for _blade in range(drng.randint(5, 9)):
            angle = -math.pi / 2 + drng.uniform(-1.0, 1.0)
            length = drng.uniform(8, 20) * SS
            x2 = x + math.cos(angle) * length
            y2 = y + math.sin(angle) * length * 0.75
            dd.line([(x, y), (x2, y2)],
                    fill=(92, 132, 64, drng.randint(110, 170)),
                    width=drng.choice([1, 1, 2]))
    for _ in range(26):
        x = drng.uniform(0, w)
        y = drng.uniform(0, h)
        t = y / h
        center = (0.25 + 0.67 * t + 0.040 * math.sin(t * 3.4 + 0.5)) * w
        width = (0.28 + 0.10 * t) * w
        if abs(x - center) < width / 2 + 4 * SS:
            continue
        for _flower in range(drng.randint(1, 3)):
            fx = x + drng.uniform(-8, 8) * SS
            fy = y + drng.uniform(-7, 7) * SS
            r = drng.uniform(1.2, 2.0) * SS
            for petal in range(5):
                angle = petal / 5.0 * math.tau
                px = fx + math.cos(angle) * r
                py = fy + math.sin(angle) * r * 0.85
                dd.ellipse([px - r * 0.6, py - r * 0.55,
                            px + r * 0.6, py + r * 0.55],
                           fill=(250, 246, 218, drng.randint(110, 160)))
    detail = detail.filter(ImageFilter.GaussianBlur(0.4))
    img = Image.alpha_composite(img, detail)

    # 叶影 / 阳光斑
    leaf_shadow = Image.new("L", (w, h), 0)
    lsd = ImageDraw.Draw(leaf_shadow)
    lrng = random.Random(seed + 17)
    for _ in range(5):
        cx = lrng.uniform(0.0, 0.7) * w
        cy = lrng.uniform(0.0, 0.8) * h
        for _ in range(20):
            angle = lrng.uniform(0, math.tau)
            dist = abs(lrng.gauss(0, 0.18)) * 0.3 * w
            x = cx + math.cos(angle) * dist
            y = cy + math.sin(angle) * dist
            r = lrng.uniform(4, 14) * SS
            lsd.ellipse([x - r * 1.5, y - r * 0.6, x + r * 1.5, y + r * 0.6],
                        fill=255)
    leaf_shadow = leaf_shadow.filter(ImageFilter.GaussianBlur(8 * SS))
    shadow_arr = np.asarray(leaf_shadow, dtype=np.float32)[:, :, None] / 255.0

    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    arr = _add_glow(arr, 0.30 * w, 0.26 * h, 0.34 * w, 0.26 * h,
                    (255, 246, 212), 0.08)
    arr = _add_glow(arr, 0.74 * w, 0.74 * h, 0.24 * w, 0.18 * h,
                    (255, 238, 194), 0.05)
    arr *= 1.0 - 0.18 * shadow_arr
    img = _to_image(arr).convert("RGBA")

    # 零星落叶与小野花
    decor = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dd = ImageDraw.Draw(decor)
    drng = random.Random(seed + 19)
    for _ in range(16):
        x = drng.uniform(0, w)
        y = drng.uniform(0, h)
        length = drng.uniform(8, 16) * SS
        angle = drng.uniform(-1.2, 1.2)
        ca, sa = math.cos(angle), math.sin(angle)
        points = [(x - length / 2, y), (x, y - length * 0.24),
                  (x + length / 2, y), (x, y + length * 0.24)]
        rotated = [
            (x + (px - x) * ca - (py - y) * sa,
             y + (px - x) * sa + (py - y) * ca)
            for px, py in points
        ]
        dd.polygon(rotated, fill=(152, 124, 76, 120))
    for _ in range(22):
        x = drng.uniform(0, w)
        y = drng.uniform(0, h)
        for petal in range(5):
            angle = petal / 5.0 * math.tau
            r = drng.uniform(1.0, 1.8) * SS
            px = x + math.cos(angle) * r
            py = y + math.sin(angle) * r * 0.85
            dd.ellipse([px - r * 0.6, py - r * 0.55, px + r * 0.6, py + r * 0.55],
                       fill=(250, 246, 218, drng.randint(110, 160)))
    decor = decor.filter(ImageFilter.GaussianBlur(0.4))
    img = Image.alpha_composite(img, decor)
    return _finish(img, seed, grain=1.1, vignette=0.0, lift=3.0,
                   contrast=1.02, top_shade=0.0, bottom_shade=0.0)


BUILDERS = {
    "courtyard": ("bg_ground_courtyard.png", courtyard_path_floor),
    "meadow": ("bg_ground_meadow.png", meadow_floor),
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="烘焙“地面”背景候选（不改游戏）")
    parser.add_argument("--only", choices=sorted(BUILDERS), action="append")
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for key in args.only or sorted(BUILDERS):
        filename, builder = BUILDERS[key]
        print("baking %s ..." % key)
        _save(builder(), filename, out_dir)


if __name__ == "__main__":
    main()
