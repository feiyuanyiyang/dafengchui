# -*- coding: utf-8 -*-
"""障碍物材质绘制（离线烘焙用，运行时只读 PNG）。

障碍物不再用“不同颜色的球”，改用被风卷起的杂物，靠**剪影和材质**区分档位：

    small  r=7   树叶 / 纸片 / 羽毛      轻、快，被风卷着走
    medium r=12  松果 / 碎石 / 纸团      有重量，需要让开
    large  r=18  树枝 / 木板 / 石块      危险，看轮廓就该躲

颜色只作为辅助线索：每档保留一层很淡的档位色外发光（金 / 橙 / 红），
保证深色背景下的可读性，但不再承担“分辨是什么”的主要职责。

调用方式（供 tools/art/bake_sprites.py 与风格预览脚本共用）：

    render_obstacle(tier, variant, radius, size, scale=4)

`size` 是设计尺寸（正方形边长），`radius` 是设计半径，返回 size*scale 见方的 RGBA。
所有坐标都按半径比例绘制，因此同一份代码可以烘任意尺寸；碰撞判定仍是原来的圆。
"""

from __future__ import annotations

import math
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUTLINE = (10, 17, 24)

# 贴图设计尺寸：半径 + 两侧留白（与运行时 assets.py 的 obstacle_sprite_size 一致）
PAD_MIN = 5.0
PAD_RATIO = 0.4


def obstacle_sprite_size(radius):
    """障碍物贴图的设计边长（正方形），随半径自动适配。"""
    pad = max(PAD_MIN, radius * PAD_RATIO)
    return int(round((radius + pad) * 2))

# 档位色：只用于很淡的外发光，保留一点“轻/中/重”的色彩暗示
TIER_GLOW = {
    "small": ((255, 209, 102), 58),
    "medium": ((247, 140, 107), 56),
    "large": ((239, 71, 111), 50),
}


# ---------------------------------------------------------------- 基础工具
def _rot(points, cx, cy, angle):
    if not angle:
        return list(points)
    ca, sa = math.cos(angle), math.sin(angle)
    out = []
    for x, y in points:
        dx, dy = x - cx, y - cy
        out.append((cx + dx * ca - dy * sa, cy + dx * sa + dy * ca))
    return out


def _ellipse_points(cx, cy, rx, ry, angle=0.0, steps=28):
    """以 (cx, cy) 为中心、半轴 rx/ry 的椭圆采样点，并按 angle 旋转。

    ImageDraw.ellipse 只能画轴对齐椭圆；需要跟主体一起转的贴图细节
    （松果鳞片、树枝端面）必须走多边形采样，否则整张贴图旋转时椭圆
    朝向会卡死不变，看起来就是“花纹没跟着转”。
    """
    pts = []
    for i in range(steps):
        t = math.tau * i / steps
        pts.append((cx + math.cos(t) * rx, cy + math.sin(t) * ry))
    return _rot(pts, cx, cy, angle)


def _colorize(mask, rgba):
    layer = Image.new("RGBA", mask.size, rgba[:3] + (0,))
    layer.putalpha(mask.point(lambda v: int(v * rgba[3] / 255.0)))
    return layer


def _polygon_mask(size, points):
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    return mask


class _BlendDraw:
    """先画到透明图层、再 alpha_composite 回画布的 ImageDraw 包装。

    Pillow 的 ImageDraw 在 RGBA 画布上是“覆盖写像素”：`fill=(r, g, b, a)`
    里的 a 会原样写进目标 alpha，并不会和已有像素做 alpha 混合。障碍物主体
    是实心的，如果把半透明细节（叶脉 / 折角 / 裂缝 / 石块切面）直接画到主体
    上，就会在贴图内部留下半透明洞，运行时透出下方的游戏背景，看起来像
    “中空”。所有细节都走这个包装，才能保持主体不透明、细节按预期叠色。
    """

    def __init__(self, image):
        self.image = image

    def _stroke(self, name, *args, **kwargs):
        layer = Image.new("RGBA", self.image.size, (0, 0, 0, 0))
        getattr(ImageDraw.Draw(layer), name)(*args, **kwargs)
        self.image.alpha_composite(layer)

    def line(self, *args, **kwargs):
        self._stroke("line", *args, **kwargs)

    def polygon(self, *args, **kwargs):
        self._stroke("polygon", *args, **kwargs)

    def ellipse(self, *args, **kwargs):
        self._stroke("ellipse", *args, **kwargs)


def _shade(img, mask, color, light, rim=None, outline=OUTLINE):
    """在掩膜内画：底色 -> 左上亮面 -> 外描边 -> 可选边缘光。"""
    w, h = img.size
    base = Image.new("RGBA", (w, h), color + (255,))
    base.putalpha(mask)
    img.alpha_composite(base)

    # 亮面：掩膜向左上平移后取交集，模糊边缘
    lit = Image.new("RGBA", (w, h), light + (120,))
    shift = max(1, int(round(w * 0.03)))
    lit = lit.transform((w, h), Image.AFFINE, (1, 0, -shift * 0.5, 0, 1, -shift * 0.5))
    lit.putalpha(Image.composite(lit.getchannel("A"), Image.new("L", (w, h), 0), mask))
    lit = lit.filter(ImageFilter.GaussianBlur(max(1, w * 0.02)))
    lit.putalpha(Image.composite(lit.getchannel("A"), Image.new("L", (w, h), 0), mask))
    img.alpha_composite(lit)

    # 描边：掩膜膨胀 - 掩膜
    grown = mask.filter(ImageFilter.MaxFilter(max(3, int(w * 0.018) * 2 + 1)))
    edge = ImageChops.subtract(grown, mask)
    img.alpha_composite(_colorize(edge, outline + (255,)))

    if rim is not None:
        glow = _colorize(edge, rim + (150,))
        glow = glow.filter(ImageFilter.GaussianBlur(max(1, w * 0.012)))
        img.alpha_composite(glow)


def _glow(img, color, strength, spread):
    """柔光外轮廓：深色背景上的可读性来源，不改变剪影。"""
    halo = _colorize(img.getchannel("A"), color + (strength,))
    halo = halo.filter(ImageFilter.GaussianBlur(max(2.0, img.size[0] * spread)))
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.alpha_composite(halo)
    out.alpha_composite(img)
    return out


# ---------------------------------------------------------------- 小档
def _leaf(s, r, angle=0.0):
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    if r <= 0:
        return img
    d = _BlendDraw(img)
    c = s / 2.0
    length, width = r * 2.0, r * 1.05
    pts = []
    steps = 26
    for i in range(steps + 1):
        t = -1 + 2 * i / steps
        pts.append((c + t * length / 2, c - math.sin(math.pi * (t + 1) / 2) * width / 2 * (1 - 0.12 * abs(t))))
    for i in range(steps + 1):
        t = 1 - 2 * i / steps
        pts.append((c + t * length / 2, c + math.sin(math.pi * (t + 1) / 2) * width / 2 * (1 - 0.12 * abs(t))))
    pts = _rot(pts, c, c, angle)
    mask = _polygon_mask(s, pts)
    _shade(img, mask, (214, 178, 74), (245, 222, 138), rim=(255, 236, 170))
    # 叶脉 + 叶柄
    vein = (108, 84, 38, 190)
    p0 = _rot([(c - length * 0.42, c)], c, c, angle)[0]
    p1 = _rot([(c + length * 0.46, c)], c, c, angle)[0]
    d.line([p0, p1], fill=vein, width=max(1, int(r * 0.09)))
    for k in (-0.5, 0.0, 0.5):
        a = _rot([(c + k * length * 0.3, c)], c, c, angle)[0]
        b = _rot([(c + k * length * 0.3 + r * 0.34, c - r * 0.36 * (1 if k <= 0 else -1))], c, c, angle)[0]
        d.line([a, b], fill=(120, 94, 44, 150), width=max(1, int(r * 0.055)))
    stem_a = _rot([(c - length * 0.46, c)], c, c, angle)[0]
    stem_b = _rot([(c - length * 0.66, c - r * 0.06)], c, c, angle)[0]
    d.line([stem_a, stem_b], fill=(120, 88, 42, 255), width=max(2, int(r * 0.13)))
    return img


def _paper(s, r, angle=0.0):
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    if r <= 0:
        return img
    d = _BlendDraw(img)
    c = s / 2.0
    rnd = random.Random(31)
    # 撕裂纸片：以矩形为骨架，每条边插入带抖动的小段，保证有直边和尖角，
    # 不要用“圆上一圈半径抖动”的写法，否则 24px 下会变成白色小球。
    skeleton = [(-0.92, -0.72), (0.9, -0.76), (0.86, 0.7), (-0.88, 0.76)]
    pts = []
    for i in range(len(skeleton)):
        x0, y0 = skeleton[i]
        x1, y1 = skeleton[(i + 1) % len(skeleton)]
        for k in range(3):
            t = k / 3.0
            jx = rnd.uniform(-0.07, 0.07) * r
            jy = rnd.uniform(-0.07, 0.07) * r
            pts.append((c + (x0 + (x1 - x0) * t) * r + jx, c + (y0 + (y1 - y0) * t) * r + jy))
    pts = _rot(pts, c, c, angle)
    mask = _polygon_mask(s, pts)
    _shade(img, mask, (230, 228, 216), (252, 252, 246), rim=(255, 255, 255))
    # 折角：右上角翻下去的一块，深一档
    fold = _rot([(c + r * 0.56, c - r * 0.74), (c + r * 0.86, c - r * 0.44), (c + r * 0.5, c - r * 0.4)], c, c, angle)
    d.polygon(fold, fill=(190, 188, 176, 235), outline=(150, 152, 150, 120))
    # 折痕 + 蓝色横线（作业纸的感觉）
    d.line(
        _rot([(c - r * 0.72, c + r * 0.18), (c + r * 0.68, c - r * 0.22)], c, c, angle),
        fill=(150, 152, 150, 190), width=max(1, int(r * 0.05)),
    )
    for k in range(3):
        y = c + r * (0.02 + k * 0.18)
        d.line(
            _rot([(c - r * 0.58, y + r * 0.08), (c + r * 0.52, y - r * 0.04)], c, c, angle),
            fill=(120, 140, 156, 200), width=max(1, int(r * 0.045)),
        )
    return img


def _feather(s, r, angle=0.0):
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    if r <= 0:
        return img
    d = _BlendDraw(img)
    c = s / 2.0
    pts = []
    steps = 20
    for i in range(steps + 1):
        t = -1 + 2 * i / steps
        pts.append((c + t * r, c - (1 - t * t) * r * 0.68 - t * r * 0.05))
    for i in range(steps + 1):
        t = 1 - 2 * i / steps
        pts.append((c + t * r, c + (1 - t * t) * r * 0.5 - t * r * 0.05))
    pts = _rot(pts, c, c, angle)
    mask = _polygon_mask(s, pts)
    _shade(img, mask, (236, 232, 220), (255, 252, 240), rim=(255, 238, 196))
    d.line(
        _rot([(c - r * 0.98, c + r * 0.14), (c + r * 0.98, c - r * 0.2)], c, c, angle),
        fill=(146, 152, 160, 235), width=max(1, int(r * 0.1)),
    )
    for k in range(5):
        t = -0.7 + k * 0.3
        a = _rot([(c + t * r, c - t * r * 0.05)], c, c, angle)[0]
        b = _rot([(c + t * r + r * 0.24, c - r * 0.55 * (1 - abs(t)))], c, c, angle)[0]
        d.line([a, b], fill=(160, 168, 178, 175), width=max(1, int(r * 0.055)))
    return img


# ---------------------------------------------------------------- 中档
def _pinecone(s, r, angle=0.0):
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    if r <= 0:
        return img
    d = _BlendDraw(img)
    c = s / 2.0
    pts = []
    steps = 30
    for i in range(steps + 1):
        t = -1 + 2 * i / steps
        pts.append((c + math.sin(math.pi * (t + 1) / 2) * r * 0.78, c + t * r * 0.95))
    for i in range(steps + 1):
        t = 1 - 2 * i / steps
        pts.append((c - math.sin(math.pi * (t + 1) / 2) * r * 0.78, c + t * r * 0.95))
    pts = _rot(pts, c, c, angle)
    mask = _polygon_mask(s, pts)
    _shade(img, mask, (150, 96, 58), (198, 140, 88), rim=(255, 196, 120))
    # 鳞片：三行交错的小圆片，画在独立图层后按剪影裁掉，避免线条支棱出轮廓。
    # 鳞片的中心位置和椭圆朝向都必须跟主体一起绕画布中心旋转：只转剪影 mask
    # 而把鳞片画在原地，转动时花纹就会“钉”在画面上，显得很怪。
    scales = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    sd = _BlendDraw(scales)
    for row, yy in enumerate((-0.52, -0.18, 0.18)):
        cols = 3 + row
        for col in range(cols):
            x = (col - (cols - 1) / 2) * r * 0.48
            y = yy * r
            rx, ry = r * 0.26, r * 0.19
            sx, sy = _rot([(c + x, c + y)], c, c, angle)[0]
            sd.polygon(
                _ellipse_points(sx, sy, rx, ry, angle),
                fill=(176, 116, 72, 255), outline=(96, 58, 32, 235), width=max(1, int(r * 0.055)),
            )
    scales.putalpha(Image.composite(scales.getchannel("A"), Image.new("L", (s, s), 0), mask))
    img.alpha_composite(scales)
    d.line(
        _rot([(c, c - r * 0.95), (c, c - r * 1.18)], c, c, angle),
        fill=(96, 66, 40, 255), width=max(2, int(r * 0.16)),
    )
    return img


def _pebble(s, r, angle=0.0):
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    if r <= 0:
        return img
    d = _BlendDraw(img)
    c = s / 2.0
    rnd = random.Random(77)
    pts = []
    n = 8
    for i in range(n):
        a = math.tau * i / n
        rr = r * rnd.uniform(0.82, 1.0)
        pts.append((c + math.cos(a) * rr, c + math.sin(a) * rr * 0.88))
    pts = _rot(pts, c, c, angle)
    mask = _polygon_mask(s, pts)
    _shade(img, mask, (120, 134, 150), (182, 198, 214), rim=(190, 224, 255))
    d.line(
        _rot([(c - r * 0.35, c - r * 0.1), (c + r * 0.1, c + r * 0.24), (c + r * 0.4, c + r * 0.06)], c, c, angle),
        fill=(68, 80, 96, 220), width=max(2, int(r * 0.08)),
    )
    d.line(
        _rot([(c + r * 0.1, c + r * 0.24), (c + r * 0.08, c + r * 0.62)], c, c, angle),
        fill=(68, 80, 96, 200), width=max(1, int(r * 0.06)),
    )
    return img


def _paperball(s, r, angle=0.0):
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    if r <= 0:
        return img
    d = _BlendDraw(img)
    c = s / 2.0
    rnd = random.Random(5)
    # 多边形 + 明显的尖角，和“球”拉开距离；再把几个面填成不同明度表现揉皱
    pts = []
    n = 11
    for i in range(n):
        a = math.tau * i / n + rnd.uniform(-0.13, 0.13)
        rr = r * rnd.uniform(0.74, 1.0)
        pts.append((c + math.cos(a) * rr, c + math.sin(a) * rr))
    pts = _rot(pts, c, c, angle)
    mask = _polygon_mask(s, pts)
    _shade(img, mask, (222, 220, 208), (250, 250, 244), rim=(255, 255, 255))
    facets = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    fd = _BlendDraw(facets)
    for _ in range(4):
        a0 = rnd.uniform(0, math.tau)
        a1 = a0 + rnd.uniform(0.7, 1.6)
        r0 = rnd.uniform(0.15, 0.45) * r
        r1 = rnd.uniform(0.6, 0.95) * r
        tri = [
            (c + math.cos(a0) * r0, c + math.sin(a0) * r0),
            (c + math.cos(a1) * r1, c + math.sin(a1) * r1),
            (c + math.cos(a1 + 0.5) * r * 0.55, c + math.sin(a1 + 0.5) * r * 0.55),
        ]
        fd.polygon(_rot(tri, c, c, angle), fill=(178, 176, 164, 120 if _ % 2 else 70))
    facets.putalpha(Image.composite(facets.getchannel("A"), Image.new("L", (s, s), 0), mask))
    img.alpha_composite(facets)
    for _ in range(5):
        x0, y0 = c + rnd.uniform(-r * 0.7, r * 0.7), c + rnd.uniform(-r * 0.7, r * 0.7)
        x1, y1 = x0 + rnd.uniform(-r * 0.8, r * 0.8), y0 + rnd.uniform(-r * 0.8, r * 0.8)
        d.line(_rot([(x0, y0), (x1, y1)], c, c, angle), fill=(142, 142, 134, 200), width=max(1, int(r * 0.05)))
    return img


# ---------------------------------------------------------------- 大档
def _branch(s, r, angle=0.0):
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    if r <= 0:
        return img
    d = _BlendDraw(img)
    c = s / 2.0
    main = []
    steps = 10
    for i in range(steps + 1):
        t = i / steps
        x = c - r * 0.95 + t * r * 1.9
        y = c + math.sin(t * math.pi * 1.2) * r * 0.1
        thick = r * (0.34 - 0.16 * t)
        main.append((x, y - thick))
    for i in range(steps, -1, -1):
        t = i / steps
        x = c - r * 0.95 + t * r * 1.9
        y = c + math.sin(t * math.pi * 1.2) * r * 0.1
        thick = r * (0.34 - 0.16 * t)
        main.append((x, y + thick))
    main = _rot(main, c, c, angle)
    mask = _polygon_mask(s, main)
    _shade(img, mask, (128, 84, 52), (182, 128, 82), rim=(255, 190, 120))
    base = _rot([(c + r * 0.32, c - r * 0.04)], c, c, angle)[0]
    tip = _rot([(c + r * 0.5, c - r * 0.56)], c, c, angle)[0]
    d.line([base, tip], fill=(104, 68, 42, 255), width=max(2, int(r * 0.13)))
    end = _rot([(c - r * 0.93, c)], c, c, angle)[0]
    er = r * 0.24
    # 端面也是贴图内容的一部分：椭圆要随主体一起转，不能始终竖着
    d.polygon(
        _ellipse_points(end[0], end[1], er * 0.4, er, angle),
        fill=(176, 126, 78, 255), outline=(92, 58, 34, 255), width=max(2, int(r * 0.05)),
    )
    return img


def _plank(s, r, angle=0.0):
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    if r <= 0:
        return img
    d = _BlendDraw(img)
    c = s / 2.0
    pts = [
        (c - r * 0.95, c - r * 0.42), (c + r * 0.62, c - r * 0.5),
        (c + r * 0.98, c - r * 0.18), (c + r * 0.9, c + r * 0.4),
        (c - r * 0.7, c + r * 0.5), (c - r * 0.98, c + r * 0.12),
    ]
    pts = _rot(pts, c, c, angle)
    mask = _polygon_mask(s, pts)
    _shade(img, mask, (156, 112, 70), (206, 158, 108), rim=(255, 198, 128))
    for k in (-0.55, 0.0, 0.55):
        d.line(
            _rot([(c + k * r * 0.9, c - r * 0.42), (c + k * r * 0.75, c + r * 0.48)], c, c, angle),
            fill=(112, 76, 46, 180), width=max(1, int(r * 0.055)),
        )
    nail = _rot([(c + r * 0.55, c - r * 0.2)], c, c, angle)[0]
    nr = r * 0.07
    d.ellipse(
        [nail[0] - nr, nail[1] - nr, nail[0] + nr, nail[1] + nr],
        fill=(150, 158, 166, 255), outline=(60, 66, 72, 255),
    )
    return img


def _boulder(s, r, angle=0.0):
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    if r <= 0:
        return img
    d = _BlendDraw(img)
    c = s / 2.0
    rnd = random.Random(99)
    pts = []
    n = 9
    for i in range(n):
        a = math.tau * i / n
        rr = r * rnd.uniform(0.86, 1.04)
        pts.append((c + math.cos(a) * rr, c + math.sin(a) * rr * 0.94))
    pts = _rot(pts, c, c, angle)
    mask = _polygon_mask(s, pts)
    _shade(img, mask, (104, 118, 136), (164, 182, 202), rim=(180, 214, 255))
    facet = _rot(
        [(c - r * 0.55, c - r * 0.05), (c - r * 0.1, c - r * 0.62), (c + r * 0.3, c - r * 0.24), (c - r * 0.02, c + r * 0.1)],
        c, c, angle,
    )
    d.polygon(facet, fill=(132, 148, 168, 160))
    d.line(
        _rot([(c + r * 0.2, c - r * 0.7), (c + r * 0.05, c - r * 0.1), (c + r * 0.42, c + r * 0.4)], c, c, angle),
        fill=(58, 70, 88, 230), width=max(2, int(r * 0.06)),
    )
    return img


# ---------------------------------------------------------------- 对外入口
MATERIALS = {
    "small": (_leaf, _paper, _feather),
    "medium": (_pinecone, _pebble, _paperball),
    "large": (_branch, _plank, _boulder),
}
# 每个变体给一个固定倾角，三个变体的朝向不重复（纯视觉差异）
VARIANT_ANGLES = {
    "small": (-0.32, 0.18, -0.14),
    "medium": (0.05, 0.0, 0.0),
    "large": (0.16, -0.1, 0.0),
}

# 小档是扁平物件，用“旋转 + 压扁”模拟迎风翻面（帧参数：额外旋转, 纵向压扁系数）。
# 帧数必须与 assets.py 的 OBSTACLE_SPRITE_FRAMES 一致（烘焙脚本会断言）。
OBSTACLE_FRAMES = {
    "small": (
        (0.00, 1.00),
        (-0.20, 0.78),
        (0.05, 0.58),
        (0.18, 0.82),
    ),
}

# 中/大档不用翻面帧，改为绕贴图中心旋转：预烘 N 个角度（N 必须与
# assets.OBSTACLE_SPRITE_FRAMES 一致），运行时按时间推进角度。
OBSTACLE_SPIN_FRAMES = {
    "medium": 144,
    "large": 180,
}


def _squash_vertical(img, factor):
    """以画布中心为基准纵向压扁，模拟扁平物件翻到侧面。"""
    if factor >= 0.999:
        return img
    w, h = img.size
    new_h = max(1, int(round(h * factor)))
    squashed = img.resize((w, new_h), Image.LANCZOS)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.alpha_composite(squashed, (0, (h - new_h) // 2))
    return out


def render_obstacle(tier, variant, radius, size, scale=4, frame=0):
    """按档位/变体/帧绘制一张障碍物贴图，返回 size*scale 见方的 RGBA。

    tier    : "small" / "medium" / "large"
    variant : 同档位内的 0/1/2，对应不同物件
    radius  : 设计半径（游戏里的碰撞半径）
    size    : 设计贴图边长（正方形画布），= obstacle_sprite_size(radius)
    frame   : 帧序号（循环取模）；small 是翻面姿态，medium/large 是绕中心旋转角度
    """
    spin_count = OBSTACLE_SPIN_FRAMES.get(tier)
    if spin_count:
        # 角度帧：绕贴图中心均匀取 N 个朝向（保留变体固定倾角作为基准）
        frame_angle = math.tau * (frame % spin_count) / spin_count
        frame_squash = 1.0
    else:
        frames = OBSTACLE_FRAMES.get(tier, ((0.0, 1.0),))
        frame_angle, frame_squash = frames[frame % len(frames)]
    draw = MATERIALS[tier][variant % 3]
    angle = VARIANT_ANGLES[tier][variant % 3] + frame_angle
    s = max(1, int(round(size * scale)))
    r = radius * scale
    color, strength = TIER_GLOW[tier]
    img = draw(s, r, angle)
    img = _squash_vertical(img, frame_squash)
    return _glow(img, color, strength, 0.16)
