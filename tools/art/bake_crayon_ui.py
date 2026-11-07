# -*- coding: utf-8 -*-
"""把 HUD / 暂停界面需要的文字与符号烘成 PNG。

    python tools/art/bake_crayon_ui.py

输出到 assets/sprites/ui/crayon/，并写一份 manifest.json（名字 -> 文件 / 尺寸 /
锚点 / 生成参数），游戏侧按名字取图、按锚点摆放，不需要知道字体和质感。

刻意不覆盖 assets/sprites/ui/ 下已有的 heart_full.png 等旧资源：
新资源全部落在 crayon/ 子目录，game.py 未改之前旧画面完全不受影响。
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageFilter

import crayon_kit as kit

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "assets" / "sprites" / "ui" / "crayon"

# 按 2 倍分辨率烘焙：游戏里再缩到 UI_SCALE，文字才不会被放大糊掉
BAKE_SCALE = 2
kit.OUT = float(BAKE_SCALE)

# HUD 文字偏小，80% 质感下 19px 会糊，所以整体放大一档
TIMER_PX = 32
LABEL_PX = 22
WIND_PX = 24
BADGE_PX = 24
PANEL_TITLE_PX = 76
BUTTON_PX = 26
# 开始界面：标题比暂停大一号（96 vs 76），按钮文案也比暂停大（32 vs 26）
START_TITLE_PX = 96
START_BUTTON_PX = 32

# 计时数字用固定格子，保证等宽（否则 1 和 8 宽度不同，秒数会抖）
DIGIT_CELL = 18
DOT_CELL = 6
S_CELL = 15
TIMER_CELL_H = 32

WHITE_ON_GRASS = dict(fill=kit.WHITE, outline=kit.INK, outline_px=2.0, strength=0.8)
INK_ON_PAPER = dict(fill=kit.INK, outline=None, outline_px=0, strength=0.8)
BTN_LIGHT = dict(fill=(255, 255, 255), outline=(30, 44, 36), outline_px=1.4, strength=0.6)
WOOD_SIZE = (420, 74)


def _text(name, parts, seed, **opts):
    return dict(name=name, kind="text", parts=parts, seed=seed, opts=opts)


def text_jobs():
    jobs = []
    # --- HUD：计时（中文标签 + 手写数字 + 单位） ---
    jobs.append(_text("timer_label", [("存活", kit.FONT_CJK, LABEL_PX)], 11,
                      **WHITE_ON_GRASS))
    for d in range(10):
        jobs.append(_text("digit_%d" % d, [(str(d), kit.FONT_LATIN, TIMER_PX)], 20 + d,
                          cell=(DIGIT_CELL, TIMER_CELL_H), **WHITE_ON_GRASS))
    jobs.append(_text("digit_dot", [(".", kit.FONT_LATIN, TIMER_PX)], 31,
                      cell=(DOT_CELL, TIMER_CELL_H), cell_valign="baseline",
                      **WHITE_ON_GRASS))
    jobs.append(_text("digit_s", [("s", kit.FONT_LATIN, TIMER_PX)], 32,
                      cell=(S_CELL, TIMER_CELL_H), **WHITE_ON_GRASS))

    # --- HUD：底部操作提示（整句静态，直接烘一张） ---
    jobs.append(_text("hint", [("WASD / 方向键移动 · 顶住风 · G 上帝模式 · M 静音 · R 重新开始",
                                kit.FONT_CJK, 20)], 60,
                      fill=(255, 255, 255), outline=kit.INK, outline_px=1.8, strength=0.8))

    # --- HUD：八向风名 ---
    for i, name in enumerate(("东", "东南", "南", "西南", "西", "西北", "北", "东北")):
        jobs.append(_text("wind_%s" % kit_name(name),
                          [("%s风" % name, kit.FONT_CJK, WIND_PX)],
                          40 + i, **WHITE_ON_GRASS))

    # --- 暂停面板 ---
    jobs.append(_text("pause_title", [("游戏暂停", kit.FONT_CJK, PANEL_TITLE_PX)], 80,
                      fill=kit.WHITE, outline=(24, 20, 18), outline_px=1.8,
                      strength=0.42, bold=0.4))
    for i, label in enumerate(("继续游戏", "重新开始", "设置", "退出游戏", "返回")):
        jobs.append(_text("btn_%s" % ("continue", "restart", "settings", "quit", "back")[i],
                          [(label, kit.FONT_CJK, BUTTON_PX)], 90 + i, **BTN_LIGHT))
    for value in (1, 2, 3):
        jobs.append(_text("countdown_%d" % value, [(str(value), kit.FONT_LATIN, 150)], 83 + value,
                          fill=kit.WHITE, outline=kit.INK, outline_px=4.0, strength=0.5))
    jobs.append(_text("countdown_suffix", [("秒后回到游戏", kit.FONT_CJK, 26)], 88,
                      fill=(255, 255, 255), outline=kit.INK, outline_px=1.8, strength=0.7))

    jobs.append(_text("settings_title", [("设置", kit.FONT_CJK, PANEL_TITLE_PX)], 81,
                      fill=kit.WHITE, outline=(24, 20, 18), outline_px=1.8,
                      strength=0.42, bold=0.4))
    jobs.append(_text("settings_hint", [("设置功能尚未实现，将在后续版本提供", kit.FONT_CJK, 18)], 82,
                      fill=(255, 255, 255), outline=kit.INK, outline_px=1.6, strength=0.8))

    jobs.append(_text("pause_hint", [("Esc 继续游戏 · R 重新开始", kit.FONT_CJK, 18)], 97,
                      fill=(255, 255, 255), outline=kit.INK, outline_px=1.6, strength=0.8))

    # --- 结束界面 ---
    jobs.append(_text("over_title", [("游戏结束", kit.FONT_CJK, PANEL_TITLE_PX)], 110,
                      fill=kit.WHITE, outline=(24, 20, 18), outline_px=1.8,
                      strength=0.42, bold=0.4))
    for key, label in (("blown", "被吹出窗口"), ("hearts", "血量归零")):
        jobs.append(_text("over_reason_%s" % key, [(label, kit.FONT_CJK, 26)], 111,
                          fill=kit.WHITE, outline=kit.INK, outline_px=1.6, strength=0.7))
    jobs.append(_text("score_label", [("本局得分", kit.FONT_CJK, 26)], 112,
                      fill=kit.WHITE, outline=kit.INK, outline_px=1.6, strength=0.7))
    jobs.append(_text("score_unit", [("秒", kit.FONT_CJK, 26)], 113,
                      fill=kit.WHITE, outline=kit.INK, outline_px=1.6, strength=0.7))
    jobs.append(_text("over_hint", [("按 R 也可重新开始", kit.FONT_CJK, 20)], 114,
                      fill=kit.WHITE, outline=kit.INK, outline_px=1.6, strength=0.7))

    # --- 开始界面：标题、三个按钮、底部提示 ---
    for i, (name, label) in enumerate((("start_btn_start", "开始游戏"),
                                       ("start_btn_custom", "自定义"),
                                       ("start_btn_quit", "退出"))):
        jobs.append(_text(name, [(label, kit.FONT_CJK, START_BUTTON_PX)],
                          120 + i, **BTN_LIGHT))
    jobs.append(_text("start_hint", [("回车 / 空格 开始游戏", kit.FONT_CJK, 18)], 123,
                      fill=(255, 255, 255), outline=kit.INK,
                      outline_px=1.6, strength=0.8))

    return jobs


def kit_name(cn):
    return {
        "东": "e", "东南": "se", "南": "s", "西南": "sw",
        "西": "w", "西北": "nw", "北": "n", "东北": "ne",
    }[cn]


def shape_jobs():
    return [
    ] + [
        # 三套不同 seed：并排的三颗心不会出现一模一样的毛边和漏底
        dict(name="heart_full" + suffix, kind="shape",
             builder=(lambda i=i: kit.heart_png(34, True, seed=1 + i * 7)))
        for i, suffix in enumerate(("", "_b", "_c"))
    ] + [
        dict(name="heart_empty" + suffix, kind="shape",
             builder=(lambda i=i: kit.heart_png(34, False, seed=2 + i * 7)))
        for i, suffix in enumerate(("", "_b", "_c"))
    ] + [
        dict(name="pause", kind="shape",
             builder=lambda: kit.pause_png(41, seed=3)),
        dict(name="pause_hover", kind="shape",
             builder=lambda: kit.pause_png(41, fill=(255, 214, 130), seed=3)),
        dict(name="compass_base", kind="shape",
             builder=lambda: kit.compass_base_png(96, seed=5)),
    ] + [
        dict(name="plank_%d" % i, kind="shape",
             builder=(lambda ii=i: kit.plank_png(WOOD_SIZE, seed=20 + ii)))
        for i in range(1, 5)
    ] + [
        dict(name="plank_primary", kind="shape",
             builder=lambda: kit.plank_png(WOOD_SIZE, fill=(246, 214, 168),
                                           grain_color=(206, 162, 108), seed=29)),
        dict(name="star", kind="shape",
             builder=lambda: kit.star_png(20, seed=13)),
        dict(name="god_badge", kind="shape",
             builder=_god_badge),
        dict(name="start_title", kind="shape",
             builder=_start_title),
    ] + [
        dict(name="compass_arrow_%s" % d, kind="shape",
             builder=(lambda dd=d: kit.compass_arrow_png(96, dd)))
        for d in ("e", "se", "s", "sw", "w", "nw", "n", "ne")
    ]


def _god_badge():
    """红星 + 白字：★ 不是字体字形，必须拼图层。"""
    star = kit.star_png(20, seed=70)
    text = kit.text_png([("上帝模式 · 按 G 关闭", kit.FONT_CJK, BADGE_PX)], seed=71,
                        fill=kit.WHITE, outline=kit.INK, outline_px=2.0, strength=0.8)
    gap = 6
    w = star.width + gap + text.width
    h = max(star.height, text.height)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.alpha_composite(star, (0, (h - star.height) // 2))
    out.alpha_composite(text, (star.width + gap, (h - text.height) // 2))
    return out


def _start_title():
    """开始界面大标题：白字 + 墨色描边 + 柔和投影。

    封面上半部是亮天空，光描边不够，所以额外烘一层投影；投影直接进 PNG，
    运行时不做任何混合（Canvas 透明像素透不出下方 item）。
    """
    title = kit.bold_text_png(
        [("大风吹", kit.FONT_CJK, START_TITLE_PX)], seed=80,
        fill=kit.WHITE, outline=(18, 18, 18), outline_px=3.0,
        strength=0.42, bold_px=0.4,
    )
    pad = 48
    canvas = Image.new(
        "RGBA", (title.width + pad * 2, title.height + pad * 2), (0, 0, 0, 0))
    alpha = title.split()[3].filter(ImageFilter.GaussianBlur(10))
    shadow = Image.new("RGBA", title.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.point(lambda v: int(v * 0.5)))
    canvas.alpha_composite(shadow, (pad + 7, pad + 11))
    canvas.alpha_composite(title, (pad, pad))
    return canvas


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"version": 1, "generated_by": "tools/art/bake_crayon_ui.py",
                "font": "ZCOOL KuaiLe (OFL) + Patrick Hand (OFL)",
                "strength": 0.8, "scale": BAKE_SCALE, "assets": {}}

    for job in shape_jobs():
        img = job["builder"]()
        rel = "%s.png" % job["name"]
        img.save(OUT / rel)
        manifest["assets"][job["name"]] = {
            "file": rel, "w": img.width, "h": img.height, "kind": "shape",
            "anchor": "center" if job["name"].startswith("compass") else "center",
        }

    for job in text_jobs():
        opts = dict(job["opts"])
        cell = opts.pop("cell", None)
        cell_valign = opts.pop("cell_valign", "center")
        bold = opts.pop("bold", None)
        if bold:
            img = kit.bold_text_png(job["parts"], seed=job["seed"], bold_px=bold, **opts)
        else:
            img = kit.text_png(job["parts"], seed=job["seed"], **opts)
        if cell:
            img = kit.cell(
                img,
                int(round(cell[0] * BAKE_SCALE)),
                int(round(cell[1] * BAKE_SCALE)),
                valign=cell_valign,
                bottom_pad=int(round(7 * BAKE_SCALE)),
            )
        rel = "%s.png" % job["name"]
        img.save(OUT / rel)
        manifest["assets"][job["name"]] = {
            "file": rel, "w": img.width, "h": img.height, "kind": "text",
            "cell": cell,
            "text": "".join(p[0] for p in job["parts"]),
        }

    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    total = len(manifest["assets"])
    print("baked %d assets -> %s" % (total, OUT.relative_to(ROOT)))
    for name, meta in sorted(manifest["assets"].items()):
        print("  %-22s %3dx%-3d %s" % (name, meta["w"], meta["h"], meta["file"]))


if __name__ == "__main__":
    main()
