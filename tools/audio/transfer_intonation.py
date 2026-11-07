#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一段短语音的语调（基频轮廓）迁移到另一段语音上，保留目标自身的音色与发音细节。

原理
    1. YIN 估计两段音频的基频 F0 轮廓（清浊判断 + 中值平滑 + 倍频纠错）；
    2. 以“有声段”归一化时间对齐，把参考音频的 F0 轮廓弯成半音曲线；
    3. 目标 = 自身音区 × 参考旋律（可混合自身旋律，strength 控制）；
    4. TD-PSOLA（基音同步叠加）重合成：只改音高、不改共振峰，所以音色和咬字细节保留；
    5. 清音段直接沿用原波形，边界做交叉淡化。

只依赖 numpy + scipy；解码 mp3/m4a 需要 PyAV（项目里已有）。
不需要联网，不需要 ElevenLabs / RVC。

常用命令
    # 先看四段音频的语调统计
    python tools/audio/transfer_intonation.py --report a.mp3 b.mp3 c.m4a d.m4a

    # 以 aiya1 的语调为准，生成另外三段的统一版本
    python tools/audio/transfer_intonation.py -r aiya1.mp3 -t aiya2.mp3 ea1.m4a ea2.m4a \
        --outdir art_preview/intonation/by_aiya1 --strength 0.9

    # 自检：把一段音频按自己的语调重合成一遍，检查 PSOLA 是否无损
    python tools/audio/transfer_intonation.py --identity a.mp3
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import wave

import numpy as np
from scipy import signal

SR = 44100


# --------------------------------------------------------------------------- #
# 读写
# --------------------------------------------------------------------------- #
def load_audio(path: str, sr: int = SR) -> np.ndarray:
    """解码任意音频为单声道 float64 @ sr。"""
    lower = path.lower()
    if lower.endswith(".wav"):
        try:
            with wave.open(path, "rb") as handle:
                channels = handle.getnchannels()
                width = handle.getsampwidth()
                rate = handle.getframerate()
                raw = handle.readframes(handle.getnframes())
            if width == 2:
                data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
            elif width == 1:
                data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
            elif width == 3:
                buf = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
                values = buf[:, 0] | (buf[:, 1] << 8) | (buf[:, 2] << 16)
                values = np.where(values >= 1 << 23, values - (1 << 24), values)
                data = values.astype(np.float64) / 8388608.0
            elif width == 4:
                data = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
            else:
                raise ValueError("不支持的 WAV 位深：%d" % width)
            if channels > 1:
                data = data.reshape(-1, channels).mean(axis=1)
            if rate != sr:
                data = signal.resample_poly(data, sr, rate)
            return np.ascontiguousarray(data, dtype=np.float64)
        except (wave.Error, EOFError):
            pass  # 交给 PyAV

    import av  # 延迟导入：只有 mp3/m4a 才需要

    with av.open(path) as container:
        stream = next(s for s in container.streams if s.type == "audio")
        resampler = av.AudioResampler(format="flt", layout="mono", rate=sr)
        chunks = []
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        raise RuntimeError("没有解码到音频：%s" % path)
    return np.concatenate(chunks).astype(np.float64)


def save_wav(path: str, samples: np.ndarray, sr: int = SR) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    data = np.clip(np.asarray(samples, dtype=np.float64), -1.0, 1.0)
    pcm = (data * 32767.0).astype("<i2")
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(pcm.tobytes())


# --------------------------------------------------------------------------- #
# F0 估计
# --------------------------------------------------------------------------- #
def yin_f0(x: np.ndarray, sr: int = SR, frame: int = 1024, hop: int = 128,
           fmin: float = 70.0, fmax: float = 700.0,
           threshold: float = 0.15) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """YIN 基频估计。返回 (f0, 非周期度, 帧 RMS)，帧中心时间见 frame_times()。"""
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    if n < frame:
        return np.zeros(0), np.ones(0), np.zeros(0)

    tau_min = max(2, int(math.floor(sr / fmax)))
    tau_max = min(frame - 2, int(math.ceil(sr / fmin)))
    seg_len = frame + tau_max
    nfft = 1 << int(math.ceil(math.log2(max(2, seg_len * 2))))
    n_frames = 1 + max(0, (n - frame) // hop)

    f0 = np.zeros(n_frames)
    aper = np.ones(n_frames)
    rms = np.zeros(n_frames)
    taus = np.arange(tau_max + 1, dtype=np.float64)

    for i in range(n_frames):
        start = i * hop
        seg = x[start:start + seg_len]
        if len(seg) < seg_len:
            seg = np.pad(seg, (0, seg_len - len(seg)))
        seg = seg - seg[:frame].mean()
        rms[i] = math.sqrt(float(np.mean(seg[:frame] ** 2)))

        # r[tau] = sum_{j<frame} seg[j] * seg[j+tau]，只累加前 frame 项
        head_spectrum = np.fft.rfft(seg[:frame], nfft)
        spectrum = np.fft.rfft(seg, nfft)
        ac = np.fft.irfft(np.conj(head_spectrum) * spectrum, nfft)[:tau_max + 1]

        energy = np.concatenate(([0.0], np.cumsum(seg * seg)))
        e0 = energy[frame]
        et = energy[taus.astype(int) + frame] - energy[taus.astype(int)]
        diff = e0 + et - 2.0 * ac
        diff[0] = 0.0

        cmnd = np.ones(tau_max + 1)
        cumulative = np.cumsum(diff[1:])
        cmnd[1:] = diff[1:] * np.arange(1, tau_max + 1) / (cumulative + 1e-12)

        search = cmnd[tau_min:tau_max + 1]
        below = np.where(search < threshold)[0]
        if len(below):
            k = tau_min + int(below[0])
        else:
            k = tau_min + int(np.argmin(search))

        delta = 0.0
        if 1 <= k < tau_max:
            a, b, c = cmnd[k - 1], cmnd[k], cmnd[k + 1]
            den = a - 2.0 * b + c
            if abs(den) > 1e-12:
                delta = float(np.clip(0.5 * (a - c) / den, -1.0, 1.0))
        tau = k + delta
        f0[i] = sr / tau if tau > 0 else 0.0
        aper[i] = float(cmnd[k])

    return f0, aper, rms


def frame_times(n_frames: int, hop: int = 128, frame: int = 1024,
                sr: int = SR) -> np.ndarray:
    return (np.arange(n_frames) * hop + frame / 2.0) / sr


def clean_f0(f0: np.ndarray, aper: np.ndarray, rms: np.ndarray,
             frame: int = 1024, hop: int = 128, sr: int = SR,
             voiced_aper: float = 0.35, floor_db: float = -45.0) -> np.ndarray:
    """清浊判定 + 短缺口填补 + 局部倍频纠错 + 中值平滑。"""
    if len(f0) == 0:
        return f0
    peak = float(np.max(rms)) if len(rms) else 0.0
    floor = peak * (10.0 ** (floor_db / 20.0))
    voiced = (f0 >= 70.0) & (f0 <= 700.0) & (aper < voiced_aper) & (rms > floor)
    work = np.where(voiced, f0, np.nan)

    # 填补 <= 3 帧的 voiced 空洞
    n = len(work)
    i = 0
    while i < n:
        if np.isnan(work[i]):
            j = i
            while j + 1 < n and np.isnan(work[j + 1]):
                j += 1
            if i > 0 and j + 1 < n and (j - i + 1) <= 3:
                work[i:j + 1] = np.linspace(work[i - 1], work[j + 1], (j - i + 1) + 2)[1:-1]
            i = j + 1
        else:
            i += 1

    # 局部中值倍频纠错
    for i in range(n):
        if np.isnan(work[i]):
            continue
        lo, hi = max(0, i - 3), min(n, i + 4)
        local = np.nanmedian(work[lo:hi])
        if np.isnan(local) or local <= 0:
            continue
        guard = 0
        while work[i] > 1.65 * local and guard < 3:
            work[i] /= 2.0
            guard += 1
        guard = 0
        while work[i] < 0.60 * local and guard < 3:
            work[i] *= 2.0
            guard += 1
        work[i] = min(work[i], 900.0)

    # 5 帧中值平滑（只平滑有声帧）
    result = work.copy()
    for i in range(n):
        if np.isnan(work[i]):
            continue
        lo, hi = max(0, i - 2), min(n, i + 3)
        vals = work[lo:hi]
        vals = vals[~np.isnan(vals)]
        if len(vals):
            result[i] = float(np.median(vals))
    return np.nan_to_num(result, nan=0.0)


def analyze(x: np.ndarray, sr: int = SR) -> dict:
    f0_raw, aper, rms = yin_f0(x, sr)
    f0 = clean_f0(f0_raw, aper, rms, sr=sr)
    return {
        "f0": f0,
        "aper": aper,
        "rms": rms,
        "times": frame_times(len(f0), sr=sr),
        "hop": 128,
        "frame": 1024,
        "sr": sr,
    }


def voiced_span(f0: np.ndarray, times: np.ndarray) -> tuple[float, float]:
    idx = np.where(f0 > 0)[0]
    if len(idx) == 0:
        return 0.0, 0.0
    return float(times[idx[0]]), float(times[idx[-1]])


def semitone_contour(f0: np.ndarray) -> tuple[np.ndarray, float]:
    """有声帧的半音轮廓（相对中位数），无声帧为 nan。"""
    st = np.full(len(f0), np.nan)
    idx = f0 > 0
    if not np.any(idx):
        return st, 0.0
    median = float(np.median(f0[idx]))
    st[idx] = 12.0 * np.log2(f0[idx] / median)
    return st, median


# --------------------------------------------------------------------------- #
# 音高标记 & TD-PSOLA
# --------------------------------------------------------------------------- #
def _voiced_runs(f0: np.ndarray) -> list[tuple[int, int]]:
    runs = []
    i = 0
    while i < len(f0):
        if f0[i] > 0:
            j = i
            while j + 1 < len(f0) and f0[j + 1] > 0:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    return runs


def _f0_lookup(f0: np.ndarray, sample: float, hop: int, frame: int,
               default: float = 0.0) -> float:
    # 帧 i 覆盖 [i*hop, i*hop+frame)，所以按帧起点索引，避免浊音段边界错位
    idx = int(sample // hop)
    idx = min(len(f0) - 1, max(0, idx))
    value = float(f0[idx])
    return value if value > 0 else default


def _run_marks(a: int, b: int, f0: np.ndarray, hop: int, frame: int,
               sr: int) -> tuple[np.ndarray, np.ndarray]:
    """在一个浊音段里按“当前周期”铺分析标记（不需要检测声门脉冲）。

    相邻标记正好隔一个基音周期，所以 2 周期 Hann 窗以 1 周期步长叠加
    会还原原波形——这是 PSOLA 能保持音色的关键。
    """
    marks: list[int] = []
    periods: list[float] = []
    pos = float(a)
    while pos < b - 2:
        mark = int(round(pos))
        period = sr / max(_f0_lookup(f0, mark, hop, frame, 120.0), 50.0)
        marks.append(mark)
        periods.append(period)
        pos += period
    return np.asarray(marks, dtype=int), np.asarray(periods, dtype=np.float64)


def detect_marks(x: np.ndarray, sr: int, f0: np.ndarray, hop: int = 128,
                 frame: int = 1024) -> tuple[np.ndarray, np.ndarray]:
    """返回整段音频的分析标记与对应周期。"""
    del x  # 仅用 F0 铺栅格；保留参数是为了接口直观
    n = len(f0) * hop + frame
    all_marks: list[np.ndarray] = []
    all_periods: list[np.ndarray] = []
    for i0, i1 in _voiced_runs(f0):
        a = max(0, i0 * hop)
        b = min(n, i1 * hop + frame)
        marks, periods = _run_marks(a, b, f0, hop, frame, sr)
        if len(marks):
            all_marks.append(marks)
            all_periods.append(periods)
    if not all_marks:
        return np.zeros(0, dtype=int), np.zeros(0)
    return np.concatenate(all_marks), np.concatenate(all_periods)


def synth_psola(x: np.ndarray, sr: int, f0_src: np.ndarray, f0_tgt: np.ndarray,
                hop: int = 128, frame: int = 1024,
                speed: float = 1.0,
                debug: bool = False) -> tuple[np.ndarray, float]:
    """按 f0_tgt 重新合成。返回 (波形, PSOLA 覆盖度)。"""
    n_in = len(x)
    n_out = int(round(n_in * speed))
    if n_out <= 0:
        return np.zeros(0), 0.0

    src_positions = np.arange(n_out, dtype=np.float64) / speed
    base = np.interp(src_positions, np.arange(n_in, dtype=np.float64), x)

    pad = 2 * frame
    out = np.zeros(n_out + pad)
    wsum = np.zeros(n_out + pad)
    placements = []

    for i0, i1 in _voiced_runs(f0_src):
        a = max(0, i0 * hop)
        b = min(n_in, i1 * hop + frame)
        if b - a < 16:
            continue
        marks, periods = _run_marks(a, b, f0_src, hop, frame, sr)
        if len(marks) < 2:
            continue

        ts = a * speed
        out_limit = b * speed
        while ts < out_limit:
            src_sample = ts / speed
            f = _f0_lookup(f0_tgt, src_sample, hop, frame, 0.0)
            if f <= 0.0:
                ts += sr / max(120.0, f0_src[max(0, int(src_sample / hop))])
                continue

            idx = int(np.searchsorted(marks, src_sample))
            if idx >= len(marks):
                idx = len(marks) - 1
            elif idx > 0 and (src_sample - marks[idx - 1]) <= (marks[idx] - src_sample):
                idx -= 1
            mark = int(marks[idx])
            period = float(periods[idx])

            lo = max(0, int(round(mark - period)))
            hi = min(n_in, int(round(mark + period)))
            if hi - lo < 8:
                ts += sr / f
                continue

            window = np.hanning(hi - lo)
            offset = int(round(ts)) - (mark - lo)
            if offset < 0 or offset + (hi - lo) > len(out):
                ts += sr / f
                continue
            out[offset:offset + (hi - lo)] += x[lo:hi] * window
            wsum[offset:offset + (hi - lo)] += window
            if debug:
                placements.append((float(ts), mark, offset - lo, int(hi - lo)))
            ts += sr / f

    covered = wsum[:n_out]
    rec = np.zeros(n_out)
    good = covered > 1e-3
    rec[good] = out[:n_out][good] / covered[good]

    weight = np.clip(covered, 0.0, 1.0)
    kernel = max(3, int(0.003 * sr))
    weight = np.convolve(weight, np.ones(kernel) / kernel, mode="same")

    y = rec * weight + base * (1.0 - weight)
    coverage = float(np.mean(weight > 0.5)) if len(weight) else 0.0
    if debug:
        return y, coverage, {"rec": rec, "weight": weight, "base": base,
                             "covered": covered, "placements": placements}
    return y, coverage


# --------------------------------------------------------------------------- #
# 语调迁移
# --------------------------------------------------------------------------- #
def transfer_contour(f0_src: np.ndarray, f0_ref: np.ndarray,
                     hop: int = 128, frame: int = 1024, sr: int = SR,
                     strength: float = 0.85, offset: float = 0.0,
                     register: str = "keep",
                     keep_extremes: bool = True) -> np.ndarray:
    """把 f0_ref 的旋律（半音形状）套到 f0_src 的音区上。"""
    src_st, src_median = semitone_contour(f0_src)
    ref_st, ref_median = semitone_contour(f0_ref)
    if src_median <= 0 or ref_median <= 0:
        return f0_src.copy()

    n_src = len(f0_src)
    times_src = frame_times(n_src, hop=hop, frame=frame, sr=sr)
    t0s, t1s = voiced_span(f0_src, times_src)
    times_ref = frame_times(len(f0_ref), hop=hop, frame=frame, sr=sr)
    t0r, t1r = voiced_span(f0_ref, times_ref)

    if t1s <= t0s or t1r <= t0r:
        return f0_src.copy()

    u_src = (times_src - t0s) / (t1s - t0s)
    u_ref = (times_ref - t0r) / (t1r - t0r)
    valid_ref = ~np.isnan(ref_st)
    ref_curve = np.interp(
        np.clip(u_src, 0.0, 1.0),
        u_ref[valid_ref],
        ref_st[valid_ref],
    )

    base = ref_median if register == "match" else src_median
    blended = (1.0 - strength) * np.nan_to_num(src_st) + strength * ref_curve
    target = base * np.power(2.0, (blended + offset) / 12.0)

    # 限制单帧升降幅度（相对原音高不超过 +/-9 半音，避免估错时啸叫）
    target = np.clip(target, f0_src * 2.0 ** (-9.0 / 12.0),
                     f0_src * 2.0 ** (9.0 / 12.0))

    # 气声/气泡音（相对自身中位数低 7 个半音以上）保持原样，别把它拉成怪声
    if keep_extremes:
        extreme = np.nan_to_num(src_st) < -7.0
        target[extreme] = f0_src[extreme]

    target[f0_src <= 0] = 0.0
    return target


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def normalize_peak(x: np.ndarray, peak: float) -> np.ndarray:
    current = float(np.max(np.abs(x))) if len(x) else 0.0
    if current <= 1e-9:
        return x
    return x * (peak / current)


def trim_silence(x: np.ndarray, sr: int, threshold_db: float = -45.0,
                 pad: float = 0.015) -> np.ndarray:
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak <= 0:
        return x
    threshold = peak * 10.0 ** (threshold_db / 20.0)
    idx = np.where(np.abs(x) > threshold)[0]
    if len(idx) == 0:
        return x
    pad_n = int(pad * sr)
    return x[max(0, idx[0] - pad_n):min(len(x), idx[-1] + pad_n)]


def contour_report(x: np.ndarray, name: str, sr: int = SR) -> None:
    data = analyze(x, sr)
    f0 = data["f0"]
    times = data["times"]
    t0, t1 = voiced_span(f0, times)
    st, median = semitone_contour(f0)
    voiced = st[~np.isnan(st)]
    if len(voiced) == 0:
        print("%-10s  没有检测到浊音" % name)
        return
    dur = len(x) / sr
    pitch = f0[f0 > 0]
    print("%-10s 时长 %.3fs  有声 %.3f–%.3fs  F0 中位 %.1f Hz  范围 %.0f–%.0f Hz  "
          "半音跨度(5–95%%) %.1f st"
          % (name, dur, t0, t1, median,
             float(np.min(pitch)), float(np.max(pitch)),
             float(np.percentile(voiced, 95) - np.percentile(voiced, 5))))
    # 20 点轮廓
    u = np.linspace(0, 1, 20)
    valid = ~np.isnan(st)
    curve = np.interp(u, (times[valid] - t0) / (t1 - t0), st[valid])
    print("           形状: " + " ".join("%+.1f" % v for v in curve))


def make_plot(entries: list[dict], out_path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = len(entries)
    fig, axes = plt.subplots(rows, 2, figsize=(15, 2.6 * rows + 1))
    if rows == 1:
        axes = np.array([axes])
    for row, entry in enumerate(entries):
        x = entry["x"]
        sr = entry["sr"]
        data = analyze(x, sr)
        f0 = data["f0"]
        times = data["times"]
        t = np.arange(len(x)) / sr
        ax = axes[row, 0]
        ax.plot(t, x, lw=0.3, color="#555")
        ax.set_ylim(-1.05, 1.05)
        ax.set_ylabel(entry["name"], rotation=0, ha="right", va="center")
        ax2 = axes[row, 1]
        valid = f0 > 0
        ax2.plot(times[valid], f0[valid], ".", ms=2.5,
                 color=entry.get("color", "#1f77b4"))
        if entry.get("ref_f0") is not None:
            rf = entry["ref_f0"]
            rt = entry["ref_times"]
            rv = rf > 0
            ax2.plot(rt[rv], rf[rv], ".", ms=2.5, color="#d62728",
                     alpha=0.8)
        ax2.set_xlim(0, len(x) / sr)
        ax2.set_ylim(150, 650)
        ax2.grid(alpha=0.25)
    axes[0, 0].set_title("waveform")
    axes[0, 1].set_title("F0 (dots) / reference (red)")
    plt.tight_layout()
    plt.savefig(out_path, dpi=110)
    print("图已保存:", out_path)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(
        description="语调用迁移：保留音色与咬字，只改音高旋律")
    parser.add_argument("files", nargs="*", help="--report 时直接列出的音频")
    parser.add_argument("--reference", "-r", help="提供语调的参考音频")
    parser.add_argument("--targets", "-t", nargs="+", default=[],
                        help="要被改语调的音频（可多个）")
    parser.add_argument("--outdir", default="art_preview/intonation",
                        help="输出目录")
    parser.add_argument("--strength", type=float, default=0.85,
                        help="迁移强度 0-1（1=完全采用参考旋律）")
    parser.add_argument("--offset", type=float, default=0.0,
                        help="整体升降调，单位半音")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="时长缩放（1=不变，>1 变长）")
    parser.add_argument("--register", choices=["keep", "match"], default="keep",
                        help="keep=保留目标自己的音区；match=连音区也对齐参考")
    parser.add_argument("--no-keep-extremes", action="store_true",
                        help="连目标的气泡音/气声也要按参考旋律改写（默认保留）")
    parser.add_argument("--peak", type=float, default=0.9,
                        help="输出峰值（默认 0.9）")
    parser.add_argument("--trim", action="store_true",
                        help="裁掉首尾静音")
    parser.add_argument("--identity", action="store_true",
                        help="自检：按自己的语调重合成，检查失真")
    parser.add_argument("--report", action="store_true",
                        help="只打印各音频的语调统计")
    parser.add_argument("--plot", help="把结果画成对比图（PNG）")
    parser.add_argument("--suffix", default=None,
                        help="输出文件名后缀（默认 by_<参考名>）")
    parser.add_argument("--bare", action="store_true",
                        help="输出文件名不加后缀（直接 <原名>.wav）")
    args = parser.parse_args()

    if not args.files and not args.targets and not args.reference:
        parser.error("至少提供 --report 的音频，或 -r/-t")

    if args.report:
        for path in list(args.files) + ([args.reference] if args.reference else []) + list(args.targets):
            contour_report(load_audio(path), os.path.basename(path))
        return 0

    if args.identity:
        for path in list(args.files) + list(args.targets) + ([args.reference] if args.reference else []):
            x = load_audio(path)
            if args.trim:
                x = trim_silence(x, SR)
            data = analyze(x)
            f0 = data["f0"]
            y, coverage = synth_psola(x, SR, f0, f0, speed=args.speed)
            margin = int(0.01 * SR)
            a = x[margin:len(x) - margin]
            b = y[margin:min(len(y), len(x) - margin)]
            n = min(len(a), len(b))
            snr = 10.0 * math.log10(
                float(np.sum(a[:n] ** 2)) / max(1e-12, float(np.sum((a[:n] - b[:n]) ** 2))))
            y = normalize_peak(y, args.peak)
            out = os.path.join(args.outdir, os.path.basename(path).rsplit(".", 1)[0] + "_identity.wav")
            save_wav(out, y)
            print("%-24s 覆盖率 %.0f%%  重建 SNR %.1f dB  -> %s"
                  % (os.path.basename(path), coverage * 100, snr, out))
        return 0

    if not args.reference:
        parser.error("迁移模式需要 -r 参考音频")
    if not args.targets:
        parser.error("迁移模式需要 -t 目标音频")

    reference = load_audio(args.reference)
    if args.trim:
        reference = trim_silence(reference, SR)
    ref_data = analyze(reference)
    ref_f0 = ref_data["f0"]
    ref_times = ref_data["times"]

    os.makedirs(args.outdir, exist_ok=True)
    print("参考：%s  F0 中位 %.1f Hz"
          % (os.path.basename(args.reference),
             float(np.median(ref_f0[ref_f0 > 0]))))

    entries = []
    for path in args.targets:
        x = load_audio(path)
        if args.trim:
            x = trim_silence(x, SR)
        data = analyze(x)
        f0 = data["f0"]

        if args.identity:
            target_f0 = f0
        else:
            target_f0 = transfer_contour(
                f0, ref_f0, strength=args.strength, offset=args.offset,
                register=args.register, keep_extremes=not args.no_keep_extremes)

        y, coverage = synth_psola(x, SR, f0, target_f0, speed=args.speed)
        y = normalize_peak(y, args.peak)
        stem = os.path.splitext(os.path.basename(path))[0]
        if args.bare:
            suffix = ""
        elif args.suffix is None:
            suffix = "by_" + os.path.splitext(os.path.basename(args.reference))[0]
        else:
            suffix = args.suffix
        name = "%s_%s.wav" % (stem, suffix) if suffix else "%s.wav" % stem
        out = os.path.join(args.outdir, name)
        save_wav(out, y)

        new_data = analyze(y)
        new_f0 = new_data["f0"]
        st_new, med_new = semitone_contour(new_f0)
        st_ref, med_ref = semitone_contour(ref_f0)
        print("%-12s -> %-10s  覆盖 %.0f%%  F0 中位 %.1f Hz  "
              "时长 %.3fs"
              % (os.path.basename(path), os.path.basename(out), coverage * 100,
                 med_new, len(y) / SR))

        entries.append({
            "name": stem, "x": y, "sr": SR,
            "ref_f0": ref_f0, "ref_times": ref_times,
            "color": "#2ca02c",
        })

    if args.plot:
        make_plot(entries, args.plot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
