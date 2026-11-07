# -*- coding: utf-8 -*-
"""方案 A 的实机验证：在真 Tk 窗口里，把「模糊背景 + 标题 + 镂空按钮」合成成一张图。

    python tools/art/demo_pause_overlay.py

为什么必须合成成一张图：Tk Canvas 的图片透明像素只会透出画布底色，不会透出
下方的 item。所以暂停界面一次性在 PIL 里合成整屏（背景模糊、压暗、边框、文字），
Canvas 只贴这一张，镂空才是真的镂空。

窗口里可以做的事：
    空格 / Esc …… 切换暂停
    鼠标移到按钮上 …… 高亮（只重绘那一块，复用已经算好的模糊背景）
    关闭窗口 …… 退出

启动后会自动进入暂停态，并把合成结果与窗口截图存到 art_preview/。
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

import tkinter as tk

from PIL import Image, ImageFilter, ImageTk

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import preview_crayon_hud as preview  # noqa: E402
from preview_crayon_hud import img, put, rounded_frame  # noqa: E402

ROOT = preview.ROOT
W, H = 1200, 840

BTN_W, BTN_H = 420, 74
BTN_X = W // 2 - BTN_W // 2
BTN_YS = (378, 468, 558, 648)
LABELS = ("btn_continue", "btn_restart", "btn_settings", "btn_quit")
TITLE_Y = 252


# =============================================================================
# 合成
# =============================================================================
def blurred_backdrop(shot):
    """截图 → 高斯模糊 → 压暗。真实游戏里这一步在按下暂停的那一帧做一次。"""
    bg = shot.convert("RGB").filter(ImageFilter.GaussianBlur(16))
    veil = Image.new("RGBA", bg.size, (16, 38, 28, 112))
    return Image.alpha_composite(bg.convert("RGBA"), veil)


def compose_pause(backdrop):
    """整屏合成：模糊背景 + 标题 + 四个镂空按钮。"""
    base = backdrop.copy()
    put(base, "pause_title", (W // 2, TITLE_Y))
    for cy, name in zip(BTN_YS, LABELS):
        base.alpha_composite(rounded_frame((BTN_W, BTN_H)), (BTN_X, cy - BTN_H // 2))
        put(base, name, (W // 2, cy))
    put(base, "pause_hint", (W // 2, 736))
    return base


def compose_hover(backdrop, cy, name):
    """hover 只重绘一个按钮：从模糊背景里裁一块，画更亮的边框。"""
    top = cy - BTN_H // 2
    box = (BTN_X - 8, top - 8, BTN_X + BTN_W + 8, top + BTN_H + 8)
    tile = backdrop.crop(box)
    pad = 8

    frame = rounded_frame((BTN_W, BTN_H), width=4, color=(255, 255, 255, 255))
    tile.alpha_composite(frame, (pad, pad))

    wash = Image.new("RGBA", (BTN_W, BTN_H), (255, 255, 255, 38))
    tile.alpha_composite(wash, (pad, pad))

    label = img(name)
    tile.alpha_composite(label, (pad + BTN_W // 2 - label.width // 2,
                                 pad + BTN_H // 2 - label.height // 2))
    return tile, box


# =============================================================================
# 应用
# =============================================================================
class Demo:
    def __init__(self, root):
        self.root = root
        root.title("方案 A 验证 · 大风吹 UI")
        root.configure(bg="#0F1720")
        root.resizable(False, False)

        self.canvas = tk.Canvas(root, width=W, height=H, highlightthickness=0,
                                bg="#0F1720")
        self.canvas.pack()

        self.scene_photo = ImageTk.PhotoImage(preview.hud_scene().convert("RGB"))
        self.canvas.create_image(0, 0, anchor="nw", image=self.scene_photo)

        self.paused = False
        self.backdrop = None
        self.pause_items = []
        self.hover_index = None
        self.photos = []

        root.bind("<space>", self.toggle)
        root.bind("<Escape>", lambda e: root.destroy())
        self.canvas.bind("<Motion>", self.on_motion)
        root.after(700, self.auto_pause)

    # --- 暂停 / 恢复 ---
    def auto_pause(self):
        if not self.paused:
            self.toggle()
        self.root.after(400, self.snapshot)

    def toggle(self, _event=None):
        if self.paused:
            self.resume()
        else:
            self.pause()

    def pause(self):
        self.root.update_idletasks()
        x, y = self.canvas.winfo_rootx(), self.canvas.winfo_rooty()
        from PIL import ImageGrab
        shot = ImageGrab.grab(bbox=(x, y, x + W, y + H))
        self.backdrop = blurred_backdrop(shot)
        composed = compose_pause(self.backdrop)

        composed.save(ROOT / "art_preview" / "demo_pause_composite.png")
        photo = ImageTk.PhotoImage(composed)
        self.photos.append(photo)
        self.pause_items.append(
            self.canvas.create_image(0, 0, anchor="nw", image=photo, tags="pause"))
        self.paused = True

    def resume(self):
        for item in self.pause_items:
            self.canvas.delete(item)
        self.pause_items.clear()
        self.photos.clear()
        self.backdrop = None
        self.paused = False
        self.hover_index = None

    # --- hover ---
    def on_motion(self, event):
        if not self.paused or self.backdrop is None:
            return
        index = None
        for i, cy in enumerate(BTN_YS):
            if abs(event.y - cy) <= BTN_H // 2 and BTN_X <= event.x <= BTN_X + BTN_W:
                index = i
                break
        if index == self.hover_index:
            return
        for item in self.pause_items[1:]:
            self.canvas.delete(item)
        del self.pause_items[1:]
        self.hover_index = index
        if index is None:
            return
        tile, box = compose_hover(self.backdrop, BTN_YS[index], LABELS[index])
        photo = ImageTk.PhotoImage(tile)
        self.photos.append(photo)
        self.pause_items.append(
            self.canvas.create_image(box[0], box[1], anchor="nw", image=photo,
                                     tags="pause"))

    # --- 存证 ---
    def snapshot(self):
        if not self.paused:
            return
        self.root.update_idletasks()
        x, y = self.canvas.winfo_rootx(), self.canvas.winfo_rooty()
        from PIL import ImageGrab
        ImageGrab.grab(bbox=(x, y, x + W, y + H)).save(
            ROOT / "art_preview" / "demo_pause_window.png")
        self.status = "saved"


def enable_dpi_awareness():
    """Windows 125% 缩放下，Tk 的逻辑坐标和 ImageGrab 的物理像素会对不上，
    截图就会整体偏移。先声明进程 DPI 感知，两者才一致。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    Demo(root)
    root.mainloop()


if __name__ == "__main__":
    main()
