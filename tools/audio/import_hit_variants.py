#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把海螺 AI（MiniMax）导出的受击音效导入成游戏用的 WAV 变体。

只做“导入三件事”：解码成 44.1 kHz 单声道 → 裁掉首尾静音 → 统一峰值。
不加变调、气声、滤波等音色处理，保留素材原来的发音细节。

默认映射（5 个里挑的 3 个）：
    hit_voice/MiniMax_唉.mp3     -> assets/audio/sfx/hit_1.wav
    hit_voice/MiniMax_饿啊.mp3   -> assets/audio/sfx/hit_2.wav
    hit_voice/MiniMax_哎呀.mp3   -> assets/audio/sfx/hit_3.wav

用法
    python tools/audio/import_hit_variants.py
    python tools/audio/import_hit_variants.py 源.mp3:hit_1 另一个.m4a:hit_2
    python tools/audio/import_hit_variants.py --peak 0.6 --trim-db -46

输出格式固定为 WAV / PCM / 44100 Hz / 16-bit / 单声道，和现有音效一致。
"""

from __future__ import annotations

import argparse
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from transfer_intonation import load_audio, save_wav  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SFX_DIR = os.path.join(ROOT, "assets", "audio", "sfx")
SR = 44100

DEFAULT_PAIRS = (
    ("hit_voice/MiniMax_唉.mp3", "hit_1"),
    ("hit_voice/MiniMax_饿啊.mp3", "hit_2"),
    ("hit_voice/MiniMax_哎呀.mp3", "hit_3"),
    ("hit_voice/MiniMax_呃.mp3", "hit_4"),
    ("hit_voice/啊11.mp3", "hit_5"),
    ("hit_voice/饿啊1.m4a", "hit_6"),
    ("hit_voice/哎呀1.mp3", "hit_7"),
    ("hit_voice/哎呀2.mp3", "hit_8"),
)


def trim(x: np.ndarray, threshold_db: float,
         pad_in: float, pad_out: float) -> np.ndarray:
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak <= 0.0:
        return x
    threshold = peak * 10.0 ** (threshold_db / 20.0)
    index = np.where(np.abs(x) > threshold)[0]
    if len(index) == 0:
        return x
    start = max(0, index[0] - int(round(pad_in * SR)))
    end = min(len(x), index[-1] + int(round(pad_out * SR)) + 1)
    return x[start:end]


def fade(x: np.ndarray, fade_in: float, fade_out: float) -> np.ndarray:
    out = np.array(x, dtype=np.float64, copy=True)
    n = len(out)
    fin = min(n, max(1, int(round(fade_in * SR))))
    fout = min(n, max(1, int(round(fade_out * SR))))
    if fin:
        out[:fin] *= 0.5 - 0.5 * np.cos(np.linspace(0.0, math.pi, fin))
    if fout:
        out[-fout:] *= 0.5 + 0.5 * np.cos(np.linspace(0.0, math.pi, fout))
    return out


def normalize(x: np.ndarray, peak: float) -> np.ndarray:
    current = float(np.max(np.abs(x))) if len(x) else 0.0
    if current <= 1e-9:
        return x
    return x * (peak / current)


def convert(source: str, name: str, peak: float, trim_db: float,
            pad_in: float, pad_out: float,
            fade_in: float, fade_out: float) -> None:
    if not os.path.isabs(source):
        source = os.path.join(ROOT, source)
    if not os.path.isfile(source):
        raise SystemExit("找不到源文件：%s" % source)
    data = load_audio(source)
    original_duration = len(data) / SR
    data = trim(data, trim_db, pad_in, pad_out)
    data = normalize(data, peak)
    data = fade(data, fade_in, fade_out)

    out = os.path.join(SFX_DIR, name + ".wav")
    os.makedirs(SFX_DIR, exist_ok=True)
    save_wav(out, data)
    rms = float(np.sqrt(np.mean(data ** 2))) if len(data) else 0.0
    print("%-22s -> %-46s %5.3fs (原 %.3fs) 峰值 %.2f  RMS %+.1f dBFS"
          % (os.path.basename(source), os.path.relpath(out, ROOT),
             len(data) / SR, original_duration, float(np.max(np.abs(data))),
             20.0 * math.log10(rms + 1e-12)))


def main() -> int:
    parser = argparse.ArgumentParser(description="导入受击音效 WAV 变体")
    parser.add_argument("pairs", nargs="*",
                        help="源文件:输出名（省略则使用默认的三个 MiniMax 素材）")
    parser.add_argument("--peak", type=float, default=0.6, help="归一化峰值")
    parser.add_argument("--trim-db", type=float, default=-46.0,
                        help="裁静音门限（相对峰值 dBFS）")
    parser.add_argument("--pad-in", type=float, default=0.010, help="保留起音前的秒数")
    parser.add_argument("--pad-out", type=float, default=0.030, help="保留尾音后的秒数")
    parser.add_argument("--fade-in", type=float, default=0.003, help="淡入秒数")
    parser.add_argument("--fade-out", type=float, default=0.030, help="淡出秒数")
    args = parser.parse_args()

    if args.pairs:
        jobs = []
        for pair in args.pairs:
            if ":" not in pair:
                raise SystemExit("格式应为 源文件:输出名，例如 a.mp3:hit_1")
            source, name = pair.rsplit(":", 1)
            jobs.append((source, name.strip()))
    else:
        jobs = list(DEFAULT_PAIRS)

    for source, name in jobs:
        convert(source, name, args.peak, args.trim_db,
                args.pad_in, args.pad_out, args.fade_in, args.fade_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
