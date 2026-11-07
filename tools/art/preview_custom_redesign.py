# -*- coding: utf-8 -*-
"""自定义界面改版效果预览（只出图，不改游戏代码）。

沿用暂停 / 结束遮罩的视觉语言：
    模糊暗底 + 蜡笔白字 + 白描边镂空按钮 + 暖纸色选中态
把它套到信息量更大的自定义页上：左侧分类，右侧槽位 / 列表 / 操作区。

    python tools/art/preview_custom_redesign.py

输出 art_preview/custom_redesign_mock.png（1200x840）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "art"))

import crayon_kit as kit  # noqa: E402

W, H = 1200, 840
S = 2  # 超采样：所有坐标先放大 2 倍，最后缩回 1200x840
kit.OUT = float(S)

WHITE = (255, 255, 255)
INK = kit.INK
WARM = (255, 246, 224)
RED = kit.RED
VEIL = (16, 38, 28, 0)

BACKDROP = ROOT / "assets" / "sprites" / "ui" / "start_cover.png"
STAR = ROOT / "assets" / "sprites" / "ui" / "crayon" / "star.png"
OUT = ROOT / "art_preview" / "custom_redesign_mock.png"


# ---------------------------------------------------------------- 基础工具
def alpha(img, value):
    if value >= 1.0:
        return img
    out = img.copy()
    out.putalpha(out.getchannel("A").point(lambda v: int(v * value)))
    return out


def text(parts, seed, fill=WHITE, outline=INK, outline_px=1.8,
         strength=0.8, bold_px=None):
    if bold_px:
        img = kit.bold_text_png(
            parts, seed=seed, fill=fill, outline=outline,
            outline_px=outline_px, strength=strength, bold_px=bold_px)
    else:
        img = kit.text_png(
            parts, seed=seed, fill=fill, outline=outline,
            outline_px=outline_px, strength=strength)
    return img


def paste_center(ui, img, cx, cy, opacity=1.0):
    img = alpha(img, opacity)
    x = round(cx * S - img.width / 2.0)
    y = round(cy * S - img.height / 2.0)
    ui.alpha_composite(img, (x, y))


def paste_left(ui, img, x, cy, opacity=1.0):
    img = alpha(img, opacity)
    ui.alpha_composite(
        img, (round(x * S), round(cy * S - img.height / 2.0)))


def paste_right(ui, img, x, cy, opacity=1.0):
    img = alpha(img, opacity)
    ui.alpha_composite(
        img, (round(x * S - img.width), round(cy * S - img.height / 2.0)))


def rrect(draw, x1, y1, x2, y2, radius, fill=None, outline=None, width=1.0):
    draw.rounded_rectangle(
        [round(x1 * S), round(y1 * S), round(x2 * S), round(y2 * S)],
        radius=round(radius * S), fill=fill, outline=outline,
        width=max(1, round(width * S)))


def button(draw, ui, x1, x2, cy, h, label, seed, primary=False,
           disabled=False, font_px=22):
    y1, y2 = cy - h / 2.0, cy + h / 2.0
    if primary:
        fill = WARM + (238,)
        outline = (255, 255, 255, 245)
        ink = INK
        width = 2.6
    elif disabled:
        fill = (255, 255, 255, 6)
        outline = (255, 255, 255, 90)
        ink = WHITE
        width = 2.0
    else:
        fill = (255, 255, 255, 12)
        outline = (255, 255, 255, 210)
        ink = WHITE
        width = 2.2
    rrect(draw, x1, y1, x2, y2, 12, fill=fill, outline=outline, width=width)
    img = text([(label, kit.FONT_CJK, font_px)], seed,
               fill=ink, outline=None if primary else INK,
               outline_px=0 if primary else 1.6, strength=0.72)
    paste_center(ui, img, (x1 + x2) / 2.0, cy,
                 opacity=0.45 if disabled else 1.0)


def checkbox(draw, cx, cy, checked, hover):
    box = 12
    x1, y1, x2, y2 = cx - box, cy - box, cx + box, cy + box
    if checked:
        rrect(draw, x1, y1, x2, y2, 5, fill=WARM + (232,),
              outline=(255, 255, 255, 235), width=2.0)
        ink = (34, 54, 45, 255)
        pts = [(cx - 6.2, cy + 0.6), (cx - 1.6, cy + 5.0), (cx + 6.4, cy - 5.4)]
        draw.line([(round(x * S), round(y * S)) for x, y in pts],
                  fill=ink, width=round(3.2 * S), joint="curve")
    else:
        rrect(draw, x1, y1, x2, y2, 5, fill=(255, 255, 255, 10),
              outline=(255, 255, 255, 175), width=2.0)


def play_icon(draw, cx, cy, hover):
    r = 15
    draw.ellipse(
        [round((cx - r) * S), round((cy - r) * S),
         round((cx + r) * S), round((cy + r) * S)],
        outline=(255, 255, 255, 240 if hover else 195), width=round(2.0 * S))
    tri = [(cx - 4.2, cy - 6.4), (cx - 4.2, cy + 6.4), (cx + 6.8, cy)]
    draw.polygon([(round(x * S), round(y * S)) for x, y in tri],
                 fill=(255, 255, 255, 250 if hover else 220))


# ---------------------------------------------------------------- 画面
def build_backdrop():
    shot = Image.open(BACKDROP).convert("RGB").resize((W * S, H * S), Image.LANCZOS)
    shot = shot.filter(ImageFilter.GaussianBlur(10 * S))
    base = shot.convert("RGBA")
    veil = Image.new("RGBA", base.size, (16, 38, 28, 158))
    return Image.alpha_composite(base, veil)


def main():
    base = build_backdrop()
    ui = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(ui)

    # ---------------- 左侧分类 ----------------
    paste_left(ui, text([("分类", kit.FONT_CJK, 17)], 201),
               84, 194, opacity=0.72)
    nav = (
        ("音效", True, False),
        ("背景", False, False),
        ("人物（即将）", False, True),
    )
    for index, (label, selected, disabled) in enumerate(nav):
        cy = 258 + index * 68
        button(draw, ui, 80, 300, cy, 58, label, 210 + index,
               primary=selected, disabled=disabled)
    button(draw, ui, 80, 300, 756, 58, "返回主菜单", 216, font_px=21)

    # ---------------- 标题 ----------------
    title = text([("自定义", kit.FONT_CJK, 72)], 220,
                 fill=WHITE, outline=(24, 20, 18), outline_px=2.6,
                 strength=0.42, bold_px=0.45)
    paste_center(ui, title, W / 2.0, 92)
    subtitle = text([("游戏资源 · 选好后点「应用」生效", kit.FONT_CJK, 19)], 221)
    paste_center(ui, subtitle, W / 2.0, 148, opacity=0.92)

    # ---------------- 右侧标题 ----------------
    star = Image.open(STAR).convert("RGBA")
    star = star.resize((round(24 * S), round(24 * S)), Image.LANCZOS)
    paste_center(ui, star, 356, 213, opacity=0.95)
    header = text([("音效", kit.FONT_CJK, 40)], 222,
                  fill=WHITE, outline=(24, 20, 18), outline_px=1.8,
                  strength=0.55)
    paste_left(ui, header, 378, 213)
    paste_right(ui, text([("8 条可用", kit.FONT_CJK, 17)], 223),
                1120, 216, opacity=0.78)

    paste_left(ui, text(
        [("选择游戏内实际播放的音效；背景音乐可多选随机播放。", kit.FONT_CJK, 18)],
        224), 344, 258, opacity=0.88)

    # ---------------- 槽位 ----------------
    slots = (
        ("受击", 100, True),
        ("风向改变", 140, False),
        ("游戏结束", 140, False),
        ("拾取道具", 140, False),
        ("背景音乐", 140, False),
    )
    x = 344
    for index, (label, width, selected) in enumerate(slots):
        x2 = x + width
        if selected:
            rrect(draw, x, 284, x2, 332, 11, fill=WARM + (232,),
                  outline=(255, 255, 255, 240), width=2.2)
            img = text([(label, kit.FONT_CJK, 19)], 230 + index,
                       fill=INK, outline=None, outline_px=0, strength=0.72)
        else:
            rrect(draw, x, 284, x2, 332, 11, fill=(255, 255, 255, 10),
                  outline=(255, 255, 255, 200), width=2.0)
            img = text([(label, kit.FONT_CJK, 19)], 230 + index,
                       outline_px=1.5, strength=0.75)
        paste_center(ui, img, (x + x2) / 2.0, 308)
        x = x2 + 12

    paste_left(ui, text(
        [("可多选（已选 5 / 最多 8 条），游戏中随机播放。", kit.FONT_CJK, 16)],
        240), 344, 352, opacity=0.82)

    # ---------------- 音效列表 ----------------
    items = (
        ("内置 · 唉", "0.48s", True),
        ("内置 · 呃啊1", "0.58s", True),
        ("内置 · 哎呀1", "0.63s", True),
        ("内置 · 呃啊2", "0.37s", True),
        ("内置 · 啊", "0.35s", True),
        ("内置 · 呃啊3", "0.55s", False),
    )
    hover_index = 2
    for index, (name, duration, checked) in enumerate(items):
        cy = 392 + index * 60
        y1, y2 = cy - 26, cy + 26
        hover = index == hover_index
        rrect(
            draw, 344, y1, 1120, y2, 13,
            fill=(255, 255, 255, 34 if hover else 14),
            outline=(255, 255, 255, 245 if hover else 150),
            width=2.4 if hover else 1.8,
        )
        checkbox(draw, 372, cy, checked, hover)
        label = text([(name, kit.FONT_CJK, 20)], 250 + index,
                     outline_px=1.5, strength=0.75)
        paste_left(ui, label, 400, cy, opacity=1.0 if hover else 0.95)
        paste_right(ui, text([(duration, kit.FONT_LATIN, 20)], 260 + index,
                               outline_px=1.4, strength=0.7),
                    1032, cy, opacity=0.85)
        play_icon(draw, 1076, cy, hover)

    # 细滚动条：提示列表还可往下翻
    rrect(draw, 1130, 366, 1138, 718, 4, fill=(255, 255, 255, 34))
    rrect(draw, 1130, 366, 1138, 506, 4, fill=(255, 255, 255, 110))

    # ---------------- 底部操作 ----------------
    paste_left(ui, text(
        [("已选 5 条 · 点「应用」生效", kit.FONT_CJK, 15)], 270),
        344, 756, opacity=0.8)
    button(draw, ui, 618, 758, 756, 50, "导入音频…", 280, font_px=20)
    button(draw, ui, 768, 856, 756, 50, "刷新", 281, font_px=20)
    button(draw, ui, 866, 998, 756, 50, "恢复默认", 282, font_px=20)
    button(draw, ui, 1008, 1120, 756, 50, "应用", 283, primary=True,
           font_px=21)
    paste_center(ui, text(
        [("Esc 返回主菜单 · 空格 试听选中音效", kit.FONT_CJK, 18)], 290),
        W / 2.0, 808, opacity=0.9)

    composed = Image.alpha_composite(base, ui)
    final = composed.resize((W, H), Image.LANCZOS).convert("RGB")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    final.save(OUT)
    print("saved", OUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
