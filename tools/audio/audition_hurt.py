# -*- coding: utf-8 -*-
"""试听受伤音效候选，并可选安装为游戏音效。

用法
    python tools/audio/audition_hurt.py                 # 顺序试听全部候选
    python tools/audio/audition_hurt.py --install b     # 把 b_* 安装到 assets/audio/sfx/hit.wav

候选位于 art_preview/hurt_candidates/，由 tools/audio/make_hurt_voice.py 生成。
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / "art_preview" / "hurt_candidates"
TARGET = ROOT / "assets" / "audio" / "sfx" / "hit.wav"


def play(path, blocking=True):
    try:
        import winsound

        flags = winsound.SND_FILENAME | winsound.SND_NODEFAULT
        if not blocking:
            flags |= winsound.SND_ASYNC
        winsound.PlaySound(str(path), flags)
        return True
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="试听/安装受伤哼唧候选")
    parser.add_argument("--install", default=None,
                        help="按文件名前缀安装（例如 b），安装到 assets/audio/sfx/hit.wav")
    parser.add_argument("--only", default=None,
                        help="只试听文件名包含该字符串的候选（例如 el222）")
    parser.add_argument("--gap", type=float, default=0.7, help="候选间隔秒数")
    args = parser.parse_args()

    files = sorted(CANDIDATES.glob("*.wav"))
    if args.only:
        files = [path for path in files if args.only in path.name]
    if not files:
        print("没有候选文件：%s" % CANDIDATES)
        return 1

    if args.install:
        matched = [path for path in files if path.name.startswith(args.install)]
        if not matched:
            print("找不到以 %r 开头的候选：%s" % (args.install, [p.name for p in files]))
            return 1
        chosen = matched[0]
        shutil.copyfile(chosen, TARGET)
        print("已安装 %s -> %s" % (chosen.name, TARGET))
        return 0

    print("试听候选（共 %d 个；按文件名前的字母安装：--install b）" % len(files))
    for index, path in enumerate(files, 1):
        import wave

        with wave.open(str(path), "rb") as handle:
            duration = handle.getnframes() / float(handle.getframerate())
        print("%d. %-20s %.3fs" % (index, path.name, duration))
        if not play(path):
            print("   本机无法播放（缺 winsound），请直接打开：%s" % path)
        time.sleep(args.gap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
