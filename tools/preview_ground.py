# -*- coding: utf-8 -*-
"""开发用：把地面背景候选临时垫进真实游戏 Canvas，截图检查可读性。

不修改游戏代码：只在运行中的 GameApp.canvas 最底层临时插入一张图片，
隐藏原来的软云，然后截图。用于评估“角色/障碍物/HUD 压在地面上”的实际效果。

    python tools/preview_ground.py
    python tools/preview_ground.py --only meadow
"""

from __future__ import annotations

import argparse
import sys
import time
import tkinter as tk
from pathlib import Path

from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import game  # noqa: E402

CANDIDATES = {
    "courtyard": ROOT / "art_preview" / "bg_ground_courtyard.png",
    "meadow": ROOT / "art_preview" / "bg_ground_meadow.png",
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="地面背景游戏内预览")
    parser.add_argument("--only", choices=sorted(CANDIDATES), action="append")
    args = parser.parse_args(argv)

    root = tk.Tk()
    app = game.GameApp(root, autostart=True)
    try:
        root.attributes("-topmost", True)
        root.lift()
        root.focus_force()
        root.update()
        # 持续推进 Tk 主循环，等障碍物生成（直接 sleep 不会跑 after 回调）
        deadline = time.time() + 2.2
        while time.time() < deadline:
            root.update()
            time.sleep(0.01)
        root.update()

        full = ImageGrab.grab()
        scale_x = full.width / float(root.winfo_screenwidth())
        scale_y = full.height / float(root.winfo_screenheight())
        x = int(round(root.winfo_rootx() * scale_x))
        y = int(round(root.winfo_rooty() * scale_y))
        w = int(round(root.winfo_width() * scale_x))
        h = int(round(root.winfo_height() * scale_y))

        for key in args.only or sorted(CANDIDATES):
            path = CANDIDATES[key]
            if not path.is_file():
                print("跳过（文件不存在）：%s" % path)
                continue
            photo = tk.PhotoImage(file=str(path))
            item = app.canvas.create_image(0, 0, anchor="nw", image=photo)
            app.canvas.tag_lower(item)
            for cloud in app.renderer.clouds:
                app.canvas.itemconfigure(cloud, state="hidden")
            # 方案 A：最终接入会关掉游戏自带的暗角，预览时同步隐藏
            if app.renderer.vignette is not None:
                app.canvas.itemconfigure(app.renderer.vignette, state="hidden")
            # 仅预览：把角色放回中心，避免被风吹到画面边缘影响对比
            app.model.player.x = game.WINDOW_WIDTH / 2
            app.model.player.y = game.WINDOW_HEIGHT / 2
            app.model.player.stun_timer = 0.0
            root.update_idletasks()
            root.update()
            time.sleep(0.3)
            root.update()

            image = ImageGrab.grab().crop((x, y, x + w, y + h))
            if image.size != (game.WINDOW_WIDTH, game.WINDOW_HEIGHT):
                image = image.resize((game.WINDOW_WIDTH, game.WINDOW_HEIGHT))
            out = ROOT / "art_preview" / ("bg_ground_%s_ingame.png" % key)
            image.save(out)
            print("saved", out)

            app.canvas.delete(item)
            del photo
    finally:
        app.close()


if __name__ == "__main__":
    main()
