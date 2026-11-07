# -*- coding: utf-8 -*-
"""蜡笔质感 UI 烘焙工具（评审 + 资源生成共用）。

把文字 / 符号渲染成带颗粒与毛边的 RGBA PNG，供 Canvas 直接贴图。
Canvas 图片的透明度只跟画布背景色混合、不跟下方 item 混合，所以这里
一律输出“最终像素”，游戏里不做任何叠加运算。

两个参数分开控制：
    strength  颗粒与整体蜡笔感（用户定稿 0.8）
    edge      边缘被“啃”的程度；小字号必须收敛，否则细笔画会碎

描边永远取自未侵蚀的干净字形，只让填充带颗粒——这样小字仍然读得出来。

    from crayon_kit import text_png, heart_png, compass_base_png, compass_arrow_png
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[2]
FONT_DIR = ROOT / "tools" / "art" / "fonts"
FONT_CJK = str(FONT_DIR / "ZCOOLKuaiLe-Regular.ttf")     # 站酷快乐体 · OFL
FONT_LATIN = str(FONT_DIR / "PatrickHand-Regular.ttf")   # 手写数字 · OFL
FONT_SYS = "C:/Windows/Fonts/msyh.ttc"

SS = 4      # 超采样倍数（bake 2x 时会调高）
OUT = 1.0   # 输出倍率：2.0 = 按两倍分辨率出图，游戏里再缩小

INK = (34, 54, 45)
WHITE = (255, 255, 255)
RED = (222, 70, 56)
TICK = (170, 181, 172)
TICK_LIGHT = (198, 206, 199)
DISC = (255, 253, 248)

DEFAULT_STRENGTH = 0.8  # 用户定稿：80% 蜡笔


# =============================================================================
# 质感管线
# =============================================================================
def ttf(path, px):
    return ImageFont.truetype(path, max(8, int(px * SS)))


def auto_edge(px):
    """字号越小，边缘越收敛：26px 以下最多只啃 55%。"""
    return max(0.55, min(1.0, px / 26.0))


def crayonize(mask, strength=DEFAULT_STRENGTH, edge=1.0):
    """颗粒 + 毛边。edge=0 时只剩颗粒，字形轮廓保持干净。"""
    if strength <= 0:
        return mask
    m = mask
    if edge > 0:
        blur = 0.25 + 1.30 * edge * strength
        m = m.filter(ImageFilter.GaussianBlur(blur))
        cut = 92 + int(38 * (1.0 - edge))  # 阈值越高笔画越细碎
        m = m.point(lambda v: 255 if v > cut else 0)
    noise = Image.effect_noise(m.size, 115).convert("L")
    floor = int(255 - 120 * strength)
    mod = noise.point(lambda v: floor + int(v * (255 - floor) / 255))
    m = ImageChops.multiply(m, mod)
    return m.point(lambda v: min(255, int(v * (1.0 + 0.6 * strength))))


def grain(mask, amount=0.35):
    """只加纸纹、不动边缘。用于白圆盘这类要求轮廓干净的面。"""
    if amount <= 0:
        return mask
    noise = Image.effect_noise(mask.size, 60).convert("L")
    floor = int(255 - 80 * amount)
    mod = noise.point(lambda v: floor + int(v * (255 - floor) / 255))
    m = ImageChops.multiply(mask, mod)
    return m.point(lambda v: min(255, int(v * 1.22)))


def _finish(clean, fill_mask, fill, outline=None, outline_px=2.0, crop=True):
    """clean 决定轮廓（含描边），fill_mask 决定颗粒。"""
    if outline is not None and outline_px > 0:
        thick = int(outline_px * 2 * SS + 1) | 1  # MaxFilter 只吃奇数
        ring = clean.filter(ImageFilter.MaxFilter(thick))
    else:
        ring = None
    box = (ring or fill_mask).getbbox()
    if not box:
        return None
    if not crop:
        box = (0, 0, clean.width, clean.height)
    layers = []
    if ring is not None:
        layers.append((ring.crop(box), outline))
    layers.append((fill_mask.crop(box), fill))
    w = max(1, int((box[2] - box[0]) * OUT // SS))
    h = max(1, int((box[3] - box[1]) * OUT // SS))
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for m, color in layers:
        alpha = m.resize((w, h), Image.LANCZOS)
        layer = Image.new("RGBA", (w, h), tuple(color) + (255,))
        layer.putalpha(alpha)
        out = Image.alpha_composite(out, layer)
    return out


def bold_text_png(parts, fill=WHITE, outline=(18, 18, 18), outline_px=3.0,
                  bold_px=1.8, seed=0, strength=DEFAULT_STRENGTH, gap=2.0,
                  jitter=1.0, rot=1.2, edge=None):
    """站酷快乐体没有粗体字重，用 MaxFilter 膨胀字形本身来“加粗”。"""
    mask = _text_mask(parts, seed, jitter, rot, gap)
    thick = int(bold_px * 2 * SS + 1) | 1
    bold = mask.filter(ImageFilter.MaxFilter(thick))
    px = max(p[2] for p in parts)
    edge = auto_edge(px) if edge is None else edge
    return _finish(bold, crayonize(bold, strength, edge), fill, outline, outline_px)


def plank_png(size=(420, 74), fill=(232, 197, 152), grain_color=(196, 150, 98),
              edge=(108, 78, 48), seed=1, strength=DEFAULT_STRENGTH, wobble=1.6,
              bow_amp=5.0, pad=14, shadow=True):
    """不规则木板：四角不齐、边线外鼓，木纹 + 顶部高光 + 自带柔和投影。

    投影直接烘进 PNG（Canvas 不能做实时阴影），所以这张图必须画在模糊背景之上。
    """
    w, h = size
    W, H = int((w + pad * 2) * SS), int((h + pad * 2) * SS)
    rng = random.Random(seed)
    m = (3 + pad) * SS
    x0, y0, x1, y1 = m, m, W - m, H - m

    def side(a, b, n):
        """每条边额外加一个正弦外鼓，木片才像手工削的而不是矩形贴图。"""
        out = []
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        for i in range(n):
            t = i / n
            bow = math.sin(math.pi * t) * bow_amp * SS
            jit = rng.uniform(-wobble, wobble) * SS
            out.append((a[0] + dx * t + nx * (bow + jit),
                        a[1] + dy * t + ny * (bow + jit)))
        return out

    n = 14
    pts = (side((x0, y0), (x1, y0), n) + side((x1, y0), (x1, y1), n)
           + side((x1, y1), (x0, y1), n) + side((x0, y1), (x0, y0), n))
    poly = pts

    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).polygon(poly, fill=255)

    # 投影
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if shadow:
        sh = Image.new("L", (W, H), 0)
        sh.paste(mask, (0, int(6 * SS)))
        sh = sh.filter(ImageFilter.GaussianBlur(5 * SS))
        layer = Image.new("RGBA", (W, H), (20, 30, 20, 255))
        layer.putalpha(sh.point(lambda v: int(v * 0.34)))
        out = Image.alpha_composite(out, layer)

    # 边缘：外扩一圈深色，再把木板压上去
    thick = int(2.2 * 2 * SS + 1) | 1
    ring = mask.filter(ImageFilter.MaxFilter(thick))
    layer = Image.new("RGBA", (W, H), tuple(edge) + (255,))
    layer.putalpha(ring)
    out = Image.alpha_composite(out, layer)

    base = Image.new("RGBA", (W, H), tuple(fill) + (255,))
    base.putalpha(grain(mask, 0.34))
    out = Image.alpha_composite(out, base)

    # 木纹：贴着长边走的细线
    lines = Image.new("L", (W, H), 0)
    ld = ImageDraw.Draw(lines)
    for i in range(6):
        yy = y0 + (y1 - y0) * (0.16 + 0.14 * i) + rng.uniform(-2.5, 2.5) * SS
        amp = rng.uniform(1.2, 3.0) * SS
        pts_l = [(x0 + (x1 - x0) * (j / 24),
                  yy + math.sin(j / 24 * 5.4 + i * 1.7) * amp) for j in range(25)]
        ld.line(pts_l, fill=255, width=max(1, int(rng.uniform(0.9, 1.8) * SS)))
    lines = ImageChops.multiply(lines.filter(ImageFilter.GaussianBlur(0.7 * SS)), mask)
    layer = Image.new("RGBA", (W, H), tuple(grain_color) + (255,))
    layer.putalpha(lines.point(lambda v: int(v * 0.42)))
    out = Image.alpha_composite(out, layer)

    # 顶部高光，木板才有厚度
    hl = Image.new("L", (W, H), 0)
    ImageDraw.Draw(hl).line(
        [(x0 + 3 * SS, y0 + 3.2 * SS), (x1 - 3 * SS, y0 + 3.2 * SS)],
        fill=255, width=max(1, int(1.6 * SS)))
    hl = ImageChops.multiply(hl.filter(ImageFilter.GaussianBlur(0.8 * SS)), mask)
    layer = Image.new("RGBA", (W, H), (255, 250, 236, 255))
    layer.putalpha(hl.point(lambda v: int(v * 0.55)))
    out = Image.alpha_composite(out, layer)

    return out.resize((int((w + pad * 2)), int((h + pad * 2))), Image.LANCZOS)


def _text_mask(parts, seed=0, jitter=1.0, rot=1.2, gap=2.0):
    """parts: [(文字, 字体路径, 逻辑字号)]，逐字轻微旋转抖动。"""
    rng = random.Random(seed)
    chars = []
    for text, path, px in parts:
        f = ttf(path, px)
        for ch in text:
            chars.append((ch, f, px))
    widths = [f.getlength(ch) for ch, f, _ in chars]
    max_px = max(px for _, _, px in chars)
    total = sum(widths) + gap * SS * max(0, len(chars) - 1)
    pad = SS * 7
    w = int(total + pad * 2)
    h = int(max_px * SS * 1.75 + pad * 2)
    layer = Image.new("L", (w, h), 0)
    x = pad
    for (ch, f, _px), cw in zip(chars, widths):
        glyph = Image.new("L", (int(cw) + 5 * SS, h), 0)
        ImageDraw.Draw(glyph).text((2 * SS, pad), ch, font=f, fill=255)
        if rot:
            glyph = glyph.rotate(rng.uniform(-rot, rot), resample=Image.BICUBIC)
        layer.paste(
            glyph,
            (int(x + rng.uniform(-jitter, jitter) * SS),
             int(rng.uniform(-jitter, jitter) * SS * 0.7)),
            glyph,
        )
        x += cw + gap * SS
    return layer


def text_png(parts, fill=WHITE, outline=INK, outline_px=2.0, seed=0,
             strength=DEFAULT_STRENGTH, gap=2.0, jitter=1.0, rot=1.2,
             edge=None):
    mask = _text_mask(parts, seed, jitter, rot, gap)
    px = max(p[2] for p in parts)
    edge = auto_edge(px) if edge is None else edge
    return _finish(mask, crayonize(mask, strength, edge), fill, outline, outline_px)


def cell(img, w, h, valign="center", bottom_pad=7):
    """把字形放进固定尺寸的格子：计时数字必须等宽，否则秒数跳动会左右抖。

    小数点不能居中——居中会飘到字高中段，看起来像间隔号；用 valign="baseline"
    把它压到基线附近。
    """
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if valign == "baseline":
        y = h - bottom_pad - img.height
    else:
        y = (h - img.height) // 2
    canvas.paste(img, ((w - img.width) // 2, max(0, y)), img)
    return canvas


def shape_png(size, painter, fill, seed=0, strength=DEFAULT_STRENGTH,
              outline=None, outline_px=2.0, wobble=1.6, texture=0.0,
              crop=True, edge=1.0):
    """painter(draw, rng, SS, wobble) 在超采样画布上作图。"""
    w, h = size
    clean = Image.new("L", (int(w * SS), int(h * SS)), 0)
    painter(ImageDraw.Draw(clean), random.Random(seed), SS, wobble)
    filled = grain(clean, texture) if texture else crayonize(clean, strength, edge)
    return _finish(clean, filled, fill, outline, outline_px, crop=crop)


# =============================================================================
# 游戏里要用的具体符号
# =============================================================================
def _heart_mask(size, smooth=1.5, ss=None):
    """两个圆瓣 + 下尖三角，再模糊+阈值把接缝融化。

    比纯参数曲线更饱满，凹口更深；用固定比例保证任何 size 都不会被裁掉。
    """
    ss = SS if ss is None else ss
    box = int(size * 1.30)
    W = box * ss
    mask = Image.new("L", (W, W), 0)
    draw = ImageDraw.Draw(mask)
    s = size * ss
    cx = W / 2.0
    lobe_r = s * 0.30
    cy_top = W * 0.355
    tip_y = W * 0.955
    for sign in (-1, 1):
        ox = cx + sign * lobe_r * 0.85
        draw.ellipse([ox - lobe_r, cy_top - lobe_r,
                      ox + lobe_r, cy_top + lobe_r], fill=255)
    draw.polygon(
        [(cx - lobe_r * 1.78, cy_top + lobe_r * 0.55),
         (cx + lobe_r * 1.78, cy_top + lobe_r * 0.55),
         (cx, tip_y)],
        fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(smooth * ss))
    return mask.point(lambda v: 255 if v > 127 else 0)


# =============================================================================
# 蜡笔笔触：左右涂抹（逐行拉伸 + 横向模糊）+ 涂抹不匀留下的空隙
# =============================================================================
def _row_runs(row, width):
    out, start = [], None
    for x in range(width):
        solid = row[x] > 0
        if solid and start is None:
            start = x
        elif not solid and start is not None:
            out.append((start, x - 1))
            start = None
    if start is not None:
        out.append((start, width - 1))
    return out


def _row_drag(mask, jitter=0.7, chance=0.25, reach=4, seed=3):
    """逐行左右错位 + 随机把某几行的两端抹出去，模仿蜡笔横向拖拽。"""
    rng = random.Random(seed)
    w, h = mask.size
    src = mask.load()
    out = Image.new("L", (w, h), 0)
    dst = out.load()
    for y in range(h):
        row = [src[x, y] for x in range(w)]
        runs = _row_runs(row, w)
        if not runs:
            continue
        dx = int(round(rng.uniform(-jitter, jitter) * SS))
        for a, b in runs:
            if rng.random() < chance:
                a -= rng.randint(1, max(1, reach * SS))
            if rng.random() < chance:
                b += rng.randint(1, max(1, reach * SS))
            for x in range(max(0, a + dx), min(w, b + dx + 1)):
                dst[x, y] = 255
    return out


def _h_smear(mask, k=3, cut=118):
    """只压宽方向：1px 的拖拽痕迹会被糊成几像素的涂抹带。"""
    w, h = mask.size
    k = max(2, int(k))
    small = mask.resize((max(1, w // k), h), Image.BILINEAR)
    return small.resize((w, h), Image.BILINEAR).point(
        lambda v: 255 if v > cut else 0)


def _percentile_cut(noise, frac):
    hist = noise.histogram()
    target = sum(hist) * frac
    acc = 0
    for value, count in enumerate(hist):
        acc += count
        if acc >= target:
            return value
    return 128


def _punch(mask, hole=0.015, thin=0.05, blur=0.9, seed=5):
    """涂抹不匀：少量完全漏底 + 一圈半透明（纸纹透出来的感觉）。"""
    noise = Image.effect_noise(mask.size, 80 + seed).convert("L")
    noise = ImageOps.equalize(noise.filter(ImageFilter.GaussianBlur(blur * SS)))
    cut_hole = _percentile_cut(noise, hole)
    cut_thin = _percentile_cut(noise, hole + thin)
    keep = noise.point(
        lambda v: 0 if v < cut_hole else (150 if v < cut_thin else 255))
    return ImageChops.multiply(mask, keep)


def _compose_ring(clean, body, fill, outline, outline_px):
    """描边只画外侧（外扩 - 本体），所以内部的空隙能真的透出背景。"""
    thick = int(outline_px * 2 * SS + 1) | 1
    ring = ImageChops.subtract(clean.filter(ImageFilter.MaxFilter(thick)), clean)
    # 描边画在本体之外，裁剪必须按“本体 ∪ 描边”来算，否则外圈会被切掉
    box = ImageChops.lighter(ring, clean).getbbox()
    if not box:
        return None
    w = max(1, (box[2] - box[0]) // SS)
    h = max(1, (box[3] - box[1]) // SS)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for layer_mask, color in ((ring, outline), (body, fill)):
        layer = Image.new("RGBA", (w, h), tuple(color) + (255,))
        layer.putalpha(layer_mask.crop(box).resize((w, h), Image.LANCZOS))
        out = Image.alpha_composite(out, layer)
    return out


def heart_png(size=34, filled=True, fill=(238, 106, 100), outline=INK, seed=1):
    """心形贴图：蜡笔质感 = 边缘横向拖拽 + 内部涂抹不匀的漏底。

    满心：实心填充 + 漏底 + 加粗描边；空心：只留描边环，漏底只做浅色不打通。
    size 是逻辑尺寸；实际按 OUT 倍率渲染，保证 2 倍烘焙后放大不糊。
    """
    s = size * OUT
    pad = int(round(s * 0.22))             # 外扩画布，给拖拽留余量（否则被裁）
    src = _heart_mask(s)
    w, h = src.size
    mask = Image.new("L", (w + 2 * pad * SS, h + 2 * pad * SS), 0)
    mask.paste(src, (pad * SS, pad * SS))

    if filled:
        ragged = _h_smear(
            _row_drag(mask, jitter=0.5, chance=0.14, reach=2, seed=seed),
            k=2, cut=95)
        body = _punch(ragged, hole=0.02, thin=0.05, blur=1.2, seed=seed)
        return _compose_ring(ragged, body, fill, outline, 2.5)

    ragged = _h_smear(
        _row_drag(mask, jitter=0.4, chance=0.10, reach=1, seed=seed),
        k=2, cut=95)
    width = max(1, int(round(s * 0.075 * SS)))
    inner = ragged.filter(ImageFilter.MinFilter(width * 2 + 1))
    ring = ImageChops.subtract(ragged, inner)
    body = _punch(ring, hole=0.0, thin=0.06, blur=1.2, seed=seed + 1)
    return _compose_ring(ring, body, fill, outline, 2.5)


def pause_png(size=41, fill=WHITE, outline=INK, seed=3):
    bar_w, bar_h, gap = size * 0.18, size * 0.48, size * 0.18

    def painter(d, rng, ss, wob):
        for i in range(2):
            x0 = (size * ss - (bar_w * 2 + gap) * ss) / 2 + i * (bar_w + gap) * ss
            y0 = (size * ss - bar_h * ss) / 2
            pts = [(x0, y0), (x0 + bar_w * ss, y0),
                   (x0 + bar_w * ss, y0 + bar_h * ss), (x0, y0 + bar_h * ss)]
            jit = [(x + rng.uniform(-wob, wob) * ss, y + rng.uniform(-wob, wob) * ss)
                   for x, y in pts]
            d.polygon(jit, fill=255)

    return shape_png((size, size), painter, fill, seed=seed, strength=0.6,
                     outline=outline, outline_px=2.0, crop=False, edge=0.7)


def compass_base_png(diameter=96, seed=5):
    """白底圆盘 + 16 个刻度（8 个方向刻度更长更深）。"""
    r = diameter / 2 - 2

    def disc(d, rng, ss, wob):
        c = diameter * ss / 2
        pts = []
        for i in range(140):
            ang = 2 * math.pi * i / 140
            rr = (r + math.sin(3 * ang) * wob * 0.35) * ss
            pts.append((c + rr * math.cos(ang), c + rr * math.sin(ang)))
        d.polygon(pts, fill=255)

    def ticks_major(d, rng, ss, wob):
        c = diameter * ss / 2
        for i in range(8):
            ang = -math.pi / 2 + i * math.pi / 4
            r0, r1 = r * 0.74 * ss, r * 0.92 * ss
            d.line([(c + r0 * math.cos(ang), c + r0 * math.sin(ang)),
                    (c + r1 * math.cos(ang), c + r1 * math.sin(ang))],
                   fill=255, width=int(3.0 * ss))

    def ticks_minor(d, rng, ss, wob):
        c = diameter * ss / 2
        for i in range(8):
            ang = -math.pi / 2 + (i + 0.5) * math.pi / 4
            r0, r1 = r * 0.82 * ss, r * 0.91 * ss
            d.line([(c + r0 * math.cos(ang), c + r0 * math.sin(ang)),
                    (c + r1 * math.cos(ang), c + r1 * math.sin(ang))],
                   fill=255, width=int(1.6 * ss))

    base = shape_png((diameter, diameter), disc, DISC, seed=seed, strength=0.0,
                     texture=0.28, crop=False)
    for painter, color, strength, edge in (
        (ticks_minor, TICK_LIGHT, 0.35, 0.4),
        (ticks_major, TICK, 0.45, 0.4),
    ):
        layer = shape_png((diameter, diameter), painter, color, seed=seed,
                          strength=strength, crop=False, edge=edge)
        base = Image.alpha_composite(base, layer)
    return base


# 八向风：名字 -> 单位向量（屏幕坐标，y 向下，“吹向”）
WIND_VECTORS = {
    "e": (1.0, 0.0),
    "se": (0.7071, 0.7071),
    "s": (0.0, 1.0),
    "sw": (-0.7071, 0.7071),
    "w": (-1.0, 0.0),
    "nw": (-0.7071, -0.7071),
    "n": (0.0, -1.0),
    "ne": (0.7071, -0.7071),
}


def compass_arrow_png(diameter=96, direction="nw", fill=RED, seed=9,
                      tail=0.0, tip=0.89, shaft=5.6, head=21.0,
                      strength=0.20):
    """红色指针：粗杆 + 大三角头，从圆心指向“吹向”。"""
    ux, uy = WIND_VECTORS[direction]
    r = diameter / 2 - 2

    def painter(d, rng, ss, wob):
        c = diameter * ss / 2
        ux, uy = WIND_VECTORS[direction]
        perp = (-uy, ux)

        start = (c + ux * r * tail * ss, c + uy * r * tail * ss)
        end = (c + ux * r * tip * ss, c + uy * r * tip * ss)

        # 箭头三角：底边中点沿反方向退 head，再沿垂线左右展开。
        base = (end[0] - ux * head * ss, end[1] - uy * head * ss)
        hw = head * ss * 0.42
        p1 = (base[0] + perp[0] * hw, base[1] + perp[1] * hw)
        p2 = (base[0] - perp[0] * hw, base[1] - perp[1] * hw)

        # 杆只画到三角内部，绝不能画到尖端：杆宽 5.5px，到尖端三角宽度趋近 0，
        # 杆会从三角两条斜边穿出来，看起来就是“尖头分了叉”。
        shaft_end = (base[0] + ux * head * ss * 0.30,
                     base[1] + uy * head * ss * 0.30)
        d.line([start, shaft_end], fill=255, width=max(2, int(shaft * ss)))
        d.polygon([end, p1, p2], fill=255)
        d.ellipse([c - 3.4 * ss, c - 3.4 * ss, c + 3.4 * ss, c + 3.4 * ss], fill=255)

    return shape_png((diameter, diameter), painter, fill, seed=seed,
                     strength=strength, crop=False, edge=0.15)


def star_png(size=22, fill=RED, outline=INK, seed=13):
    """五角星：站酷快乐体没有 ★ 字形（会渲染成豆腐块），所以自己画。"""
    w = h = int(size * 1.34)

    def painter(d, rng, ss, wob):
        cx, cy = w * ss / 2, h * ss / 2
        outer, inner = size * ss * 0.50, size * ss * 0.21
        pts = []
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            rr = outer if i % 2 == 0 else inner
            pts.append((cx + rr * math.cos(ang) + rng.uniform(-wob, wob) * ss,
                        cy + rr * math.sin(ang) + rng.uniform(-wob, wob) * ss))
        d.polygon(pts, fill=255)

    return shape_png((w, h), painter, fill, seed=seed, strength=0.5,
                     outline=outline, outline_px=1.2, crop=False)
