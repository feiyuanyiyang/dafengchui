# -*- coding: utf-8 -*-
"""开发用：起一个真实 Tk 窗口，渲染一帧后截图，用于检查贴图在游戏里的实际效果。

    python tools/preview_live.py [输出路径]

脚本不会跑主循环，只 update 一次就抓图，然后关闭窗口。
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
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "art_preview" / "live_scene.png"
    hit_out = out.with_name("live_hit.png")
    root = tk.Tk()
    app = game.GameApp(root, autostart=True)
    app.model.god_mode = True
    # 推进一段时间，让障碍物自然生成；同时把玩家钉在中心方便观察
    for _ in range(600):
        app.model.step(game.FIXED_DT, (0.0, 0.0))
        app.model.player.x = game.WINDOW_WIDTH / 2
        app.model.player.y = game.WINDOW_HEIGHT / 2
        app.model.events.clear()
    app.model.player.hearts = 2
    app.model.player.face_x, app.model.player.face_y = 0.6, 0.5
    app.model.player.invuln_timer = 0.0
    # 模拟“有方向输入”，让截图落在奔跑帧而不是待机帧
    app.model.player.input_active = True
    app.renderer.player._walk_clock = 0.13
    app.renderer.render(app.model, 60.0, 0.0, False)
    app.paused = True  # 冻结主循环，避免截图时又切回 idle
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    root.update_idletasks()
    root.update()
    time.sleep(0.5)
    root.update()

    # Windows 缩放（如 150%）下 Tk 用逻辑坐标、屏幕截图用物理像素，需要按比例换算。
    full = ImageGrab.grab()
    scale_x = full.width / float(root.winfo_screenwidth())
    scale_y = full.height / float(root.winfo_screenheight())

    def grab(path):
        x = int(round(root.winfo_rootx() * scale_x))
        y = int(round(root.winfo_rooty() * scale_y))
        w = int(round(root.winfo_width() * scale_x))
        h = int(round(root.winfo_height() * scale_y))
        img = ImageGrab.grab().crop((x, y, x + w, y + h))
        if img.size != (game.WINDOW_WIDTH, game.WINDOW_HEIGHT):
            img = img.resize((game.WINDOW_WIDTH, game.WINDOW_HEIGHT))
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
        print("saved %s %s (scale %.2f)" % (path, img.size, scale_x))

    grab(out)

    # 受击状态：硬直 + 受击贴图 + 闪白
    app.model.player.stun_timer = 0.5
    app.model.player.invuln_timer = 0.5
    app.model.player.flash_phase = True
    app.renderer.render(app.model, 60.0, 0.0, False)
    root.update()
    time.sleep(0.2)
    root.update()
    grab(hit_out)
    app.close()


if __name__ == "__main__":
    main()
