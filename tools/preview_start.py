# -*- coding: utf-8 -*-
"""开发用：截图开始界面、自定义音效页与背景音乐页，检查布局。

    python tools/preview_start.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tkinter as tk  # noqa: E402

import game  # noqa: E402
from PIL import ImageGrab  # noqa: E402


def main():
    out_dir = ROOT / "art_preview"
    root = tk.Tk()
    app = game.GameApp(root, autostart=False)
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    root.update_idletasks()
    root.update()
    time.sleep(0.3)
    root.update()

    full = ImageGrab.grab()
    scale_x = full.width / float(root.winfo_screenwidth())
    scale_y = full.height / float(root.winfo_screenheight())

    def grab(name):
        root.lift()
        root.attributes("-topmost", True)
        root.update()
        time.sleep(0.45)
        root.update_idletasks()
        root.update()
        x = int(round(root.winfo_rootx() * scale_x))
        y = int(round(root.winfo_rooty() * scale_y))
        w = int(round(root.winfo_width() * scale_x))
        h = int(round(root.winfo_height() * scale_y))
        image = ImageGrab.grab().crop((x, y, x + w, y + h))
        if image.size != (game.WINDOW_WIDTH, game.WINDOW_HEIGHT):
            image = image.resize((game.WINDOW_WIDTH, game.WINDOW_HEIGHT))
        path = out_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        print("saved", path)

    grab("start_screen.png")
    app._on_custom_clicked()
    root.update()
    time.sleep(0.2)
    root.update()
    grab("custom_screen.png")
    app.custom_screen._select_slot("bgm")
    root.update()
    time.sleep(0.2)
    root.update()
    grab("custom_bgm_page.png")
    app.custom_screen._select_category("background")
    root.update()
    time.sleep(0.2)
    root.update()
    grab("custom_background_page.png")
    app.close()


if __name__ == "__main__":
    main()
