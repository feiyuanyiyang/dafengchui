# -*- coding: utf-8 -*-
"""《大风吹》音频层：BGM 随机播放 + 多音效混音。

后端优先级
    1. pygame.mixer —— 支持多音效叠加、音量/暂停；推荐方案。
    2. waveOut     —— Windows 标准库（ctypes + winmm），启动时打开一个固定
                      流池（8 条）并常驻，播放时只重新装填缓冲区，起音延迟
                      只有几毫秒；BGM 走一条独立的流。
    3. winsound    —— 仅 Windows 标准库，只能播 WAV、无音量、无混音；
                      每次调用都可能等待系统提示音设备唤醒（部分笔记本上
                      超过 1 秒），只作为最后的兜底。
    4. 静默        —— 都不可用时游戏照常运行，只是没有声音。

背景音乐（BGM）从“单选循环”改成了“随机池”：内置曲目和玩家导入的曲目
可以由玩家勾选成一个池子，游戏中按“一轮内不重复、轮与轮之间不连播同一首”
的顺序随机播放；只选一首时仍然无缝循环。
"""

from __future__ import annotations

import atexit
import os
import random
import sys
import time
from array import array
from pathlib import Path

try:  # 自定义音效资源；缺失时退回内置路径
    import audio_profile
except Exception:  # pragma: no cover - 极端环境下仍可运行
    audio_profile = None

# 逻辑事件名：模型/应用层只认这些
SFX_NAMES = ("hit", "wind_change", "game_over", "pickup")
# 受伤音效的随机变体：play("hit") 时从这三个里随机选一个
HIT_VARIANTS = ("hit_1", "hit_2", "hit_3")
# 实际需要加载/校验的音频文件（逻辑事件 hit 由 HIT_VARIANTS 代替）
SFX_FILES = ("wind_change", "game_over", "pickup") + HIT_VARIANTS
# 逻辑事件 -> 随机变体。想加第四条受击音，把文件加进 HIT_VARIANTS 即可。
RANDOM_GROUPS = {"hit": HIT_VARIANTS}
BGM_NAME = "wind_ambience"  # 旧接口保留；BGM 现在由播放列表管理
BGM_CHANNEL = 0  # 固定占一个声道给 BGM，避免音效抢占
PREVIEW_CHANNEL = 7  # 自定义界面试听专用，避开 BGM 与游戏音效
WAVEOUT_POOL_SIZE = 8  # waveOut 固定流池：任意多的变体共用这几条流
WAVEFORMAT_PCM = 1
WAVE_MAPPER = 0xFFFFFFFF
SAMPLE_RATE = 44100
WHDR_BEGINLOOP = 0x00000004
WHDR_ENDLOOP = 0x00000008
WHDR_LOOP_FOREVER = 0xFFFFFFFF
TIME_SAMPLES = 0x00000002  # MMTIME.wType：waveOutGetPosition 按采样数返回
BGM_END_TOLERANCE = 0.03  # 距结尾 30 ms 就切下一首，避免听到尾部空白
BGM_CHECK_INTERVAL = 0.10  # 主循环里检查“当前曲目是否播完”的间隔


class BgmSequence:
    """BGM 随机顺序：洗牌成“一轮”，一轮内不重复，轮间避免连播同一首。

    与音频后端完全解耦，便于自测。只选一首时每轮都返回 0，由后端决定是否
    用循环标志无缝循环。
    """

    def __init__(self, count, rng=None):
        self.count = max(0, int(count))
        self._rng = rng or random.Random()
        self._bag: list[int] = []
        self._last = None

    def reset(self):
        self._bag = []
        self._last = None

    def next_index(self):
        if self.count <= 0:
            return None
        if not self._bag:
            self._bag = list(range(self.count))
            self._rng.shuffle(self._bag)
            if (self.count > 1 and self._last is not None
                    and self._bag[0] == self._last):
                self._bag.append(self._bag.pop(0))
        index = self._bag.pop(0)
        self._last = index
        return index



def audio_root():
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "assets" / "audio"
    return Path(__file__).resolve().parent / "assets" / "audio"


def _builtin_sound_set(root: Path | None = None) -> dict[str, list[Path]]:
    """内置音效兜底：不依赖自定义配置，保证游戏永远有声音。"""
    if audio_profile is not None:
        try:
            return audio_profile.builtin_sound_set()
        except Exception:
            pass
    base = root if root is not None else audio_root()
    return {
        "hit": [base / "sfx" / name for name in
                ("hit_1.wav", "hit_2.wav", "hit_3.wav")],
        "wind_change": [base / "sfx" / "wind_change.wav"],
        "game_over": [base / "sfx" / "game_over.wav"],
        "pickup": [base / "sfx" / "pickup.wav"],
        "bgm": [base / "bgm" / "wind_ambience.wav"],
    }


def read_sfx_pcm(path, volume=1.0):
    """读入单声道 16-bit / 44.1 kHz WAV，裁掉开头静音并按音量缩放。

    裁掉开头静音是给“自录音效”留的保险：玩家用自己的哼唧声替换 hit.wav
    时，录音开头哪怕只有 100 ms 空白，也会听起来像音效迟到。这里以文件
    峰值的 0.2%（约 -54 dBFS）为门限，只裁真正的静音，不会把 20–30 ms 的
    软起音当成静音剪掉。
    返回小端 int16 字节串（waveOut 用），格式不符时返回 None。
    """
    import wave

    try:
        with wave.open(str(path), "rb") as handle:
            if (
                handle.getnchannels() != 1
                or handle.getsampwidth() != 2
                or handle.getframerate() != SAMPLE_RATE
            ):
                return None
            pcm = array("h")
            pcm.frombytes(handle.readframes(handle.getnframes()))
    except Exception:
        return None
    if not pcm:
        return None
    if sys.byteorder == "big":
        pcm.byteswap()
    peak = max(abs(value) for value in pcm)
    threshold = max(24, int(peak * 0.002))
    start = 0
    while start < len(pcm) and abs(pcm[start]) < threshold:
        start += 1
    if start:
        pcm = pcm[start:]
    if not pcm:
        return None
    if volume != 1.0:
        for index, value in enumerate(pcm):
            pcm[index] = max(-32768, min(32767, int(value * volume)))
    if sys.byteorder == "big":
        pcm.byteswap()
    return pcm.tobytes()


def _scale_pcm(raw: bytes, volume: float) -> bytes:
    """按音量缩放小端 int16 PCM；优先用标准库 audioop（C 速度）。

    一首 4 分钟的 BGM 有约 1000 万个采样，纯 Python 逐样本缩放会卡住加载；
    audioop.mul 在 C 里完成并自动削波。Python 3.13+ 没有 audioop 时退回
    逐样本循环，只影响速度，不影响正确性。
    """
    if abs(volume - 1.0) < 1e-6:
        return raw
    try:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import audioop

        return audioop.mul(raw, 2, float(volume))
    except Exception:
        pcm = array("h")
        pcm.frombytes(raw)
        if sys.byteorder == "big":
            pcm.byteswap()
        for index, value in enumerate(pcm):
            pcm[index] = max(-32768, min(32767, int(value * volume)))
        if sys.byteorder == "big":
            pcm.byteswap()
        return pcm.tobytes()


def read_bgm_pcm(path, volume=1.0):
    """读整首 BGM 的 PCM（44.1k / 单声道 / 16-bit），不裁首尾静音。

    背景音乐可能从很轻的前奏开始，不能像音效那样裁掉“开头静音”；也不做
    淡入淡出，保留原曲听感。格式不符时返回 None。
    """
    import wave

    try:
        with wave.open(str(path), "rb") as handle:
            if (
                handle.getnchannels() != 1
                or handle.getsampwidth() != 2
                or handle.getframerate() != SAMPLE_RATE
            ):
                return None
            raw = handle.readframes(handle.getnframes())
    except Exception:
        return None
    if not raw:
        return None
    return _scale_pcm(raw, volume)


def wav_duration(path) -> float:
    """只读 WAV 头得到时长；失败返回 0。"""
    import wave

    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            frames = handle.getnframes()
        return frames / float(rate) if rate else 0.0
    except Exception:
        return 0.0


class WaveOutPlayer:
    """Windows waveOut 低延迟音效后端。

    `winsound.PlaySound` 每次播放都要经过系统提示音设备；在部分笔记本的
    Realtek 扬声器上，设备唤醒会让声音比画面晚 0.2～1 s。这里在初始化时
    打开固定数量的常驻输出流（流池），播放时把对应 PCM 装进空闲流并
    `prepare + waveOutWrite`，实测起音在 20 ms 量级；多个音效共用流池，
    所以变体再多也不会出现“一文件一条流”把声卡拖垮的问题。
    """

    def __init__(self, paths, volume=1.0, bgm_paths=None, bgm_volume=1.0):
        import ctypes
        from ctypes import wintypes

        class WAVEFORMATEX(ctypes.Structure):
            _fields_ = [
                ("wFormatTag", wintypes.WORD),
                ("nChannels", wintypes.WORD),
                ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD),
                ("nBlockAlign", wintypes.WORD),
                ("wBitsPerSample", wintypes.WORD),
                ("cbSize", wintypes.WORD),
            ]

        class WAVEHDR(ctypes.Structure):
            _fields_ = [
                ("lpData", ctypes.c_void_p),
                ("dwBufferLength", wintypes.DWORD),
                ("dwBytesRecorded", wintypes.DWORD),
                ("dwUser", ctypes.c_void_p),
                ("dwFlags", wintypes.DWORD),
                ("dwLoops", wintypes.DWORD),
                ("lpNext", ctypes.c_void_p),
                ("reserved", ctypes.c_void_p),
            ]

        class MMTIME_UNION(ctypes.Union):
            _fields_ = [
                ("ms", wintypes.DWORD),
                ("sample", wintypes.DWORD),
                ("cb", wintypes.DWORD),
                ("ticks", wintypes.DWORD),
                ("pad", ctypes.c_byte * 8),
            ]

        class MMTIME(ctypes.Structure):
            _fields_ = [
                ("wType", wintypes.UINT),
                ("u", MMTIME_UNION),
            ]

        self._ctypes = ctypes
        self._WAVEHDR = WAVEHDR
        self._MMTIME = MMTIME
        winmm = ctypes.windll.winmm
        self._winmm = winmm
        winmm.waveOutOpen.argtypes = [
            ctypes.POINTER(ctypes.c_void_p), wintypes.UINT,
            ctypes.POINTER(WAVEFORMATEX), ctypes.c_void_p, ctypes.c_void_p,
            wintypes.UINT,
        ]
        winmm.waveOutOpen.restype = wintypes.UINT
        winmm.waveOutPrepareHeader.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT,
        ]
        winmm.waveOutPrepareHeader.restype = wintypes.UINT
        winmm.waveOutWrite.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT,
        ]
        winmm.waveOutWrite.restype = wintypes.UINT
        winmm.waveOutReset.argtypes = [ctypes.c_void_p]
        winmm.waveOutReset.restype = wintypes.UINT
        winmm.waveOutUnprepareHeader.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT,
        ]
        winmm.waveOutUnprepareHeader.restype = wintypes.UINT
        winmm.waveOutClose.argtypes = [ctypes.c_void_p]
        winmm.waveOutClose.restype = wintypes.UINT
        winmm.waveOutPause.argtypes = [ctypes.c_void_p]
        winmm.waveOutPause.restype = wintypes.UINT
        winmm.waveOutRestart.argtypes = [ctypes.c_void_p]
        winmm.waveOutRestart.restype = wintypes.UINT
        winmm.waveOutGetPosition.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(MMTIME), wintypes.UINT,
        ]
        winmm.waveOutGetPosition.restype = wintypes.UINT
        self._fmt = WAVEFORMATEX(
            WAVEFORMAT_PCM, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16, 0
        )
        # 先把 PCM 全部读进内存；播放时从固定流池里借一条流，写入对应 PCM。
        self._pcm = {}
        for name, path in paths.items():
            pcm = read_sfx_pcm(path, volume)
            if pcm is not None:
                self._pcm[name] = pcm
        if not self._pcm:
            raise RuntimeError("waveOut 没有可加载的音效")
        self._max_pcm = max(len(pcm) for pcm in self._pcm.values())

        self._slots = []
        for _index in range(WAVEOUT_POOL_SIZE):
            handle = ctypes.c_void_p()
            if winmm.waveOutOpen(
                ctypes.byref(handle), WAVE_MAPPER, ctypes.byref(self._fmt),
                None, None, 0,
            ) != 0:
                continue
            buffer = ctypes.create_string_buffer(self._max_pcm)
            header = WAVEHDR()
            header.lpData = ctypes.cast(buffer, ctypes.c_void_p)
            header.dwBufferLength = self._max_pcm
            self._slots.append({
                "handle": handle,
                "buffer": buffer,
                "header": header,
                "name": None,
                "busy_until": 0.0,
            })
        if not self._slots:
            raise RuntimeError("waveOut 没有可用的输出流")

        # 常驻一条极低电平的循环流，让音频端点一直保持 ACTIVE。否则机器
        # 静音一段时间后第一次受伤时，waveOutWrite 要等设备唤醒，主线程会
        # 卡住几百毫秒，看起来就是“先卡一下，再播受伤贴图和音效”。
        # ±1 LSB（约 -90 dBFS）听不见，但足以避免驱动的静音掉电策略。
        self._keepalive = None
        keepalive_handle = ctypes.c_void_p()
        try:
            if winmm.waveOutOpen(
                ctypes.byref(keepalive_handle), WAVE_MAPPER,
                ctypes.byref(self._fmt),
                None, None, 0,
            ) == 0:
                silent = array(
                    "h", (1 if index & 1 else -1 for index in range(SAMPLE_RATE))
                ).tobytes()
                keepalive_buffer = ctypes.create_string_buffer(silent)
                keepalive_header = WAVEHDR()
                keepalive_header.lpData = ctypes.cast(keepalive_buffer, ctypes.c_void_p)
                keepalive_header.dwBufferLength = len(silent)
                keepalive_header.dwFlags = WHDR_BEGINLOOP | WHDR_ENDLOOP
                keepalive_header.dwLoops = WHDR_LOOP_FOREVER
                prepared = winmm.waveOutPrepareHeader(
                    keepalive_handle, ctypes.byref(keepalive_header),
                    ctypes.sizeof(keepalive_header),
                ) == 0
                if prepared and winmm.waveOutWrite(
                    keepalive_handle, ctypes.byref(keepalive_header),
                    ctypes.sizeof(keepalive_header),
                ) == 0:
                    self._keepalive = (
                        keepalive_handle, keepalive_buffer, keepalive_header
                    )
                else:
                    if prepared:
                        winmm.waveOutUnprepareHeader(
                            keepalive_handle, ctypes.byref(keepalive_header),
                            ctypes.sizeof(keepalive_header),
                        )
                    winmm.waveOutClose(keepalive_handle)
        except Exception:
            self._keepalive = None

        # BGM：单独一条常驻流，与音效流池互不抢占。启动时只开流、不预读
        # 音乐；每次切歌时读取当前这一首的 PCM，避免 12 首歌同时占几百 MB。
        self._bgm_paths = [Path(path) for path in (bgm_paths or ())
                           if Path(path).is_file()]
        self._bgm_volume = bgm_volume
        self._bgm = None
        self._bgm_playing = False
        self._bgm_paused = False
        self._bgm_started = 0.0
        self._bgm_paused_at = 0.0
        self._bgm_paused_total = 0.0
        if self._bgm_paths:
            bgm_handle = ctypes.c_void_p()
            if winmm.waveOutOpen(
                ctypes.byref(bgm_handle), WAVE_MAPPER,
                ctypes.byref(self._fmt), None, None, 0,
            ) == 0:
                self._bgm = {
                    "handle": bgm_handle,
                    "buffer": None,
                    "header": WAVEHDR(),
                    "length": 0,
                    "samples": 0,
                    "looped": False,
                    "prepared": False,
                }
            else:
                self._bgm_paths = []

        self._closed = False
        # 常驻循环流在播放期间，缓冲区必须一直有效：进程退出前主动收尾，
        # 避免解释器释放 ctypes 缓冲区时驱动仍在读导致崩溃。
        atexit.register(self.close)

    def __len__(self):
        return len(self._pcm)

    def names(self):
        return tuple(self._pcm)

    def _choose_slot(self, now):
        for slot in self._slots:
            if now >= slot["busy_until"]:
                return slot
        return min(self._slots, key=lambda slot: slot["busy_until"])

    def play(self, name):
        pcm = self._pcm.get(name)
        if not pcm:
            return False
        ctypes = self._ctypes
        now = time.perf_counter()
        slot = self._choose_slot(now)
        handle = slot["handle"]
        buffer = slot["buffer"]
        header = slot["header"]
        try:
            if now < slot["busy_until"]:
                self._winmm.waveOutReset(handle)
                slot["busy_until"] = 0.0
            self._winmm.waveOutUnprepareHeader(
                handle, ctypes.byref(header), ctypes.sizeof(header))
            ctypes.memmove(buffer, pcm, len(pcm))
            header.dwBufferLength = len(pcm)
            if self._winmm.waveOutPrepareHeader(
                handle, ctypes.byref(header), ctypes.sizeof(header)
            ) != 0:
                self._winmm.waveOutReset(handle)
                if self._winmm.waveOutPrepareHeader(
                    handle, ctypes.byref(header), ctypes.sizeof(header)
                ) != 0:
                    return False
            result = self._winmm.waveOutWrite(
                handle, ctypes.byref(header), ctypes.sizeof(header))
            if result != 0:
                self._winmm.waveOutUnprepareHeader(
                    handle, ctypes.byref(header), ctypes.sizeof(header))
                return False
            slot["name"] = name
            slot["busy_until"] = now + len(pcm) / 2.0 / SAMPLE_RATE + 0.02
            return True
        except Exception:
            return False

    def stop(self, name):
        """立即停掉某个音效（用于受伤时压掉正在响的风变呼啸）。"""
        found = False
        now = time.perf_counter()
        for slot in self._slots:
            if slot["name"] != name:
                continue
            found = True
            try:
                if now < slot["busy_until"]:
                    self._winmm.waveOutReset(slot["handle"])
            except Exception:
                pass
            slot["busy_until"] = 0.0
            slot["name"] = None
        return found

    # ------------------------------------------------------------ BGM
    def _unprepare_bgm(self):
        bgm = self._bgm
        if bgm is None:
            return
        ctypes = self._ctypes
        try:
            self._winmm.waveOutReset(bgm["handle"])
        except Exception:
            pass
        if bgm.get("prepared"):
            try:
                self._winmm.waveOutUnprepareHeader(
                    bgm["handle"], ctypes.byref(bgm["header"]),
                    ctypes.sizeof(bgm["header"]),
                )
            except Exception:
                pass
        bgm["prepared"] = False
        bgm["buffer"] = None
        bgm["length"] = 0
        bgm["samples"] = 0
        bgm["looped"] = False

    def start_bgm(self, path, loop=False):
        """开始播放一首 BGM；loop=True 时由驱动无缝循环（只选一首的场景）。"""
        bgm = self._bgm
        if bgm is None or not path:
            return False
        pcm = read_bgm_pcm(path, self._bgm_volume)
        if not pcm:
            return False
        ctypes = self._ctypes
        winmm = self._winmm
        self._unprepare_bgm()
        buffer = ctypes.create_string_buffer(pcm)
        header = bgm["header"]
        header.lpData = ctypes.cast(buffer, ctypes.c_void_p)
        header.dwBufferLength = len(pcm)
        # 循环标志必须在 prepare 之前设置；dwLoops 用 0xFFFFFFFF 表示无限循环。
        header.dwFlags = (WHDR_BEGINLOOP | WHDR_ENDLOOP) if loop else 0
        header.dwLoops = WHDR_LOOP_FOREVER if loop else 1
        bgm["buffer"] = buffer
        bgm["length"] = len(pcm)
        bgm["samples"] = len(pcm) // 2
        bgm["looped"] = bool(loop)
        try:
            if winmm.waveOutPrepareHeader(
                bgm["handle"], ctypes.byref(header), ctypes.sizeof(header)
            ) != 0:
                return False
            bgm["prepared"] = True
            if winmm.waveOutWrite(
                bgm["handle"], ctypes.byref(header), ctypes.sizeof(header)
            ) != 0:
                self._unprepare_bgm()
                return False
        except Exception:
            return False
        self._bgm_started = time.perf_counter()
        self._bgm_paused_at = 0.0
        self._bgm_paused_total = 0.0
        self._bgm_playing = True
        self._bgm_paused = False
        return True

    def bgm_elapsed(self):
        """当前 BGM 已经播放的秒数；优先问驱动，失败时用墙钟估算。"""
        bgm = self._bgm
        if bgm is None or not self._bgm_playing:
            return 0.0
        wall = time.perf_counter() - self._bgm_started - self._bgm_paused_total
        if self._bgm_paused and self._bgm_paused_at > 0.0:
            wall -= time.perf_counter() - self._bgm_paused_at
        # 驱动位置会随 waveOutPause 一起冻结，比墙钟可靠。
        try:
            position = self._MMTIME()
            position.wType = TIME_SAMPLES
            result = self._winmm.waveOutGetPosition(
                bgm["handle"], self._ctypes.byref(position),
                self._ctypes.sizeof(position),
            )
            if result == 0:
                seconds = position.u.sample / float(SAMPLE_RATE)
                # 有些驱动在刚写入时短暂返回 0；此时用墙钟兜底，避免永远
                # 认为歌曲没播完。
                if seconds > 0.0 or wall < 0.25:
                    return seconds
        except Exception:
            pass
        return max(0.0, wall)

    def bgm_finished(self):
        """当前曲目是否已经播完（循环曲目永远返回 False）。"""
        bgm = self._bgm
        if bgm is None or not self._bgm_playing or self._bgm_paused:
            return False
        if bgm["looped"] or bgm["samples"] <= 0:
            return False
        duration = bgm["samples"] / float(SAMPLE_RATE)
        return self.bgm_elapsed() >= duration - BGM_END_TOLERANCE

    def stop_bgm(self):
        if self._bgm is None:
            return
        self._unprepare_bgm()
        self._bgm_playing = False
        self._bgm_paused = False

    def set_bgm_paused(self, paused):
        """暂停/恢复 BGM 并保留播放位置；短音效不走这条路径。"""
        if self._bgm is None or not self._bgm_playing:
            return
        paused = bool(paused)
        if paused == self._bgm_paused:
            return
        now = time.perf_counter()
        try:
            handle = self._bgm["handle"]
            if paused:
                self._winmm.waveOutPause(handle)
                self._bgm_paused_at = now
            else:
                self._winmm.waveOutRestart(handle)
                if self._bgm_paused_at > 0.0:
                    self._bgm_paused_total += now - self._bgm_paused_at
                self._bgm_paused_at = 0.0
        except Exception:
            return
        self._bgm_paused = paused

    def close(self):
        if getattr(self, "_closed", True):
            return
        self._closed = True
        for slot in self._slots:
            handle = slot["handle"]
            header = slot["header"]
            try:
                ctypes = self._ctypes
                self._winmm.waveOutReset(handle)
                self._winmm.waveOutUnprepareHeader(
                    handle, ctypes.byref(header), ctypes.sizeof(header)
                )
                self._winmm.waveOutClose(handle)
            except Exception:
                pass
        self._slots.clear()
        self._pcm.clear()
        keepalive = self._keepalive
        self._keepalive = None
        if keepalive is not None:
            handle, _buffer, header = keepalive
            try:
                ctypes = self._ctypes
                self._winmm.waveOutReset(handle)
                self._winmm.waveOutUnprepareHeader(
                    handle, ctypes.byref(header), ctypes.sizeof(header)
                )
                self._winmm.waveOutClose(handle)
            except Exception:
                pass

        bgm = self._bgm
        self._bgm = None
        self._bgm_playing = False
        self._bgm_paused = False
        if bgm is not None:
            handle = bgm["handle"]
            try:
                ctypes = self._ctypes
                self._winmm.waveOutReset(handle)
                if bgm.get("prepared"):
                    self._winmm.waveOutUnprepareHeader(
                        handle, ctypes.byref(bgm["header"]),
                        ctypes.sizeof(bgm["header"])
                    )
                self._winmm.waveOutClose(handle)
            except Exception:
                pass
        self._bgm_paths = []

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class AudioPlayer:
    """音频播放器；构造失败不会抛异常，调用方无需 try/except。"""

    def __init__(self, enabled=True, sfx_volume=0.7, bgm_volume=0.35, root=None,
                 sound_set=None):
        self.enabled = bool(enabled)
        self.sfx_volume = sfx_volume
        self.bgm_volume = bgm_volume
        self.root = Path(root) if root is not None else audio_root()
        self.backend = "none"
        self.error = ""
        self.muted = False
        self._pygame = None
        self._sounds = {}
        self._pygame_channels = {}
        self._available_keys = set()
        self._paths = {}
        self._slot_keys = {}
        self.sound_set = {}
        self.profile = {}
        self.last_played = None
        self._bgm_paths = []
        self._bgm_sequence = BgmSequence(0)
        self._bgm_current = None
        self._bgm_active = False
        self._bgm_wanted = False
        # 仅 BGM 的暂停（结束界面用）：与全局 set_paused 区分，音效照样能响。
        self._bgm_paused = False
        self._bgm_sound = None
        self._bgm_channel = None
        self._bgm_track_duration = 0.0
        self._bgm_track_started = 0.0
        self._bgm_next_check = 0.0
        self._bgm_winsound_stopped = False
        self._preview_channel = None
        self._preview_sound = None
        self._preview_winsound = None
        self._winsound = None
        self._waveout = None
        self._paused = False

        if self.enabled:
            resolved, meta = self._resolve_sound_set(sound_set)
            self.profile = meta
            self._install_sound_set(resolved)
            self._init()
        else:
            self.profile = {"mode": "disabled"}
            self._install_sound_set(sound_set or {})

    # ------------------------------------------------------------ 自定义音效
    def _resolve_sound_set(self, sound_set):
        if sound_set is not None:
            return sound_set, {"mode": "explicit"}
        if audio_profile is not None:
            try:
                return audio_profile.resolve_audio()
            except Exception as exc:
                self.error = "读取自定义音效失败：%s" % (exc,)
        return _builtin_sound_set(self.root), {
            "mode": "builtin", "errors": [self.error] if self.error else []}

    def _install_sound_set(self, sound_set):
        """把 {slot: [Path]} 展开成稳定的 key，后端只认 key。"""
        self.sound_set = {}
        self._paths = {}
        self._slot_keys = {}
        for slot, paths in (sound_set or {}).items():
            files = [Path(path) for path in paths]
            self.sound_set[slot] = files
            keys = []
            for index, path in enumerate(files):
                key = "%s#%d" % (slot, index)
                self._paths[key] = path
                keys.append(key)
            self._slot_keys[slot] = keys

        # BGM 是一个播放列表：只保留真实存在的文件，顺序与选择清单一致。
        self._bgm_paths = [
            self._paths[key] for key in self._slot_keys.get("bgm", [])
            if self._paths[key].is_file()
        ]
        self._bgm_sequence = BgmSequence(len(self._bgm_paths))
        self._bgm_current = None
        self._bgm_active = False
        self._bgm_wanted = False
        self._bgm_paused = False
        self._bgm_sound = None
        self._bgm_track_duration = 0.0
        self._bgm_track_started = 0.0
        self._bgm_next_check = 0.0
        self._bgm_winsound_stopped = False

    # ------------------------------------------------------------ 初始化
    def _init(self):
        forced = os.environ.get("DAFENG_AUDIO_BACKEND", "").strip().lower()
        if forced == "pygame" and self._init_pygame():
            return
        if forced == "waveout" and self._init_waveout():
            return
        if forced == "winsound" and self._init_winsound():
            return
        if self._init_pygame():
            return
        if self._init_waveout():
            return
        if self._init_winsound():
            return
        self.backend = "none"

    def _init_pygame(self):
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        try:
            import pygame
        except Exception as exc:
            self.error = "pygame 未安装：%s" % (exc,)
            return False
        try:
            pygame.mixer.pre_init(44100, -16, 2, 512)
            pygame.mixer.init()
            pygame.mixer.set_num_channels(8)
            # 预留 0 号声道给 BGM，避免音效满声道时 find_channel(True) 把 BGM 抢掉。
            pygame.mixer.set_reserved(1)
        except Exception as exc:
            self.error = "音频设备不可用：%s" % (exc,)
            return False

        for key, path in self._paths.items():
            if key.startswith("bgm#") or not path.is_file():
                continue
            try:
                sound = pygame.mixer.Sound(str(path))
                sound.set_volume(self.sfx_volume)
                self._sounds[key] = sound
            except Exception:
                continue
        self._available_keys = set(self._sounds)
        self._bgm_channel = pygame.mixer.Channel(BGM_CHANNEL)
        self._pygame = pygame
        self.backend = "pygame"
        return True

    def _init_waveout(self):
        if sys.platform != "win32":
            return False
        paths = {
            key: path
            for key, path in self._paths.items()
            if not key.startswith("bgm#") and path.is_file()
        }
        if not paths:
            return False
        try:
            self._waveout = WaveOutPlayer(
                paths, self.sfx_volume, self._bgm_paths, self.bgm_volume)
        except Exception as exc:
            self._waveout = None
            self.error = "waveOut 不可用：%s" % (exc,)
            return False
        self._available_keys = set(self._waveout.names())
        self.backend = "waveout"
        self.error = ""
        return True

    def _init_winsound(self):
        try:
            import winsound
        except Exception:
            return False
        available = [key for key, path in self._paths.items()
                     if not key.startswith("bgm#") and path.is_file()]
        if not available:
            return False
        self._winsound = winsound
        self._available = set(available)
        self._available_keys = set(available)
        self.backend = "winsound"
        return True

    # ------------------------------------------------------------ 播放
    def play(self, event_name):
        """按模型语义事件名播放一次音效；未知事件静默忽略。

        "hit" 这类随机槽会在可用条目里抽一条；槽位与条目由
        audio_profile.resolve_audio() 决定（内置或自定义 active 清单）。
        """
        if not self.enabled or self.muted or self._paused:
            return False
        keys = [key for key in self._slot_keys.get(event_name, [])
                if key in self._available_keys]
        if not keys:
            return False
        key = random.choice(keys)
        self.last_played = key

        if self.backend == "pygame":
            sound = self._sounds.get(key)
            if sound is None:
                return False
            try:
                channel = self._pygame.mixer.find_channel(True)
                if channel is not None:
                    channel.play(sound)
                    self._pygame_channels[key] = channel
                else:
                    sound.play()
                return True
            except Exception:
                return False
        if self.backend == "waveout":
            return self._waveout.play(key)
        if self.backend == "winsound":
            path = self._paths.get(key)
            if path is None:
                return False
            try:
                self._winsound.PlaySound(
                    str(path),
                    self._winsound.SND_FILENAME
                    | self._winsound.SND_ASYNC
                    | self._winsound.SND_NODEFAULT,
                )
                return True
            except Exception:
                return False
        return False

    def stop(self, event_name):
        """停止某个槽位正在播放的音效；不存在的槽位静默忽略。"""
        keys = self._slot_keys.get(event_name, [])
        if self.backend == "pygame":
            stopped = False
            for key in keys:
                channel = self._pygame_channels.pop(key, None)
                if channel is not None:
                    try:
                        channel.stop()
                        stopped = True
                    except Exception:
                        pass
            return stopped
        if self.backend == "waveout" and self._waveout is not None:
            stopped = False
            for key in keys:
                stopped = self._waveout.stop(key) or stopped
            return stopped
        # winsound 只能全局停止/替换，无法单独停某个音效，保持现状
        return False

    # ------------------------------------------------------------ BGM
    def start_bgm(self):
        """开始随机播放当前 BGM 池；已在播放时幂等。"""
        if not self.enabled or not self._bgm_paths:
            self._bgm_wanted = False
            return False
        # 记住“本局想要 BGM”。即使用户在静音/暂停中进入游戏，取消静音/恢复
        # 后也能自动开始，而不是整局都没有背景音乐。
        self._bgm_wanted = True
        self._bgm_paused = False
        if self.muted or self._paused:
            return False
        if self._bgm_active:
            return True
        self._bgm_active = True
        self._bgm_sequence.reset()
        self._bgm_current = None
        self._bgm_next_check = 0.0
        if not self._start_next_bgm():
            self._bgm_active = False
            return False
        return True

    def _start_next_bgm(self):
        """按随机池顺序开始下一首；全部失败时返回 False。"""
        if not self._bgm_paths:
            return False
        loop_single = len(self._bgm_paths) == 1
        for _attempt in range(len(self._bgm_paths)):
            index = self._bgm_sequence.next_index()
            if index is None:
                return False
            path = self._bgm_paths[index]
            if not path.is_file():
                continue
            if self._play_bgm_track(path, loop=loop_single):
                self._bgm_current = index
                return True
        return False

    def _play_bgm_track(self, path, loop):
        if self.backend == "pygame":
            if self._pygame is None:
                return False
            try:
                sound = self._pygame.mixer.Sound(str(path))
                sound.set_volume(self.bgm_volume)
                if self._bgm_channel is None:
                    self._bgm_channel = self._pygame.mixer.Channel(BGM_CHANNEL)
                self._bgm_channel.stop()
                # 清掉可能残留的单独暂停状态，否则新曲目会“静音播放”。
                self._bgm_channel.unpause()
                self._bgm_channel.play(sound, loops=-1 if loop else 0)
                self._bgm_sound = sound
                self._bgm_track_duration = float(sound.get_length())
                return True
            except Exception:
                return False
        if self.backend == "waveout":
            if self._waveout is None:
                return False
            return self._waveout.start_bgm(path, loop=loop)
        if self.backend == "winsound":
            if self._winsound is None:
                return False
            try:
                self._winsound.PlaySound(
                    str(path),
                    self._winsound.SND_FILENAME
                    | self._winsound.SND_ASYNC
                    | self._winsound.SND_NODEFAULT,
                )
                self._bgm_track_duration = wav_duration(path)
                self._bgm_track_started = time.perf_counter()
                return True
            except Exception:
                return False
        return False

    def update(self):
        """主循环每帧调用：当前曲目播完时切到随机池的下一首。

        没有 BGM、暂停、静音或后端不可用时直接返回。内部把检查节流到
        100 ms，避免每帧都去问驱动。
        """
        if not self.enabled or not self._bgm_active or not self._bgm_paths:
            return False
        if self._paused or self.muted or self._bgm_paused:
            return False
        now = time.perf_counter()
        if now < self._bgm_next_check:
            return False
        self._bgm_next_check = now + BGM_CHECK_INTERVAL

        ended = False
        if self.backend == "pygame":
            if self._bgm_channel is None:
                return False
            try:
                ended = not self._bgm_channel.get_busy()
            except Exception:
                ended = True
        elif self.backend == "waveout":
            ended = self._waveout is not None and self._waveout.bgm_finished()
        elif self.backend == "winsound":
            ended = (
                self._bgm_track_duration > 0.0
                and now - self._bgm_track_started >= self._bgm_track_duration
            )
        if not ended:
            return False
        if not self._start_next_bgm():
            self._bgm_active = False
            return False
        return True

    def pause_bgm(self):
        """只暂停 BGM 并保留播放位置（结束界面用），音效不受影响。

        和 set_paused 的区别：set_paused 会连音效一起暂停，这里只冻结 BGM，
        所以结束画面出现时 game_over 音效还能正常播完。
        """
        if not self.enabled or not self._bgm_active:
            return False
        if self._bgm_paused:
            return True
        self._bgm_paused = True
        if self.backend == "pygame":
            if self._bgm_channel is not None:
                try:
                    self._bgm_channel.pause()
                except Exception:
                    pass
        elif self.backend == "waveout":
            if self._waveout is not None:
                self._waveout.set_bgm_paused(True)
        elif self.backend == "winsound" and self._winsound is not None:
            # winsound 没有暂停/续播接口，只能停掉，恢复时从当前曲目重放。
            try:
                self._winsound.PlaySound(None, self._winsound.SND_PURGE)
            except Exception:
                pass
        return True

    def resume_bgm(self):
        """恢复被 pause_bgm 暂停的 BGM，从原位置继续；未暂停时幂等。"""
        if not self.enabled or not self._bgm_active:
            return False
        if not self._bgm_paused:
            return True
        self._bgm_paused = False
        silent = self.muted or self._paused
        if self.backend == "pygame":
            if self._bgm_channel is not None:
                # 静音/全局暂停时 mixer 整体是暂停的，这里只解除声道上的单独
                # 暂停标记，取消静音/恢复时直接出声，不会提前漏音。
                try:
                    self._bgm_channel.unpause()
                except Exception:
                    pass
        elif self.backend == "waveout":
            if self._waveout is not None and not silent:
                # 静音/暂停中保持驱动暂停，等取消静音/恢复时由
                # toggle_mute / set_paused 从原位置续播。
                self._waveout.set_bgm_paused(False)
        elif self.backend == "winsound":
            index = self._bgm_current
            if silent:
                # winsound 没有暂停接口，暂停靠 purge；这里保持静音状态，
                # 之后取消静音时由 toggle_mute 重新起播。
                self._bgm_winsound_stopped = True
                return True
            if index is not None and 0 <= index < len(self._bgm_paths):
                self._bgm_current = None
                if self._play_bgm_track(
                    self._bgm_paths[index], loop=len(self._bgm_paths) == 1
                ):
                    self._bgm_current = index
                    return True
            return False
        return True

    def stop_bgm(self):
        self._bgm_wanted = False
        self._bgm_active = False
        self._bgm_paused = False
        self._bgm_current = None
        self._bgm_sound = None
        if self.backend == "waveout":
            if self._waveout is not None:
                self._waveout.stop_bgm()
        elif self.backend == "pygame":
            if self._bgm_channel is not None:
                try:
                    self._bgm_channel.stop()
                    # 同步清掉单独暂停状态，避免下一局起播时还是暂停的。
                    self._bgm_channel.unpause()
                except Exception:
                    pass
        elif self.backend == "winsound" and self._winsound is not None:
            try:
                self._winsound.PlaySound(None, self._winsound.SND_PURGE)
            except Exception:
                pass
        self._bgm_winsound_stopped = False

    # ------------------------------------------------------------ 状态
    def _resume_wanted_bgm(self):
        if not self.enabled or not self._bgm_wanted or self._bgm_active:
            return
        if self._paused or self.muted or self._bgm_paused:
            return
        self.start_bgm()

    def set_paused(self, paused):
        """暂停/恢复全部声音；暂停时音效也不再触发。"""
        self._paused = bool(paused)
        if self.backend == "waveout":
            if self._waveout is not None:
                self._waveout.set_bgm_paused(
                    self._paused or self.muted or self._bgm_paused)
        elif self.backend == "winsound":
            if self._paused and self._bgm_active and self._winsound is not None:
                try:
                    self._winsound.PlaySound(None, self._winsound.SND_PURGE)
                    self._bgm_winsound_stopped = True
                except Exception:
                    pass
            elif not self._paused and self._bgm_winsound_stopped and self._bgm_wanted:
                self._bgm_winsound_stopped = False
                self._bgm_active = False
                self.start_bgm()
        elif self.backend == "pygame":
            try:
                if paused:
                    self._pygame.mixer.pause()
                else:
                    self._pygame.mixer.unpause()
                    # mixer.unpause 会恢复所有声道，单独暂停的 BGM 要再压回去。
                    if self._bgm_paused and self._bgm_channel is not None:
                        self._bgm_channel.pause()
            except Exception:
                pass
        if not self._paused:
            self._resume_wanted_bgm()

    def toggle_mute(self):
        self.muted = not self.muted
        if self.backend == "pygame":
            try:
                if self.muted:
                    self._pygame.mixer.pause()
                elif not self._paused:
                    self._pygame.mixer.unpause()
                    if self._bgm_paused and self._bgm_channel is not None:
                        self._bgm_channel.pause()
            except Exception:
                pass
        elif self.backend == "waveout" and self._waveout is not None:
            self._waveout.set_bgm_paused(
                self.muted or self._paused or self._bgm_paused)
        elif self.backend == "winsound":
            if self.muted and self._bgm_active and self._winsound is not None:
                try:
                    self._winsound.PlaySound(None, self._winsound.SND_PURGE)
                    self._bgm_winsound_stopped = True
                except Exception:
                    pass
            elif not self.muted and self._bgm_winsound_stopped and self._bgm_wanted:
                self._bgm_winsound_stopped = False
                self._bgm_active = False
                self.start_bgm()
        if not self.muted:
            self._resume_wanted_bgm()
        return self.muted

    def preview(self, path):
        """试听任意 WAV（自定义音效页用）；不占用游戏音效流。"""
        if not self.enabled or self.muted:
            return False
        path = Path(path)
        if not path.is_file():
            return False
        if self.backend == "pygame" and self._pygame is not None:
            try:
                sound = self._pygame.mixer.Sound(str(path))
                sound.set_volume(self.sfx_volume)
                if self._preview_channel is None:
                    self._preview_channel = self._pygame.mixer.Channel(PREVIEW_CHANNEL)
                self._preview_channel.stop()
                self._preview_channel.play(sound)
                self._preview_sound = sound
                return True
            except Exception:
                pass
        if sys.platform == "win32":
            try:
                import winsound

                winsound.PlaySound(
                    str(path),
                    winsound.SND_FILENAME
                    | winsound.SND_ASYNC
                    | winsound.SND_NODEFAULT,
                )
                self._preview_winsound = winsound
                return True
            except Exception:
                pass
        return False

    def stop_preview(self):
        if self._preview_channel is not None:
            try:
                self._preview_channel.stop()
            except Exception:
                pass
        self._preview_sound = None
        if self._preview_winsound is not None:
            try:
                self._preview_winsound.PlaySound(
                    None, self._preview_winsound.SND_PURGE)
            except Exception:
                pass

    def status(self):
        count = len(self._available_keys)
        mode = self.profile.get("mode", "builtin") if self.profile else "builtin"
        return "音频 %s（%d 个音效 / %d 首 BGM，%s）" % (
            self.backend,
            count,
            len(self._bgm_paths),
            "自定义" if mode == "custom" else "内置",
        )

    def reload(self, sound_set=None):
        """重新读取自定义配置并重建后端；应用新选择后调用。"""
        self.close()
        resolved, meta = self._resolve_sound_set(sound_set)
        self.profile = meta
        self._install_sound_set(resolved)
        if self.enabled:
            self._init()
        return self.backend != "none"

    def close(self):
        self.stop_preview()
        self.stop_bgm()
        if self.backend == "pygame" and self._pygame is not None:
            try:
                self._pygame.mixer.stop()
                self._pygame.mixer.quit()
            except Exception:
                pass
        elif self.backend == "waveout" and self._waveout is not None:
            try:
                self._waveout.close()
            except Exception:
                pass
            self._waveout = None
        elif self.backend == "winsound" and self._winsound is not None:
            try:
                self._winsound.PlaySound(None, self._winsound.SND_PURGE)
            except Exception:
                pass
        self.backend = "none"
        self._sounds.clear()
        self._available_keys = set()
        self._bgm_sound = None
        self._bgm_current = None
        self._bgm_active = False
