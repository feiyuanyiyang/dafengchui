# -*- coding: utf-8 -*-
"""开发评审用：HUD 亮色化三方案 + 暂停面板浅色化（不进游戏）。

    python tools/art/preview_hud_style.py

只做“画图评审”，不导入也不修改 game.py：用 assets 里真实的草地、角色、心形、
障碍物贴图拼出游戏内画面，再把候选 HUD 画上去，输出到 art_preview/。

    art_preview/ui_hud_option_a.png   白卡圆角（信息分块）
    art_preview/ui_hud_option_b.png   无底纹极简（描边字）
    art_preview/ui_hud_option_c.png   风向盘 + 风迹线（中央彻底留白）
    art_preview/ui_pause_light.png    暂停面板浅色化 + 柔和压暗

亮底上用半透明白卡，等价于“预烘带底色的 PNG”（Canvas 图片 alpha 只跟画布
背景混，不跟下方 item 混），所以方案 A/C 落地时就是把卡片整体烘成 PNG。
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
SPRITES = ROOT / "assets" / "sprites"
OUT = ROOT / "art_preview"
FONTS = Path("C:/Windows/Fonts")

W, H = 1200, 840
CAPTION_H = 46

# game.py 用的是 Tk 的“点”字号，96 dpi 下 1pt≈1.333px；贴着实际观感换算成像素
SMALL_PX = 19     # 14pt
NORMAL_PX = 25    # 19pt
BIG_PX = 55       # 41pt

# 与 game.py 一致的布局常量（HUD 边距 29、暂停键 41、心 34、计时 y=36）
MARGIN = 29
PAUSE_SIZE = 41
HEART_SIZE = 34
HEART_GAP = 10
TIMER_Y = 36

# --- 亮色主题调色板 ---------------------------------------------------------
INK = (34, 54, 45, 255)          # 主文字：深墨绿，压在草地上够稳
INK_SOFT = (86, 110, 98, 255)    # 次要文字
CARD = (255, 255, 255, 233)      # 半透明白卡
CARD_SOFT = (255, 255, 255, 190) # 更薄的白卡（风带/副信息）
CARD_BORDER = (255, 255, 255, 245)
SHADOW = (22, 46, 35, 70)
ACCENT = (46, 158, 125, 255)     # 薄荷绿：正向操作
ACCENT_WARM = (240, 163, 62, 255)  # 暖橙：风速/警示
WHITE_TEXT = (255, 255, 255, 245)
STROKE = (26, 48, 38, 220)


def font(size, bold=False):
    name = "msyhbd.ttc" if bold else "msyh.ttc"
    return ImageFont.truetype(str(FONTS / name), size)


def mono(size, bold=False):
    name = "consolab.ttf" if bold else "consola.ttf"
    return ImageFont.truetype(str(FONTS / name), size)


_MEASURE = ImageDraw.Draw(Image.new("RGB", (8, 8)))


# --- 画布封装：所有绘制走透明图层再合成，避免直接涂抹破坏抗锯齿 ------------
class Painter:
    def __init__(self, base):
        self.base = base.convert("RGBA")

    def _overlay(self):
        layer = Image.new("RGBA", self.base.size, (0, 0, 0, 0))
        return layer, ImageDraw.Draw(layer)

    def _commit(self, layer):
        self.base = Image.alpha_composite(self.base, layer)

    def rounded(self, box, radius, fill, outline=None, width=1):
        layer, d = self._overlay()
        d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
        self._commit(layer)
        return self

    def card(self, box, radius, fill=CARD, shadow=SHADOW, blur=9, dy=3,
             outline=None, width=1):
        """带柔和投影的圆角卡片（落地时整张烘成 PNG）。"""
        layer = Image.new("RGBA", self.base.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle(
            (box[0], box[1] + dy, box[2], box[3] + dy), radius=radius, fill=shadow)
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
        self._commit(layer)
        self.rounded(box, radius, fill, outline, width)
        return self

    def text(self, xy, s, f, fill, anchor="la", stroke=0, stroke_fill=None):
        layer, d = self._overlay()
        d.text(xy, s, font=f, fill=fill, anchor=anchor,
               stroke_width=stroke, stroke_fill=stroke_fill)
        self._commit(layer)
        return self

    def line(self, xy, fill, width=2):
        layer, d = self._overlay()
        d.line(xy, fill=fill, width=width)
        self._commit(layer)
        return self

    def polygon(self, pts, fill):
        layer, d = self._overlay()
        d.polygon(pts, fill=fill)
        self._commit(layer)
        return self

    def image(self, img, xy, anchor="mm", shadow=None, blur=3, offset=(1, 2)):
        pos = _anchor_pos(self.base.size, img.size, xy, anchor)
        if shadow is not None:
            sil = Image.new("RGBA", img.size, (0, 0, 0, 0))
            sil.paste(shadow, (0, 0), img.split()[3])
            sil = sil.filter(ImageFilter.GaussianBlur(blur))
            self.base.alpha_composite(sil, (pos[0] + offset[0], pos[1] + offset[1]))
        self.base.alpha_composite(img, pos)
        return self


def _anchor_pos(base_size, size, xy, anchor):
    """anchor 语义与 PIL 文本 anchor 一致：m/u/l/r/b 组合，这里只用到 m/b。"""
    del base_size
    x, y = xy
    w, h = size
    if anchor[0] == "m":
        x -= w // 2
    elif anchor[0] == "r":
        x -= w
    if anchor[1] == "m":
        y -= h // 2
    elif anchor[1] == "b":
        y -= h
    return (int(x), int(y))


def arrow(p, x1, y1, x2, y2, color, width=3, head=11):
    ang = math.atan2(y2 - y1, x2 - x1)
    p.line([(x1, y1), (x2, y2)], fill=color, width=width)
    p.polygon(
        [
            (x2, y2),
            (x2 - head * math.cos(ang - 0.45), y2 - head * math.sin(ang - 0.45)),
            (x2 - head * math.cos(ang + 0.45), y2 - head * math.sin(ang + 0.45)),
        ],
        fill=color,
    )


def mixed_text(p, xy, parts, anchor="lm", gap=5):
    """中英混排：汉字用雅黑、数字用等宽，避免 Consolas 缺 CJK 变成豆腐块。

    anchor 只区分水平对齐：l 左 / m 居中 / r 右；竖直方向固定在基线中线。
    """
    widths = [_MEASURE.textlength(text, font=f) for text, f, *_ in parts]
    total = sum(widths) + gap * (len(parts) - 1)
    if anchor[0] == "m":
        x = xy[0] - total / 2
    elif anchor[0] == "r":
        x = xy[0] - total
    else:
        x = xy[0]
    for (text, f, fill, stroke, stroke_fill), w in zip(parts, widths):
        p.text((x, xy[1]), text, f, fill, anchor="lm",
               stroke=stroke, stroke_fill=stroke_fill)
        x += w + gap


# --- 场景底板：真实草地 + 角色 + 障碍物，用作 HUD 的对比底 -------------------
def scene(blur=0.0, wind_streaks=False):
    base = Image.open(SPRITES / "bg" / "ground_meadow.png").convert("RGBA")
    base = base.resize((W, H), Image.LANCZOS)
    p = Painter(base)
    if wind_streaks:
        streaks(p)
    p.image(_sprite("ui/player_shadow.png", (83, 31)), (600, 443), anchor="mb")
    p.image(_sprite("player/player_e_idle.png", (125, 135)), (600, 445), anchor="mb")
    for name, pos in (
        ("obstacles/obstacle_small_1.png", (250, 330)),
        ("obstacles/obstacle_medium_1.png", (800, 640)),
        ("obstacles/obstacle_large_0.png", (1000, 250)),
    ):
        path = SPRITES / name
        if path.is_file():
            img = Image.open(path).convert("RGBA")
            p.image(img, pos, anchor="mm", shadow=(22, 46, 35, 70))
    base = p.base
    if blur:
        base = base.filter(ImageFilter.GaussianBlur(blur))
    return base


def _sprite(name, size):
    return Image.open(SPRITES / name).convert("RGBA").resize(size, Image.LANCZOS)


def hearts(p, x, y, count=3, total=3, shadow=False):
    """只画心，不再跟一个重复的 3/3 数字。"""
    for i in range(total):
        full = i < count
        img = _sprite("ui/heart_full.png" if full else "ui/heart_empty.png",
                      (HEART_SIZE, HEART_SIZE))
        if not full:
            img.putalpha(img.getchannel("A").point(lambda v: int(v * 0.55)))
        p.image(img, (x + i * (HEART_SIZE + HEART_GAP), y),
                anchor="la", shadow=(22, 46, 35, 80) if shadow else None)


def pause_chip(p, variant):
    box = (MARGIN, MARGIN, MARGIN + PAUSE_SIZE, MARGIN + PAUSE_SIZE)
    bar_w, bar_h, gap = 6, 19, 10
    total = bar_w * 2 + gap
    x0 = box[0] + (PAUSE_SIZE - total) // 2
    y0 = box[1] + (PAUSE_SIZE - bar_h) // 2
    if variant == "b":
        # 无卡片：白色竖杠 + 深色描边，靠投影拉开对比
        layer = Image.new("RGBA", p.base.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for x in (x0, x0 + bar_w + gap):
            d.rounded_rectangle((x - 2, y0 - 2, x + bar_w + 2, y0 + bar_h + 2),
                                radius=4, fill=WHITE_TEXT,
                                outline=STROKE, width=2)
        layer = layer.filter(ImageFilter.GaussianBlur(0.4))
        p._commit(layer)
        return
    p.card(box, radius=14)
    for x in (x0, x0 + bar_w + gap):
        p.rounded((x, y0, x + bar_w, y0 + bar_h), radius=2, fill=INK)


def timer_chip(p, variant):
    stroke = 3 if variant == "b" else 0
    fill = WHITE_TEXT if variant == "b" else INK
    stroke_fill = STROKE if variant == "b" else None
    parts = [
        ("存活", font(17), fill, stroke, stroke_fill),
        ("12.4", mono(26, True), fill, stroke, stroke_fill),
        ("s", font(17), fill, stroke, stroke_fill),
    ]
    if variant == "b":
        # 无卡片版：右对齐，靠描边压住草地
        mixed_text(p, (W - MARGIN, TIMER_Y + 20), parts, anchor="rm")
        return
    box_w, box_h = 168, 45
    box = (W - MARGIN - box_w, MARGIN, W - MARGIN, MARGIN + box_h)
    p.card(box, radius=15)
    # 等宽数字：秒数跳动时宽度不变，不会左右抖
    mixed_text(p, ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2), parts, anchor="mm")


def wind_pill(p, direction_text="西北风", speed=180):
    """方案 A：把风向收进一枚药丸卡，中央不再单独压一行字。"""
    cy = 74
    box = (W // 2 - 180, cy - 27, W // 2 + 180, cy + 27)
    p.card(box, radius=27)
    px, py = -0.707, -0.707
    ax, ay = box[0] + 38, cy + 13
    arrow(p, ax, ay, ax + px * 44, ay + py * 44, INK, width=3, head=10)
    p.text((box[0] + 100, cy), "%s · 风速 %d" % (direction_text, speed),
           font(NORMAL_PX, True), INK, anchor="lm")


def wind_stranded(p, direction_text="西北风", speed=180):
    """方案 B：沿用原来的位置，但白字 + 深描边，不加任何底板。"""
    cy_arrow, cy_text = 72, 115
    px, py = -0.707, -0.707
    ax, ay = W // 2, cy_arrow
    arrow(p, ax, ay, ax + px * 55, ay + py * 55, WHITE_TEXT, width=3, head=11)
    p.text((W // 2, cy_text), "%s · 风速 %d" % (direction_text, speed),
           font(NORMAL_PX), WHITE_TEXT, anchor="mm", stroke=3, stroke_fill=STROKE)


def wind_dial(p, direction_text="西北风", speed=180, cx=None, cy=152, r=38):
    """方案 C：右上角罗盘，箭头指向“吹向”；八个刻度对应八向风。"""
    cx = cx if cx is not None else W - MARGIN - r
    box = (cx - r, cy - r, cx + r, cy + r)
    p.card(box, radius=r, fill=CARD, shadow=SHADOW, blur=8, dy=2)
    for i in range(8):
        ang = -math.pi / 2 + i * math.pi / 4
        inner, outer = r - 9, r - 4
        p.line(
            [(cx + inner * math.cos(ang), cy + inner * math.sin(ang)),
             (cx + outer * math.cos(ang), cy + outer * math.sin(ang))],
            fill=(150, 172, 160, 190), width=2,
        )
    ax = cx - 0.707 * 8
    ay = cy - 0.707 * 8
    arrow(p, ax, ay, cx - 0.707 * (r - 12), cy - 0.707 * (r - 12), ACCENT_WARM,
          width=4, head=12)
    p.text((cx, cy + r + 12), "%s %d" % (direction_text, speed),
           font(SMALL_PX, True), WHITE_TEXT, anchor="mm", stroke=3, stroke_fill=STROKE)


def streaks(p, direction=(-0.707, -0.707), count=14, seed=7):
    """风迹线：极淡的短线，用来“让风吹过画面”，不能抢角色。"""
    rng = random.Random(seed)
    for _ in range(count):
        x = rng.uniform(40, W - 40)
        y = rng.uniform(170, H - 60)
        length = rng.uniform(70, 170)
        alpha = rng.randint(26, 62)
        p.line(
            [(x, y), (x + direction[0] * length, y + direction[1] * length)],
            fill=(255, 255, 255, alpha), width=2,
        )


def caption(base, title, note="评审图 · 未改动游戏代码"):
    """在画面下方加一条说明条，不遮挡游戏区。"""
    canvas = Image.new("RGBA", (W, H + CAPTION_H), (246, 249, 247, 255))
    canvas.alpha_composite(base, (0, 0))
    p = Painter(canvas)
    p.line([(0, H), (W, H)], fill=(222, 232, 226, 255), width=1)
    p.text((24, H + CAPTION_H // 2), title, font(19, True), INK, anchor="lm")
    p.text((W - 24, H + CAPTION_H // 2), note, font(14),
           (140, 156, 148, 255), anchor="rm")
    return p.base.convert("RGB")


# --- 三个 HUD 方案 ----------------------------------------------------------
def option_a():
    p = Painter(scene())
    pause_chip(p, "a")
    hearts(p, MARGIN + PAUSE_SIZE + 17, MARGIN + 3)
    wind_pill(p)
    timer_chip(p, "a")
    return caption(p.base, "方案 A · 白卡圆角：信息分块，去掉重复的 3/3")


def option_b():
    p = Painter(scene())
    pause_chip(p, "b")
    hearts(p, MARGIN + PAUSE_SIZE + 17, MARGIN + 3, shadow=True)
    wind_stranded(p)
    timer_chip(p, "b")
    return caption(p.base, "方案 B · 无底纹极简：零装饰，白字 + 深色描边保证对比")


def option_c():
    p = Painter(scene(wind_streaks=True))
    pause_chip(p, "a")
    hearts(p, MARGIN + PAUSE_SIZE + 17, MARGIN + 3)
    timer_chip(p, "a")
    wind_dial(p)
    # 上帝模式是“玩家状态”，跟生命值放一起比挤在右上角干净
    p.text((MARGIN, 98), "★ 上帝模式 · 按 G 关闭", font(SMALL_PX), ACCENT_WARM,
           anchor="lm", stroke=3, stroke_fill=STROKE)
    return caption(p.base, "方案 C · 风向盘 + 风迹线：中央彻底留白，风变成环境")


# --- 暂停面板：浅色化 + 柔和压暗（替代 gray50 点阵） ------------------------
def pause_light():
    base = scene()
    blurred = base.filter(ImageFilter.GaussianBlur(7))
    veil = Image.new("RGBA", (W, H), (24, 44, 34, 128))
    base = Image.alpha_composite(blurred, veil)
    p = Painter(base)

    panel = (336, 168, 864, 672)
    p.card(panel, radius=26, fill=(252, 253, 252, 244), shadow=(16, 34, 26, 90),
           blur=18, dy=4)

    p.text((W // 2, 228), "游戏暂停", font(BIG_PX, True), INK, anchor="mm")

    items = (
        ("继续游戏", True),
        ("重新开始", False),
        ("设置", False),
        ("退出游戏", False),
    )
    # 按钮按“中心 y”排，节奏比现状拉开一点（现状 4 个按钮挤到面板底部）
    y = 318
    for label, primary in items:
        box = (432, y - 31, 768, y + 31)
        if primary:
            p.card(box, radius=13, fill=ACCENT, shadow=(30, 110, 84, 70), blur=8, dy=3)
            p.text((W // 2, y), label, font(23, True), (255, 255, 255, 255),
                   anchor="mm")
        else:
            p.rounded(box, radius=13, fill=(243, 247, 245, 255),
                      outline=(219, 231, 224, 255), width=2)
            p.text((W // 2, y), label, font(23), INK, anchor="mm")
        y += 62 + 20

    p.text((W // 2, 636), "Esc 继续游戏 · R 重新开始", font(16),
           (128, 146, 137, 255), anchor="mm")
    return caption(p.base, "暂停面板 · 浅色化 + 柔和压暗（取代 gray50 点阵）")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = (
        ("ui_hud_option_a.png", option_a),
        ("ui_hud_option_b.png", option_b),
        ("ui_hud_option_c.png", option_c),
        ("ui_pause_light.png", pause_light),
    )
    for name, fn in jobs:
        path = OUT / name
        fn().save(path)
        print("写入", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
