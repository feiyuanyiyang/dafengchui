# -*- coding: utf-8 -*-
"""从一段行走/待机视频里抽帧，供游戏动画使用（不依赖 ffmpeg，使用 PyAV）。

用法
    python tools/art/extract_frames.py walk.mp4 --count 8
    python tools/art/extract_frames.py walk.mp4 --count 8 --start 0.3 --end 1.1
    python tools/art/extract_frames.py walk.mp4 --count 8 --crop-bottom 160

输出
    art_preview/walk_frames/frame_00.png ... frame_07.png

说明
    - 即梦图生视频通常 3 s / 24 fps；抽 8 帧约等于 0.67 s 的循环。
    - 抽帧后先用 import_player.py 检查脚底/大小，再决定用哪几帧。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import av
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "art_preview" / "walk_frames"


def extract(video_path, out_dir, count, start, end, crop_bottom):
    container = av.open(str(video_path))
    stream = container.streams.video[0]
    duration = None
    if stream.duration and stream.time_base:
        duration = float(stream.duration * stream.time_base)
    if end is None:
        end = duration if duration else 3.0
    if end <= start:
        raise SystemExit("--end 必须大于 --start")
    targets = [start + (end - start) * index / count for index in range(count)]
    print(
        "视频 %s：%sx%s, %.3f fps, 时长 %.2fs；抽 %d 帧，区间 %.2f–%.2fs"
        % (
            video_path,
            stream.codec_context.width,
            stream.codec_context.height,
            float(stream.average_rate) if stream.average_rate else 0.0,
            duration or 0.0,
            count,
            start,
            end,
        )
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    target_index = 0
    saved = 0
    for frame in container.decode(stream):
        if target_index >= len(targets):
            break
        timestamp = float(frame.pts * stream.time_base) if frame.pts is not None else 0.0
        if timestamp + 1e-6 < targets[target_index]:
            continue
        image = frame.to_image()
        if crop_bottom > 0:
            width, height = image.size
            image = image.crop((0, 0, width, max(1, height - crop_bottom)))
        path = out_dir / ("frame_%02d.png" % saved)
        image.save(path)
        print("  %s  t=%.3fs" % (path.name, timestamp))
        saved += 1
        target_index += 1
    container.close()
    if saved < count:
        print("警告：只抽到 %d/%d 帧，可能区间超出视频长度。" % (saved, count))
    return saved


def main(argv=None):
    parser = argparse.ArgumentParser(description="从视频抽帧（PyAV，无需 ffmpeg）")
    parser.add_argument("video", help="输入视频（mp4/mov 等）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    parser.add_argument("--count", type=int, default=8, help="抽帧数量")
    parser.add_argument("--start", type=float, default=0.0, help="起始秒")
    parser.add_argument("--end", type=float, default=None, help="结束秒（默认整段）")
    parser.add_argument("--crop-bottom", type=int, default=0, help="裁掉底部 N 像素（去水印）")
    args = parser.parse_args(argv)
    saved = extract(
        Path(args.video), Path(args.out), max(1, args.count), args.start, args.end, args.crop_bottom
    )
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
