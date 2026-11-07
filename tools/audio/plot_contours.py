#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把多段语音的语调画在同一张图上（归一化时间 + 相对半音），便于比较差异。

用法：
    python tools/audio/plot_contours.py a.mp3 b.mp3 c.m4a d.m4a -o art_preview/intonation/contours.png
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import transfer_intonation as ti  # noqa: E402


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+")
    parser.add_argument("-o", "--out", default="art_preview/intonation/contours.png")
    args = parser.parse_args()

    fig, axes = plt.subplots(2, 1, figsize=(11, 8))
    palette = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#8c564b"]
    summary = []
    for i, path in enumerate(args.files):
        x = ti.load_audio(path)
        data = ti.analyze(x)
        f0 = data["f0"]
        times = data["times"]
        st, median = ti.semitone_contour(f0)
        valid = ~np.isnan(st)
        t0, t1 = ti.voiced_span(f0, times)
        u = (times[valid] - t0) / (t1 - t0)
        color = palette[i % len(palette)]
        label = os.path.basename(path)
        axes[0].plot(u, st[valid], color=color, lw=1.6, label=label)
        axes[1].plot(times[valid] * 1000, f0[valid], color=color, lw=1.2, label=label)
        summary.append((label, median, float(np.max(f0)), float(np.min(f0[f0 > 0])),
                        (t1 - t0) * 1000))

    axes[0].axhline(0, color="#999", lw=0.6, ls="--")
    axes[0].set_ylabel("semitones vs own median")
    axes[0].set_title("normalised intonation contours")
    axes[0].legend(fontsize=8, ncol=2)
    axes[0].grid(alpha=0.25)
    axes[1].set_xlabel("time (ms)")
    axes[1].set_ylabel("F0 (Hz)")
    axes[1].set_title("raw F0 traces")
    axes[1].legend(fontsize=8, ncol=2)
    axes[1].grid(alpha=0.25)
    plt.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    plt.savefig(args.out, dpi=120)
    print("图已保存:", args.out)
    print("文件          中位F0   最低    最高   有声段时长")
    for label, med, hi, lo, dur in summary:
        print("%-12s %7.1f %6.0f %6.0f %10.0f ms" % (label, med, lo, hi, dur))
    return 0


if __name__ == "__main__":
    sys.exit(main())
