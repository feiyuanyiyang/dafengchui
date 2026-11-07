# -*- coding: utf-8 -*-
"""截一张真实游戏画面（用 game.py 里的 Renderer / HudRenderer），验证 HUD 接入。

    python tools/art/shot_hud.py [输出名]

只读，不修改游戏状态；窗口截完就关。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import game  # noqa: E402
from PIL import ImageGrab  # noqa: E402


def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main():
    scene = sys.argv[1] if len(sys.argv) > 1 else "hud"
    out_name = {
        "hud": "crayon_hud_ingame.png",
        "over": "crayon_gameover_ingame.png",
        "hover": "crayon_hud_hover.png",
        "pause": "crayon_pause_ingame.png",
        "pause-hover": "crayon_pause_hover_ingame.png",
        "countdown": "crayon_pause_countdown.png",
        "settings": "crayon_pause_settings.png",
    }.get(scene, "crayon_hud_ingame.png")
    # 默认跟游戏一致（不声明 DPI 感知）；设 SHOT_DPI_AWARE=1 才强制高清
    if os.environ.get("SHOT_DPI_AWARE") == "1":
        enable_dpi_awareness()

    root = tk.Tk()
    root.title("大风吹 · HUD 截图")
    canvas = tk.Canvas(
        root, width=game.WINDOW_WIDTH, height=game.WINDOW_HEIGHT, highlightthickness=0)
    canvas.pack()
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    root.update()

    assets = game.AssetStore() if game.ASSETS_ENABLED else None
    renderer = game.Renderer(canvas, assets)
    model = game.GameModel(seed=7)

    # 摆几个能看清 HUD 的状态：3 颗心、活了 12.4 秒、西北风、上帝模式开着
    model.elapsed = 12.4
    model.player.hearts = int(os.environ.get("SHOT_HEARTS", "3"))
    model.god_mode = True
    _name, _ux, _uy = game.WIND_DIRECTIONS[5]
    model.wind = game.Wind(index=5, ux=_ux, uy=_uy, remaining=6.0, duration=7.0)
    model.wind_speed = 180
    model.player.x = game.WINDOW_WIDTH // 2
    model.player.y = game.WINDOW_HEIGHT // 2 + 40

    if scene in ("hud", "hover"):
        # 放几个障碍物，顺带验证深度排序不会盖住 HUD
        for index, (tier, dx, dy) in enumerate(
            (("small", -260, 40), ("medium", 300, 120), ("large", 150, 250))
        ):
            info = game.TIER_BY_NAME[tier]
            model.obstacles.append(
                game.Obstacle(
                    oid=index + 1,
                    x=model.player.x + dx,
                    y=model.player.y + dy,
                    vx=0.0,
                    vy=0.0,
                    radius=info["radius"],
                    tier=tier,
                    damage=info["damage"],
                    base_speed=info["speed"],
                )
            )
    if scene == "hover":
        renderer.hud.set_pause_hover(True)
    if scene == "over":
        model.state = game.STATE_GAME_OVER
        model.reason = game.REASON_HEARTS_ZERO

    for _ in range(3):
        renderer.render(model, 60.0, 0.0, False)
    root.update()

    if scene.startswith("pause") or scene in ("countdown", "settings"):
        pause_view = game.CrayonPauseView(
            canvas, assets,
            on_continue=lambda: None,
            on_restart=lambda: None,
            on_settings=lambda: None,
            on_quit=lambda: None,
            on_back=lambda: None,
        )
        if scene == "countdown":
            pause_view.show_countdown(3)
        elif scene == "settings":
            pause_view.show_settings()
        else:
            pause_view.show_menu()
        root.update()
        if scene == "pause-hover":
            pause_view.on_motion(game.WINDOW_WIDTH // 2, game.CRAYON_PAUSE_BUTTON_YS[0])
            root.update()
    time.sleep(0.45)
    root.update()
    root.lift()

    scale = 1.0 if game.process_dpi_awareness() else game.display_scale()
    x = int(round(canvas.winfo_rootx() * scale))
    y = int(round(canvas.winfo_rooty() * scale))
    w = int(round(game.WINDOW_WIDTH * scale))
    h = int(round(game.WINDOW_HEIGHT * scale))
    shot = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    if shot.size != (game.WINDOW_WIDTH, game.WINDOW_HEIGHT):
        shot = shot.resize((game.WINDOW_WIDTH, game.WINDOW_HEIGHT))
    target = ROOT / "art_preview" / out_name
    shot.save(target)
    root.destroy()
    print("write %s (%dx%d)" % (target.relative_to(ROOT), shot.width, shot.height))


if __name__ == "__main__":
    main()
