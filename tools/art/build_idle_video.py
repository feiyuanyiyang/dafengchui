# -*- coding: utf-8 -*-
"""把一段“原地待机”视频处理成游戏 idle 帧。

    python tools/art/build_idle_video.py 待机动作.mp4 --count 30 --start 0 --end 3

输出：
    assets/sprites/player/player_e_idle.png + player_e_idle1..N-1.png
    assets/sprites/player/player_w_idle.png + player_w_idle1..N-1.png

约定：
    - 视频里的角色与“锚点原图”同向（鲸鱼尾巴在右）；游戏 e_idle 需要尾巴在左，
      所以 e = 视频帧的水平镜像，w = 原视频帧。
    - 固定取景框 + 固定比例 + 固定地面线，保留视频里的自然起伏和左右摆动。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import av
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "art"))
import import_player as ip  # noqa: E402

PLAYER = ROOT / "assets" / "sprites" / "player"
TARGET_W, TARGET_H = ip.TARGET_W, ip.TARGET_H


def read_frames(video, count, start, end, crop_bottom):
    container = av.open(str(video))
    stream = container.streams.video[0]
    duration = float(stream.duration * stream.time_base) if stream.duration and stream.time_base else 3.0
    if end is None:
        end = duration
    targets = [start + (end - start) * index / count for index in range(count)]
    frames = []
    target_index = 0
    for frame in container.decode(stream):
        if target_index >= len(targets):
            break
        timestamp = float(frame.pts * stream.time_base) if frame.pts is not None else 0.0
        if timestamp + 1e-6 < targets[target_index]:
            continue
        image = frame.to_image().convert("RGB")
        width, height = image.size
        if crop_bottom:
            image = image.crop((0, 0, width, max(1, height - crop_bottom)))
        frames.append(image)
        target_index += 1
    container.close()
    print("读取 %d 帧，区间 %.2f–%.2fs" % (len(frames), start, end))
    return frames


def main(argv=None):
    parser = argparse.ArgumentParser(description="待机视频 → 游戏 idle 帧")
    parser.add_argument("video")
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--end", type=float, default=None)
    parser.add_argument("--crop-bottom", type=int, default=80)
    parser.add_argument("--tolerance", type=float, default=55.0)
    parser.add_argument("--out", default=str(PLAYER))
    args = parser.parse_args(argv)

    frames = read_frames(Path(args.video), args.count, args.start, args.end, args.crop_bottom)
    if not frames:
        raise SystemExit("没有读到帧")

    cuts = []
    boxes = []
    for image in frames:
        cut = ip.remove_background(image, ip.corner_color(image), args.tolerance)
        box = cut.getchannel("A").getbbox()
        if box is None:
            continue
        cuts.append(cut)
        boxes.append(box)

    ux1 = min(b[0] for b in boxes)
    uy1 = min(b[1] for b in boxes)
    ux2 = max(b[2] for b in boxes)
    uy2 = max(b[3] for b in boxes)
    uw, uh = ux2 - ux1, uy2 - uy1
    scale = min(ip.INNER_MAX_W / uw, ip.INNER_MAX_H / uh)
    print("并集 %s，缩放 %.4f" % ((ux1, uy1, ux2, uy2), scale))

    out = Path(args.out)
    for index, cut in enumerate(cuts):
        piece = cut.crop((ux1, uy1, ux2, uy2)).resize(
            (max(1, round(uw * scale)), max(1, round(uh * scale))), Image.LANCZOS
        )
        canvas = Image.new("RGBA", (TARGET_W, TARGET_H), (0, 0, 0, 0))
        canvas.alpha_composite(piece, ((TARGET_W - piece.width) // 2, TARGET_H - piece.height))
        canvas = ip.clean_sprite(canvas)
        right = ImageOps.mirror(canvas)  # e：尾巴在左
        name = "idle" if index == 0 else "idle%d" % index
        right.save(out / ("player_e_%s.png" % name))
        canvas.save(out / ("player_w_%s.png" % name))
    print("已写入 %d 帧 idle → %s" % (len(cuts), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
