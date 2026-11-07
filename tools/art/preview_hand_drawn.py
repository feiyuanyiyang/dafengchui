# -*- coding: utf-8 -*-
"""开发评审用：方案 B（无底纹）+ C（罗盘）+ 手写字体/蜡笔质感可行性验证。

    python tools/art/preview_hand_drawn.py

输出到 art_preview/：
    ui_hud_bc.png          结构定稿：零装饰 + 右上角罗盘（雅黑，无质感）
    ui_hud_bc_hand.png     同结构，手写字体 + 蜡笔质感 + 手绘符号
    ui_font_samples.png    字体候选 / 质感强度 / 符号小样 对照表

字体只放在 tools/art/fonts/，不参与游戏打包，也不改 game.py。
所有文字与符号都是 Pillow 光栅化后再加噪、侵蚀、磨边，等价于“预烘 PNG”。
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from preview_hud_style import (  # noqa: E402
    H,
    MARGIN,
    PAUSE_SIZE,
    STROKE,
    TIMER_Y,
    W,
    WHITE_TEXT,
    Painter,
    caption,
    scene,
)

FONT_DIR = ROOT / "tools" / "art" / "fonts"
CJK_HAND = str(FONT_DIR / "ZCOOLKuaiLe-Regular.ttf")     # 站酷快乐体 · OFL
CJK_BRUSH = str(FONT_DIR / "MaShanZheng-Regular.ttf")    # 马善政毛笔楷 · OFL
LATIN_HAND = str(FONT_DIR / "PatrickHand-Regular.ttf")   # 手写数字/拉丁 · OFL
SYSTEM = "C:/Windows/Fonts/"

SS = 4  # 超采样倍数：先在 4 倍尺寸做噪点/侵蚀，最后降采样得到干净的抗锯齿

INK = (34, 54, 45)
PAPER = (250, 247, 240)


# =============================================================================
# 1. 蜡笔质感：把任意字形/图形 mask 变成“被蜡笔涂过”的样子
# =============================================================================
def ttf(path, px):
    return ImageFont.truetype(path, max(8, int(px * SS)))


def _chars_of(parts):
    """parts: [(text, font_path, px)] -> [(char, font, px)]，汉字与数字可混排。"""
    items = []
    for text, path, px in parts:
        f = ttf(path, px)
        for ch in text:
            items.append((ch, f, px))
    return items


def text_mask(parts, seed=0, jitter=1.1, rot=1.4, gap=2.0):
    chars = _chars_of(parts)
    rng = random.Random(seed)
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


def crayonize(mask, strength=0.6):
    """0 = 干净矢量字；1 = 重蜡笔（毛边 + 颗粒 + 干笔白点）。"""
    if strength <= 0:
        return mask
    m = mask.filter(ImageFilter.GaussianBlur(1.5))
    m = m.point(lambda v: 255 if v > 92 else 0)          # 毛边：模糊后硬阈值
    noise = Image.effect_noise(m.size, 115).convert("L")  # 纸纹颗粒
    floor = int(255 - 120 * strength)
    mod = noise.point(lambda v: floor + int(v * (255 - floor) / 255))
    m = ImageChops.multiply(m, mod)
    m = m.point(lambda v: min(255, int(v * (1.0 + 0.6 * strength))))
    return m


def _origin(xy, anchor, w, h):
    x, y = xy
    if anchor[0] == "m":
        x -= w / 2
    elif anchor[0] == "r":
        x -= w
    if anchor[1] == "m":
        y -= h / 2
    elif anchor[1] == "b":
        y -= h
    return int(x), int(y)


def _paste(base, alpha, color, xy):
    layer = Image.new("RGBA", alpha.size, tuple(color) + (255,))
    layer.putalpha(alpha)
    base.alpha_composite(layer, xy)


def draw_text(base, xy, parts, color, anchor="lm", seed=0, strength=0.55,
              jitter=1.1, rot=1.4, gap=2.0, outline=None, outline_px=2):
    """在 RGBA 画布上画一段带蜡笔质感的文字（可选中式描边）。"""
    m = crayonize(text_mask(parts, seed, jitter, rot, gap), strength)
    bb = m.getbbox()
    if not bb:
        return base
    m = m.crop(bb)
    w, h = max(1, m.width // SS), max(1, m.height // SS)
    alpha = m.resize((w, h), Image.LANCZOS)
    if outline is not None:
        t = outline_px
        thick = int(t * 2 * SS + 1) | 1  # MaxFilter 只接受奇数
        om = m.filter(ImageFilter.MaxFilter(thick))
        oa = om.resize((om.width // SS, om.height // SS), Image.LANCZOS)
        ox, oy = _origin(xy, anchor, oa.width, oa.height)
        _paste(base, oa, outline, (ox, oy))
    px, py = _origin(xy, anchor, w, h)
    _paste(base, alpha, color, (px, py))
    return base


# =============================================================================
# 2. 手绘符号：心 / 暂停 / 罗盘，全部先画 mask 再 crayonize
# =============================================================================
def _shape_layer(size, painter, seed=0, wobble=1.6):
    """painter(draw, rng, SS, wobble) 在超采样画布上作画，返回 L mask。"""
    w, h = size
    img = Image.new("L", (int(w * SS), int(h * SS)), 0)
    painter(ImageDraw.Draw(img), random.Random(seed), SS, wobble)
    return img


def _wobble_pts(points, rng, amt):
    return [(x + rng.uniform(-amt, amt), y + rng.uniform(-amt, amt)) for x, y in points]


def heart_points(cx, cy, sx, sy, n=100):
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((cx + x * sx / 16.0, cy - y * sy / 16.0))
    return pts


def draw_heart(base, xy, size, color, seed=0, strength=0.75, filled=True,
               outline=None, outline_px=2):
    w = h = int(size * 1.15)

    def painter(d, rng, ss, wob):
        cx, cy = w * ss / 2, h * ss / 2.32
        pts = heart_points(cx, cy, size * ss * 0.52, size * ss * 0.52)
        if filled:
            d.polygon(_wobble_pts(pts, rng, wob * ss), fill=255)
        else:
            d.line(_wobble_pts(pts + [pts[0]], rng, wob * ss), fill=255,
                   width=max(2, int(ss * 1.8)))

    m = crayonize(_shape_layer((w, h), painter, seed), strength)
    bb = m.getbbox()
    if not bb:
        return base
    m = m.crop(bb)
    a = m.resize((max(1, m.width // SS), max(1, m.height // SS)), Image.LANCZOS)
    if outline is not None:
        om = m.filter(ImageFilter.MaxFilter(int(outline_px * 2 * SS + 1) | 1))
        oa = om.resize((om.width // SS, om.height // SS), Image.LANCZOS)
        ox, oy = _origin(xy, "mm", oa.width, oa.height)
        _paste(base, oa, outline, (ox, oy))
    px, py = _origin(xy, "mm", a.width, a.height)
    _paste(base, a, color, (px, py))
    return base


def draw_pause(base, xy, size, color, seed=0, strength=0.6, outline=None,
               outline_px=2):
    bar_w, bar_h, gap = size * 0.17, size * 0.46, size * 0.16

    def painter(d, rng, ss, wob):
        for i in range(2):
            x0 = (size * ss - (bar_w * 2 + gap) * ss) / 2 + i * (bar_w + gap) * ss
            y0 = (size * ss - bar_h * ss) / 2
            pts = [
                (x0, y0), (x0 + bar_w * ss, y0),
                (x0 + bar_w * ss, y0 + bar_h * ss), (x0, y0 + bar_h * ss),
            ]
            d.polygon(_wobble_pts(pts, rng, wob * ss), fill=255)

    w = h = int(size)
    m = crayonize(_shape_layer((w, h), painter, seed), strength)
    a = m.resize((m.width // SS, m.height // SS), Image.LANCZOS)
    if outline is not None:
        om = m.filter(ImageFilter.MaxFilter(int(outline_px * 2 * SS + 1) | 1))
        oa = om.resize((om.width // SS, om.height // SS), Image.LANCZOS)
        ox, oy = _origin(xy, "mm", oa.width, oa.height)
        _paste(base, oa, outline, (ox, oy))
    px, py = _origin(xy, "mm", a.width, a.height)
    _paste(base, a, color, (px, py))
    return base


def draw_compass(base, cx, cy, r, color, needle, seed=0, strength=0.55,
                 outline=None, outline_px=2, ticks=True):
    """手绘罗盘：抖动的圆环 + 八刻度 + 指向“吹向”的箭头。"""
    size = int(r * 2 + 8)

    def painter(d, rng, ss, wob):
        c = size * ss / 2

        def ring(radius, width):
            pts = []
            for i in range(120):
                ang = 2 * math.pi * i / 120
                rr = radius + math.sin(3 * ang + 0.7) * wob * ss * 0.5
                pts.append((c + rr * math.cos(ang), c + rr * math.sin(ang)))
            d.line(pts + [pts[0]], fill=255, width=width)

        ring(r * ss, max(2, int(2.2 * ss)))
        if ticks:
            for i in range(8):
                ang = -math.pi / 2 + i * math.pi / 4
                r0, r1 = r * ss * 0.80, r * ss * 0.90
                d.line(
                    [(c + r0 * math.cos(ang) + rng.uniform(-wob, wob) * ss,
                      c + r0 * math.sin(ang) + rng.uniform(-wob, wob) * ss),
                     (c + r1 * math.cos(ang) + rng.uniform(-wob, wob) * ss,
                      c + r1 * math.sin(ang) + rng.uniform(-wob, wob) * ss)],
                    fill=255, width=max(2, int(1.8 * ss)),
                )
        nx, ny = needle
        tip = (c + nx * r * ss * 0.72, c + ny * r * ss * 0.72)
        tail = (c - nx * r * ss * 0.28, c - ny * r * ss * 0.28)
        d.line([tail, tip], fill=255, width=max(2, int(3.8 * ss)))
        head = 0.44
        h1 = (tip[0] - math.cos(math.atan2(ny, nx) - head) * r * ss * 0.40,
              tip[1] - math.sin(math.atan2(ny, nx) - head) * r * ss * 0.3)
        h2 = (tip[0] - math.cos(math.atan2(ny, nx) + head) * r * ss * 0.40,
              tip[1] - math.sin(math.atan2(ny, nx) + head) * r * ss * 0.3)
        d.polygon([tip, h1, h2], fill=255)
        d.ellipse([c - 2.2 * ss, c - 2.2 * ss, c + 2.2 * ss, c + 2.2 * ss], fill=255)

    m = crayonize(_shape_layer((size, size), painter, seed), strength)
    bb = m.getbbox()
    if not bb:
        return base
    m = m.crop(bb)
    a = m.resize((max(1, m.width // SS), max(1, m.height // SS)), Image.LANCZOS)
    if outline is not None:
        om = m.filter(ImageFilter.MaxFilter(int(outline_px * 2 * SS + 1) | 1))
        oa = om.resize((om.width // SS, om.height // SS), Image.LANCZOS)
        ox, oy = _origin((cx, cy), "mm", oa.width, oa.height)
        _paste(base, oa, outline, (ox, oy))
    px, py = _origin((cx, cy), "mm", a.width, a.height)
    _paste(base, a, color, (px, py))
    return base


# =============================================================================
# 3. 图一：结构定稿（雅黑 + 几何罗盘，零装饰）
# =============================================================================
def hud_bc_structure():
    base = scene()
    p = Painter(base)

    # 左上：暂停 + 心
    gap = 10
    bar_w, bar_h = 6, 19
    for i in range(2):
        x0 = MARGIN + (PAUSE_SIZE - (bar_w * 2 + gap)) // 2 + i * (bar_w + gap)
        y0 = MARGIN + (PAUSE_SIZE - bar_h) // 2
        p.rounded((x0 - 3, y0 - 3, x0 + bar_w + 3, y0 + bar_h + 3), radius=5,
                  fill=(255, 255, 255, 240), outline=tuple(STROKE), width=2)
    hx = MARGIN + PAUSE_SIZE + 17
    for i in range(3):
        img = Image.open(ROOT / "assets" / "sprites" / "ui" / "heart_full.png")
        img = img.convert("RGBA").resize((34, 34), Image.LANCZOS)
        p.image(img, (hx + i * 44, MARGIN + 3), anchor="la", shadow=(22, 46, 35, 80))

    # 右上：计时（白字 + 描边，无卡片）
    from preview_hud_style import font, mixed_text, mono

    parts = [
        ("存活", font(17), WHITE_TEXT, 3, tuple(STROKE)),
        ("12.4", mono(26, True), WHITE_TEXT, 3, tuple(STROKE)),
        ("s", font(17), WHITE_TEXT, 3, tuple(STROKE)),
    ]
    mixed_text(p, (W - MARGIN, TIMER_Y + 20), parts, anchor="rm")

    # 右上偏下：几何罗盘（替代原来的正上方风向箭头 + 文字）
    cx, cy, r = W - MARGIN - 44, 158, 38

    def ring_pts(radius):
        return [(cx + radius * math.cos(2 * math.pi * i / 90),
                 cy + radius * math.sin(2 * math.pi * i / 90)) for i in range(91)]

    for color, extra in ((tuple(STROKE), 3), (WHITE_TEXT, 0)):
        for i in range(8):
            ang = -math.pi / 2 + i * math.pi / 4
            p.line([(cx + (r - 10) * math.cos(ang), cy + (r - 10) * math.sin(ang)),
                    (cx + (r - 3) * math.cos(ang), cy + (r - 3) * math.sin(ang))],
                   fill=color, width=3 + extra)
        p.line(ring_pts(r), fill=color, width=3 + extra)
        arrow(p, cx - 0.707 * 6, cy - 0.707 * 6, cx - 0.707 * (r - 12),
              cy - 0.707 * (r - 12), color, width=4 + extra, head=13 + extra)
    return caption(p.base, "结构定稿 · 方案 B + C：零装饰 HUD，风向收进右上角罗盘")


def arrow(p, x1, y1, x2, y2, color, width=3, head=11):
    ang = math.atan2(y2 - y1, x2 - x1)
    p.line([(x1, y1), (x2, y2)], fill=color, width=width)
    p.polygon([(x2, y2),
               (x2 - head * math.cos(ang - 0.45), y2 - head * math.sin(ang - 0.45)),
               (x2 - head * math.cos(ang + 0.45), y2 - head * math.sin(ang + 0.45))],
              fill=color)


# =============================================================================
# 4. 图二：手写字体 + 蜡笔质感（同结构）
# =============================================================================
def hud_bc_hand():
    base = scene().convert("RGBA")
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.alpha_composite(base)
    canvas = out

    white = (255, 255, 255)
    ink = INK

    # 左上：手绘暂停 + 三颗蜡笔心（不再有卡片和描边方框）
    draw_pause(canvas, (MARGIN + PAUSE_SIZE / 2, MARGIN + PAUSE_SIZE / 2),
               PAUSE_SIZE, white, seed=3, strength=0.35, outline=ink, outline_px=2)
    hx = MARGIN + PAUSE_SIZE + 26
    for i in range(3):
        draw_heart(canvas, (hx + i * 42, MARGIN + 21), 34, (238, 106, 100),
                   seed=10 + i, strength=0.7, outline=ink, outline_px=2)

    # 右上：手写计时（中文用站酷快乐体，数字用 Patrick Hand）
    draw_text(canvas, (W - MARGIN, TIMER_Y + 20),
              [("存活", CJK_HAND, 19), ("12.4", LATIN_HAND, 28), ("s", LATIN_HAND, 28)],
              white, anchor="rm", seed=7, strength=0.25, gap=7,
              outline=ink, outline_px=1.5, jitter=0.8, rot=1.0)

    # 右上偏下：手绘罗盘
    cx, cy, r = W - MARGIN - 44, 168, 40
    draw_compass(canvas, cx, cy, r, white, (-0.707, -0.707), seed=5,
                 strength=0.5, outline=ink, outline_px=2)
    draw_text(canvas, (cx, cy + r + 22), [("西北风", CJK_HAND, 17), ("180", LATIN_HAND, 22)],
              white, anchor="mm", seed=9, strength=0.3,
              outline=ink, outline_px=2, jitter=0.8, rot=1.2, gap=6)

    return caption(canvas, "手写 + 蜡笔质感定稿 · 同结构，仅换字形与笔触")


# =============================================================================
# 5. 图三：字体候选 / 质感强度 / 符号小样
# =============================================================================
def _panel_label(p, xy, text, size=15, color=(120, 134, 124)):
    from preview_hud_style import font as sysfont

    p.text(xy, text, sysfont(size), tuple(color) + (255,), anchor="lm")


def font_samples():
    Ws, Hs = 1200, 1150
    canvas = Image.new("RGBA", (Ws, Hs), PAPER + (255,))
    p = Painter(canvas)
    canvas = p.base  # Painter 会复制画布，后续直接绘制必须用它的 base
    from preview_hud_style import font as sysfont

    p.text((40, 34), "字体与质感对照（评审用，未接入游戏）", sysfont(26, True),
           INK + (255,), anchor="lm")
    p.text((40, 68), "所有字形都是 Pillow 逐字旋转 + 噪点侵蚀；符号是同一条管线画出来的", sysfont(14),
           (130, 144, 134, 255), anchor="lm")

    y = 110
    p.text((40, y), "A · 中文候选（同一句游戏文案）", sysfont(16, True), INK + (255,), anchor="lm")
    y += 34
    samples = [
        ("雅黑（现状）", SYSTEM + "msyh.ttc"),
        ("幼圆", SYSTEM + "SIMYOU.TTF"),
        ("楷体", SYSTEM + "simkai.ttf"),
        ("华文行楷", SYSTEM + "STXINGKA.TTF"),
        ("站酷快乐体 · OFL", CJK_HAND),
        ("马善政毛笔楷 · OFL", CJK_BRUSH),
    ]
    for name, path in samples:
        _panel_label(p, (46, y + 22), name)
        for i, (txt, px) in enumerate((("西北风 180", 40), ("游戏暂停", 40))):
            f = ImageFont.truetype(path, px)
            p.text((250 + i * 330, y + 22), txt, f, INK + (255,), anchor="lm")
        y += 56
    p.line([(40, y + 8), (Ws - 40, y + 8)], fill=(226, 220, 208, 255), width=1)

    # B 区：质感强度
    y += 34
    p.text((40, y), "B · 蜡笔质感强度（站酷快乐体，0 / 40% / 80%）", sysfont(16, True),
           INK + (255,), anchor="lm")
    y += 30
    for diff, label in ((0.0, "0% 纯字形"), (0.4, "40% 轻蜡笔"), (0.8, "80% 重蜡笔")):
        draw_text(p.base, (60, y + 34), [("西北风 180", CJK_HAND, 44)],
                  INK, anchor="lm", seed=21, strength=diff, jitter=1.4, rot=1.8)
        _panel_label(p, (500, y + 34), label)
        y += 76
    p.line([(40, y + 8), (Ws - 40, y + 8)], fill=(226, 220, 208, 255), width=1)

    # C 区：符号小样（游戏内实际尺寸）
    y += 30
    p.text((40, y), "C · 符号小样（与游戏内实际尺寸一致）", sysfont(16, True),
           INK + (255,), anchor="lm")
    y += 74
    xs = [120, 240, 360, 490, 650, 800]
    draw_heart(p.base, (xs[0], y), 34, (238, 106, 100), seed=1, strength=0.7, outline=INK)
    draw_heart(p.base, (xs[1], y), 34, (238, 106, 100), seed=2, strength=0.7,
               filled=False, outline=INK)
    draw_pause(p.base, (xs[2], y), 41, INK, seed=3, strength=0.55)
    draw_compass(p.base, xs[3], y, 40, INK, (-0.707, -0.707), seed=4, strength=0.55)
    draw_text(p.base, (xs[4] - 40, y), [("西北风", CJK_HAND, 22)], INK,
              anchor="lm", seed=6, strength=0.4, jitter=0.8, rot=1.0)
    draw_text(p.base, (xs[5] - 40, y), [("12.4", LATIN_HAND, 30)], INK,
              anchor="lm", seed=8, strength=0.4, jitter=0.8, rot=1.0)
    for label, x in (("心·满", xs[0]), ("心·空", xs[1]), ("暂停", xs[2]),
                     ("罗盘", xs[3]), ("手写中文", xs[4] - 40), ("手写数字", xs[5] - 40)):
        p.text((x, y + 56), label, sysfont(13), (130, 144, 134, 255), anchor="lm")

    footer = ("结论：字形换掉只需一张 TTF；粗糙感来自同一套噪点管线，与字体无关。"
              "游戏里落地方式＝预烘成 PNG，Canvas 只贴图。")
    p.line([(40, Hs - 96), (Ws - 40, Hs - 96)], fill=(226, 220, 208, 255), width=1)
    p.text((40, Hs - 64), footer, sysfont(14), (110, 124, 114, 255), anchor="lm")
    return p.base.convert("RGB")


def main():
    out = ROOT / "art_preview"
    out.mkdir(parents=True, exist_ok=True)
    for name, fn in (
        ("ui_hud_bc.png", hud_bc_structure),
        ("ui_hud_bc_hand.png", hud_bc_hand),
        ("ui_font_samples.png", font_samples),
    ):
        path = out / name
        fn().save(path)
        print("write", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
