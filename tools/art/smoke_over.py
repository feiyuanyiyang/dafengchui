# -*- coding: utf-8 -*-
"""结束界面冒烟：进入结束态 → 遮罩弹出 → 点重新开始 → 恢复对局。

    python tools/art/smoke_over.py

只碰应用层（GameApp / CrayonGameOverView），不跑主循环，几秒内结束。
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import game  # noqa: E402


def main():
    game.prepare_ui_scale()
    root = tk.Tk()
    root.geometry("+40+40")
    app = game.GameApp(root)
    app.start_game()

    for _ in range(3):
        app.model.debug_damage()
    app.renderer.render(app.model, 60, 0.0, False)
    app._sync_over_view()

    over = app.over_view
    assert over is not None, "没有创建 CrayonGameOverView"
    assert over.visible, "结束遮罩没有弹出"
    assert app.model.state == game.STATE_GAME_OVER
    # 旧的 Canvas 点阵遮罩必须彻底让位，否则会和蜡笔遮罩叠成两层
    assert not app.renderer.hud.overlay_items, "旧结束遮罩仍然存在"

    # 点“重新开始”：应该回到对局并收起遮罩
    assert over.on_click(600, game.CRAYON_OVER_BUTTON_YS[0]), "重新开始没命中"
    assert app.model.state != game.STATE_GAME_OVER, "重新开始后仍在结束态"
    assert not over.visible, "重新开始后遮罩仍在"
    assert not app.renderer.hud.overlay_items, "重开后旧结束遮罩又冒出来了"
    assert app.renderer.hud._overlay_crayon, "重开后蜡笔遮罩接管被清掉了"

    # 再死一次，点“退出游戏”：应该请求关闭
    for _ in range(3):
        app.model.debug_damage()
    app._sync_over_view()
    assert over.visible, "第二次结束遮罩没弹出"
    assert not app.renderer.hud.overlay_items, "第二局旧结束遮罩仍然存在"
    over.on_click(600, game.CRAYON_OVER_BUTTON_YS[1])

    root.destroy()
    print("smoke over: OK")


if __name__ == "__main__":
    main()
