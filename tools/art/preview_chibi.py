# -*- coding: utf-8 -*-
"""临时预览：8 方向 × 待机/走路两帧的 Q 版占位角色。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import chibi  # noqa: E402

DIRS = ("e", "se", "s", "sw", "w", "nw", "n", "ne")
FRAMES = ("idle", "walk0", "walk1")
SCALE = 1.6
CELL_W, CELL_H = int(96 * SCALE), int(104 * SCALE)

cols = len(DIRS)
rows = len(FRAMES)
sheet = Image.new("RGBA", (cols * CELL_W + 40, rows * CELL_H + 90), (15, 23, 32, 255))
d = ImageDraw.Draw(sheet)
font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 16)
title = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 20)
d.text((20, 16), "占位角色：8 方向 × 待机/走路（1.6x）", font=title, fill=(230, 237, 243))

for row, frame in enumerate(FRAMES):
    for col, direction in enumerate(DIRS):
        img = chibi.render_chibi(direction, frame)
        img = img.resize((CELL_W, CELL_H), Image.LANCZOS)
        sheet.alpha_composite(img, (20 + col * CELL_W, 60 + row * CELL_H))
        if row == 0:
            d.text((20 + col * CELL_W + 30, 42), direction, font=font, fill=(139, 152, 165))
    d.text((20, 62 + row * CELL_H), frame, font=font, fill=(139, 152, 165))

out = Path(__file__).resolve().parents[2] / "art_preview" / "chibi_preview.png"
sheet.convert("RGB").save(out)
print("saved", out, sheet.size)
