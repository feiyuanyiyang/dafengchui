# -*- coding: utf-8 -*-
"""评审图：用烘焙好的 PNG 拼出游戏内最终效果（不跑游戏、不改 game.py）。

    python tools/art/preview_crayon_hud.py

输出：
    art_preview/ui_hud_final.png      用真实烘焙资源合成的 HUD
    art_preview/ui_crayon_assets.png  烘焙清单总览（符号 / 文字 / 按钮）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from preview_hud_style import Painter, caption, scene  # noqa: E402

ASSET_DIR = ROOT / "assets" / "sprites" / "ui" / "crayon"
MANIFEST = json.loads((ASSET_DIR / "manifest.json").read_text(encoding="utf-8"))
CACHE = {}

MARGIN = 29
W, H = 1200, 840


def img(name):
    if name not in CACHE:
        CACHE[name] = Image.open(ASSET_DIR / MANIFEST["assets"][name]["file"]).convert("RGBA")
    return CACHE[name]


def put(base, name, xy, anchor="mm"):
    im = img(name)
    x, y = xy
    if anchor[0] == "m":
        x -= im.width / 2
    elif anchor[0] == "r":
        x -= im.width
    if anchor[1] == "m":
        y -= im.height / 2
    elif anchor[1] == "b":
        y -= im.height
    base.alpha_composite(im, (int(x), int(y)))
    return im.width


def hud_scene():
    """HUD 单独一层（不含底部说明条），demo 与本预览共用。"""
    base = scene().convert("RGBA")

    # 左上：暂停 + 三颗手绘心
    put(base, "pause", (MARGIN + 20, MARGIN + 20))
    for i in range(3):
        put(base, "heart_full", (MARGIN + 41 + 17 + 21 + i * 42, MARGIN + 20))

    # 右上：存活 12.4 s —— 中文标签 + 等宽手写数字
    text = [("timer_label", 12), ("digit_1", 0), ("digit_2", 0), ("digit_dot", 0),
            ("digit_4", 2), ("digit_s", 9)]
    widths = [img(n).width + gap for n, gap in text]
    x = W - MARGIN - sum(widths)
    for (name, gap), w in zip(text, widths):
        im = img("btn_continue_light" if i == 0 else name)
        base.alpha_composite(im, (int(x), int(49 - im.height / 2)))
        x += w

    # 右上偏下：罗盘（底盘 + 红色指针）
    cx, cy = W - MARGIN - 48, 176
    put(base, "compass_base", (cx, cy))
    put(base, "compass_arrow_nw", (cx, cy))

    # 罗盘下方：风向名（速度用同一套等宽数字，这里只示意）
    put(base, "wind_nw", (cx, cy + 62))

    # 上帝模式徽标：归到“玩家状态”一侧
    put(base, "god_badge", (MARGIN, 104), anchor="lm")

    # 底部操作提示
    put(base, "hint", (W // 2, H - 31))

    return base


def hud_final():
    return caption(hud_scene(),
                   "最终效果 · 站酷快乐体 + 80% 蜡笔 + 白盘红针罗盘（烘焙 PNG）")


def rounded_frame(size, radius=16, width=3, color=(255, 255, 255, 235), ss=4):
    """白色圆角长条边框，内部完全透明（贴到模糊背景上就是镂空）。"""
    from PIL import ImageDraw

    w, h = size
    im = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle(
        (width * ss // 2, width * ss // 2, w * ss - width * ss // 2, h * ss - width * ss // 2),
        radius=radius * ss, outline=color, width=width * ss)
    return im.resize((w, h), Image.LANCZOS)


def pause_final():
    """无面板暂停：模糊背景上直接浮白色圆角边框 + 白字。"""
    from PIL import ImageFilter

    bg = scene().convert("RGB").filter(ImageFilter.GaussianBlur(16))
    veil = Image.new("RGBA", bg.size, (16, 38, 28, 112))
    base = Image.alpha_composite(bg.convert("RGBA"), veil)

    put(base, "pause_title", (W // 2, 252))

    labels = ("btn_continue", "btn_restart", "btn_settings", "btn_quit")
    for i, name in enumerate(labels):
        cy = 378 + i * 90
        frame = rounded_frame((420, 74))
        base.alpha_composite(frame, (W // 2 - 210, cy - 37))
        put(base, name, (W // 2, cy))

    put(base, "pause_hint", (W // 2, 736))
    return caption(base, "暂停 · 无面板 · 白色圆角镂空按钮 · 黑描边白字标题")


def contact_sheet():
    """资源清单总览：直接用 ImageDraw，不再经过 Painter（避免复制画布丢层）。"""
    from PIL import ImageDraw

    from preview_hud_style import font as sysfont

    INK = (34, 54, 45)
    DIM = (132, 146, 136)
    canvas = Image.new("RGB", (1200, 900), (250, 247, 240))
    d = ImageDraw.Draw(canvas)
    total = len(MANIFEST["assets"])

    d.text((40, 30), "烘焙资源清单 · assets/sprites/ui/crayon/",
           font=sysfont(24, True), fill=INK, anchor="lm")
    d.text((40, 64),
           "%d 个 PNG，全部为最终像素（含描边/颗粒），游戏侧只贴图、不做叠加" % total,
           font=sysfont(14), fill=DIM, anchor="lm")

    # 罗盘
    y = 120
    d.text((40, y), "罗盘 96×96 · 白盘灰刻度 + 红色指针（八向各一张，同坐标系）",
           font=sysfont(15, True), fill=INK, anchor="lm")
    base_img = img("compass_base")
    canvas.paste(base_img, (60, 140), base_img)
    for i, name in enumerate(("n", "ne", "e", "se", "s", "sw", "w", "nw")):
        im = img("compass_arrow_%s" % name)
        x = 200 + i * 92
        canvas.paste(im, (x, 140), im)
        d.text((x + 48, 254), name.upper(), font=sysfont(12), fill=DIM, anchor="mm")

    # 符号
    y = 300
    d.text((40, y), "符号", font=sysfont(15, True), fill=INK, anchor="lm")
    for i, (name, label) in enumerate((("heart_full", "心·满"), ("heart_empty", "心·空"),
                                       ("pause", "暂停"), ("star", "星"))):
        im = img(name)
        cx = 90 + i * 95
        canvas.paste(im, (int(cx - im.width / 2), y + 26), im)
        d.text((cx, y + 72), label, font=sysfont(12), fill=DIM, anchor="mm")

    # 计时数字
    d.text((470, y), "计时数字 · 固定格子保证等宽（小数点压基线）",
           font=sysfont(15, True), fill=INK, anchor="lm")
    x = 470
    for name in ["digit_%d" % i for i in range(10)] + ["digit_dot", "digit_s"]:
        im = img(name)
        canvas.paste(im, (x, y + 20), im)
        x += im.width

    # 文字
    y = 420
    d.text((40, y), "文字", font=sysfont(15, True), fill=INK, anchor="lm")
    canvas.paste(img("timer_label"), (60, y + 32), img("timer_label"))
    d.text((60, y + 88), "计时标签 22px", font=sysfont(12), fill=DIM, anchor="lm")

    d.text((240, y + 20), "八向风名 24px", font=sysfont(12), fill=DIM, anchor="lm")
    x = 240
    for name in ("e", "se", "s", "sw", "w", "nw"):
        im = img("wind_%s" % name)
        canvas.paste(im, (x, y + 40), im)
        x += im.width + 12

    im = img("god_badge")
    canvas.paste(im, (60, y + 118), im)
    d.text((60, y + 150), "上帝模式徽标（红五角星是画的，字体里没有 ★）",
           font=sysfont(12), fill=DIM, anchor="lm")

    # 暂停界面：白描边镂空按钮 + 黑描边白字标题
    y = 612
    d.text((40, y), "暂停界面 · 白色圆角镂空按钮 + 黑描边白字标题",
           font=sysfont(15, True), fill=INK, anchor="lm")
    title = img("pause_title")
    canvas.paste(title, (60, y + 24), title)

    strip_w, strip_h = 1120, 130
    strip = Image.new("RGBA", (strip_w, strip_h), (26, 50, 36, 255))
    sq = Image.new("RGBA", (strip_w, strip_h), (16, 38, 28, 120))
    strip = Image.alpha_composite(strip, sq)
    for i, name in enumerate(("btn_continue", "btn_restart")):
        ox = 36 + i * 436
        strip.alpha_composite(rounded_frame((420, 74)), (ox, 28))
        im = img(name)
        strip.alpha_composite(im, (ox + 210 - im.width // 2, 28 + 37 - im.height // 2))
    canvas.paste(strip, (40, y + 104), strip)

    d.text((40, 872),
           "字体：站酷快乐体（中文）/ Patrick Hand（数字）· 均 OFL 可商用 · 质感强度 80%",
           font=sysfont(13), fill=DIM, anchor="lm")
    return canvas


def main():
    out = ROOT / "art_preview"
    hud_final().save(out / "ui_hud_final.png")
    pause_final().save(out / "ui_pause_final.png")
    contact_sheet().save(out / "ui_crayon_assets.png")
    print("write art_preview/ui_hud_final.png")
    print("write art_preview/ui_pause_final.png")
    print("write art_preview/ui_crayon_assets.png")


if __name__ == "__main__":
    main()
