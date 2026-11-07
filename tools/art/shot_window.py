# -*- coding: utf-8 -*-
"""实机整窗截图（含真实 UI 缩放），用法：

    python tools/art/shot_window.py hud
    python tools/art/shot_window.py pause

与 shot_hud.py 的区别：这里走真正的 GameApp + ScaledCanvas，所以能验证
“界面放大后画布/贴图/暂停遮罩是否都跟着放大”。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import tkinter as tk  # noqa: E402

from PIL import ImageGrab  # noqa: E402

import game  # noqa: E402


def main(argv):
    scene = argv[1] if len(argv) > 1 else "hud"
    out_name = {
        "hud": "scaled_hud.png",
        "pause": "scaled_pause.png",
        "start": "scaled_start.png",
        "custom": "scaled_custom.png",
        "over": "scaled_over.png",
        "over-hover": "scaled_over_hover.png",
        "over-blown": "scaled_over_blown.png",
    }.get(scene, "scaled_%s.png" % scene)

    scale = game.prepare_ui_scale()
    print("ui scale: %.2f" % scale)

    root = tk.Tk()
    app = game.GameApp(root)
    if scene not in ("start", "custom"):
        app.start_game()
    if scene == "pause":
        app.pause_game()
    elif scene == "custom":
        app._on_custom_clicked()
    elif scene in ("over", "over-hover", "over-blown"):
        for _ in range(3):
            app.model.debug_damage()
        app.model.elapsed = 12.4
        if scene == "over-blown":
            app.model.reason = game.REASON_BLOWN_AWAY

    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    root.update()
    for _ in range(3):
        root.update()
        time.sleep(0.2)

    if scene == "over-hover" and app.over_view is not None:
        app.over_view.on_motion(600, game.CRAYON_OVER_BUTTON_YS[0])
        root.update()

    canvas = app.canvas._canvas
    left = int(round(canvas.winfo_rootx()))
    top = int(round(canvas.winfo_rooty()))
    width = int(round(game.WINDOW_WIDTH * scale))
    height = int(round(game.WINDOW_HEIGHT * scale))
    shot = ImageGrab.grab(bbox=(left, top, left + width, top + height))
    target = ROOT / "art_preview" / out_name
    shot.save(target)
    print("write %s (%dx%d)" % (target.relative_to(ROOT), shot.width, shot.height))

    app.close()
    try:
        root.destroy()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
