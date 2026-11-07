# -*- coding: utf-8 -*-
"""障碍物贴图风格表（开发评审用，不进游戏）。

    python tools/art/obstacle_styles_preview.py

生成 art_preview/obstacle_styles.png：旧版柔光球 -> 新版“风卷杂物”的对照，
以及 1× / 4× 实际尺寸和角色同框比例。材质实现只依赖 obstacle_art.py，
避免预览与实际烘焙各画一套。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from obstacle_art import obstacle_sprite_size, render_obstacle

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "art_preview" / "obstacle_styles.png"
PLAYER = ROOT / "assets" / "sprites" / "player" / "player_e_idle.png"

BG = (15, 23, 32)
PANEL = (19, 30, 41)
TEXT = (230, 237, 243)
DIM = (139, 152, 165)
SS = 8

OLD_TIERS = (("small", 7.0), ("medium", 12.0), ("large", 18.0))
TIERS = (("small", 11.0), ("medium", 20.0), ("large", 31.5))
NAMES = {
    "small": ("树叶", "纸片", "羽毛"),
    "medium": ("松果", "碎石", "纸团"),
    "large": ("树枝", "木板", "石块"),
}
OLD_COLORS = {"small": (255, 209, 102), "medium": (247, 140, 107), "large": (239, 71, 111)}


def font(size, bold=False):
    return ImageFont.truetype("C:/Windows/Fonts/" + ("msyhbd.ttc" if bold else "msyh.ttc"), size)


def downsample(img, logical):
    return img.resize((int(round(logical)), int(round(logical))), Image.LANCZOS)


def old_ball(radius, color):
    """旧版障碍物的样子：柔光球，仅用于对照。"""
    size = obstacle_sprite_size(radius)
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c, r = s / 2, radius * SS
    halo = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    hd.ellipse(
        [c - r * 1.5, c - r * 1.5, c + r * 1.5, c + r * 1.5],
        fill=color + (60,),
    )
    halo = halo.filter(ImageFilter.GaussianBlur(r * 0.45))
    img.alpha_composite(halo)
    d.ellipse([c - r, c - r, c + r, c + r], fill=color + (255,), outline=(10, 17, 24, 255), width=max(2, int(r * 0.1)))
    d.ellipse([c - r * 0.55, c - r * 0.6, c + r * 0.05, c - r * 0.05], fill=(255, 255, 255, 80))
    return downsample(img, size)


def sheet():
    W, H = 1180, 1120
    canvas = Image.new("RGBA", (W, H), BG + (255,))
    d = ImageDraw.Draw(canvas, "RGBA")
    f_title, f_head, f_body, f_small = font(30, True), font(21, True), font(15), font(13)

    d.text((40, 28), "障碍物贴图：旧版柔光球 → 新版风卷杂物", fill=TEXT, font=f_title)
    d.text((40, 68), "碰撞判定仍用原来的圆；材质只换视觉，树叶/纸片/羽毛 = 轻，松果/碎石/纸团 = 中，树枝/木板/石块 = 重", fill=DIM, font=f_body)

    # 旧版
    y = 110
    d.text((40, y), "旧版：柔光球（三档只靠颜色和大小区分）", fill=DIM, font=f_head)
    x = 40
    for name, radius in OLD_TIERS:
        img = old_ball(radius, OLD_COLORS[name])
        canvas.alpha_composite(img, (x, y + 40))
        x += img.width + 18
    x += 30
    for name, radius in OLD_TIERS:
        img = old_ball(radius, OLD_COLORS[name])
        canvas.alpha_composite(img.resize((img.width * 3, img.height * 3), Image.LANCZOS), (x, y + 40))
        x += img.width * 3 + 20
    d.text((40, y + 104), "1× 实际尺寸", fill=DIM, font=f_small)

    # 新版
    y = 330
    d.text((40, y), "新版：靠剪影和材质分辨轻重（颜色只保留一层很淡的档位光）", fill=TEXT, font=f_head)
    y += 34
    for tier, radius in TIERS:
        size = obstacle_sprite_size(radius)
        d.text((40, y + 6), "%s r=%.1f" % (tier, radius), fill=DIM, font=f_small)
        x = 120
        for variant in range(3):
            img = downsample(render_obstacle(tier, variant, radius, size), size)
            canvas.alpha_composite(img, (x, y))
            d.text((x, y + size + 4), NAMES[tier][variant], fill=DIM, font=f_small)
            x += size + 26
        x = 360
        for variant in range(3):
            zoom = downsample(render_obstacle(tier, variant, radius, size), size * 2)
            canvas.alpha_composite(zoom, (x, y - 8))
            x += size * 2 + 24
        y += size * 2 + 26

    # 角色同框
    scene_y = 820
    d.rectangle([40, scene_y, W - 40, H - 34], fill=PANEL)
    d.text((56, scene_y + 10), "同框比例（1×，角色 125×135）", fill=DIM, font=f_body)
    player = Image.open(PLAYER).convert("RGBA").resize((125, 135), Image.LANCZOS)
    canvas.alpha_composite(player, (86, scene_y + 56))
    slots = [
        (240, scene_y + 70), (310, scene_y + 100), (380, scene_y + 56),
        (470, scene_y + 104), (540, scene_y + 64), (650, scene_y + 96),
        (760, scene_y + 58), (840, scene_y + 100), (940, scene_y + 66),
    ]
    index = 0
    for tier, radius in TIERS:
        size = obstacle_sprite_size(radius)
        for variant in range(3):
            img = downsample(render_obstacle(tier, variant, radius, size), size)
            ox, oy = slots[index]
            canvas.alpha_composite(img, (ox, oy))
            index += 1

    canvas.convert("RGB").save(OUT)
    print("saved", OUT)


if __name__ == "__main__":
    sheet()
