# -*- coding: utf-8 -*-
"""诊断“啪”声和延迟来自哪个播放后端。

用法（在项目根目录）：
    python tools/audio/backend_ab_test.py
    python tools/audio/backend_ab_test.py --wind   # 额外测试“风变呼啸→受伤”叠加

每个小节播放 3 次，注意听：
    1) waveout（游戏内当前后端）是否有“啪”和延迟
    2) winsound（试听脚本用的后端）是否干净
    3) wind→hit：风变呼啸正在响时受伤，是否有“啪”
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from audio import AudioPlayer  # noqa: E402


def play_waveout(count=3, label="waveout"):
    print("\n=== %s ===" % label)
    player = AudioPlayer()
    print("backend:", player.backend)
    if player.backend != "waveout":
        print("跳过：当前后端不是 waveout")
        player.close()
        return
    for index in range(count):
        print("  hit %d" % (index + 1))
        player.play("hit")
        time.sleep(1.0)
    player.close()


def play_winsound(count=3):
    print("\n=== winsound（试听路径） ===")
    try:
        import winsound
    except Exception:
        print("跳过：没有 winsound")
        return
    sfx = Path(__file__).resolve().parents[2] / "assets" / "audio" / "sfx"
    paths = [sfx / "hit_1.wav", sfx / "hit_2.wav", sfx / "hit_3.wav"]
    for index in range(count):
        path = paths[index % len(paths)]
        print("  hit %d (%s)" % (index + 1, path.name))
        winsound.PlaySound(
            str(path), winsound.SND_FILENAME | winsound.SND_NODEFAULT
        )
        time.sleep(1.0)


def play_wind_then_hit():
    print("\n=== wind_change → hit（waveout） ===")
    player = AudioPlayer()
    print("backend:", player.backend)
    player.play("wind_change")
    time.sleep(0.12)
    print("  hit")
    player.play("hit")
    time.sleep(1.0)
    player.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wind", action="store_true", help="额外测试风变→受伤叠加")
    args = parser.parse_args()
    play_waveout()
    play_winsound()
    if args.wind:
        play_wind_then_hit()
    print("\n完成。告诉我哪一个小节有“啪”/延迟即可。")


if __name__ == "__main__":
    main()
