# -*- coding: utf-8 -*-
"""开发用：截图新版蜡笔自定义界面的音效页 / 背景音乐页 / 背景页。

    python tools/preview_custom_live.py

输出到 art_preview/custom_live_*.png，不覆盖旧版截图。
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tkinter as tk  # noqa: E402

import game  # noqa: E402
from PIL import ImageGrab  # noqa: E402

OUT_DIR = ROOT / "art_preview"


def main():
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
        path = OUT_DIR / name
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        image.save(path)
        print("saved", path)

    try:
        app._on_custom_clicked()
        root.update()
        time.sleep(0.25)
        root.update()
        grab("custom_live_audio.png")

        app.canvas.event_generate("<Motion>", x=700, y=484)
        root.update()
        grab("custom_live_hover.png")

        app.custom_screen._select_slot("bgm")
        root.update()
        time.sleep(0.2)
        root.update()
        grab("custom_live_bgm.png")

        app.custom_screen._select_category("background")
        root.update()
        time.sleep(0.2)
        root.update()
        grab("custom_live_background.png")
    except Exception:
        traceback.print_exc()
    finally:
        app.close()


if __name__ == "__main__":
    main()
