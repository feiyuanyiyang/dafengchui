#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""画波形 + 频谱图，便于肉眼检查音频内容。"""
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import av


def decode(path, sr=44100):
    c = av.open(path)
    s = next(x for x in c.streams if x.type == "audio")
    r = av.AudioResampler(format="flt", layout="mono", rate=sr)
    out = []
    for f in c.decode(s):
        for o in r.resample(f):
            out.append(o.to_ndarray().reshape(-1))
    return np.concatenate(out).astype(np.float32), sr


path = sys.argv[1]
out = sys.argv[2] if len(sys.argv) > 2 else "art_preview/voice_takes/wave.png"
x, sr = decode(path)
x = x / (np.max(np.abs(x)) or 1)
t = np.arange(len(x)) / sr
fig, ax = plt.subplots(3, 1, figsize=(16, 9), sharex=True)
ax[0].plot(t, x, lw=0.3)
ax[0].set_ylim(-1.05, 1.05)
ax[0].set_title("waveform")
ax[1].specgram(x, NFFT=1024, Fs=sr, noverlap=768, cmap="magma", vmin=-120)
ax[1].set_ylim(0, 8000)
ax[1].set_title("spectrogram 0-8k")
ax[2].specgram(x, NFFT=4096, Fs=sr, noverlap=3584, cmap="magma", vmin=-120)
ax[2].set_ylim(0, 1200)
ax[2].set_title("spectrogram 0-1.2k (pitch)")
ax[2].set_xlabel("seconds")
plt.tight_layout()
plt.savefig(out, dpi=110)
print(out, "%.2fs" % (len(x) / sr))
