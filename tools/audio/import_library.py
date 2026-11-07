#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把音频导入玩家音效库，并可选择立刻写进 profile。

用法（项目根目录）：
    python tools/audio/import_library.py 音频.mp3 --slot hit
    python tools/audio/import_library.py 音频1.wav 音频2.wav --slot hit --enable add
    python tools/audio/import_library.py 音乐.mp3 --slot bgm --enable only
    python tools/audio/import_library.py --list

导入只做：解码 -> 44.1k 单声道 -> 裁静音 -> 归一化 -> 写 librariesfx/bgm。
不改音高、不加效果。压缩格式（MP3/M4A/OGG/FLAC）需要 PyAV。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import audio_import  # noqa: E402
import audio_profile  # noqa: E402


def print_entries(root: Path) -> int:
    index = audio_profile.load_library_index(root)
    entries = index["entries"]
    if not entries:
        print("音效库是空的：%s" % audio_profile.library_dir(root))
        return 0
    print("音效库：%s（%d 条）" % (audio_profile.library_dir(root), len(entries)))
    print("%-12s %-24s %-6s %7s  %s"
          % ("id", "显示名", "槽位", "时长", "文件"))
    for entry in entries:
        print("%-12s %-24s %-6s %6.2fs  %s"
              % (str(entry.get("id", ""))[:12],
                 str(entry.get("display_name", ""))[:24],
                 str(entry.get("slot_hint", ""))[:6],
                 float(entry.get("duration", 0.0)),
                 entry.get("file", "")))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="导入玩家音效到自定义库")
    parser.add_argument("files", nargs="*", help="要导入的音频文件（可多个）")
    parser.add_argument("--slot", default="hit", choices=audio_profile.SLOT_IDS,
                        help="目标槽位（默认 hit）")
    parser.add_argument("--name", default=None, help="显示名（只导入单个文件时使用）")
    parser.add_argument("--notes", default="", help="备注，写进 index.json")
    parser.add_argument("--no-trim", action="store_true", help="不裁首尾静音")
    parser.add_argument("--no-normalize", action="store_true", help="不做峰值归一化")
    parser.add_argument("--peak", type=float, default=None,
                        help="归一化峰值（默认受击 0.6 / BGM 0.35）")
    parser.add_argument("--enable", choices=["add", "only"], default=None,
                        help="导入后写进 profile：add=追加，only=只保留它")
    parser.add_argument("--root", default=None,
                        help="覆盖可写根（默认项目/custom 或 %%APPDATA%%）")
    parser.add_argument("--list", action="store_true", help="只列出库内容")
    args = parser.parse_args()

    if args.root:
        root = Path(args.root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
    else:
        root = audio_profile.resource_root()
    if root is None:
        print("没有可写的资源目录；请检查权限或设置 DAFENG_RESOURCE_DIR", file=sys.stderr)
        return 2

    if args.list or not args.files:
        print_entries(root)
        if not args.files:
            return 0

    if args.name and len(args.files) > 1:
        print("--name 只能用于单个文件；多个文件时用各自的文件名", file=sys.stderr)
        return 2

    imported = []
    for name in args.files:
        source = Path(name).expanduser()
        options = audio_import.ImportOptions(
            slot=args.slot,
            display_name=args.name,
            notes=args.notes,
            trim=False if args.no_trim else None,
            normalize=not args.no_normalize,
            peak=args.peak,
        )
        try:
            result = audio_import.import_audio(root, source, options)
        except audio_import.AudioImportError as error:
            print("[失败] %s：%s" % (source.name, error), file=sys.stderr)
            return 1
        entry = result.entry
        state = "新增" if result.created else "已存在"
        print("[%s] %-24s id=%s  %5.2fs  %s"
              % (state, entry["display_name"], entry["id"],
                 float(entry["duration"]), result.path))
        imported.append(entry)

    if args.enable:
        for entry in imported:
            try:
                audio_import.set_slot_entry(root, args.slot, entry["id"], args.enable)
            except audio_import.AudioImportError as error:
                print("写入 profile 失败：%s" % error, file=sys.stderr)
                return 1
        print("已写入 profile：%s=%s" % (
            args.slot, "仅这一条" if args.enable == "only" else "追加"))
        sound_set, meta = audio_profile.resolve_audio(root)
        print("active 模式：%s；槽位：%s"
              % (meta.get("mode"), {key: len(value) for key, value in sound_set.items()}))
    else:
        print("\n手动启用：把下面这一行加进 %s 的 \"%s\" 列表："
              % (audio_profile.profile_path(root), args.slot))
        for entry in imported:
            print("  \"lib:%s\"" % entry["id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
