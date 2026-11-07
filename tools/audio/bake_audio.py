#!/usr/bin/env python3
"""Deterministically bake all audio assets for 大风吹 using only the stdlib.

Output format: 44100 Hz, 16-bit signed PCM, mono WAV.
No numpy/scipy/pydub: synthesis is done with lists/floats and the wave module.

Run from anywhere:
    python tools/audio/bake_audio.py
    python tools/audio/bake_audio.py --force-hit   # 也重新合成旧的合成闷响

`assets/audio/sfx/hit.wav` 现在默认是“受伤哼唧”语音（由
tools/audio/make_hurt_voice.py 生成），所以本脚本**不会覆盖已存在的
hit.wav**；只有显式 --force-hit 才会用内置合成器重做。

Re-running produces byte-identical WAV files because every random source is
seeded from the parameter table below.
"""

import argparse
import math
import os
import random
import sys
import wave
from array import array


SAMPLE_RATE = 44100
SAMPLE_WIDTH = 2  # bytes
CHANNELS = 1
TWO_PI = 2.0 * math.pi

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
SFX_DIR = os.path.join(PROJECT_ROOT, "assets", "audio", "sfx")
BGM_DIR = os.path.join(PROJECT_ROOT, "assets", "audio", "bgm")

# Per-sound-effect constants.  All times are seconds, all frequencies Hz.
# The peak column is the normalization target applied after synthesis.
SFX_SPECS = {
    "hit": {
        "file_name": "hit.wav",
        "duration": 0.25,
        "peak": 0.60,
        "seed": 1001,
        # No fade-in: the impact transient must land on sample 0 so the sound
        # is heard the instant the player is hit (a soft attack reads as lag).
        "fade_in": 0.0,
        "fade_out": 0.018,
        "thump_start_hz": 150.0,
        "thump_end_hz": 55.0,
        "rumble_cutoff_hz": 340.0,
        "rumble_gain": 0.24,
        "smack_cutoff_hz": 1400.0,
        "smack_gain": 0.90,
        "smack_decay_s": 0.014,
    },
    "wind_change": {
        "file_name": "wind_change.wav",
        "duration": 0.70,
        "peak": 0.60,
        "seed": 1003,
        "fade_in": 0.006,
        "fade_out": 0.030,
        "sweep_low_hz": 360.0,
        "sweep_high_hz": 1850.0,
        "bandpass_q": 1.35,
        "rumble_cutoff_hz": 520.0,
    },
    "game_over": {
        "file_name": "game_over.wav",
        "duration": 1.20,
        "peak": 0.60,
        "seed": 1004,
        "fade_in": 0.004,
        "fade_out": 0.040,
        # Descending D-minor figure: A4 -> F4 -> D4.
        "notes": (
            (440.00, 0.00, 0.36),
            (349.23, 0.26, 0.40),
            (293.66, 0.54, 0.66),
        ),
        "reverb_mix": 0.22,
    },
    "pickup": {
        "file_name": "pickup.wav",
        "duration": 0.35,
        "peak": 0.60,
        "seed": 1005,
        "fade_in": 0.002,
        "fade_out": 0.025,
        # Bright ascending two-note bell: C6 -> G6.
        "notes": (
            (1046.50, 0.000, 0.220),
            (1567.98, 0.115, 0.235),
        ),
        "echo_delay_s": 0.075,
        "echo_feedback": 0.18,
        "echo_wet": 0.12,
    },
}

# The ambient bed must loop.  We render ``duration + crossfade`` seconds, then
# crossfade the final ``crossfade`` seconds into the head.  At the loop point
# the last body sample and the new first sample are adjacent noise samples, so
# the join is continuous.  All slow LFO periods divide the 12 s loop exactly.
BGM_SPEC = {
    "file_name": "wind_ambience.wav",
    "duration": 12.0,
    "crossfade": 0.75,
    "peak": 0.35,
    "seed": 2001,
    "pad_a_hz": 110.0,   # A2
    "pad_e_hz": 165.0,   # E3
    "air_cutoff_hz": 1200.0,
    "air_gain": 0.50,
    "final_lowpass_hz": 1200.0,
    "seam_threshold": 0.010,
}


# ---------------------------------------------------------------------------
# Basic DSP helpers
# ---------------------------------------------------------------------------

def frames_for(duration):
    """Convert seconds to an exact whole number of sample frames."""
    return int(round(duration * SAMPLE_RATE))


def smoothstep(x):
    """Smooth 0..1 interpolation curve used by fades and the loop splice."""
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def adsr_envelope(n, attack, decay, sustain_level, release, sr=SAMPLE_RATE):
    """Linear ADSR envelope of exactly n samples.

    ``attack``/``decay``/``release`` are seconds.  The sustain segment is the
    remaining time.  The envelope ends at 0 so no click can be introduced.
    """
    if n <= 0:
        return []
    if not (0.0 <= sustain_level <= 1.0):
        raise ValueError("sustain_level must be in [0, 1]")
    a = max(1, int(round(attack * sr)))
    d = max(0, int(round(decay * sr)))
    r = max(1, int(round(release * sr)))
    if a + d + r > n:
        raise ValueError("ADSR stages do not fit in the requested duration")
    hold = n - a - d - r
    env = []
    for i in range(n):
        if i < a:
            value = i / (a - 1) if a > 1 else 1.0
        elif i < a + d:
            pos = (i - a) / (d - 1) if d > 1 else 1.0
            value = 1.0 + (sustain_level - 1.0) * pos
        elif i < a + d + hold:
            value = sustain_level
        else:
            pos = (i - (a + d + hold)) / (r - 1) if r > 1 else 1.0
            value = sustain_level * (1.0 - pos)
        env.append(value)
    return env


def oscillator(n, freq_start, freq_end=None, waveform="sine", phase=0.0,
               sr=SAMPLE_RATE):
    """Generate n samples with an exponential pitch glide between two freqs."""
    if n <= 0:
        return []
    if freq_end is None:
        freq_end = freq_start
    if freq_start <= 0.0 or freq_end <= 0.0:
        raise ValueError("oscillator frequencies must be positive")
    ratio = freq_end / freq_start
    phase = phase % TWO_PI
    out = []
    denom = n - 1 if n > 1 else 1
    for i in range(n):
        p = i / denom
        freq = freq_start * (ratio ** p)
        phase = (phase + TWO_PI * freq / sr) % TWO_PI
        if waveform == "sine":
            sample = math.sin(phase)
        elif waveform == "triangle":
            sample = (2.0 / math.pi) * math.asin(math.sin(phase))
        else:
            raise ValueError("unknown waveform: %r" % (waveform,))
        out.append(sample)
    return out


def white_noise(n, seed):
    """Deterministic white noise in [-1, 1] from a private RNG."""
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(n)]


def lowpass_1pole(samples, cutoff, sr=SAMPLE_RATE):
    """One-pole low-pass filter, useful for soft wind rumble."""
    cutoff = max(1.0, min(cutoff, sr * 0.45))
    alpha = 1.0 - math.exp(-TWO_PI * cutoff / sr)
    y = 0.0
    out = []
    for x in samples:
        y += alpha * (x - y)
        out.append(y)
    return out


def highpass_1pole(samples, cutoff, sr=SAMPLE_RATE):
    """One-pole high-pass filter, used to remove rumble from airy noise."""
    cutoff = max(1.0, min(cutoff, sr * 0.45))
    dt = 1.0 / sr
    rc = 1.0 / (TWO_PI * cutoff)
    alpha = rc / (rc + dt)
    prev_x = 0.0
    prev_y = 0.0
    out = []
    for x in samples:
        y = alpha * (prev_y + x - prev_x)
        prev_x = x
        prev_y = y
        out.append(y)
    return out


def bandpass_sweep(samples, freqs, q=1.2, sr=SAMPLE_RATE):
    """Time-varying RBJ band-pass filter for the wind whoosh.

    ``freqs`` is one center frequency per sample.  Coefficients are updated
    every sample; the filter is stable for the frequencies used here.
    """
    if len(samples) != len(freqs):
        raise ValueError("samples and freqs must have equal length")
    if q <= 0.0:
        raise ValueError("q must be positive")
    z1 = 0.0
    z2 = 0.0
    out = []
    for x, freq in zip(samples, freqs):
        freq = max(20.0, min(freq, sr * 0.45))
        w0 = TWO_PI * freq / sr
        alpha = math.sin(w0) / (2.0 * q)
        cos_w0 = math.cos(w0)
        a0 = 1.0 + alpha
        b0_over_a0 = alpha / a0
        b2_over_a0 = -alpha / a0
        a1_over_a0 = (-2.0 * cos_w0) / a0
        a2_over_a0 = (1.0 - alpha) / a0
        y = b0_over_a0 * x + z1
        z1 = -a1_over_a0 * y + z2
        z2 = b2_over_a0 * x - a2_over_a0 * y
        out.append(y)
    return out


def apply_delay(samples, delay_s, feedback=0.25, wet=0.2, sr=SAMPLE_RATE):
    """Simple feedback echo; ``wet`` is the blended effect amount."""
    d = int(round(delay_s * sr))
    if d <= 0 or d >= len(samples):
        return list(samples)
    wet_signal = [0.0] * len(samples)
    for i, x in enumerate(samples):
        if i >= d:
            x += wet_signal[i - d] * feedback
        wet_signal[i] = x
    return [(1.0 - wet) * dry + wet * echo
            for dry, echo in zip(samples, wet_signal)]


def apply_reverb(samples, taps, mix=0.2, sr=SAMPLE_RATE):
    """Lightweight multi-tap reverb.  ``taps`` is (delay_s, gain) pairs."""
    wet_signal = [0.0] * len(samples)
    for delay_s, gain in taps:
        d = int(round(delay_s * sr))
        if d <= 0 or d >= len(samples):
            continue
        for i in range(d, len(samples)):
            wet_signal[i] += samples[i - d] * gain
    return [(1.0 - mix) * dry + mix * wet
            for dry, wet in zip(samples, wet_signal)]


def apply_fade_edges(samples, fade_in, fade_out, sr=SAMPLE_RATE):
    """Fade both edges to exact zero with smoothstep, preventing clicks."""
    out = list(samples)
    n = len(out)
    if n == 0:
        return out
    fin = min(n // 2, max(0, int(round(fade_in * sr))))
    fout = min(n // 2, max(0, int(round(fade_out * sr))))
    if fin > 1:
        for i in range(fin):
            out[i] *= smoothstep(i / (fin - 1))
    if fout > 1:
        for j in range(fout):
            out[n - 1 - j] *= smoothstep(j / (fout - 1))
    return out


def normalize(samples, peak):
    """Normalize a signal so its absolute peak equals ``peak`` (< 1.0)."""
    if not samples:
        raise ValueError("cannot normalize an empty signal")
    if not (0.0 < peak < 1.0):
        raise ValueError("normalization peak must be in (0, 1)")
    current = 0.0
    for x in samples:
        if not math.isfinite(x):
            raise ValueError("signal contains NaN/Inf")
        current = max(current, abs(x))
    if current <= 0.0:
        raise ValueError("cannot normalize silence")
    gain = peak / current
    return [x * gain for x in samples]


def finalize_sfx(samples, spec):
    """Apply edge fades and normalize a one-shot to its target peak."""
    faded = apply_fade_edges(
        samples, spec.get("fade_in", 0.003), spec.get("fade_out", 0.02)
    )
    return normalize(faded, spec["peak"])


# ---------------------------------------------------------------------------
# SFX synthesisers
# ---------------------------------------------------------------------------

def synth_hit(spec):
    """Instant-impact thump: a broadband smack on sample 0 plus a low body.

    The previous version faded in over 3 ms and ramped the 120 Hz thump over
    4 ms, so the audible "hit" only arrived a few tens of ms late.  The smack
    layer starts at full level on the very first sample, which is what the ear
    locks onto when matching the sound to the collision on screen.
    """
    n = frames_for(spec["duration"])
    thump = oscillator(
        n, spec["thump_start_hz"], spec["thump_end_hz"], phase=0.0
    )
    thump_env = adsr_envelope(
        n, attack=0.0, decay=0.090, sustain_level=0.18, release=0.130
    )

    rumble = lowpass_1pole(
        white_noise(n, spec["seed"]), spec["rumble_cutoff_hz"]
    )
    rumble_env = adsr_envelope(
        n, attack=0.0, decay=0.050, sustain_level=0.03, release=0.100
    )

    smack = highpass_1pole(
        white_noise(n, spec["seed"] + 1), spec["smack_cutoff_hz"]
    )
    smack_env = adsr_envelope(
        n, attack=0.0, decay=spec["smack_decay_s"],
        sustain_level=0.0, release=0.004,
    )

    out = []
    for i in range(n):
        body = thump[i] * thump_env[i]
        body += rumble[i] * rumble_env[i] * spec["rumble_gain"]
        body += smack[i] * smack_env[i] * spec["smack_gain"]
        # Gentle saturation adds low harmonics without a hard clip.
        out.append(math.tanh(1.35 * body))
    return out





def synth_wind_change(spec):
    """Band-passed noise swept low->high->low, with a soft low rumble bed."""
    n = frames_for(spec["duration"])
    noise = white_noise(n, spec["seed"])
    rumble = lowpass_1pole(
        white_noise(n, spec["seed"] + 1), spec["rumble_cutoff_hz"]
    )

    freqs = []
    denom = n - 1 if n > 1 else 1
    for i in range(n):
        p = i / denom
        # A broad hump: quiet low start, whistling peak near the middle, fall.
        sweep = math.sin(math.pi * p) ** 1.35
        freqs.append(spec["sweep_low_hz"]
                     + (spec["sweep_high_hz"] - spec["sweep_low_hz"]) * sweep)
    whoosh = bandpass_sweep(noise, freqs, q=spec["bandpass_q"])

    whoosh_env = adsr_envelope(
        n, attack=0.060, decay=0.100, sustain_level=0.72, release=0.320
    )
    rumble_env = adsr_envelope(
        n, attack=0.120, decay=0.150, sustain_level=0.45, release=0.340
    )

    out = []
    for i in range(n):
        p = i / denom
        gust = 0.82 + 0.18 * math.sin(TWO_PI * 2.5 * p)
        value = whoosh[i] * whoosh_env[i] * gust
        value += rumble[i] * rumble_env[i] * 0.22
        out.append(value)
    return out


def synth_game_over(spec):
    """Soft descending D-minor three-note figure with a short reverb tail."""
    n = frames_for(spec["duration"])
    buf = [0.0] * n

    for freq, start_s, note_duration in spec["notes"]:
        start = frames_for(start_s)
        length = min(frames_for(note_duration), n - start)
        if length <= 0:
            continue
        env = adsr_envelope(
            length,
            attack=0.014,
            decay=0.090,
            sustain_level=0.46,
            release=max(0.10, min(0.32, note_duration - 0.20)),
        )
        fundamental = oscillator(length, freq, phase=0.0)
        second = oscillator(length, freq * 2.0, phase=0.35)
        third = oscillator(length, freq * 3.0, phase=0.8)
        for i in range(length):
            voice = fundamental[i] + second[i] * 0.16 + third[i] * 0.045
            buf[start + i] += voice * env[i] * 0.42

    taps = ((0.041, 0.20), (0.073, 0.13), (0.109, 0.08))
    return apply_reverb(buf, taps, mix=spec["reverb_mix"])


def synth_pickup(spec):
    """Bright two-note bell (C6 -> G6) with a whisper of echo."""
    n = frames_for(spec["duration"])
    buf = [0.0] * n

    # Slightly inharmonic partials make this read as a small bell, not a beep.
    partials = ((1.00, 1.00), (2.01, 0.30), (3.01, 0.10))
    for freq, start_s, note_duration in spec["notes"]:
        start = frames_for(start_s)
        length = min(frames_for(note_duration), n - start)
        if length <= 0:
            continue
        env = adsr_envelope(
            length,
            attack=0.002,
            decay=0.075,
            sustain_level=0.22,
            release=max(0.06, min(0.16, note_duration - 0.14)),
        )
        for ratio, gain in partials:
            part = oscillator(length, freq * ratio, phase=ratio * 0.4)
            for i in range(length):
                buf[start + i] += part[i] * env[i] * gain * 0.34

    return apply_delay(
        buf,
        spec["echo_delay_s"],
        feedback=spec["echo_feedback"],
        wet=spec["echo_wet"],
    )


SFX_SYNTHS = {
    "hit": synth_hit,
    "wind_change": synth_wind_change,
    "game_over": synth_game_over,
    "pickup": synth_pickup,
}


# ---------------------------------------------------------------------------
# Seamless wind ambience
# ---------------------------------------------------------------------------

def synth_wind_ambience(spec):
    """Build a seamless-looping wind bed with a quiet A2/E3 pad.

    Render length is loop + crossfade.  The first ``crossfade`` samples then
    become an equal-length blend from the extra tail into the original head.
    The head blend starts exactly on the last body sample, so the first and
    last stored samples are bit-identical after quantization; the following
    0.75 s crossfade returns smoothly to the original head.  The loop boundary
    is therefore click-free and the seam difference is exactly zero.
    """
    loop_n = frames_for(spec["duration"])
    xfade_n = frames_for(spec["crossfade"])
    render_n = loop_n + xfade_n

    low_noise = white_noise(render_n, spec["seed"])
    air_noise = white_noise(render_n, spec["seed"] + 1)

    # Deep rumble: two gentle low-pass stages.  Air layer: heavily smoothed,
    # band-limited hiss (two low-pass stages, then a DC/rumble block).
    rumble = lowpass_1pole(lowpass_1pole(low_noise, 110.0), 260.0)
    air = lowpass_1pole(air_noise, spec["air_cutoff_hz"])
    air = lowpass_1pole(air, spec["air_cutoff_hz"] * 1.4)
    air = highpass_1pole(air, 170.0)

    pad_a = spec["pad_a_hz"]
    pad_e = spec["pad_e_hz"]
    raw = []
    for i in range(render_n):
        t = i / SAMPLE_RATE

        # Slow, loop-periodic gusts (periods 12 s, 6 s and 3 s).
        gust = (0.55
                + 0.25 * math.sin(TWO_PI * t / 12.0 - 0.8)
                + 0.12 * math.sin(TWO_PI * t / 6.0 + 1.1)
                + 0.08 * math.sin(TWO_PI * t / 3.0 + 0.4))
        slow = (0.6 + 0.4 * math.sin(TWO_PI * t / 12.0 + 0.3))

        wind = rumble[i] * (0.62 + 0.38 * max(0.0, gust)) * 1.05
        wind += air[i] * (0.30 + 0.22 * slow) * spec["air_gain"]

        # Very quiet drone, slowly breathing; no melody and no percussion.
        pad_lfo = 0.72 + 0.28 * math.sin(TWO_PI * t / 12.0 + 0.9)
        pad = (
            math.sin(TWO_PI * pad_a * t + 0.2) * 0.075
            + math.sin(TWO_PI * pad_e * t + 1.1) * 0.050
            + math.sin(TWO_PI * (pad_a * 0.5) * t + 0.6) * 0.018
        ) * pad_lfo
        raw.append(wind + pad * 0.72)

    # A final gentle low-pass removes the last bit of digital hiss from the bed.
    raw = lowpass_1pole(raw, spec["final_lowpass_hz"])

    # The extra tail continues the exact same performance.  At i=0 the blend
    # uses raw[loop_n - 1], which is also the last stored body sample, so the
    # loop join is sample-exact; w then glides to the original head by i=X-1.
    loop = raw[:loop_n]
    for i in range(xfade_n):
        w = smoothstep(i / (xfade_n - 1)) if xfade_n > 1 else 1.0
        tail = raw[loop_n - 1 + i]
        head = raw[i]
        loop[i] = tail * (1.0 - w) + head * w

    return normalize(loop, spec["peak"])


# ---------------------------------------------------------------------------
# WAV writing / validation
# ---------------------------------------------------------------------------

def to_pcm16(samples):
    """Convert floats to little-endian int16 values, checking for non-finite."""
    pcm = array("h")
    for value in samples:
        if not math.isfinite(value):
            raise ValueError("refusing to write NaN/Inf sample")
        value = max(-1.0, min(1.0, value))
        q = int(round(value * 32767.0))
        if q < -32768 or q > 32767:
            raise OverflowError("sample overflow while converting to int16")
        pcm.append(q)
    if sys.byteorder == "big":
        pcm.byteswap()
    return pcm


def write_wav(path, samples):
    """Write mono 44.1 kHz / 16-bit PCM WAV."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pcm = to_pcm16(samples)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())


def inspect_wav(path):
    """Read the file back and verify format; return peak/frames metadata."""
    with wave.open(path, "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        frame_count = wf.getnframes()
        frames = wf.readframes(frame_count)

    if channels != CHANNELS:
        raise RuntimeError("%s: channels=%d, expected %d"
                           % (path, channels, CHANNELS))
    if width != SAMPLE_WIDTH:
        raise RuntimeError("%s: sample width=%d, expected %d"
                           % (path, width, SAMPLE_WIDTH))
    if rate != SAMPLE_RATE:
        raise RuntimeError("%s: rate=%d, expected %d"
                           % (path, rate, SAMPLE_RATE))
    if len(frames) != frame_count * SAMPLE_WIDTH:
        raise RuntimeError("%s: truncated PCM data" % path)

    pcm = array("h")
    pcm.frombytes(frames)
    if sys.byteorder == "big":
        pcm.byteswap()

    finite = True
    peak = 0.0
    clipped = False
    for value in pcm:
        if value < -32768 or value > 32767:
            clipped = True
        peak = max(peak, abs(value) / 32768.0)

    return {
        "channels": channels,
        "sample_width": width,
        "rate": rate,
        "frames": frame_count,
        "duration": frame_count / float(rate),
        "peak": peak,
        "finite": finite,
        "clipped": clipped,
        "first_sample": pcm[0] / 32768.0 if pcm else 0.0,
        "last_sample": pcm[-1] / 32768.0 if pcm else 0.0,
        "pcm": pcm,
        "size": os.path.getsize(path),
    }


def relative(path):
    try:
        return os.path.relpath(path, PROJECT_ROOT)
    except ValueError:
        return path


# ---------------------------------------------------------------------------
# Baking / self-check
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="烘焙大风吹的音频资源")
    parser.add_argument(
        "--force-hit", action="store_true",
        help="重新合成旧的 hit.wav 闷响（默认保留自定义的受伤哼唧语音）",
    )
    args = parser.parse_args()

    os.makedirs(SFX_DIR, exist_ok=True)
    os.makedirs(BGM_DIR, exist_ok=True)

    print("Baking DaFengChui audio: 44100 Hz, 16-bit, mono")
    print("SFX dir: %s" % SFX_DIR)
    print("BGM dir: %s" % BGM_DIR)
    print()

    sfx_reports = {}
    for key, spec in SFX_SPECS.items():
        path = os.path.join(SFX_DIR, spec["file_name"])
        if key == "hit" and os.path.isfile(path) and not args.force_hit:
            info = inspect_wav(path)
            sfx_reports[key] = info
            print("[sfx] %-30s kept (自定义受伤哼唧；--force-hit 可覆盖)"
                  % relative(path))
            continue
        raw = SFX_SYNTHS[key](spec)
        samples = finalize_sfx(raw, spec)
        write_wav(path, samples)
        info = inspect_wav(path)
        sfx_reports[key] = info
        print("[sfx] %-30s %6d frames  %6.3f s  peak %.3f  %7d bytes"
              % (relative(path), info["frames"], info["duration"],
                 info["peak"], info["size"]))

    bgm_raw = synth_wind_ambience(BGM_SPEC)
    bgm_path = os.path.join(BGM_DIR, BGM_SPEC["file_name"])
    write_wav(bgm_path, bgm_raw)
    bgm_info = inspect_wav(bgm_path)
    print("[bgm] %-30s %6d frames  %6.3f s  peak %.3f  %7d bytes"
          % (relative(bgm_path), bgm_info["frames"], bgm_info["duration"],
             bgm_info["peak"], bgm_info["size"]))
    print()

    # Final self-check, deliberately reading everything back from disk.
    print("Self-check (read-back WAV data):")
    for key, info in sfx_reports.items():
        if info["peak"] >= 0.99:
            raise RuntimeError("%s peak %.3f is too high" % (key, info["peak"]))
        if info["clipped"]:
            raise RuntimeError("%s contains int16 overflow" % key)
        if not info["finite"]:
            raise RuntimeError("%s contains NaN/Inf" % key)
        print("  %-13s peak=%.3f < 0.99, NaN/overflow=none, "
              "channels=%d, width=%d, rate=%d, duration=%.3f s"
              % (key, info["peak"], info["channels"],
                 info["sample_width"], info["rate"], info["duration"]))

    if bgm_info["frames"] != frames_for(BGM_SPEC["duration"]):
        raise RuntimeError("BGM frame count does not match its 12 s duration")
    if bgm_info["peak"] >= 0.99 or bgm_info["clipped"] or not bgm_info["finite"]:
        raise RuntimeError("BGM failed peak/overflow/finite checks")

    seam_delta = abs(bgm_info["first_sample"] - bgm_info["last_sample"])
    print("  BGM loop seam samples: last=%.6f, first=%.6f, |diff|=%.6f"
          % (bgm_info["last_sample"], bgm_info["first_sample"], seam_delta))
    if seam_delta > BGM_SPEC["seam_threshold"]:
        raise RuntimeError("BGM loop seam too large: %.6f" % seam_delta)
    print("  BGM seam OK (|last-first| <= %.3f); exact frames=%d, "
          "duration=%.3f s" % (BGM_SPEC["seam_threshold"],
                               bgm_info["frames"], bgm_info["duration"]))
    print()
    print("Done. Re-run this script to regenerate identical files.")


if __name__ == "__main__":
    main()
