# -*- coding: utf-8 -*-
"""运行时蜡笔文字：把任意字符串烘成带颗粒/描边的 RGBA 图。

与 tools/art/crayon_kit.py 的烘焙管线同源，区别只在字号：
    - 离线烘焙走 SS=4、OUT=2，追求印刷级质感；
    - 运行时走 SS=2、OUT=1，动态文本（条目名、时长、状态提示）也能承受。
字体放在 assets/fonts/（随 PyInstaller 的 assets 一起打包），
所以打包后不依赖 tools/ 目录。

    from crayon_text import text_png
    image = text_png([("音效", FONT_CJK, 20)], seed=3, fill=WHITE,
                     outline=INK, outline_px=1.6)

结果建议由调用方缓存：同一字符串只烘一次。
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
FONT_DIR = ROOT / "assets" / "fonts"
FONT_CJK = str(FONT_DIR / "ZCOOLKuaiLe-Regular.ttf")     # 站酷快乐体 · OFL
FONT_LATIN = str(FONT_DIR / "PatrickHand-Regular.ttf")   # 手写数字 · OFL

SS = 2          # 超采样倍数：运行时取 2，兼顾毛边与速度
OUT = 1.0       # 输出倍率（调用方按 UI_SCALE 自行放大字号）
INK = (34, 54, 45)
WHITE = (255, 255, 255)
DEFAULT_STRENGTH = 0.8


def available():
    """字体与 Pillow 都就绪时才启用蜡笔界面。"""
    return Path(FONT_CJK).is_file() and Path(FONT_LATIN).is_file()


def _font(path, px):
    return ImageFont.truetype(path, max(8, int(px * SS)))


def _auto_edge(px):
    """字号越小，边缘越收敛：26px 以下最多只啃 55%。"""
    return max(0.55, min(1.0, px / 26.0))


def _crayonize(mask, strength=DEFAULT_STRENGTH, edge=1.0):
    if strength <= 0:
        return mask
    if edge > 0:
        blur = 0.25 + 1.30 * edge * strength
        m = mask.filter(ImageFilter.GaussianBlur(blur))
        cut = 92 + int(38 * (1.0 - edge))
        m = m.point(lambda v: 255 if v > cut else 0)
    else:
        m = mask
    noise = Image.effect_noise(m.size, 115).convert("L")
    floor = int(255 - 120 * strength)
    mod = noise.point(lambda v: floor + int(v * (255 - floor) / 255))
    m = ImageChops.multiply(m, mod)
    return m.point(lambda v: min(255, int(v * (1.0 + 0.6 * strength))))


def _finish(clean, fill_mask, fill, outline=None, outline_px=2.0):
    """clean 决定轮廓（含描边），fill_mask 决定颗粒。"""
    if outline is not None and outline_px > 0:
        thick = int(outline_px * 2 * SS + 1) | 1  # MaxFilter 只吃奇数
        ring = clean.filter(ImageFilter.MaxFilter(thick))
    else:
        ring = None
    box = (ring or fill_mask).getbbox()
    if not box:
        return None
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


def _text_mask(parts, seed=0, jitter=1.0, rot=1.2, gap=2.0):
    """parts: [(文字, 字体路径, 逻辑字号)]，逐字轻微旋转抖动。"""
    rng = random.Random(seed)
    chars = []
    for label, path, px in parts:
        font = _font(path, px)
        for ch in label:
            chars.append((ch, font, px))
    widths = [font.getlength(ch) for ch, font, _px in chars]
    max_px = max(px for _ch, _font, px in chars)
    total = sum(widths) + gap * SS * max(0, len(chars) - 1)
    pad = SS * 7
    w = int(total + pad * 2)
    h = int(max_px * SS * 1.75 + pad * 2)
    layer = Image.new("L", (w, h), 0)
    x = pad
    for (ch, font, _px), cw in zip(chars, widths):
        glyph = Image.new("L", (int(cw) + 5 * SS, h), 0)
        ImageDraw.Draw(glyph).text((2 * SS, pad), ch, font=font, fill=255)
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
             edge=None, bold_px=None):
    """把一段文字烘成 RGBA；bold_px 用膨胀字形模拟粗体（标题用）。"""
    mask = _text_mask(parts, seed, jitter, rot, gap)
    if bold_px:
        thick = int(bold_px * 2 * SS + 1) | 1
        clean = mask.filter(ImageFilter.MaxFilter(thick))
    else:
        clean = mask
    px = max(p[2] for p in parts)
    edge = _auto_edge(px) if edge is None else edge
    return _finish(clean, _crayonize(clean, strength, edge),
                   fill, outline, outline_px)


def measure(text, path, px):
    """估算文字宽度（逻辑像素），给状态栏换行用。"""
    font = ImageFont.truetype(path, max(8, int(px)))
    try:
        return font.getlength(text)
    except Exception:
        return len(text) * px * 0.9
