#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时分析脚本：把一段 m4a/wav 拆成若干发声片段，输出时长/F0/能量摘要。

用法：python tools/audio/analyze_takes.py <音频> [--outdir 目录]
"""

from __future__ import annotations

import argparse
import os
import wave
from array import array

import numpy as np

SR = 44100


def decode(path):
    """用 PyAV 解码任意音频为 44.1k 单声道 float32 ndarray。"""
    import av

    container = av.open(path)
    stream = next(s for s in container.streams if s.type == "audio")
    resampler = av.AudioResampler(format="flt", layout="mono", rate=SR)
    chunks = []
    for frame in container.decode(stream):
        for out in resampler.resample(frame):
            chunks.append(out.to_ndarray().reshape(-1))
    for out in resampler.resample(None):
        chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        raise SystemExit("没有解码到音频")
    return np.concatenate(chunks).astype(np.float32)


def write_wav(path, samples, sr=SR):
    data = np.clip(samples, -1.0, 1.0)
    pcm = (data * 32767.0).astype("<i2")
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(pcm.tobytes())


def f0_track(samples, sr=SR, frame=1024, hop=128, fmin=60.0, fmax=600.0):
    """归一化自相关 + 抛物线插值的 F0 估计，返回 (秒, Hz, 清浊置信度)。"""
    win = np.hanning(frame).astype(np.float32)
    lo = int(sr / fmax)
    hi = int(sr / fmin)
    times, f0s, confs = [], [], []
    for start in range(0, len(samples) - frame, hop):
        seg = samples[start:start + frame].astype(np.float64)
        seg = seg - seg.mean()
        energy = float(np.sqrt(np.mean(seg * seg)))
        if energy < 1e-4:
            times.append(start / sr)
            f0s.append(0.0)
            confs.append(0.0)
            continue
        seg *= win
        corr = np.correlate(seg, seg, mode="full")[frame - 1:]
        corr /= corr[0] + 1e-12
        seg_lo, seg_hi = max(lo, 1), min(hi, len(corr) - 1)
        region = corr[seg_lo:seg_hi]
        idx = int(np.argmax(region))
        peak = region[idx]
        lag = seg_lo + idx
        if 0 < lag < len(corr) - 1:
            a, b, c = corr[lag - 1], corr[lag], corr[lag + 1]
            denom = a - 2 * b + c
            shift = 0.5 * (a - c) / denom if abs(denom) > 1e-12 else 0.0
            lag = lag + float(np.clip(shift, -1.0, 1.0))
        f0 = sr / lag if lag > 0 else 0.0
        times.append(start / sr)
        f0s.append(f0 if peak > 0.30 else 0.0)
        confs.append(float(peak))
    return np.array(times), np.array(f0s), np.array(confs)


def frames(samples, sr=SR, frame=1024, hop=128):
    n = max(0, (len(samples) - frame) // hop + 1)
    idx = np.arange(frame)[None, :] + hop * np.arange(n)[:, None]
    return samples[idx].astype(np.float64)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--outdir", default="art_preview/voice_takes/takes")
    parser.add_argument("--threshold", type=float, default=0.04,
                        help="片段切分门限（相对峰值）")
    parser.add_argument("--min-gap", type=float, default=0.10)
    parser.add_argument("--pad", type=float, default=0.02)
    args = parser.parse_args()

    samples = decode(args.path)
    peak = float(np.max(np.abs(samples))) or 1.0
    norm = samples / peak
    print("总时长 %.3f s  峰值 %.4f" % (len(samples) / SR, peak))

    # 10 ms 包络
    env_hop = 441
    n = len(norm) // env_hop
    env = np.abs(norm[: n * env_hop]).reshape(n, env_hop).max(axis=1)
    speech = env > args.threshold
    # 合并间隔
    min_gap = int(args.min_gap * SR / env_hop)
    i = 0
    while i < len(speech):
        if speech[i]:
            j = i
            while j + 1 < len(speech) and speech[j + 1]:
                j += 1
            i = j + 1
        else:
            i += 1
    # 找连续块，允许短静音
    regions = []
    i = 0
    while i < n:
        if speech[i]:
            j = i
            gap = 0
            last = i
            while j + 1 < n:
                j += 1
                if speech[j]:
                    gap = 0
                    last = j
                else:
                    gap += 1
                    if gap > min_gap:
                        break
            regions.append((i, last + 1))
            i = j + 1
        else:
            i += 1

    os.makedirs(args.outdir, exist_ok=True)
    print("检测到 %d 段" % len(regions))
    for k, (a, b) in enumerate(regions):
        t0 = a * env_hop / SR
        t1 = (b * env_hop + env_hop) / SR
        t0 = max(0.0, t0 - args.pad)
        t1 = min(len(samples) / SR, t1 + args.pad)
        seg = norm[int(t0 * SR): int(t1 * SR)]
        tt, f0, conf = f0_track(seg)
        voiced = f0 > 0
        if voiced.sum() >= 3:
            v = f0[voiced]
            vt = tt[voiced]
            med = float(np.median(v))
            lo, hi = float(np.percentile(v, 10)), float(np.percentile(v, 90))
            slope = float(np.polyfit(vt, 12 * np.log2(v / med), 1)[0])
            print(
                "段%d  %.3f–%.3f s  时长 %.3f s  有声 %.0f%%  "
                "F0 中位 %.1f Hz  P10–90 %.1f–%.1f  半音斜率 %+.1f st/s"
                % (k + 1, t0, t1, t1 - t0, 100.0 * voiced.mean(),
                   med, lo, hi, slope)
            )
            # 打印轮廓（每 25 ms 一个点）
            step = max(1, int(0.025 / (tt[1] - tt[0])) if len(tt) > 1 else 1)
            contour = []
            for idx in range(0, len(tt), step):
                if f0[idx] > 0:
                    contour.append("%+.0f" % (12 * np.log2(f0[idx] / med)))
                else:
                    contour.append(".")
            print("      轮廓(相对中位, 半音): " + " ".join(contour))
        out = os.path.join(args.outdir, "take_%02d.wav" % (k + 1))
        write_wav(out, seg * 0.95)
        print("      -> %s" % out)


if __name__ == "__main__":
    main()
