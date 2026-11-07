# -*- coding: utf-8 -*-
"""试听“语调统一”后的四段受伤音，并可选生成整组连播文件。

先用 tools/audio/transfer_intonation.py 生成模板组，再运行本脚本：
    python tools/audio/audition_intonation.py --list
    python tools/audio/audition_intonation.py --set template_ea1
    python tools/audio/audition_intonation.py --set template_ea1 --ab
    python tools/audio/audition_intonation.py --set template_ea1 --concat

模板组位于 art_preview/intonation/template_<参考名>/，每组 4 个 WAV：
aiya1 / aiya2 / ea1 / ea2，均已按同一语调轮廓重合成。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "art_preview" / "intonation"
SOURCES = ROOT / "tools" / "audio" / "source" / "takes"
ORIGINALS = {
    "aiya1": SOURCES / "aiya1.mp3",
    "aiya2": SOURCES / "aiya2.mp3",
    "ea1": SOURCES / "ea1.m4a",
    "ea2": SOURCES / "ea2.m4a",
}


def play(path: Path) -> bool:
    try:
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
        return True
    except Exception:
        try:
            import pygame  # 非 Windows 时的后备

            pygame.mixer.init()
            pygame.mixer.music.load(str(path))
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                time.sleep(0.05)
            return True
        except Exception:
            return False


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def sets() -> list[Path]:
    if not BASE.is_dir():
        return []
    return sorted([p for p in BASE.iterdir() if p.is_dir() and p.name.startswith("template_")])


def concat_wavs(paths: list[Path], out: Path, gap: float = 0.45) -> None:
    """把多个 44.1k 单声道 16-bit WAV 用静音拼接，方便一次听完一组。"""
    frames = []
    for path in paths:
        with wave.open(str(path), "rb") as handle:
            if handle.getframerate() != 44100 or handle.getnchannels() != 1:
                raise SystemExit("只支持 44.1k 单声道 WAV：%s" % path)
            frames.append(handle.readframes(handle.getnframes()))
        frames.append(b"\x00\x00" * int(gap * 44100))
    with wave.open(str(out), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        for chunk in frames:
            handle.writeframes(chunk)
    print("整组连播文件：%s" % out)


def main() -> int:
    parser = argparse.ArgumentParser(description="试听语调统一后的受伤音")
    parser.add_argument("--list", action="store_true", help="列出所有模板组")
    parser.add_argument("--set", default=None, help="模板组名（template_ea1 或 ea1）")
    parser.add_argument("--gap", type=float, default=0.6, help="每条之间的停顿（秒）")
    parser.add_argument("--ab", action="store_true", help="每条先放原声、再放统一后的版本")
    parser.add_argument("--concat", action="store_true", help="生成整组连播 WAV")
    args = parser.parse_args()

    groups = sets()
    if args.list or not args.set:
        if not groups:
            print("还没有模板组，先运行 transfer_intonation.py 生成：")
            print("  python tools/audio/transfer_intonation.py -r tools/audio/source/takes/ea1.m4a \\")
            print("      -t tools/audio/source/takes/*.mp3 tools/audio/source/takes/*.m4a \\")
            print("      --outdir art_preview/intonation/template_ea1 --strength 1.0 --trim --bare")
            return 1
        print("可试听的模板组：")
        for group in groups:
            files = sorted(p for p in group.glob("*.wav") if p.name != "_all.wav")
            print("  %-20s %d 条" % (group.name, len(files)))
        print("\n用法：python tools/audio/audition_intonation.py --set ea1 [--ab] [--concat]")
        return 0

    name = args.set
    if not name.startswith("template_"):
        name = "template_" + name
    group = BASE / name
    if not group.is_dir():
        print("找不到模板组：%s" % group)
        return 1

    files = sorted(p for p in group.glob("*.wav") if p.name != "_all.wav")
    if not files:
        print("模板组里没有 WAV：%s" % group)
        return 1

    print("模板组：%s（%d 条，间隔 %.1f s%s）"
          % (name, len(files), args.gap, "，A/B 对比" if args.ab else ""))
    for path in files:
        stem = path.stem
        print("--- %-7s %5.2f s" % (stem, wav_duration(path)))
        if args.ab and stem in ORIGINALS:
            print("    原声：%s" % ORIGINALS[stem].name)
            if not play(ORIGINALS[stem]):
                print("    本机无法播放，请手动打开：%s" % ORIGINALS[stem])
            time.sleep(0.25)
            print("    统一：%s" % path.name)
        if not play(path):
            print("    本机无法播放（缺 winsound/pygame），请手动打开：%s" % path)
        time.sleep(args.gap)

    if args.concat:
        concat_wavs(files, group / "_all.wav")
    return 0


if __name__ == "__main__":
    sys.exit(main())
