#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一段短语音做成《大风吹》的受伤音效（哼唧声）。

用途
    1. 你现在听到的 hit.wav 是用 Windows 中文女声（Huihui）合成的“呜啊”，
       再经过本脚本升调 + 气声处理得到；
    2. 你从网上/视频里截到更满意的 DeepSeek娘 语音后，也可以直接丢给本脚本，
       得到同样“短、亮、贴画面”的受伤声。

处理链（全部标准库，不需要 numpy）
    多声道 -> 单声道 44.1 kHz；裁掉首尾静音；变调变速（升调同时变短）；
    可选尾音降调；叠一点气声；轻低通去毛刺；淡入淡出；归一化到峰值 0.6；
    可用 --duration 只截时长（不升调），适合“想更短但音调别太高”。

常用命令
    python tools/audio/make_hurt_voice.py 源文件.wav
    python tools/audio/make_hurt_voice.py 源文件.wav --speed 1.3 --fall 0.9 --breath 0.05
    python tools/audio/make_hurt_voice.py 源文件.wav --speed 1.0 --duration 0.30
    python tools/audio/make_hurt_voice.py 源文件.wav --pitch -2 --punch 2.0 `
        --punch-click 0 --punch-hf 0 --skip 0.02 --split 0.12 --first-gain 6 `
        --second-gain -3 --second-decay 0.10 --blend 0.08 --duration 0.28 `
        --cutoff 9000 --lpf-order 3 --presence 12 --presence-high 7500 --low-cut 220
    python tools/audio/make_hurt_voice.py 源文件.wav -o art_preview/候选A.wav

替换游戏音效
    默认输出 assets/audio/sfx/hit.wav（游戏里的“受伤”事件）。
    注意：tools/audio/bake_audio.py 默认不会覆盖已存在的 hit.wav，
    只有显式加 --force-hit 才会重新生成旧的合成闷响。
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import wave
from array import array

SAMPLE_RATE = 44100


def read_wav(path):
    """读 WAV，返回 (单声道 float 列表, 源采样率)。"""
    with wave.open(path, "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width == 1:
        data = [(value - 128) / 128.0 for value in frames]
    elif width == 2:
        pcm = array("h")
        pcm.frombytes(frames)
        if sys.byteorder == "big":
            pcm.byteswap()
        data = [value / 32768.0 for value in pcm]
    elif width == 3:
        data = []
        for index in range(0, len(frames), 3):
            raw = frames[index:index + 3]
            value = int.from_bytes(raw, "little", signed=True)
            data.append(value / 8388608.0)
    elif width == 4:
        pcm = array("i")
        pcm.frombytes(frames)
        if sys.byteorder == "big":
            pcm.byteswap()
        data = [value / 2147483648.0 for value in pcm]
    else:
        raise ValueError("不支持的位深：%d 字节" % width)
    if channels > 1:
        mono = []
        for index in range(0, len(data) - channels + 1, channels):
            mono.append(sum(data[index:index + channels]) / channels)
        data = mono
    return data, rate


def trim_silence(samples, threshold_ratio=0.015):
    """裁掉首尾静音；threshold_ratio 相对峰值，保留气声起音。"""
    if not samples:
        return samples
    peak = max(abs(value) for value in samples)
    if peak <= 0.0:
        return []
    threshold = peak * threshold_ratio
    start = 0
    while start < len(samples) and abs(samples[start]) < threshold:
        start += 1
    end = len(samples) - 1
    while end > start and abs(samples[end]) < threshold:
        end -= 1
    return samples[start:end + 1]


def interpolate(samples, position):
    """线性插值读取（position 可越界时夹到端点）。"""
    if position <= 0.0:
        return samples[0]
    if position >= len(samples) - 1:
        return samples[-1]
    index = int(position)
    frac = position - index
    return samples[index] * (1.0 - frac) + samples[index + 1] * frac


def resample_voice(samples, source_rate, speed, fall):
    """按 speed 变调变速；fall<1 时音高在结尾下降，像“哼~唧~”的尾音。

    speed > 1 会同时升调并变短：对拟人角色来说，升调本身就是“更幼、更可爱”。
    """
    if not samples:
        return []
    step_scale = source_rate / float(SAMPLE_RATE)
    position = 0.0
    out = []
    total_estimate = max(1.0, len(samples) / step_scale / speed)
    count = 0
    while position < len(samples) - 1:
        progress = min(1.0, count / total_estimate)
        rate = speed * (1.0 + (fall - 1.0) * progress)
        out.append(interpolate(samples, position))
        position += step_scale * max(0.2, rate)
        count += 1
    return out


def lowpass(samples, cutoff):
    if not samples or cutoff <= 0.0:
        return list(samples)
    alpha = 1.0 - math.exp(-2.0 * math.pi * cutoff / SAMPLE_RATE)
    state = 0.0
    out = []
    for value in samples:
        state += alpha * (value - state)
        out.append(state)
    return out


def highpass(samples, cutoff):
    if not samples or cutoff <= 0.0:
        return list(samples)
    dt = 1.0 / SAMPLE_RATE
    rc = 1.0 / (2.0 * math.pi * cutoff)
    alpha = rc / (rc + dt)
    previous_in = 0.0
    previous_out = 0.0
    out = []
    for value in samples:
        current = alpha * (previous_out + value - previous_in)
        previous_in = value
        previous_out = current
        out.append(current)
    return out


def add_breath(samples, amount, seed=20241025):
    """叠一层随音量开合的气声，让 TTS 的干声更软、更像真人的哼唧。"""
    if amount <= 0.0 or not samples:
        return list(samples)
    peak = max(abs(value) for value in samples)
    if peak <= 0.0:
        return list(samples)
    rng = random.Random(seed)
    noise = [rng.uniform(-1.0, 1.0) for _ in samples]
    noise = highpass(lowpass(noise, 6500.0), 1200.0)
    envelope = []
    attack = 0.0
    for value in samples:
        target = min(1.0, abs(value) / peak)
        attack += (target - attack) * 0.02
        envelope.append(attack)
    return [
        dry + wet * envelope_value * amount
        for dry, wet, envelope_value in zip(samples, noise, envelope)
    ]


def apply_envelope(samples, fade_in=0.0015, fade_out=0.025):
    out = list(samples)
    n = len(out)
    if n == 0:
        return out
    fin = min(n, max(1, int(round(fade_in * SAMPLE_RATE))))
    fout = min(n, max(1, int(round(fade_out * SAMPLE_RATE))))
    for index in range(fin):
        out[index] *= index / float(fin)
    for index in range(fout):
        out[n - 1 - index] *= index / float(fout)
    return out


def normalize(samples, peak):
    if not samples:
        return samples
    current = max(abs(value) for value in samples)
    if current <= 0.0:
        return samples
    gain = peak / current
    return [value * gain for value in samples]


def write_wav(path, samples):
    pcm = array("h")
    for value in samples:
        clamped = max(-1.0, min(1.0, value))
        pcm.append(max(-32768, min(32767, int(round(clamped * 32767.0)))))
    if sys.byteorder == "big":
        pcm.byteswap()
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())


def moving_rms(samples, window):
    """滑动 RMS，用前缀和实现，避免 O(n·window)。"""
    n = len(samples)
    prefix = [0.0] * (n + 1)
    for index, value in enumerate(samples):
        prefix[index + 1] = prefix[index] + value * value
    half = max(1, window // 2)
    out = []
    for index in range(n):
        start = max(0, index - half)
        end = min(n, index + half + 1)
        out.append(math.sqrt((prefix[end] - prefix[start]) / max(1, end - start)))
    return out


def punch_up(samples, strength=1.0, click_amount=0.2, hf_amount=0.55):
    """加强第一个音节的爆发感：压平软起音，可选冲击瞬态与高频提亮。

    strength = 0 关闭；click_amount=0 去掉合成噪声“刷”声；
    hf_amount=0 去掉起音高频提亮。只作用于开头 160 ms。
    """
    if strength <= 0.0 or not samples:
        return list(samples)
    n = len(samples)
    window = max(32, int(0.003 * SAMPLE_RATE))
    env = moving_rms(samples, window)
    region = min(n, int(0.16 * SAMPLE_RATE))
    top = max(env[:region]) or 1e-9
    floor = top * 0.01
    attack = max(1, int(0.003 * SAMPLE_RATE))
    hold = max(0, int(0.050 * SAMPLE_RATE))
    gains = [1.0] * n
    for index in range(region):
        if index < attack:
            target_ratio = index / float(attack)
        elif index < attack + hold:
            target_ratio = 1.0
        else:
            progress = (index - attack - hold) / float(max(1, region - attack - hold))
            target_ratio = 1.0 - 0.45 * progress
        gain = (target_ratio * top) / max(env[index], floor)
        gains[index] = min(gain, 120.0)
    # 轻度平滑，避免增益曲线本身产生“抽吸”声
    for index in range(1, region):
        gains[index] = gains[index - 1] * 0.75 + gains[index] * 0.25
    # 尾端 20 ms 平滑回 1.0，避免进入后段时突然掉音量
    blend = min(int(0.02 * SAMPLE_RATE), region)
    for offset in range(blend):
        index = region - blend + offset
        w = offset / float(max(1, blend))
        gains[index] = gains[index] * (1 - w) + 1.0 * w
    out = [value * gain for value, gain in zip(samples, gains)]

    # 2–3 ms 冲击瞬态：让“呃”更像被打到（click_amount=0 可去掉）
    if click_amount > 0.0:
        rng = random.Random(20251025)
        click = max(1, int(0.003 * SAMPLE_RATE))
        for index in range(click):
            decay = math.exp(-index / (0.0013 * SAMPLE_RATE))
            value = (rng.uniform(-1, 1) * 0.6 + math.sin(2 * math.pi * 2600 * index / SAMPLE_RATE) * 0.4)
            out[index] += value * decay * click_amount * strength

    # 起音 100 ms 提亮（hf_amount=0 可去掉）
    if hf_amount > 0.0:
        air = highpass(out, 3500.0)
        early = min(n, int(0.10 * SAMPLE_RATE))
        for index in range(early):
            fade = 1.0 - index / float(early)
            out[index] += air[index] * hf_amount * strength * fade

    drive = 1.0 + 0.5 * strength
    out = [math.tanh(drive * value) for value in out]
    return normalize(out, 0.6)


def trim_duration(samples, seconds, tail_fade=0.06):
    """把成品截到指定时长，并在结尾做一段淡出（不改音高）。

    这比“加速”更适合“只想变短、不想升调”的情况：起音和元音共振完全保留，
    只把后面的拖尾收掉。
    """
    if seconds <= 0.0:
        return samples
    limit = int(seconds * SAMPLE_RATE)
    if len(samples) <= limit:
        return samples
    out = list(samples[:limit])
    fade = max(1, min(len(out), int(tail_fade * SAMPLE_RATE)))
    for index in range(fade):
        out[len(out) - 1 - index] *= index / float(fade)
    return out


def shape_syllables(samples, split, first_gain_db=0.0, second_gain_db=0.0,
                    second_decay=0.0, crossfade=0.02):
    """按音节分界处理：前半段（呃）提增益，后半段（啊）减弱并平滑收尾。

    分界处用 smoothstep 在 crossfade（默认 20 ms）内过渡，而不是硬切；
    second_decay 从过渡结束之后才开始计时，避免“先掉音量再衰减”的双重台阶。
    """
    if split <= 0.0 or not samples:
        return list(samples)
    split_n = min(len(samples), int(split * SAMPLE_RATE))
    blend = max(1, int(max(0.002, crossfade) * SAMPLE_RATE))
    half = blend // 2
    first_gain = 10.0 ** (first_gain_db / 20.0)
    second_gain = 10.0 ** (second_gain_db / 20.0)
    out = []
    for index, value in enumerate(samples):
        start = split_n - half
        end = split_n + half
        if index < start:
            gain = first_gain
        elif index < end:
            ratio = (index - start) / float(max(1, end - start))
            weight = ratio * ratio * (3.0 - 2.0 * ratio)  # smoothstep
            gain = first_gain * (1.0 - weight) + second_gain * weight
        else:
            gain = second_gain
            if second_decay > 0.0:
                elapsed = index - end
                gain *= math.exp(-elapsed / (second_decay * SAMPLE_RATE))
        out.append(value * gain)
    return out


def add_brightness(samples, db):
    """全频段轻度提亮：把 3.5 kHz 以上补一点，抵消降调/低通带来的沉闷感。"""
    if db <= 0.0 or not samples:
        return list(samples)
    air = highpass(samples, 3500.0)
    gain = 10.0 ** (db / 20.0) - 1.0
    return [dry + gain * wet for dry, wet in zip(samples, air)]


def add_presence(samples, db, low=2500.0, high=6500.0):
    """只提亮 2.5–6.5 kHz 的“存在感”频段，避免把 8 kHz 以上的嘶声一起放大。"""
    if db <= 0.0 or not samples:
        return list(samples)
    band = lowpass(highpass(samples, low), high)
    gain = 10.0 ** (db / 20.0) - 1.0
    return [dry + gain * wet for dry, wet in zip(samples, band)]


def build_voice(source, speed=1.3, fall=1.0, breath=0.04, peak=0.6,
                cutoff=9000.0, fade_in=0.0015, fade_out=0.025,
                trim_threshold=0.015, duration=0.0, tail_fade=0.06,
                pitch=0.0, punch=0.0, skip=0.0, split=0.0,
                first_gain=0.0, second_gain=0.0, second_decay=0.0, blend=0.02,
                punch_click=0.2, punch_hf=0.55, brightness=0.0, low_cut=0.0,
                presence=0.0, presence_high=6500.0, lpf_order=1):
    samples, rate = read_wav(source)
    samples = trim_silence(samples, trim_threshold)
    if not samples:
        raise ValueError("源音频是静音：%s" % source)
    if skip > 0.0:
        samples = samples[int(skip * rate):]
        if not samples:
            raise ValueError("--skip 超过了音频长度")
    # pitch 以半音为单位；负值降调。与 speed 相乘后交给同一重采样器。
    rate_factor = speed * (2.0 ** (pitch / 12.0))
    samples = resample_voice(samples, rate, rate_factor, fall)
    samples = add_breath(samples, breath)
    for _ in range(max(1, int(lpf_order))):
        samples = lowpass(samples, cutoff)
    if low_cut > 0.0:
        samples = highpass(samples, low_cut)
    samples = apply_envelope(samples, fade_in, fade_out)
    samples = normalize(samples, peak)
    if punch > 0.0:
        samples = punch_up(samples, punch, punch_click, punch_hf)
    if split > 0.0:
        samples = shape_syllables(
            samples, split, first_gain, second_gain, second_decay, blend
        )
        samples = normalize(samples, peak)
    if brightness > 0.0:
        samples = normalize(add_brightness(samples, brightness), peak)
    if presence > 0.0:
        samples = normalize(add_presence(samples, presence, high=presence_high), peak)
    return trim_duration(samples, duration, tail_fade)


def main():
    parser = argparse.ArgumentParser(description="把短语音处理成受伤音效 hit.wav")
    parser.add_argument("source", help="源 WAV（任意声道/采样率，16-bit 最佳）")
    parser.add_argument("-o", "--output", default="assets/audio/sfx/hit.wav",
                        help="输出路径（默认 assets/audio/sfx/hit.wav）")
    parser.add_argument("--speed", type=float, default=1.3,
                        help="升调/变速系数：>1 更高更短（默认 1.3）")
    parser.add_argument("--fall", type=float, default=1.0,
                        help="尾音降调系数：0.9 表示结尾音高降到 90%%（默认 1.0）")
    parser.add_argument("--breath", type=float, default=0.04,
                        help="气声比例 0~0.2（默认 0.04）")
    parser.add_argument("--peak", type=float, default=0.6, help="归一化峰值（默认 0.6）")
    parser.add_argument("--cutoff", type=float, default=9000.0,
                        help="低通截止 Hz，减小 TTS 毛刺（默认 9000；0 = 不滤波）")
    parser.add_argument("--trim-threshold", type=float, default=0.015,
                        help="裁掉首尾静音的相对门限（默认 1.5%%；保留软起音可调小）")
    parser.add_argument("--duration", type=float, default=0.0,
                        help="输出最长时长（秒）；0 = 不截断。只变短、不升调")
    parser.add_argument("--tail-fade", type=float, default=0.06,
                        help="--duration 截断时的收尾淡出秒数（默认 0.06）")
    parser.add_argument("--pitch", type=float, default=0.0,
                        help="升降调半音数（负值降调，例如 -2；默认 0）")
    parser.add_argument("--punch", type=float, default=0.0,
                        help="首音节爆发感 0~1.5（0 关闭；默认 0）")
    parser.add_argument("--skip", type=float, default=0.0,
                        help="从裁完静音的起点再跳过 N 秒（默认 0；软起音可跳过几毫秒）")
    parser.add_argument("--split", type=float, default=0.0,
                        help="音节分界秒数（“呃”结束/“啊”开始；默认 0 关闭）")
    parser.add_argument("--first-gain", type=float, default=0.0,
                        help="分界前（呃）增益 dB，例如 +2（默认 0）")
    parser.add_argument("--second-gain", type=float, default=0.0,
                        help="分界后（啊）增益 dB，例如 -8（默认 0）")
    parser.add_argument("--second-decay", type=float, default=0.0,
                        help="分界后指数衰减时间常数秒，例如 0.09（默认 0 不衰减）")
    parser.add_argument("--blend", type=float, default=0.02,
                        help="“呃/啊”增益过渡时长秒（默认 0.02；调大更顺滑）")
    parser.add_argument("--punch-click", type=float, default=0.2,
                        help="起音冲击瞬态强度（默认 0.2；0 = 去掉合成的“刷”声）")
    parser.add_argument("--punch-hf", type=float, default=0.55,
                        help="起音高频提亮强度（默认 0.55；0 = 保留原始音色）")
    parser.add_argument("--brightness", type=float, default=0.0,
                        help="全频段提亮 dB（3.5 kHz 以上，默认 0；沉闷时可试 3~6）")
    parser.add_argument("--low-cut", type=float, default=0.0,
                        help="低切 Hz（默认 0；沉闷时可试 200~300，去掉低频糊）")
    parser.add_argument("--presence", type=float, default=0.0,
                        help="仅提亮 2.5–6.5 kHz 的存在感 dB（比 --brightness 更不容易带出嘶声）")
    parser.add_argument("--presence-high", type=float, default=6500.0,
                        help="存在感提亮的上限 Hz（默认 6500；可到 7500）")
    parser.add_argument("--lpf-order", type=int, default=1,
                        help="低通阶数（默认 1；用 3~4 可真正切掉 8 kHz 以上的嘶声）")
    parser.add_argument("--fade-in", type=float, default=0.0015,
                        help="淡入秒数（默认 0.0015；起音有“啪”声时可加到 0.008~0.015）")
    args = parser.parse_args()

    samples = build_voice(
        args.source, speed=args.speed, fall=args.fall, breath=args.breath,
        peak=args.peak, cutoff=args.cutoff, trim_threshold=args.trim_threshold,
        duration=args.duration, tail_fade=args.tail_fade,
        pitch=args.pitch, punch=args.punch, skip=args.skip,
        split=args.split, first_gain=args.first_gain,
        second_gain=args.second_gain, second_decay=args.second_decay,
        blend=args.blend, punch_click=args.punch_click, punch_hf=args.punch_hf,
        brightness=args.brightness, low_cut=args.low_cut, presence=args.presence,
        presence_high=args.presence_high, lpf_order=args.lpf_order,
        fade_in=args.fade_in,
    )
    write_wav(args.output, samples)
    duration = len(samples) / float(SAMPLE_RATE)
    print("%s -> %s  (%.3fs, %.0f Hz, peak %.2f)" % (
        args.source, args.output, duration, SAMPLE_RATE, args.peak))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
