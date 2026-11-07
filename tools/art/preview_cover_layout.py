# -*- coding: utf-8 -*-
"""开发用：在没有图像输入的环境里核对开始界面/自定义界面的最终排版。

    python tools/art/preview_cover_layout.py

按 game.py 里的布局常量（标题 y=286、按钮 454/548/642、提示 y=790）把
封面背景 + 文字 + 按钮画出来，输出 art_preview/cover_start_mock.png /
cover_custom_mock.png，用来目视检查（真正运行游戏时由 Tk 绘制）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "assets" / "sprites" / "ui"
OUT = ROOT / "art_preview"

W, H = 1200, 840

# 与 game.py 一致的布局
TITLE_Y = 286
SUBTITLE_Y = 200
BUTTON_WIDTH = 360
BUTTON_HEIGHT = 72
BUTTON_YS = (454, 548, 642)
HINT_Y = H - 50
TITLE_PX = round(77 * 96 / 72)
SUBTITLE_PX = round(22 * 96 / 72)
NORMAL_PX = round(19 * 96 / 72)
SMALL_PX = round(14 * 96 / 72)

# 与 game.py 一致的开始界面文字色（封面上用深色）
TITLE_FILL = "#12263D"
SUB_FILL = "#16304B"
HINT_FILL = "#E6EDF3"
BUTTON_FILL = "#1B3242"
BUTTON_BORDER = "#4FC3F7"
TEXT_FILL = "#E6EDF3"
CARD_BG = "#0F1720"
PANEL_BG = "#132430"
DIM = "#8B98A5"


def font(size, bold=False):
    candidates = ["msyhbd.ttc" if bold else "msyh.ttc", "msyh.ttf", "simhei.ttf"]
    for name in candidates:
        path = Path("C:/Windows/Fonts") / name
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:
                continue
    return ImageFont.load_default()


def center(draw, xy, text, f, fill):
    draw.text(xy, text, font=f, fill=fill, anchor="mm")


def mock_start():
    image = Image.open(UI / "start_cover.png").convert("RGB")
    draw = ImageDraw.Draw(image)
    center(draw, (W // 2, TITLE_Y), "大风吹", font(TITLE_PX, bold=True), TITLE_FILL)
    center(draw, (W // 2, SUBTITLE_Y), "八向风力 · Q 版角色 · 60 FPS",
           font(SUBTITLE_PX), SUB_FILL)
    for y, label in zip(BUTTON_YS, ("开始游戏", "自定义", "退出")):
        x0 = W // 2 - BUTTON_WIDTH // 2
        draw.rectangle(
            [x0, y - BUTTON_HEIGHT // 2, x0 + BUTTON_WIDTH, y + BUTTON_HEIGHT // 2],
            fill=BUTTON_FILL, outline=BUTTON_BORDER, width=1)
        center(draw, (W // 2, y), label, font(NORMAL_PX), TEXT_FILL)
    center(draw, (W // 2, HINT_Y), "回车 / 空格 开始游戏", font(SMALL_PX), HINT_FILL)
    path = OUT / "cover_start_mock.png"
    image.save(path)
    print("saved", path)


def mock_custom():
    image = Image.open(UI / "custom_backdrop.png").convert("RGB")
    draw = ImageDraw.Draw(image)
    card = (48, 36, W - 48, H - 36)
    draw.rectangle(card, fill=CARD_BG, outline="#223444", width=1)
    draw.rectangle([card[0], card[1], card[0] + 240, card[3]], fill=PANEL_BG)
    draw.text((card[0] + 16, card[1] + 24), "自定义", font=font(32, bold=True), fill=TEXT_FILL)
    draw.text((card[0] + 16, card[1] + 70), "游戏资源", font=font(SMALL_PX), fill=DIM)
    for index, label in enumerate(("音效", "背景", "人物（即将）")):
        y = card[1] + 110 + index * 52
        draw.rectangle([card[0] + 14, y, card[0] + 226, y + 40], fill=BUTTON_FILL)
        draw.text((card[0] + 26, y + 8), label, font=font(NORMAL_PX), fill=TEXT_FILL)
    draw.text((card[0] + 16, card[3] - 50), "返回主菜单",
              font=font(NORMAL_PX), fill=TEXT_FILL)
    content_x = card[0] + 240 + 26
    draw.text((content_x, card[1] + 18), "音效", font=font(37, bold=True), fill=TEXT_FILL)
    draw.text((content_x, card[1] + 78),
              "选择游戏内实际播放的音效；背景音乐可多选随机播放；改动点“应用”后生效。",
              font=font(SMALL_PX), fill=DIM)
    for index, label in enumerate(("受击", "风向", "结束", "拾取", "背景音乐")):
        x = content_x + index * 96
        draw.rectangle([x, card[1] + 118, x + 84, card[1] + 152], fill=BUTTON_FILL)
        draw.text((x + 12, card[1] + 124), label, font=font(SMALL_PX), fill=TEXT_FILL)
    for row in range(5):
        y = card[1] + 190 + row * 54
        draw.rectangle([content_x, y, card[3] - 30, y + 44], fill=PANEL_BG)
        draw.text((content_x + 12, y + 10), "内置音效 %d" % (row + 1),
                  font=font(SMALL_PX), fill=TEXT_FILL)
        draw.text((card[3] - 120, y + 10), "试听", font=font(SMALL_PX), fill=BUTTON_BORDER)
    draw.rectangle([card[3] - 330, card[3] - 56, card[3] - 30, card[3] - 18], fill=PANEL_BG)
    for index, label in enumerate(("导入音频…", "刷新", "恢复默认", "应用")):
        x = card[3] - 320 + index * 78
        draw.text((x, card[3] - 47), label, font=font(SMALL_PX), fill=TEXT_FILL)
    path = OUT / "cover_custom_mock.png"
    image.save(path)
    print("saved", path)


if __name__ == "__main__":
    mock_start()
    mock_custom()
