# -*- coding: utf-8 -*-
"""烘焙游戏内背景候选（不改游戏代码）。

三张 1250×875 的整屏候选：
    bg_candidate_midnight_courtyard.png  深夜庭院
    bg_candidate_watercolor_night.png    水彩夜色
    bg_candidate_emerald_grassland.png   翠绿草原

设计纪律：中间游戏区只放低频渐变，细节压在边缘；整体比角色暗 2~3 档；
主色不超过 2 个 + 1 个强调色；最后统一加暗角与细微颗粒防渐变色带。

    python tools/art/bake_background.py
    python tools/art/bake_background.py --only courtyard
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

W, H = 1250, 875
SS = 2  # 内部超采样倍数，最后 LANCZOS 缩回 W×H


# =============================================================================
# 基础绘制
# =============================================================================
def _to_image(arr):
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGB")


def _gradient(stops, w=W * SS, h=H * SS):
    """竖向渐变；stops = [(位置 0..1, (r, g, b)), ...]。"""
    ys = np.linspace(0.0, 1.0, h)
    positions = np.array([stop[0] for stop in stops], dtype=np.float32)
    colors = np.array([stop[1] for stop in stops], dtype=np.float32)
    out = np.empty((h, w, 3), dtype=np.float32)
    for channel in range(3):
        out[:, :, channel] = np.interp(
            ys, positions, colors[:, channel]
        )[:, None]
    return _to_image(out)


def _add_glow(base, cx, cy, rx, ry, color, strength, power=2.0):
    """可叠加的椭圆光晕（加色），比直接叠半透明圆更干净。"""
    h, w = base.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    d = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
    falloff = np.exp(-(d ** (power * 0.5)) * 2.2)
    tint = np.array(color, dtype=np.float32)
    return base + falloff[:, :, None] * tint * strength


def _radial_mask(w, h, cx, cy, radius, feather=0.18, wobble=0.0, seed=0):
    """半径可被角度扰动（水彩边缘）；返回 0..1 的 float mask。"""
    yy, xx = np.mgrid[0:h, 0:w]
    dx = xx - cx
    dy = yy - cy
    theta = np.arctan2(dy, dx)
    if wobble:
        rng = np.random.default_rng(seed)
        radius_map = radius * (
            1.0
            + wobble * np.sin(3 * theta + rng.uniform(0, math.tau))
            + wobble * 0.6 * np.sin(7 * theta + rng.uniform(0, math.tau))
        )
    else:
        radius_map = radius
    dist = np.hypot(dx, dy) / radius_map
    mask = np.clip((1.0 - dist) / feather, 0.0, 1.0)
    return mask * mask * (3.0 - 2.0 * mask)


def _smooth_noise_1d(seed, length, points=6, octaves=3):
    rng = random.Random(seed)
    xs = np.linspace(0.0, 1.0, length)
    out = np.zeros(length, dtype=np.float32)
    for octave in range(octaves):
        count = max(2, int(points * (2 ** octave)))
        values = np.array([rng.uniform(0, 1) for _ in range(count + 1)],
                          dtype=np.float32)
        out += np.interp(xs, np.linspace(0.0, 1.0, count + 1), values) / (
            2 ** octave
        )
    out -= out.min()
    return out / max(float(out.max()), 1e-6)


def _add_fog(arr, y_ratio, thickness_ratio, color, strength, seed, power=2.0):
    """低对比雾带：横向缓慢起伏，纵向高斯衰减。"""
    h, w = arr.shape[:2]
    band = _smooth_noise_1d(seed, w, points=5, octaves=3)
    band = 0.35 + 0.65 * band
    yy = np.arange(h, dtype=np.float32)[:, None]
    falloff = np.exp(
        -(((yy - y_ratio * h) / (thickness_ratio * h)) ** 2) * power
    )
    return arr + falloff[:, :, None] * band[None, :, None] * np.array(
        color, dtype=np.float32
    ) * strength


def _ridge_points(seed, base_y, amplitude, w, octaves=4):
    rng = random.Random(seed)
    xs = np.linspace(0.0, 1.0, w, dtype=np.float32)
    y = np.full_like(xs, base_y)
    for octave in range(octaves):
        freq = 0.55 * (1.9 ** octave)
        weight = 1.0 / (1.6 ** octave)
        phase = rng.uniform(0.0, math.tau)
        y += amplitude * weight * np.sin(math.tau * freq * xs + phase)
    return xs, y


def _ridge_mask(w, h, xs, ys):
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    points = [(0, h), (0, int(ys[0]))]
    for x, y in zip(xs[::4], ys[::4]):
        points.append((int(x * w), int(y)))
    points.append((w, int(ys[-1])))
    points.append((w, h))
    draw.polygon(points, fill=255)
    return mask


def _silhouette(img, mask, color, softness=1.2, alpha=255):
    if softness:
        mask = mask.filter(ImageFilter.GaussianBlur(softness))
    layer = Image.new("RGBA", img.size, color + (alpha,))
    return Image.composite(layer, img.convert("RGBA"), mask)


def _composite_rgba(img, layer):
    return Image.alpha_composite(img.convert("RGBA"), layer)


def _finish(img2x, seed, grain=2.0, vignette=0.44, lift=-2.0, contrast=1.02,
            grade=None):
    """缩回 1250×875，做轻微调色 + 颗粒 + 暗角，返回 RGB。"""
    img = img2x.resize((W, H), Image.LANCZOS)
    arr = np.asarray(img.convert("RGB")).astype(np.float32)

    arr = (arr - 128.0) * contrast + 128.0 + lift
    if grade is not None:
        arr = grade(arr)

    rng = np.random.default_rng(seed)
    arr += rng.normal(0.0, grain, (H, W, 1)).astype(np.float32)

    yy, xx = np.mgrid[0:H, 0:W]
    nx = (xx + 0.5) / W - 0.5
    ny = (yy + 0.5) / H - 0.5
    d = np.hypot(nx / 0.62, ny / 0.60)
    t = np.clip((d - 0.42) / 0.78, 0.0, 1.0)
    v = t * t * (3.0 - 2.0 * t)
    arr *= (1.0 - vignette * v)[:, :, None]

    return _to_image(arr)


def _speckles(img, seed, count, area, size, color, alpha, blur=0.0):
    rng = random.Random(seed)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = area
    for _ in range(count):
        x = rng.uniform(x0, x1)
        y = rng.uniform(y0, y1)
        r = rng.uniform(0.55, 1.0) * size
        a = int(alpha * rng.uniform(0.35, 1.0))
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color + (a,))
    if blur:
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
    return _composite_rgba(img, layer)


# =============================================================================
# 场景部件
# =============================================================================
def _soft_moon(img, cx, cy, radius, seed, color=(222, 234, 230), alpha=200,
               halo_color=(70, 116, 132), halo_strength=0.5,
               halo_rx=3.2, halo_ry=2.8, wobble=0.02, mottle=0.0,
               craters=0, feather=0.12):
    w, h = img.size
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    arr = _add_glow(arr, cx, cy, radius * halo_rx, radius * halo_ry,
                    halo_color, halo_strength)
    arr = _add_glow(arr, cx, cy, radius * 1.35, radius * 1.35,
                    tuple(min(255, c + 40) for c in color),
                    halo_strength * 0.30)
    img = _to_image(arr)

    mask = _radial_mask(w, h, cx, cy, radius, feather=feather,
                        wobble=wobble, seed=seed)
    if mottle:
        rng = np.random.default_rng(seed + 1)
        low = rng.normal(0.0, 1.0, (14, 14)).astype(np.float32)
        low = (low - low.min()) / max(float(low.max() - low.min()), 1e-6)
        low_img = Image.fromarray((low * 255).astype(np.uint8), "L").resize(
            img.size, Image.BICUBIC
        ).filter(ImageFilter.GaussianBlur(10 * SS))
        low = np.asarray(low_img, dtype=np.float32) / 255.0
        mask = np.clip(mask * (1.0 - mottle * (1.0 - low)), 0.0, 1.0)

    moon_layer = Image.new("RGBA", img.size, color + (255,))
    moon_layer.putalpha(Image.fromarray(
        np.clip(mask * alpha, 0, 255).astype(np.uint8), "L"))
    img = _composite_rgba(img, moon_layer)

    if craters:
        rng = random.Random(seed + 2)
        crater = Image.new("RGBA", img.size, (0, 0, 0, 0))
        cd = ImageDraw.Draw(crater)
        for _ in range(craters):
            ang = rng.uniform(0, math.tau)
            dist = rng.uniform(0.0, radius * 0.62)
            x = cx + math.cos(ang) * dist
            y = cy + math.sin(ang) * dist
            r = rng.uniform(0.08, 0.20) * radius
            cd.ellipse([x - r, y - r * 0.9, x + r, y + r * 0.9],
                       fill=(150, 168, 166, rng.randint(10, 26)))
        crater = crater.filter(ImageFilter.GaussianBlur(2.0 * SS))
        crater.putalpha(Image.fromarray(
            (np.asarray(crater.getchannel("A"), dtype=np.float32)
             * np.clip(mask, 0, 1)).astype(np.uint8), "L"))
        img = _composite_rgba(img, crater)
    return img


def _cloud_wisp(img, x0, x1, y, seed, color=(160, 186, 198), alpha=16):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rng = random.Random(seed)
    for _ in range(5):
        cy = y + rng.uniform(-0.010, 0.010) * img.size[1]
        hh = rng.uniform(0.010, 0.020) * img.size[1]
        draw.ellipse([x0, cy - hh, x1, cy + hh],
                     fill=color + (rng.randint(max(6, alpha - 8), alpha),))
    layer = layer.filter(ImageFilter.GaussianBlur(9 * SS))
    return _composite_rgba(img, layer)


def _blade(draw, x, y_base, height, lean, width, color):
    top_x = x + lean
    top_y = y_base - height
    mid_x = x + lean * 0.28
    mid_y = y_base - height * 0.55
    points = []
    for step in range(9):
        t = step / 8.0
        px = (1 - t) ** 2 * x + 2 * (1 - t) * t * mid_x + t ** 2 * top_x
        py = (1 - t) ** 2 * y_base + 2 * (1 - t) * t * mid_y + t ** 2 * top_y
        points.append((px, py))
    draw.line(points, fill=color, width=width, joint="curve")


def _grass_band(img, seed, baseline, color, rim, count=120, tall_edges=True,
                lean_bias=0.0, rim_chance=0.22, seed_heads=0.0):
    """成簇生长的草：比均匀撒点更像草地，也更容易压住画面底边。"""
    rng = random.Random(seed)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    w, _h = img.size
    clusters = max(8, count // 4)
    for _ in range(clusters):
        cx = rng.uniform(-24, w + 24)
        edge_bias = min(max(cx, 0) / w, 1.0 - min(max(cx, 0) / w, 1.0))
        tall = (
            (1.0 - min(1.0, edge_bias / 0.30)) if tall_edges else 0.0
        )
        base_height = rng.uniform(12, 28) + tall * rng.uniform(58, 150)
        span = rng.uniform(12, 30)
        for _blade_index in range(rng.randint(3, 6)):
            x = cx + rng.uniform(-span, span)
            height = base_height * rng.uniform(0.62, 1.16)
            lean = (
                rng.uniform(-14, 18) * (0.5 + height / 120.0)
                + lean_bias * height
            )
            width = rng.choice([2, 2, 3, 3, 4])
            y_base = baseline + rng.uniform(-8, 10)
            _blade(draw, x, y_base, height, lean, width, color)
            if rng.random() < rim_chance:
                tip_x = x + lean
                tip_y = y_base - height
                draw.line(
                    [(tip_x, tip_y),
                     (tip_x - lean * 0.07, tip_y + height * 0.18)],
                    fill=rim, width=1,
                )
            if seed_heads and height > 62 and rng.random() < seed_heads:
                tip_x = x + lean
                tip_y = y_base - height
                head = rng.uniform(4, 8) * SS
                draw.ellipse(
                    [tip_x - 1.2 * SS, tip_y - head, tip_x + 1.2 * SS, tip_y],
                    fill=color,
                )
                draw.line(
                    [(tip_x, tip_y), (tip_x + head * 0.18, tip_y + head * 0.9)],
                    fill=rim, width=1,
                )
    return _composite_rgba(img, layer)


def _tapered_line(draw, points, width_start, width_end, fill):
    count = len(points) - 1
    for index in range(count):
        t = index / max(count - 1, 1)
        width = max(1, int(round(width_start + (width_end - width_start) * t)))
        draw.line([points[index], points[index + 1]], fill=fill, width=width)


def _quad(p0, p1, p2, steps=16):
    result = []
    for index in range(steps + 1):
        t = index / steps
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        result.append((x, y))
    return result


def _windswept_tree(img, base_x, base_y, height, seed, color, rim,
                    foliage=1.0, lean=0.20, spread=0.55, alpha=255,
                    detached=True):
    """被风吹向右侧的剪影树。

    树冠 = 几团压暗的“叶团底”+ 大量小叶片；先铺体积再收边缘，
    避免出现一根杆顶几个圆球的棒棒糖形状。
    """
    w, h = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rng = random.Random(seed)

    # 主干：底部略粗，向上收细并顺风倾斜
    trunk_top = (base_x + lean * height * 0.42, base_y - height * 0.50)
    trunk_ctrl = (base_x + lean * height * 0.03, base_y - height * 0.26)
    trunk = _quad((base_x, base_y), trunk_ctrl, trunk_top, steps=16)
    _tapered_line(
        draw, [(x - 1.6 * SS, y - 1.8 * SS) for x, y in trunk],
        int(12 * SS), int(4 * SS), rim,
    )
    _tapered_line(draw, trunk, int(10 * SS), int(3.2 * SS), color)

    # 根部外扩一点，避免树干像插在地上
    for direction in (-1, 1):
        draw.line(
            [(base_x, base_y),
             (base_x + direction * height * 0.055,
              base_y + height * 0.012)],
            fill=color, width=int(4 * SS),
        )

    branches = []
    for ratio, dx, dy in (
        (0.42, 0.20, -0.28),   # 向左上的反枝，让树冠不单调
        (0.58, 0.32, -0.34),
        (0.74, 0.44, -0.22),
        (0.88, 0.40, -0.10),
    ):
        index = int(ratio * (len(trunk) - 1))
        start = trunk[index]
        end = (start[0] + dx * height * spread + lean * height * 0.10,
               start[1] + dy * height)
        ctrl = (start[0] + dx * height * 0.35,
                start[1] + dy * height * 0.55)
        branch = _quad(start, ctrl, end, steps=12)
        _tapered_line(
            draw, [(x - 1.2 * SS, y - 1.4 * SS) for x, y in branch],
            int(5.2 * SS), int(1.6 * SS), rim,
        )
        _tapered_line(draw, branch, int(4.4 * SS), int(1.3 * SS), color)
        branches.append(branch)

    # 叶团底：沿枝末段铺几团有体积的暗色，再叠小叶片
    mass_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    mass_draw = ImageDraw.Draw(mass_layer)
    masses = []
    for branch in branches:
        anchors = branch[int(len(branch) * 0.55):] + [branch[-1]]
        for anchor in anchors[::2]:
            for _ in range(2):
                cx = anchor[0] + rng.uniform(-0.02, 0.05) * height
                cy = anchor[1] + rng.uniform(-0.03, 0.02) * height
                rx = rng.uniform(0.045, 0.075) * height * (0.85 + foliage * 0.2)
                ry = rx * rng.uniform(0.50, 0.72)
                mass_draw.ellipse(
                    [cx - rx, cy - ry, cx + rx, cy + ry],
                    fill=color + (255,),
                )
                masses.append((cx, cy, rx, ry))
    mass_layer = mass_layer.filter(ImageFilter.GaussianBlur(4.0 * SS))
    layer = Image.alpha_composite(layer, mass_layer)

    leaf_draw = ImageDraw.Draw(layer)
    for cx, cy, rx, ry in masses:
        count = int(rng.uniform(34, 54) * foliage)
        for _ in range(count):
            angle = rng.uniform(0, math.tau)
            radius = rng.uniform(0, 1) ** 0.55
            px = cx + math.cos(angle) * rx * radius * 1.12
            py = cy + math.sin(angle) * ry * radius * 1.05
            size = rng.uniform(1.5, 3.2) * SS
            shade = rng.uniform(0.72, 1.30)
            leaf_color = tuple(
                min(255, int(channel * shade)) for channel in color[:3]
            ) + (alpha,)
            leaf_draw.ellipse(
                [px - size * 1.35, py - size * 0.55,
                 px + size * 1.35, py + size * 0.55],
                fill=leaf_color,
            )
        # 树冠上缘的冷色亮边
        for _ in range(int(rng.uniform(6, 12) * foliage)):
            angle = rng.uniform(math.pi * 0.85, math.pi * 1.75)
            radius = rng.uniform(0.75, 1.05)
            px = cx + math.cos(angle) * rx * radius
            py = cy + math.sin(angle) * ry * radius
            size = rng.uniform(1.2, 2.4) * SS
            leaf_draw.ellipse(
                [px - size, py - size * 0.5, px + size, py + size * 0.5],
                fill=rim,
            )

    if detached:
        for _ in range(10):
            px = base_x + rng.uniform(0.10, 0.40) * height
            py = base_y - rng.uniform(0.28, 0.78) * height
            size = rng.uniform(1.4, 2.8) * SS
            leaf_draw.ellipse(
                [px - size * 1.3, py - size * 0.45,
                 px + size * 1.3, py + size * 0.45],
                fill=color[:3] + (int(alpha * 0.5),),
            )

    layer = layer.filter(ImageFilter.GaussianBlur(0.6 * SS))
    if alpha < 255:
        layer.putalpha(
            layer.getchannel("A").point(lambda value: value * alpha // 255)
        )
    return _composite_rgba(img, layer)


def _treeline(img, base_ratio, seed, color, count=14, size_ratio=0.05,
              alpha=210):
    """远山脊线上的小树剪影（提供尺度感，不做细节）。"""
    w, h = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rng = random.Random(seed)
    for _ in range(count):
        x = rng.uniform(0.0, 1.0) * w
        y = base_ratio * h + rng.uniform(-0.012, 0.010) * h
        size = rng.uniform(0.55, 1.0) * size_ratio * h
        trunk_w = max(1, int(size * 0.06))
        draw.line([(x, y), (x, y - size * 0.55)], fill=color + (alpha,),
                  width=trunk_w)
        for _leaf in range(3):
            cx = x + rng.uniform(-0.30, 0.30) * size
            cy = y - size * rng.uniform(0.55, 0.95)
            r = size * rng.uniform(0.16, 0.28)
            draw.ellipse([cx - r, cy - r * 0.8, cx + r, cy + r * 0.8],
                         fill=color + (alpha,))
    layer = layer.filter(ImageFilter.GaussianBlur(1.4))
    return _composite_rgba(img, layer)


def _forest_band(img, base_ratio, height_ratio, color, seed, alpha=200,
                 softness=2.0):
    """连续远树带：用起伏的树冠轮廓代替一排“棒棒糖”。"""
    w, h = img.size
    edge = _smooth_noise_1d(seed, w, points=14, octaves=3)
    top = base_ratio * h - (0.15 + 0.85 * edge) * height_ratio * h
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    points = [(0, h)]
    step = 6
    for index in range(0, w, step):
        points.append((index, int(top[min(index, w - 1)])))
    points.extend([(w, int(top[-1])), (w, h)])
    draw.polygon(points, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(softness))
    layer = Image.new("RGBA", (w, h), color + (alpha,))
    return Image.composite(layer, img.convert("RGBA"), mask)


def _ridge_band_mask(w, h, xs, ys, bottom_y):
    """山脊到水/地面之间的带状 mask：不会把下半屏也填成山。"""
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    points = [(0, int(bottom_y)), (0, int(ys[0]))]
    for x, y in zip(xs[::4], ys[::4]):
        points.append((int(x * w), int(y)))
    points.extend([(w, int(ys[-1])), (w, int(bottom_y))])
    draw.polygon(points, fill=255)
    return mask


def _reeds(img, seed, color, rim):
    """水边芦苇：只压左右下角，给湖面一点前景层次。"""
    w, h = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rng = random.Random(seed)
    for x0, x1 in ((0.0, 0.32), (0.84, 1.0)):
        for _ in range(16):
            x = rng.uniform(x0 * w, x1 * w)
            height = rng.uniform(0.06, 0.18) * h
            lean = rng.uniform(4, 18) * (height / (0.10 * h))
            base_y = 0.995 * h
            width = rng.choice([2, 2, 3, 3, 4])
            _blade(draw, x, base_y, height, lean, width, color)
            if rng.random() < 0.30:
                tip_x = x + lean
                tip_y = base_y - height
                head_h = rng.uniform(7, 13) * SS
                draw.ellipse(
                    [tip_x - 1.6 * SS, tip_y - head_h, tip_x + 1.6 * SS, tip_y],
                    fill=color,
                )
                draw.line([(tip_x, tip_y), (tip_x, tip_y + 1.2 * SS)],
                          fill=rim, width=1)
    layer = layer.filter(ImageFilter.GaussianBlur(1.0 * SS))
    return _composite_rgba(img, layer)


def _ridge_rim(img, xs, ys, color, alpha=40, width=1.6, softness=1.2):
    """山脊顶部的受光边（很淡），让丘陵不是纯色块。"""
    w, h = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    points = [(x * w, y) for x, y in zip(xs[::4], ys[::4])]
    draw.line(points, fill=color + (alpha,), width=max(1, int(width * SS)),
              joint="curve")
    layer = layer.filter(ImageFilter.GaussianBlur(softness * SS))
    return _composite_rgba(img, layer)


def _small_tree(img, cx, base_y, height, seed, color, alpha=235, blur=1.0):
    """远景小树：只用剪影，不追求枝干细节。"""
    w, h = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rng = random.Random(seed)
    top = (cx + rng.uniform(-0.05, 0.05) * height, base_y - height * 0.55)
    draw.line([(cx, base_y), top], fill=color + (alpha,),
              width=max(1, int(2.6 * SS)))
    for _ in range(rng.randint(4, 6)):
        lx = top[0] + rng.uniform(-0.28, 0.28) * height
        ly = top[1] + rng.uniform(-0.20, 0.12) * height
        rx = rng.uniform(0.13, 0.22) * height
        ry = rx * rng.uniform(0.65, 0.95)
        draw.ellipse([lx - rx, ly - ry, lx + rx, ly + ry],
                     fill=color + (alpha,))
    layer = layer.filter(ImageFilter.GaussianBlur(blur * SS))
    return _composite_rgba(img, layer)


def _pine(img, cx, base_y, height, seed, color, rim=None, alpha=235,
          blur=1.0):
    """风格化松树剪影：叠层三角树冠，适合草原的远景和左侧框景。"""
    w, h = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rng = random.Random(seed)
    trunk_h = height * 0.22
    draw.line([(cx, base_y), (cx, base_y - trunk_h)],
              fill=color + (alpha,), width=max(1, int(2.4 * SS)))
    layers = 5
    for index in range(layers):
        ratio = index / (layers - 1)  # 0 顶部 -> 1 底部
        layer_width = height * (0.10 + 0.30 * ratio)
        layer_height = height * (0.30 - 0.035 * index)
        top_y = base_y - trunk_h - height * (0.16 + 0.16 * index)
        bottom_y = top_y + layer_height
        tip_x = cx + rng.uniform(-0.01, 0.01) * height
        left = (tip_x - layer_width * 0.5, bottom_y)
        right = (tip_x + layer_width * 0.5, bottom_y)
        # 下缘做一点不规则，避免完美等腰三角
        points = [
            (tip_x, top_y),
            (right[0], right[1] - rng.uniform(0, 0.03) * height),
            (right[0] - layer_width * 0.30, right[1]),
            (tip_x + layer_width * 0.16, bottom_y - layer_height * 0.10),
            (left[0] + layer_width * 0.30, left[1]),
            (left[0], left[1] - rng.uniform(0, 0.03) * height),
        ]
        draw.polygon(points, fill=color + (alpha,))
    if rim is not None:
        # 左上受光边缘：沿树冠轮廓轻描一笔
        for index in range(layers):
            ratio = index / (layers - 1)
            layer_width = height * (0.10 + 0.30 * ratio)
            top_y = base_y - trunk_h - height * (0.16 + 0.16 * index)
            bottom_y = top_y + height * (0.30 - 0.035 * index)
            draw.line(
                [(cx - layer_width * 0.48, bottom_y - 0.03 * height),
                 (cx - layer_width * 0.10, top_y + 0.02 * height)],
                fill=rim, width=max(1, int(1.0 * SS)),
            )
    layer = layer.filter(ImageFilter.GaussianBlur(blur * SS))
    return _composite_rgba(img, layer)


def _god_rays(img, cx, cy, seed, color=(255, 226, 180), count=3, alpha=6):
    """从太阳斜向下的宽光束；只在大气里加一点方向感，绝不压过角色。"""
    w, h = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rng = random.Random(seed)
    for _ in range(count):
        angle = math.radians(rng.uniform(36, 66))
        length = 1.5 * max(w, h)
        width = rng.uniform(0.020, 0.055) * w
        dx = math.cos(angle) * length
        dy = math.sin(angle) * length
        points = [
            (cx - width * 0.15, cy),
            (cx + width * 0.15, cy),
            (cx + dx + width * 1.6, cy + dy),
            (cx + dx - width * 1.6, cy + dy),
        ]
        draw.polygon(points, fill=color + (alpha,))
    layer = layer.filter(ImageFilter.GaussianBlur(26 * SS))
    return _composite_rgba(img, layer)


def _stone_basin(img, cx, base_y, size, color, rim):
    """庭院角落的石水钵：一个矮石台 + 水面的一点月反光。"""
    w, h = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.ellipse([cx - size * 0.95, base_y - size * 0.30,
                  cx + size * 0.95, base_y + size * 0.14],
                 fill=color + (255,))
    draw.rectangle([cx - size * 0.55, base_y - size * 0.44,
                    cx + size * 0.55, base_y - size * 0.10],
                   fill=color + (255,))
    draw.ellipse([cx - size * 0.62, base_y - size * 0.50,
                  cx + size * 0.62, base_y - size * 0.22],
                 fill=color + (255,))
    # 水面与高光
    draw.ellipse([cx - size * 0.48, base_y - size * 0.46,
                  cx + size * 0.48, base_y - size * 0.28],
                 fill=(12, 28, 34, 220))
    draw.arc([cx - size * 0.48, base_y - size * 0.46,
              cx + size * 0.48, base_y - size * 0.28],
             200, 340, fill=rim, width=max(1, int(1.2 * SS)))
    draw.line([(cx - size * 0.24, base_y - size * 0.38),
               (cx + size * 0.10, base_y - size * 0.38)],
              fill=(120, 158, 166, 65), width=max(1, int(1.2 * SS)))
    layer = layer.filter(ImageFilter.GaussianBlur(0.9 * SS))
    return _composite_rgba(img, layer)


def _stream(img, seed, x_start, y_start, x_end, y_end, width_start,
            width_end, color, highlight):
    """一条蜿蜒的浅色溪流（低对比，只做引导线）。"""
    w, h = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rng = random.Random(seed)
    top = (x_start * w, y_start * h)
    bottom = (x_end * w, y_end * h)
    ctrl = ((x_start + (x_end - x_start) * 0.42) * w,
            (y_start + (y_end - y_start) * 0.40) * h)
    center = _quad(top, ctrl, bottom, steps=28)
    left, right = [], []
    for index, (x, y) in enumerate(center):
        t = index / (len(center) - 1)
        half = (width_start + (width_end - width_start) * t) * w * 0.5
        offset = math.sin(t * math.pi * 1.6 + 0.4) * 0.004 * w
        left.append((x - half + offset, y))
        right.append((x + half + offset, y))
    polygon = left + list(reversed(right))
    draw.polygon(polygon, fill=color)
    # 亮一点的岸线
    draw.line(right, fill=highlight, width=max(1, int(1.4 * SS)),
              joint="curve")
    layer = layer.filter(ImageFilter.GaussianBlur(5 * SS))
    return _composite_rgba(img, layer)


def _fence(img, x0, x1, base_y, seed, color, rim):
    """低矮木栅栏剪影：庭院边界用，压在草丛后面。"""
    w, h = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rng = random.Random(seed)
    height = 0.056 * h
    step = 0.075 * w
    x = x0
    while x < x1:
        top_y = base_y - height * rng.uniform(0.92, 1.05)
        lean = rng.uniform(-2, 3) * SS
        draw.line([(x, base_y + 4 * SS), (x + lean, top_y)],
                  fill=color, width=max(2, int(6 * SS)))
        draw.line([(x - 1 * SS, base_y), (x + lean - 1 * SS, top_y)],
                  fill=rim, width=max(1, int(1.2 * SS)))
        x += step * rng.uniform(0.92, 1.12)
    for ratio, sag in ((0.36, 0.008), (0.66, 0.006)):
        y = base_y - height * ratio
        points = _quad((x0, y), ((x0 + x1) / 2, y + sag * h), (x1, y), steps=10)
        draw.line(points, fill=color, width=max(2, int(4.5 * SS)), joint="curve")
        draw.line([(px, py - 1.2 * SS) for px, py in points],
                  fill=rim, width=max(1, int(1.1 * SS)), joint="curve")
    layer = layer.filter(ImageFilter.GaussianBlur(1.0 * SS))
    return _composite_rgba(img, layer)


def _overhanging_branch(img, seed, color, rim, foliage=1.0):
    """从左侧伸入画面的庭院树枝：只占左上边缘，不压中心。"""
    w, h = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rng = random.Random(seed)

    start = (-0.02 * w, 0.335 * h)
    end = (0.315 * w, 0.345 * h)
    ctrl = (0.13 * w, 0.245 * h)
    main = _quad(start, ctrl, end, steps=20)
    _tapered_line(
        draw, [(x - 1.4 * SS, y - 1.6 * SS) for x, y in main],
        int(11 * SS), int(2.4 * SS), rim,
    )
    _tapered_line(draw, main, int(9 * SS), int(1.8 * SS), color)

    branches = []
    for ratio, dx, dy in (
        (0.30, 0.085, -0.070),
        (0.48, 0.115, 0.045),
        (0.66, 0.140, -0.060),
        (0.83, 0.120, 0.050),
    ):
        start_point = main[int(ratio * (len(main) - 1))]
        end_point = (start_point[0] + dx * w, start_point[1] + dy * h)
        ctrl_point = (start_point[0] + dx * w * 0.30,
                      start_point[1] + dy * h * 0.55)
        branch = _quad(start_point, ctrl_point, end_point, steps=10)
        _tapered_line(
            draw, [(x - 1.1 * SS, y - 1.2 * SS) for x, y in branch],
            int(4.8 * SS), int(1.2 * SS), rim,
        )
        _tapered_line(draw, branch, int(4.0 * SS), int(1.0 * SS), color)
        branches.append(branch)

    mass_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    mass_draw = ImageDraw.Draw(mass_layer)
    masses = []
    for branch in branches:
        anchors = branch[int(len(branch) * 0.45):]
        for anchor in anchors[::2]:
            cx = anchor[0] + rng.uniform(0, 0.018) * w
            cy = anchor[1] + rng.uniform(-0.018, 0.018) * h
            rx = rng.uniform(0.020, 0.032) * w * foliage
            ry = rx * rng.uniform(0.42, 0.62)
            mass_draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry],
                              fill=color + (255,))
            masses.append((cx, cy, rx, ry))
    mass_layer = mass_layer.filter(ImageFilter.GaussianBlur(3.0 * SS))
    layer = Image.alpha_composite(layer, mass_layer)

    leaf_draw = ImageDraw.Draw(layer)
    for cx, cy, rx, ry in masses:
        for _ in range(int(rng.uniform(38, 58) * foliage)):
            angle = rng.uniform(0, math.tau)
            radius = rng.uniform(0, 1) ** 0.55
            px = cx + math.cos(angle) * rx * radius * 1.1
            py = cy + math.sin(angle) * ry * radius
            size = rng.uniform(1.2, 2.4) * SS
            shade = rng.uniform(0.85, 1.55)
            leaf_draw.ellipse(
                [px - size * 1.3, py - size * 0.5,
                 px + size * 1.3, py + size * 0.5],
                fill=tuple(min(255, int(c * shade)) for c in color[:3])
                + (255,),
            )
        for _ in range(int(rng.uniform(3, 6) * foliage)):
            angle = rng.uniform(math.pi * 0.9, math.pi * 1.8)
            px = cx + math.cos(angle) * rx * rng.uniform(0.7, 1.0)
            py = cy + math.sin(angle) * ry * rng.uniform(0.7, 1.0)
            size = rng.uniform(1.0, 2.0) * SS
            leaf_draw.ellipse(
                [px - size, py - size * 0.45, px + size, py + size * 0.45],
                fill=rim,
            )
    layer = layer.filter(ImageFilter.GaussianBlur(0.5 * SS))
    return _composite_rgba(img, layer)


def _birds(img, spots, seed, color=(196, 214, 208), alpha=55, size=9):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rng = random.Random(seed)
    w, h = img.size
    for x_ratio, y_ratio in spots:
        cx, cy = x_ratio * w, y_ratio * h
        wing = size * SS * rng.uniform(0.7, 1.1)
        draw.line(
            [(cx - wing, cy), (cx - wing * 0.35, cy - wing * 0.30),
             (cx, cy - wing * 0.08), (cx + wing * 0.35, cy - wing * 0.34),
             (cx + wing, cy)],
            fill=color + (alpha,), width=max(1, int(1.2 * SS)),
            joint="curve",
        )
    return _composite_rgba(img, layer)


def _wildflowers(img, seed, baseline, count=14, color=(214, 208, 152, 95)):
    """草间的细小野花：只放在两侧，不进入中心。"""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rng = random.Random(seed)
    w, _h = img.size
    for _ in range(count // 2):
        edge = rng.choice([0, 1])
        cx = rng.uniform(0.0, 0.20) * w if edge == 0 else rng.uniform(0.80, 1.0) * w
        cy = baseline + rng.uniform(-46, 6)
        for _flower in range(rng.randint(2, 4)):
            fx = cx + rng.uniform(-12, 12) * SS
            fy = cy + rng.uniform(-10, 10) * SS
            stem = rng.uniform(5, 11) * SS
            draw.line([(fx, fy), (fx + rng.uniform(-1, 1), fy + stem)],
                      fill=color[:3] + (max(10, color[3] // 2),), width=1)
            r = rng.uniform(1.3, 2.2) * SS
            for petal in range(5):
                angle = petal / 5.0 * math.tau
                px = fx + math.cos(angle) * r
                py = fy + math.sin(angle) * r * 0.85
                draw.ellipse([px - r * 0.62, py - r * 0.55,
                              px + r * 0.62, py + r * 0.55], fill=color)
            draw.ellipse([fx - 0.55 * SS, fy - 0.55 * SS,
                          fx + 0.55 * SS, fy + 0.55 * SS],
                         fill=(240, 226, 150, min(255, color[3] + 50)))
    layer = layer.filter(ImageFilter.GaussianBlur(0.4))
    return _composite_rgba(img, layer)


def _stepping_stones(img, seed):
    w, h = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rng = random.Random(seed)
    path = [(0.40, 0.905), (0.47, 0.890), (0.545, 0.900), (0.62, 0.885)]
    for x_ratio, y_ratio in path:
        cx, cy = x_ratio * w, y_ratio * h
        rx = rng.uniform(0.028, 0.045) * w
        ry = rx * rng.uniform(0.22, 0.30)
        draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry],
                     fill=(8, 18, 23, 255))
        draw.arc([cx - rx, cy - ry, cx + rx, cy + ry], 190, 350,
                 fill=(54, 92, 100, 110), width=max(1, int(1.4 * SS)))
    layer = layer.filter(ImageFilter.GaussianBlur(1.2))
    return _composite_rgba(img, layer)


def _wisp_layer(img, seed, count=3):
    """水彩云雾/薄霭：低分辨率绘制后放大，边缘更自然。"""
    low = Image.new("RGBA", (110, 76), (0, 0, 0, 0))
    draw = ImageDraw.Draw(low)
    rng = random.Random(seed)
    for _ in range(count * 8):
        cx, cy = rng.uniform(0, 110), rng.uniform(0, 76)
        rx, ry = rng.uniform(8, 40), rng.uniform(2, 10)
        draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry],
                     fill=(150, 184, 190, rng.randint(10, 24)))
    up = low.resize(img.size, Image.BICUBIC)
    up = up.filter(ImageFilter.GaussianBlur(20 * SS))
    return _composite_rgba(img, up)


# =============================================================================
# 候选 1：深夜庭院
# =============================================================================
def midnight_courtyard(seed=1101):
    w, h = W * SS, H * SS
    img = _gradient(
        [
            (0.00, (7, 12, 22)),
            (0.24, (10, 24, 38)),
            (0.46, (15, 40, 56)),
            (0.62, (22, 60, 76)),
            (0.72, (26, 74, 90)),
            (0.78, (17, 44, 57)),
            (0.85, (8, 20, 27)),
            (1.00, (4, 10, 15)),
        ],
        w, h,
    )
    arr = np.asarray(img).astype(np.float32)
    arr = _add_glow(arr, 0.50 * w, 0.745 * h, 0.60 * w, 0.085 * h,
                    (30, 88, 106), 0.65)
    arr = _add_glow(arr, 0.795 * w, 0.178 * h, 0.19 * w, 0.26 * h,
                    (64, 108, 126), 0.34)
    img = _to_image(arr)

    # 月亮 + 薄云
    img = _soft_moon(
        img, 0.795 * w, 0.178 * h, 33 * SS, seed,
        color=(232, 240, 234), alpha=222,
        halo_color=(86, 132, 148), halo_strength=0.72,
        halo_rx=3.0, halo_ry=2.5, wobble=0.010, mottle=0.05, craters=5,
    )
    img = _cloud_wisp(img, 0.58 * w, 1.03 * w, 0.196 * h, seed + 4,
                      color=(160, 188, 198), alpha=24)
    img = _cloud_wisp(img, 0.30 * w, 0.82 * w, 0.300 * h, seed + 5,
                      color=(96, 132, 146), alpha=12)

    # 星星
    img = _speckles(
        img, seed + 11, 66, (0, 0, w, 0.62 * h), 1.0 * SS,
        (206, 224, 232), 82, blur=0.5,
    )
    img = _speckles(
        img, seed + 12, 10, (0, 0.05 * h, w, 0.5 * h), 0.8 * SS,
        (230, 240, 240), 130, blur=0.0,
    )

    # 远山三层
    layers = [
        (0.735, 0.028, (14, 36, 48), 2.0),
        (0.766, 0.020, (10, 26, 35), 1.7),
        (0.800, 0.016, (6, 17, 23), 1.3),
    ]
    for index, (base, amp, color, soft) in enumerate(layers):
        xs, ys = _ridge_points(seed + 21 + index, base * h, amp * h, w,
                               octaves=4)
        img = _silhouette(img, _ridge_mask(w, h, xs, ys), color, softness=soft)

    # 地面 + 雾 + 石板小径
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    arr = _add_fog(arr, 0.795, 0.030, (34, 72, 84), 0.38, seed + 26)
    arr = _add_fog(arr, 0.835, 0.070, (18, 42, 50), 0.30, seed + 27)
    yy, xx = np.mgrid[0:h, 0:w]
    ground = np.clip((yy - 0.79 * h) / (0.21 * h), 0, 1) ** 1.4
    arr = arr * (1.0 - 0.32 * ground[:, :, None])
    img = _to_image(arr)
    img = _stepping_stones(img, seed + 31)

    # 庭院边界：左侧木栅栏 + 从左上伸入的树枝
    img = _fence(img, -0.02 * w, 0.34 * w, 0.862 * h, seed + 35,
                 color=(4, 11, 15, 255), rim=(44, 82, 88, 120))
    img = _overhanging_branch(
        img, seed + 37, color=(6, 16, 21), rim=(46, 86, 94, 95),
        foliage=0.55,
    )
    img = _stone_basin(img, 0.245 * w, 0.905 * h, 54 * SS,
                       (5, 13, 17), (52, 88, 94, 95))

    # 右侧石灯笼
    lantern = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lantern)
    lx, base_y = 0.855 * w, 0.885 * h
    stone = (6, 14, 18, 255)
    ld.polygon(
        [(lx - 38 * SS, base_y), (lx + 38 * SS, base_y),
         (lx + 28 * SS, base_y - 18 * SS), (lx - 28 * SS, base_y - 18 * SS)],
        fill=stone,
    )
    ld.rectangle([lx - 10 * SS, base_y - 84 * SS, lx + 10 * SS, base_y - 18 * SS],
                 fill=stone)
    ld.polygon(
        [(lx - 28 * SS, base_y - 84 * SS), (lx + 28 * SS, base_y - 84 * SS),
         (lx + 24 * SS, base_y - 124 * SS), (lx - 24 * SS, base_y - 124 * SS)],
        fill=stone,
    )
    ld.polygon(
        [(lx - 44 * SS, base_y - 124 * SS), (lx + 44 * SS, base_y - 124 * SS),
         (lx + 28 * SS, base_y - 142 * SS), (lx - 28 * SS, base_y - 142 * SS)],
        fill=stone,
    )
    ld.polygon(
        [(lx - 36 * SS, base_y - 140 * SS), (lx + 36 * SS, base_y - 140 * SS),
         (lx + 20 * SS, base_y - 154 * SS), (lx - 20 * SS, base_y - 154 * SS)],
        fill=stone,
    )
    ld.rectangle([lx - 3 * SS, base_y - 170 * SS, lx + 3 * SS, base_y - 154 * SS],
                 fill=stone)
    # 檐口一条冷光
    ld.line([(lx - 42 * SS, base_y - 126 * SS), (lx + 42 * SS, base_y - 126 * SS)],
            fill=(32, 60, 66, 110), width=max(1, int(1.2 * SS)))
    img = _composite_rgba(img, lantern)

    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    arr = _add_glow(arr, lx, base_y - 104 * SS, 36 * SS, 28 * SS,
                    (255, 188, 116), 0.30)
    arr = _add_glow(arr, lx, base_y - 104 * SS, 96 * SS, 78 * SS,
                    (208, 132, 74), 0.11)
    img = _to_image(arr)
    window = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    wd = ImageDraw.Draw(window)
    wd.rectangle([lx - 13 * SS, base_y - 118 * SS, lx + 13 * SS, base_y - 90 * SS],
                 fill=(255, 208, 150, 160))
    wd.rectangle([lx - 1.2 * SS, base_y - 118 * SS, lx + 1.2 * SS, base_y - 90 * SS],
                 fill=(70, 40, 24, 120))
    window = window.filter(ImageFilter.GaussianBlur(2.6))
    img = _composite_rgba(img, window)

    # 灯笼在地面的一小片暖光
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    arr = _add_glow(arr, lx, base_y + 4 * SS, 96 * SS, 18 * SS,
                    (206, 138, 82), 0.12)
    img = _to_image(arr)

    # 前景草 + 萤火
    img = _grass_band(
        img, seed + 41, 0.890 * h, (4, 10, 13, 255), (52, 94, 94, 140),
        count=190, tall_edges=True, lean_bias=0.02,
    )
    img = _wildflowers(img, seed + 43, 0.905 * h, count=8,
                       color=(206, 206, 150, 70))
    img = _speckles(
        img, seed + 51, 24, (0.02 * w, 0.64 * h, 0.98 * w, 0.92 * h),
        1.8 * SS, (214, 238, 172), 116, blur=1.3,
    )
    return _finish(img, seed, grain=2.1, vignette=0.37, lift=3.0)


# =============================================================================
# 候选 2：水彩夜色
# =============================================================================
def watercolor_night(seed=2202):
    """水彩夜色：湿画法天空 + 远山 + 月下湖面，靠色块和纸纹而不是细节。"""
    w, h = W * SS, H * SS
    img = _gradient(
        [
            (0.00, (10, 16, 30)),
            (0.20, (14, 28, 40)),
            (0.42, (20, 46, 52)),
            (0.60, (24, 55, 58)),
            (0.695, (16, 39, 43)),
            (0.715, (11, 28, 33)),
            (0.86, (7, 19, 23)),
            (1.00, (5, 13, 17)),
        ],
        w, h,
    )
    arr = np.asarray(img).astype(np.float32)

    # 大色块：靛蓝 / 蓝绿 / 一点橄榄绿，避免整张发灰
    washes = [
        (0.20, 0.20, 0.36, 0.26, (52, 78, 118), 0.22),
        (0.74, 0.28, 0.34, 0.24, (40, 96, 94), 0.22),
        (0.48, 0.52, 0.50, 0.22, (60, 110, 104), 0.14),
        (0.18, 0.78, 0.36, 0.20, (28, 52, 64), 0.20),
        (0.80, 0.80, 0.34, 0.20, (20, 44, 52), 0.22),
        (0.42, 0.96, 0.46, 0.16, (14, 36, 40), 0.24),
    ]
    for cx, cy, rx, ry, color, strength in washes:
        arr = _add_glow(arr, cx * w, cy * h, rx * w, ry * h, color,
                        strength, power=1.5)
    img = _to_image(arr)

    # 湿画法色斑：低分辨率铺色 -> 放大柔化 -> 轻微水痕边缘
    bloom = Image.new("RGBA", (120, 84), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bloom)
    brng = random.Random(seed + 7)
    palette = [(36, 84, 88), (44, 64, 102), (30, 70, 64), (56, 104, 90),
               (24, 52, 60)]
    for _ in range(70):
        cx, cy = brng.uniform(0, 120), brng.uniform(0, 84)
        rx, ry = brng.uniform(7, 30), brng.uniform(5, 22)
        bd.ellipse([cx - rx, cy - ry, cx + rx, cy + ry],
                   fill=palette[brng.randrange(len(palette))]
                   + (brng.randint(12, 34),))
    bloom = bloom.resize((w, h), Image.BICUBIC)
    bloom = bloom.filter(ImageFilter.GaussianBlur(16 * SS))
    bloom = bloom.filter(ImageFilter.UnsharpMask(radius=5 * SS, percent=90,
                                                 threshold=0))
    img = _composite_rgba(img, bloom)

    # 月亮
    img = _soft_moon(
        img, 0.735 * w, 0.205 * h, 50 * SS, seed + 9,
        color=(232, 236, 218), alpha=192,
        halo_color=(126, 158, 150), halo_strength=0.46,
        halo_rx=3.4, halo_ry=3.0, wobble=0.010, mottle=0.05,
        feather=0.07,
    )
    img = _cloud_wisp(img, 0.47 * w, 1.03 * w, 0.228 * h, seed + 12,
                      color=(164, 190, 186), alpha=26)
    img = _cloud_wisp(img, 0.62 * w, 1.04 * w, 0.180 * h, seed + 13,
                      color=(146, 172, 178), alpha=16)
    img = _birds(img, [(0.50, 0.315), (0.545, 0.290), (0.590, 0.318),
                       (0.625, 0.300)], seed + 15,
                 color=(206, 220, 210), alpha=72, size=7)
    img = _wisp_layer(img, seed + 17, count=2)

    # 水彩远山：只填到水平线，给湖面留出空间
    for index, (base, amp, soft, color, alpha) in enumerate((
        (0.640, 0.048, 7 * SS, (28, 56, 62), 150),
        (0.674, 0.030, 4 * SS, (12, 30, 36), 205),
    )):
        xs, ys = _ridge_points(seed + 31 + index, base * h, amp * h, w,
                               octaves=4)
        mask = _ridge_band_mask(w, h, xs, ys, 0.700 * h).filter(
            ImageFilter.GaussianBlur(soft)
        )
        layer = Image.new("RGBA", (w, h), color + (alpha,))
        img = Image.composite(layer, img.convert("RGBA"), mask)

    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    arr = _add_fog(arr, 0.700, 0.018, (140, 172, 162), 0.26, seed + 35)
    arr = _add_fog(arr, 0.748, 0.030, (80, 116, 116), 0.18, seed + 36)
    img = _to_image(arr)
    img = _reeds(img, seed + 38, (5, 13, 16, 255), (48, 74, 76, 80))

    # 月亮在湖面的一条倒影（很淡，带水面断纹）
    yy, xx = np.mgrid[0:h, 0:w]
    column = np.exp(-(((xx - 0.735 * w) / (0.038 * w)) ** 2)) * np.exp(
        -(((yy - 0.835 * h) / (0.145 * h)) ** 2)
    )
    ripple = 0.55 + 0.45 * (
        0.5 + 0.5 * np.sin(yy / (3.2 * SS) + 0.8 * np.sin(xx / (17 * SS)))
    )
    arr = arr + (column * ripple)[:, :, None] * np.array(
        (166, 188, 170), dtype=np.float32
    ) * 0.26
    # 几笔横向水光
    for y_ratio, width, alpha in ((0.738, 0.055, 26), (0.772, 0.085, 30),
                                  (0.812, 0.130, 24), (0.850, 0.100, 18),
                                  (0.900, 0.075, 14)):
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        draw.ellipse(
            [0.735 * w - width * w / 2, y_ratio * h - 1.8 * SS,
             0.735 * w + width * w / 2, y_ratio * h + 1.8 * SS],
            fill=(180, 204, 182, alpha),
        )
        layer = layer.filter(ImageFilter.GaussianBlur(2.4 * SS))
        img = _composite_rgba(img, layer)

    # 左下一点暖色，破掉全冷调
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    arr = _add_glow(arr, 0.24 * w, 0.80 * h, 0.18 * w, 0.09 * h,
                    (214, 176, 116), 0.09)
    img = _to_image(arr)

    img = _paper_texture(img, seed + 39)
    img = _speckles(
        img, seed + 43, 42, (0.03 * w, 0.10 * h, 0.97 * w, 0.92 * h),
        0.9 * SS, (192, 214, 202), 46, blur=0.5,
    )
    img = _speckles(
        img, seed + 44, 12, (0.08 * w, 0.15 * h, 0.95 * w, 0.85 * h),
        0.8 * SS, (216, 184, 126), 26, blur=0.7,
    )
    return _finish(
        img, seed, grain=3.0, vignette=0.42, lift=2.0, contrast=1.02,
        grade=_watercolor_grade,
    )


def _emerald_grade(arr):
    """让草原的绿色更透一点，同时保留暗部层次。"""
    gray = arr.mean(axis=2, keepdims=True)
    arr = gray + (arr - gray) * 1.10
    luminance = arr.mean(axis=2, keepdims=True)
    highlight = np.clip((luminance - 85.0) / 130.0, 0.0, 1.0)
    arr = arr + highlight * np.array((5.0, 4.0, 0.0), dtype=np.float32)
    return arr


def _watercolor_grade(arr):
    """轻微分离冷暖：阴影偏蓝绿、高光保留一点暖，避免水彩发灰。"""
    luminance = arr.mean(axis=2, keepdims=True)
    shadow = np.clip((110.0 - luminance) / 110.0, 0.0, 1.0)
    arr = arr + shadow * np.array((1.0, 6.0, 8.0), dtype=np.float32)
    return arr


def _paper_texture(img, seed):
    """低频纸浆斑驳 + 细颗粒，让水彩不显得像纯渐变。"""
    rng = np.random.default_rng(seed)
    low = rng.normal(0.0, 1.0, (28, 40)).astype(np.float32)
    low = np.asarray(
        Image.fromarray(
            ((low - low.min()) / (low.max() - low.min()) * 255).astype(np.uint8)
        ).resize(img.size, Image.BICUBIC).filter(ImageFilter.GaussianBlur(9)),
        dtype=np.float32,
    )
    low = (low - 128.0) / 128.0
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    arr += low[:, :, None] * 6.0
    arr += rng.normal(0.0, 2.0, (img.size[1], img.size[0], 1)).astype(np.float32)
    return _to_image(arr)


# =============================================================================
# 候选 3：翠绿草原（暮色，保证 UI 对比）
# =============================================================================
def emerald_grassland(seed=3303):
    """翠绿草原：晨光低斜、暖色日光 + 冷绿丘陵，靠层次和雾撑住画面。"""
    w, h = W * SS, H * SS
    img = _gradient(
        [
            (0.00, (6, 15, 12)),
            (0.18, (9, 27, 19)),
            (0.36, (16, 45, 30)),
            (0.50, (26, 64, 40)),
            (0.57, (48, 82, 48)),
            (0.615, (66, 98, 54)),
            (0.650, (46, 76, 46)),
            (0.705, (28, 56, 36)),
            (0.800, (16, 40, 26)),
            (0.900, (9, 25, 16)),
            (1.00, (6, 17, 11)),
        ],
        w, h,
    )
    arr = np.asarray(img).astype(np.float32)
    arr = _add_glow(arr, 0.28 * w, 0.575 * h, 0.26 * w, 0.12 * h,
                    (255, 196, 140), 0.22)
    arr = _add_glow(arr, 0.50 * w, 0.620 * h, 0.55 * w, 0.10 * h,
                    (150, 168, 100), 0.13)
    arr = _add_glow(arr, 0.86 * w, 0.74 * h, 0.20 * w, 0.18 * h,
                    (58, 116, 82), 0.18)
    img = _to_image(arr)

    # 低斜的太阳，上半轮露在山脊上
    img = _soft_moon(
        img, 0.285 * w, 0.565 * h, 26 * SS, seed + 5,
        color=(255, 246, 224), alpha=178,
        halo_color=(255, 208, 170), halo_strength=0.24,
        halo_rx=5.0, halo_ry=4.0, wobble=0.010, mottle=0.05,
        feather=0.42,
    )
    img = _god_rays(img, 0.285 * w, 0.565 * h, seed + 6, alpha=6)
    img = _speckles(
        img, seed + 7, 30, (0, 0, w, 0.44 * h), 0.8 * SS,
        (226, 236, 210), 52, blur=0.4,
    )

    # 远山与丘陵：越近越暗，脊线带一点暖色受光
    layers = [
        (0.600, 0.040, (34, 68, 46), 2.2, 4, (170, 168, 108, 26)),
        (0.648, 0.032, (25, 55, 37), 1.8, 4, (188, 178, 112, 30)),
        (0.710, 0.026, (16, 41, 27), 1.4, 4, (166, 160, 98, 24)),
        (0.790, 0.020, (9, 27, 18), 1.0, 5, None),
    ]
    for index, (base, amp, color, soft, octaves, rim) in enumerate(layers):
        xs, ys = _ridge_points(seed + 31 + index, base * h, amp * h, w,
                               octaves=octaves)
        img = _silhouette(img, _ridge_mask(w, h, xs, ys), color,
                          softness=soft)
        if rim is not None:
            img = _ridge_rim(img, xs, ys, rim[:3], alpha=rim[3], width=1.5,
                             softness=1.4)

    img = _forest_band(img, 0.635, 0.026, (12, 31, 21), seed + 36,
                       alpha=180, softness=2.0)
    img = _forest_band(img, 0.684, 0.020, (8, 23, 15), seed + 37,
                       alpha=160, softness=1.8)

    # 山谷晨雾
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    for y_ratio, thickness, color, strength in (
        (0.628, 0.020, (206, 198, 148), 0.20),
        (0.700, 0.024, (176, 184, 134), 0.18),
        (0.772, 0.028, (116, 158, 116), 0.17),
        (0.870, 0.050, (66, 110, 78), 0.15),
    ):
        arr = _add_fog(arr, y_ratio, thickness, color, strength, seed + 41)
    arr = _add_glow(arr, 0.18 * w, 0.885 * h, 0.22 * w, 0.10 * h,
                    (178, 190, 104), 0.10)
    img = _to_image(arr)

    # 左侧前景松树留空：等选定方向后再作为可选项单独调形。

    # 远景小树（给丘陵一点尺度）
    img = _small_tree(img, 0.105 * w, 0.722 * h, 0.085 * h, seed + 44,
                      (9, 26, 17), alpha=200, blur=1.4)
    img = _small_tree(img, 0.165 * w, 0.730 * h, 0.060 * h, seed + 45,
                      (8, 24, 16), alpha=190, blur=1.2)
    img = _small_tree(img, 0.905 * w, 0.738 * h, 0.070 * h, seed + 46,
                      (8, 24, 16), alpha=185, blur=1.6)

    # 前景缓坡，给草地一个落脚面
    xs, ys = _ridge_points(seed + 34, 0.918 * h, 0.014 * h, w, octaves=3)
    img = _silhouette(img, _ridge_mask(w, h, xs, ys), (5, 16, 10),
                      softness=2.2)
    img = _ridge_rim(img, xs, ys, (92, 116, 62), alpha=45, width=1.4,
                     softness=1.6)

    img = _birds(img, [(0.56, 0.215), (0.605, 0.190), (0.645, 0.222),
                       (0.535, 0.255)], seed + 49,
                 color=(214, 224, 190), alpha=78, size=8)

    # 远近两层草 + 野花，形成纵深
    img = _grass_band(
        img, seed + 51, 0.880 * h, (7, 21, 13, 255), (96, 132, 78, 100),
        count=200, tall_edges=False, lean_bias=0.02, rim_chance=0.16,
    )
    img = _grass_band(
        img, seed + 53, 0.945 * h, (4, 13, 8, 255), (176, 190, 108, 140),
        count=380, tall_edges=True, lean_bias=0.035, rim_chance=0.32,
        seed_heads=0.18,
    )
    img = _wildflowers(img, seed + 55, 0.945 * h, count=32,
                       color=(240, 228, 164, 120))

    # 蒲公英种子（被风带向右侧，少量）
    seeds = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(seeds)
    srng = random.Random(seed + 57)
    for _ in range(9):
        cx = srng.uniform(0.30, 0.92) * w
        cy = srng.uniform(0.16, 0.60) * h
        r = srng.uniform(3.8, 6.4) * SS
        alpha = srng.randint(44, 70)
        sd.ellipse([cx - 1.4 * SS, cy - 1.4 * SS, cx + 1.4 * SS, cy + 1.4 * SS],
                   fill=(238, 240, 208, alpha))
        for index in range(7):
            a = index / 7.0 * math.tau + srng.uniform(-0.18, 0.18)
            sd.line(
                [(cx, cy), (cx + math.cos(a) * r, cy + math.sin(a) * r)],
                fill=(228, 234, 200, int(alpha * 0.62)), width=1,
            )
        sd.line([(cx, cy), (cx + r * 1.5, cy + r * 0.35)],
                fill=(228, 234, 200, int(alpha * 0.45)), width=1)
    seeds = seeds.filter(ImageFilter.GaussianBlur(0.5))
    img = _composite_rgba(img, seeds)

    img = _speckles(
        img, seed + 61, 30, (0.02 * w, 0.52 * h, 0.98 * w, 0.94 * h),
        1.4 * SS, (234, 236, 166), 82, blur=1.0,
    )
    return _finish(img, seed, grain=2.3, vignette=0.38, lift=0.5,
                   contrast=1.02, grade=_emerald_grade)


# =============================================================================
# 输出
# =============================================================================
BUILDERS = {
    "courtyard": ("bg_candidate_midnight_courtyard.png", midnight_courtyard),
    "watercolor": ("bg_candidate_watercolor_night.png", watercolor_night),
    "grassland": ("bg_candidate_emerald_grassland.png", emerald_grassland),
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="烘焙背景候选（不改游戏）")
    parser.add_argument(
        "--only", choices=sorted(BUILDERS), action="append",
        help="只生成指定候选，可重复；默认全部",
    )
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    names = args.only or sorted(BUILDERS)
    for key in names:
        filename, builder = BUILDERS[key]
        print("baking %s ..." % key)
        image = builder()
        path = out_dir / filename
        image.save(path, optimize=True)
        print("  -> %s  (%d×%d, %.1f KB)" % (
            path, image.width, image.height, path.stat().st_size / 1024.0))


if __name__ == "__main__":
    main()
