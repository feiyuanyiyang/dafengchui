# -*- coding: utf-8 -*-
"""从一张待机图生成“轻微摇摆 + 呼吸起伏”的多帧 idle。

    python tools/art/bake_idle_sway.py

输入：
    assets/sprites/player/player_e_idle.png   （第 0 帧，保持不动）
输出：
    player_e_idle1.png … player_e_idle11.png  （默认 12 帧一个循环）
    player_w_idle1.png … player_w_idle11.png  （e 的镜像）

原理：
    - 以脚底为锚点做极小的水平错切，身体会左右轻摆，脚不滑；
    - 同时做 0.6% 的纵向缩放，模拟呼吸；
    - 两个动作都从第 0 帧的“零位移”出发，循环接缝平滑。
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
PLAYER = ROOT / "assets" / "sprites" / "player"


def sway_frame(base, phase, shear, breath):
    """phase ∈ [0,1)；以底边为锚点做错切 + 纵向缩放。"""
    width, height = base.size
    theta = math.tau * phase
    k = shear * math.sin(theta)
    scale = 1.0 + breath * math.sin(2.0 * theta)

    e = 1.0 / scale
    f = height * (1.0 - e)
    # 输出坐标 -> 输入坐标的仿射系数（Pillow AFFINE 的语义）
    a, b, c = 1.0, -k * e, k * (height - f)
    d, dd, ff = 0.0, e, f
    return base.transform(
        (width, height),
        Image.AFFINE,
        (a, b, c, d, dd, ff),
        resample=Image.BICUBIC,
        fillcolor=(0, 0, 0, 0),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成待机摇摆/呼吸帧")
    parser.add_argument("--frames", type=int, default=12, help="一个循环的总帧数（含第 0 帧）")
    parser.add_argument("--shear", type=float, default=0.02, help="左右错切幅度（0.02 ≈ 顶部 2 px）")
    parser.add_argument("--breath", type=float, default=0.01, help="呼吸纵向缩放幅度（0.01 ≈ 1 px）")
    parser.add_argument("--out", default=str(PLAYER), help="输出目录")
    parser.add_argument("--force", action="store_true", help="即使已有视频 idle 帧也覆盖")
    args = parser.parse_args(argv)

    out = Path(args.out)
    if (out / "player_e_idle12.png").exists() and not args.force:
        print("检测到视频版 idle 帧（player_e_idle12.png），跳过程序化摇摆；--force 可强制覆盖")
        return 0
    base = Image.open(out / "player_e_idle.png").convert("RGBA")
    for index in range(1, max(1, args.frames)):
        frame = sway_frame(base, index / float(args.frames), args.shear, args.breath)
        frame.save(out / ("player_e_idle%d.png" % index))
        ImageOps.mirror(frame).save(out / ("player_w_idle%d.png" % index))
    print("已生成 %d 帧 idle（第 0 帧沿用 player_e_idle.png）→ %s" % (args.frames, out))


if __name__ == "__main__":
    main()
