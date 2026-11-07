# -*- coding: utf-8 -*-
"""冒烟测试：走一遍暂停界面的真实交互路径（不开主循环）。

    python tools/art/smoke_pause.py

验证：进入暂停 → hover → 点设置 → 点返回 → 点继续（倒计时）→ 恢复。
任何回调抛异常都会在这里暴露。
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import game  # noqa: E402


def main():
    game.enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    app = game.GameApp(root)
    app.start_game()
    root.update()

    assert isinstance(app.pause_view, game.CrayonPauseView), "没有走蜡笔暂停界面"

    app.pause_game()
    root.update()
    assert app.pause_view.visible, "暂停界面没显示"
    assert len(app.pause_view._hit_rects) == 4, "菜单按钮数量不对"

    # hover 第一个按钮再移开
    cx = game.WINDOW_WIDTH // 2
    app.pause_view.on_motion(cx, game.CRAYON_PAUSE_BUTTON_YS[0])
    assert app.pause_view._hover == "continue", "hover 没有命中"
    app.pause_view.on_motion(10, 10)
    assert app.pause_view._hover is None, "移开后 hover 没清掉"

    # 点设置 → 设置页；点返回 → 菜单
    assert app.pause_view.on_click(cx, game.CRAYON_PAUSE_BUTTON_YS[2])
    root.update()
    assert app.pause_view.mode == "settings", "没有进入设置页"
    assert len(app.pause_view._hit_rects) == 1, "设置页应只有一个按钮"
    assert app.pause_view.on_click(cx, game.CRAYON_PAUSE_SINGLE_Y)
    root.update()
    assert app.pause_view.mode == "menu", "没有返回菜单"

    # 点继续 → 倒计时 → 立刻恢复
    assert app.pause_view.on_click(cx, game.CRAYON_PAUSE_BUTTON_YS[0])
    root.update()
    assert app.pause_view.mode == "countdown", "没有进入倒计时"
    app._cancel_pause_job()
    app.resume_game()
    root.update()
    assert not app.paused, "没有恢复"
    assert not app.pause_view.visible, "暂停界面没隐藏"

    # 再次暂停应重新抓图（backdrop 已清空）
    app.pause_game()
    root.update()
    assert app.pause_view.visible and app.pause_view._backdrop is not None

    root.destroy()
    print("smoke pause: OK")


if __name__ == "__main__":
    main()
