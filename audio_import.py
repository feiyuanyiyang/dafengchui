#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把玩家音频导入自定义音效库（P1）。

流程：
    校验格式/大小 -> 解码 -> 转 44.1k 单声道 -> 裁静音 -> 归一化 -> 写 WAV
    -> 去重（sha256）-> 追加 library/index.json

支持格式：
    .wav                     标准库直接解码（PCM 8/16/24/32-bit）
    .mp3/.ogg/.flac          用 pygame.mixer 解码（SDL_mixer 内建 dr_mp3 /
                             stb_vorbis / dr_flac），不依赖 PyAV，
                             所以打包版也能用
    .m4a                     需要 PyAV（`pip install av`）；打包版没有 PyAV，
                             会给出明确提示（SDL_mixer 不含 AAC 解码器）

PyAV 只在这种情况下才用得上：普通 WAV 解不开（浮点/特殊编码）、
以及 M4A。打包版把 PyAV 排除掉了（av.libs 有 83 MB，不值得）。
"""

from __future__ import annotations

import array
import ctypes
import hashlib
import math
import os
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import audio_profile

SAMPLE_RATE = 44100
SUPPORTED_EXTENSIONS = (".wav", ".mp3", ".m4a", ".ogg", ".flac")
# 交给 pygame.mixer（SDL_mixer）解码的格式：无需 PyAV，打包版可用。
MIXER_EXTENSIONS = (".mp3", ".ogg", ".flac")
MAX_BYTES = 50 * 1024 * 1024
MAX_SFX_SECONDS = 30.0
MAX_BGM_SECONDS = 600.0
MIN_SECONDS = 0.05
SILENCE_PEAK = 32  # 约 -60 dBFS（int16）


class AudioImportError(Exception):
    """可以直接展示给玩家的导入错误。"""


@dataclass
class ImportOptions:
    slot: str = "hit"
    display_name: str | None = None
    notes: str = ""
    trim: bool | None = None            # None = 受击类裁、BGM 不裁
    normalize: bool = True
    peak: float | None = None           # None = 受击 0.6 / BGM 0.35
    trim_db: float = -46.0
    pad_in: float = 0.010
    pad_out: float = 0.030
    fade_in: float = 0.003
    fade_out: float = 0.030

    def resolved_slot(self):
        slot = audio_profile.SLOT_BY_ID.get(self.slot)
        if slot is None:
            raise AudioImportError("未知槽位：%s" % self.slot)
        return slot

    def resolved_peak(self) -> float:
        if self.peak is not None:
            return float(self.peak)
        return 0.35 if self.slot == "bgm" else 0.6

    def resolved_trim(self) -> bool:
        if self.trim is not None:
            return bool(self.trim)
        return self.slot != "bgm"


@dataclass
class ImportResult:
    entry: dict
    path: Path
    created: bool


# --------------------------------------------------------------------------- #
# 解码
# --------------------------------------------------------------------------- #
def _decode_wav(path: Path) -> array.array:
    with wave.open(str(path), "rb") as handle:
        if handle.getcomptype() != "NONE":
            raise AudioImportError("WAV 使用了非 PCM 编码，请转成普通 PCM WAV")
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    if not raw:
        raise AudioImportError("WAV 里没有音频数据")

    if width == 1:
        samples = array.array("h", ((value - 128) << 8 for value in raw))
    elif width == 2:
        samples = array.array("h")
        samples.frombytes(raw)
        if sys.byteorder == "big":
            samples.byteswap()
    elif width == 3:
        samples = array.array("h")
        for index in range(0, len(raw) - 2, 3):
            value = int.from_bytes(raw[index:index + 3], "little", signed=True)
            samples.append(value >> 8)
    elif width == 4:
        wide = array.array("i")
        wide.frombytes(raw)
        if sys.byteorder == "big":
            wide.byteswap()
        samples = array.array("h", (value >> 16 for value in wide))
    else:
        raise AudioImportError("不支持的 WAV 位深：%d 字节" % width)

    if channels > 1:
        samples = _downmix(samples, channels)
    if rate != SAMPLE_RATE:
        samples = _resample(samples, rate)
    return samples


def _downmix(samples: array.array, channels: int) -> array.array:
    if channels == 2:
        try:
            import audioop

            raw = samples.tobytes()
            if sys.byteorder == "big":
                swapped = array.array("h", samples)
                swapped.byteswap()
                raw = swapped.tobytes()
            mono = audioop.tomono(raw, 2, 0.5, 0.5)
            output = array.array("h")
            output.frombytes(mono)
            if sys.byteorder == "big":
                output.byteswap()
            return output
        except Exception:
            pass
    total = len(samples) - (len(samples) % channels)
    output = array.array("h")
    for base in range(0, total, channels):
        output.append(sum(samples[base:base + channels]) // channels)
    return output


def _resample(samples: array.array, source_rate: int) -> array.array:
    if source_rate == SAMPLE_RATE:
        return samples
    try:
        import audioop

        raw = samples.tobytes()
        if sys.byteorder == "big":
            swapped = array.array("h", samples)
            swapped.byteswap()
            raw = swapped.tobytes()
        converted, _ = audioop.ratecv(raw, 2, 1, source_rate, SAMPLE_RATE, None)
        output = array.array("h")
        output.frombytes(converted)
        if sys.byteorder == "big":
            output.byteswap()
        return output
    except Exception:
        pass

    # 纯 Python 线性插值兜底
    ratio = SAMPLE_RATE / float(source_rate)
    count = int(len(samples) * ratio)
    if count <= 0:
        return array.array("h")
    step = source_rate / float(SAMPLE_RATE)
    output = array.array("h")
    last = len(samples) - 1
    position = 0.0
    for _ in range(count):
        index = int(position)
        fraction = position - index
        if index >= last:
            output.append(samples[last])
        else:
            left = samples[index]
            right = samples[index + 1]
            output.append(int(left + (right - left) * fraction))
        position += step
    return output


def _av_frame_bytes(frame) -> bytes:
    plane = frame.planes[0]
    return ctypes.string_at(plane.buffer_ptr, frame.samples * 2)


def _decode_pyav(path: Path) -> array.array:
    try:
        import av
    except Exception as exc:
        raise AudioImportError(
            "M4A（以及普通解码器打不开的 WAV）需要 PyAV（pip install av）；"
            "打包版没有 PyAV，请把 M4A 转成 WAV / MP3 / OGG / FLAC。"
            "原始错误：%s" % exc)

    try:
        container = av.open(str(path))
    except Exception as exc:
        raise AudioImportError("无法打开音频文件：%s" % exc)

    with container:
        stream = next((item for item in container.streams if item.type == "audio"), None)
        if stream is None:
            raise AudioImportError("文件里没有音频轨道")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        chunks = []
        try:
            for frame in container.decode(stream):
                for output in resampler.resample(frame):
                    chunks.append(_av_frame_bytes(output))
            tail = resampler.resample(None)
            for output in (tail or []):
                chunks.append(_av_frame_bytes(output))
        except Exception as exc:
            raise AudioImportError("解码失败：%s" % exc)

    raw = b"".join(chunks)
    if not raw:
        raise AudioImportError("解码后没有音频数据")
    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def _downmix_to_mono(samples: array.array, channels: int) -> array.array:
    """多声道 → 单声道（左右取平均，避免只取一边导致音量减半）。"""
    if channels <= 1:
        return samples
    audioop = _audioop_module()
    if audioop is not None:
        mono = audioop.tomono(samples.tobytes(), 2, 0.5, 0.5)
        out = array.array("h")
        out.frombytes(mono)
        if sys.byteorder == "big":
            out.byteswap()
        return out
    frames = len(samples) // channels
    out = array.array("h", bytes(0))
    append = out.append
    if channels == 2:
        for index in range(0, frames * 2, 2):
            append((samples[index] + samples[index + 1]) // 2)
        return out
    for frame in range(frames):
        base = frame * channels
        append(sum(samples[base:base + channels]) // channels)
    return out


def _resample_mono(samples: array.array, source_rate: int,
                   target_rate: int) -> array.array:
    """单声道 int16 重采样到目标采样率。"""
    if not samples or source_rate == target_rate:
        return samples
    audioop = _audioop_module()
    if audioop is not None:
        converted, _ = audioop.ratecv(
            samples.tobytes(), 2, 1, source_rate, target_rate, None)
        out = array.array("h")
        out.frombytes(converted)
        if sys.byteorder == "big":
            out.byteswap()
        return out
    # 没有 audioop（Python 3.13+）时退回线性插值。
    ratio = target_rate / float(source_rate)
    total = max(1, int(round(len(samples) * ratio)))
    out = array.array("h", bytes(0))
    last = samples[-1]
    for index in range(total):
        position = index / ratio
        lower = int(position)
        fraction = position - lower
        first = samples[lower] if lower < len(samples) else last
        second = samples[lower + 1] if lower + 1 < len(samples) else first
        out.append(int(first + (second - first) * fraction))
    return out


def _decode_mixer(path: Path) -> array.array:
    """用 pygame.mixer 解码 MP3 / OGG / FLAC。

    SDL_mixer 内建 dr_mp3、stb_vorbis、dr_flac，所以不需要 PyAV —— 这就是
    打包版能直接导入 MP3 的原因。Sound 会按 mixer 当前参数解码，这里再统一
    成 44.1k 单声道 int16。
    """
    try:
        import pygame
    except Exception as exc:
        raise AudioImportError(
            "解码 %s 需要 pygame，当前环境不可用：%s" % (path.suffix, exc))

    started_here = False
    try:
        state = pygame.mixer.get_init()
        if state is None:
            pygame.mixer.init()
            started_here = True
            state = pygame.mixer.get_init()
        sound = pygame.mixer.Sound(str(path))
        raw = sound.get_raw()
    except Exception as exc:
        raise AudioImportError("无法解码 %s：%s" % (path.suffix, exc))
    finally:
        if started_here:
            pygame.mixer.quit()

    if not raw:
        raise AudioImportError("解码后没有音频数据")
    rate, size, channels = state or (SAMPLE_RATE, -16, 1)
    if size != -16:
        raise AudioImportError("解码位深不受支持（%s 位），请改用 WAV" % size)

    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()
    if channels and channels > 1:
        samples = _downmix_to_mono(samples, channels)
    if rate != SAMPLE_RATE:
        samples = _resample_mono(samples, rate, SAMPLE_RATE)
    return samples


def decode_audio(path: Path) -> array.array:
    suffix = path.suffix.lower()
    if suffix == ".wav":
        try:
            return _decode_wav(path)
        except AudioImportError:
            raise
        except Exception:
            pass  # 浮点 WAV / 特殊编码交给 PyAV
    elif suffix in MIXER_EXTENSIONS:
        return _decode_mixer(path)
    return _decode_pyav(path)


# --------------------------------------------------------------------------- #
# 处理
# --------------------------------------------------------------------------- #
def _audioop_module():
    """返回标准库 audioop（C 速度）；Python 3.13+ 缺失时返回 None。"""
    try:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import audioop

        return audioop
    except Exception:
        return None


def _peak(samples: array.array) -> int:
    if not samples:
        return 0
    op = _audioop_module()
    if op is not None:
        try:
            raw = samples.tobytes()
            if sys.byteorder == "big":
                swapped = array.array("h", samples)
                swapped.byteswap()
                raw = swapped.tobytes()
            return op.max(raw, 2)
        except Exception:
            pass
    return max(max(samples), -min(samples))


def _trim(samples: array.array, threshold_db: float,
          pad_in: float, pad_out: float) -> array.array:
    peak = _peak(samples)
    if peak <= 0:
        raise AudioImportError("音频是静音")
    threshold = max(1, int(peak * 10.0 ** (threshold_db / 20.0)))
    start = 0
    while start < len(samples) and abs(samples[start]) < threshold:
        start += 1
    end = len(samples) - 1
    while end > start and abs(samples[end]) < threshold:
        end -= 1
    start = max(0, start - int(round(pad_in * SAMPLE_RATE)))
    end = min(len(samples), end + int(round(pad_out * SAMPLE_RATE)) + 1)
    return samples[start:end]


def _normalize(samples: array.array, target_peak: float) -> array.array:
    peak = _peak(samples)
    if peak <= 0:
        raise AudioImportError("音频是静音")
    scale = target_peak * 32767.0 / peak
    if abs(scale - 1.0) < 1e-6:
        return samples
    # 长 BGM 有上千万个采样，用 audioop.mul 在 C 里缩放并削波；缺失时退回
    # 逐样本循环。结果一致，只是速度不同。
    op = _audioop_module()
    if op is not None:
        try:
            raw = samples.tobytes()
            if sys.byteorder == "big":
                swapped = array.array("h", samples)
                swapped.byteswap()
                raw = swapped.tobytes()
            scaled = op.mul(raw, 2, scale)
            output = array.array("h")
            output.frombytes(scaled)
            if sys.byteorder == "big":
                output.byteswap()
            return output
        except Exception:
            pass
    return array.array(
        "h", (max(-32768, min(32767, int(value * scale))) for value in samples))


def _fade(samples: array.array, fade_in: float, fade_out: float) -> array.array:
    output = array.array("h", samples)
    count = len(output)
    fin = min(count, max(1, int(round(fade_in * SAMPLE_RATE))))
    fout = min(count, max(1, int(round(fade_out * SAMPLE_RATE))))
    for index in range(fin):
        output[index] = int(output[index] * 0.5 * (1.0 - math.cos(math.pi * index / fin)))
    for index in range(fout):
        output[count - 1 - index] = int(
            output[count - 1 - index] * 0.5 * (1.0 + math.cos(math.pi * index / fout)))
    return output


def _write_wav(path: Path, samples: array.array) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    output = samples
    if sys.byteorder == "big":
        output = array.array("h", samples)
        output.byteswap()
    with wave.open(str(temporary), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(output.tobytes())
    os.replace(temporary, path)


# --------------------------------------------------------------------------- #
# 导入
# --------------------------------------------------------------------------- #
def _safe_display_name(value: str) -> str:
    cleaned = "".join(ch for ch in value if ch >= " " and ch != "\x7f").strip()
    return cleaned[:80] or "未命名音效"


def _ensure_dirs(root: Path) -> None:
    base = audio_profile.library_dir(root)
    (base / "sfx").mkdir(parents=True, exist_ok=True)
    (base / "bgm").mkdir(parents=True, exist_ok=True)


def _next_id(digest: str, entries: list[dict]) -> str:
    length = 10
    while True:
        candidate = digest[:length]
        conflicts = [entry for entry in entries
                     if entry.get("id") == candidate
                     and entry.get("sha256") != digest]
        if not conflicts:
            return candidate
        length += 2
        if length > len(digest):
            raise AudioImportError("无法生成唯一的音效 id")


def import_audio(root: Path, source: Path, options: ImportOptions | None = None,
                 ) -> ImportResult:
    options = options or ImportOptions()
    slot = options.resolved_slot()
    source = Path(source)
    if not source.is_file():
        raise AudioImportError("找不到文件：%s" % source)
    if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise AudioImportError(
            "不支持的格式 %s；可用：%s" % (source.suffix, " ".join(SUPPORTED_EXTENSIONS)))
    size = source.stat().st_size
    if size <= 0:
        raise AudioImportError("文件是空的")
    if size > MAX_BYTES:
        raise AudioImportError("文件超过 %d MB" % (MAX_BYTES // (1024 * 1024)))

    samples = decode_audio(source)
    duration = len(samples) / float(SAMPLE_RATE)
    if duration < MIN_SECONDS:
        raise AudioImportError("音频太短（%.2f s，至少要 %.2f s）" % (duration, MIN_SECONDS))
    limit = MAX_BGM_SECONDS if slot.kind == "bgm" else MAX_SFX_SECONDS
    if duration > limit:
        raise AudioImportError("音频太长（%.1f s，上限 %.0f s）" % (duration, limit))
    if _peak(samples) < SILENCE_PEAK:
        raise AudioImportError("音频几乎是静音")

    if options.resolved_trim():
        samples = _trim(samples, options.trim_db, options.pad_in, options.pad_out)
        duration = len(samples) / float(SAMPLE_RATE)
        if duration < MIN_SECONDS:
            raise AudioImportError("裁掉静音后太短（%.2f s）" % duration)
    if options.normalize:
        samples = _normalize(samples, options.resolved_peak())
    else:
        peak = _peak(samples)
        if peak > 0:
            samples = _normalize(samples, min(1.0, peak / 32768.0))
    if slot.kind != "bgm":
        samples = _fade(samples, options.fade_in, options.fade_out)

    if sys.byteorder == "big":
        little = array.array("h", samples)
        little.byteswap()
        payload = little.tobytes()
    else:
        payload = samples.tobytes()
    digest = hashlib.sha256(payload).hexdigest()

    _ensure_dirs(root)
    index = audio_profile.load_library_index(root)
    entries = index["entries"]
    for entry in entries:
        if entry.get("sha256") == digest:
            existing = audio_profile.library_dir(root) / str(entry.get("file", ""))
            if existing.is_file():
                return ImportResult(dict(entry), existing, created=False)

    identifier = _next_id(digest, entries)
    category = "bgm" if slot.kind == "bgm" else "sfx"
    relative = "%s/lib_%s.wav" % (category, identifier)
    target = audio_profile.library_dir(root) / relative
    _write_wav(target, samples)

    display_name = _safe_display_name(options.display_name or source.stem)
    entry = {
        "id": identifier,
        "display_name": display_name,
        "file": relative,
        "slot_hint": slot.id,
        "duration": round(len(samples) / float(SAMPLE_RATE), 3),
        "sample_rate": SAMPLE_RATE,
        "channels": 1,
        "peak": round(_peak(samples) / 32768.0, 4),
        "sha256": digest,
        "source_format": source.suffix.lower().lstrip("."),
        "source_name": source.name,
        "imported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "notes": options.notes,
        "bytes": len(payload),
    }
    entries.append(entry)
    audio_profile.save_library_index(root, index)
    return ImportResult(entry, target, created=True)


def rescan_library(root: Path) -> list[dict]:
    """扫描 library/{sfx,bgm} 里没有登记进 index 的 WAV，补登记。

    只接受已经是 44.1k / 单声道 / 16-bit 的 WAV；不符合的留给界面提示“需要转换”。
    """
    _ensure_dirs(root)
    index = audio_profile.load_library_index(root)
    entries = index["entries"]
    base = audio_profile.library_dir(root)
    resolved_base = base.resolve()

    # 先清掉文件已经不在的孤儿条目，避免 --list / UI 出现点不开的记录
    kept = []
    removed = False
    for entry in entries:
        relative = str(entry.get("file", ""))
        candidate = None
        if relative:
            try:
                candidate = (base / relative).resolve()
            except OSError:
                candidate = None
        if (candidate is not None and candidate.is_relative_to(resolved_base)
                and candidate.is_file()):
            kept.append(entry)
        else:
            removed = True
    if removed:
        entries = kept
        index["entries"] = entries

    known_files = {str(entry.get("file", "")) for entry in entries}
    known_hashes = {str(entry.get("sha256", "")) for entry in entries
                    if entry.get("sha256")}
    added: list[dict] = []

    for category in ("sfx", "bgm"):
        folder = base / category
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.wav")):
            relative = path.relative_to(base).as_posix()
            if relative in known_files:
                continue
            try:
                with wave.open(str(path), "rb") as handle:
                    if (handle.getnchannels() != 1
                            or handle.getsampwidth() != 2
                            or handle.getframerate() != SAMPLE_RATE
                            or handle.getcomptype() != "NONE"):
                        continue
                    frames = handle.readframes(handle.getnframes())
            except Exception:
                continue
            if not frames:
                continue
            samples = array.array("h")
            samples.frombytes(frames)
            if sys.byteorder == "big":
                samples.byteswap()
            digest = hashlib.sha256(frames).hexdigest()
            if digest in known_hashes:
                continue
            entry_id = _next_id(digest, entries)
            entry = {
                "id": entry_id,
                "display_name": _safe_display_name(path.stem),
                "file": relative,
                "slot_hint": "bgm" if category == "bgm" else "",
                "duration": round(len(samples) / float(SAMPLE_RATE), 3),
                "sample_rate": SAMPLE_RATE,
                "channels": 1,
                "peak": round(_peak(samples) / 32768.0, 4),
                "sha256": digest,
                "source_format": "wav",
                "source_name": path.name,
                "imported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "notes": "直接放入库目录",
                "bytes": len(frames),
            }
            entries.append(entry)
            known_files.add(relative)
            known_hashes.add(digest)
            added.append(entry)

    if added or removed:
        audio_profile.save_library_index(root, index)
    return added


def set_slot_entry(root: Path, slot_id: str, entry_id: str,
                   mode: str = "add") -> dict:
    """把库条目写进 profile；mode=add 追加，mode=only 只保留它。"""
    slot = audio_profile.SLOT_BY_ID.get(slot_id)
    if slot is None:
        raise AudioImportError("未知槽位：%s" % slot_id)
    reference = "lib:" + entry_id
    profile = audio_profile.load_profile(root)
    references = list(profile["audio"].get(slot_id, []))

    if slot.max_count <= 1 or mode == "only":
        references = [reference]
    elif reference not in references:
        if len(references) >= slot.max_count:
            raise AudioImportError(
                "槽位 %s 已满（最多 %d 条）；先用 --enable only，或移除一条"
                % (slot_id, slot.max_count))
        references.append(reference)

    profile["audio"][slot_id] = references[:slot.max_count]
    audio_profile.save_profile(root, profile)
    return profile
