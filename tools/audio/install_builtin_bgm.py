#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把下载好的音乐安装成《大风吹》的内置 BGM。

用法（项目根目录）：
    # 先装 PyAV，才能直接读 MP3/M4A；只有 WAV 时不需要
    python -m pip install av

    python tools/audio/install_builtin_bgm.py 歌曲1.mp3 歌曲2.flac
    python tools/audio/install_builtin_bgm.py 歌曲.mp3 --title "风起时" --artist "某某"
    python tools/audio/install_builtin_bgm.py --list

做三件事：
    1. 解码成 44.1 kHz / 单声道 / 16-bit WAV（不裁前后、不加淡入淡出）；
    2. 峰值归一到 0.35（和原来的环境音一致，再乘游戏内 BGM 音量 0.35）；
    3. 写到 assets/audio/bgm/<slug>.wav，并更新 bgm.json 的标题/作者元数据。

之后启动游戏：内置音乐会自动扫描进“自定义 → 音效 → 背景音乐”，
和玩家导入的音乐一起随机播放。只导入、不替玩家勾选；旧版单选配置会
在读取时自动迁移成“全部内置 + 已选导入”的随机池。
"""

from __future__ import annotations

import argparse
import array
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import audio_import  # noqa: E402
import audio_profile  # noqa: E402

BGM_DIR = ROOT / "assets" / "audio" / "bgm"
META_PATH = BGM_DIR / "bgm.json"
MAX_SECONDS = audio_import.MAX_BGM_SECONDS
MIN_SECONDS = audio_import.MIN_SECONDS


def _read_meta() -> dict:
    try:
        data = json.loads(META_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("tracks"), list):
        return {"version": 1, "tracks": []}
    tracks = [entry for entry in data["tracks"] if isinstance(entry, dict)]
    return {"version": 1, "tracks": tracks}


def _write_meta(meta: dict) -> None:
    BGM_DIR.mkdir(parents=True, exist_ok=True)
    temporary = META_PATH.with_name(META_PATH.name + ".tmp")
    temporary.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, META_PATH)


def _slugify(value: str, digest: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = text.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^0-9A-Za-z_-]+", "_", text).strip("._-").lower()
    if not slug:
        slug = "bgm_%s" % digest[:8]
    return slug[:48]


def _little_payload(samples: array.array) -> bytes:
    if sys.byteorder == "big":
        copy = array.array("h", samples)
        copy.byteswap()
        return copy.tobytes()
    return samples.tobytes()


def _write_wav(path: Path, samples: array.array) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with wave.open(str(temporary), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(_little_payload(samples))
    os.replace(temporary, path)


def _find_by_hash(meta: dict, digest: str) -> dict | None:
    for entry in meta["tracks"]:
        if str(entry.get("sha256", "")) == digest:
            return entry
    return None


def _unique_slug(base: str, digest: str, meta: dict, force: bool) -> str:
    existing = {str(entry.get("file", "")) for entry in meta["tracks"]}
    candidate = base
    if force:
        return candidate
    if "%s.wav" % candidate not in existing and not (BGM_DIR / ("%s.wav" % candidate)).is_file():
        return candidate
    while True:
        candidate = "%s_%s" % (base[:38], digest[:8])
        if ("%s.wav" % candidate not in existing
                and not (BGM_DIR / ("%s.wav" % candidate)).is_file()):
            return candidate


def _print_entries() -> None:
    meta = _read_meta()
    tracks = meta["tracks"]
    if not tracks:
        print("内置背景音乐：空（%s）" % BGM_DIR)
        return
    print("内置背景音乐：%d 首（%s）" % (len(tracks), META_PATH))
    print("%-32s %-26s %8s  %s" % ("文件", "标题", "时长", "作者"))
    for entry in tracks:
        name = str(entry.get("file", ""))
        path = BGM_DIR / name
        duration = 0.0
        try:
            with wave.open(str(path), "rb") as handle:
                duration = handle.getnframes() / float(handle.getframerate())
        except Exception:
            pass
        print("%-32s %-26s %7.2fs  %s" % (
            name, str(entry.get("title", path.stem))[:26],
            duration, entry.get("artist", "")))


def install(source: Path, args, meta: dict) -> bool:
    source = source.expanduser()
    if not source.is_file():
        print("[失败] 找不到文件：%s" % source, file=sys.stderr)
        return False
    if source.suffix.lower() not in audio_import.SUPPORTED_EXTENSIONS:
        print("[失败] 不支持的格式：%s（可用 %s）"
              % (source.suffix, " ".join(audio_import.SUPPORTED_EXTENSIONS)),
              file=sys.stderr)
        return False
    try:
        samples = audio_import.decode_audio(source)
    except audio_import.AudioImportError as error:
        print("[失败] %s：%s" % (source.name, error), file=sys.stderr)
        return False
    duration = len(samples) / 44100.0
    if duration < MIN_SECONDS:
        print("[失败] %s 太短（%.2f s）" % (source.name, duration), file=sys.stderr)
        return False
    if duration > MAX_SECONDS:
        print("[失败] %s 太长（%.1f s，上限 %.0f s）"
              % (source.name, duration, MAX_SECONDS), file=sys.stderr)
        return False
    if audio_import._peak(samples) < audio_import.SILENCE_PEAK:
        print("[失败] %s 几乎是静音" % source.name, file=sys.stderr)
        return False
    if not args.no_normalize:
        samples = audio_import._normalize(samples, args.peak)
    digest = hashlib.sha256(_little_payload(samples)).hexdigest()

    duplicate = _find_by_hash(meta, digest)
    if duplicate is not None and not args.force:
        print("[已存在] %s -> %s（标题：%s）"
              % (source.name, duplicate.get("file"),
                 duplicate.get("title") or Path(str(duplicate.get("file"))).stem))
        return False

    if duplicate is not None and args.force:
        # 内容相同且要求覆盖：直接更新原来那一条，不生成重复文件。
        relative = str(duplicate.get("file", ""))
        if not relative:
            relative = "%s.wav" % _slugify(source.stem, digest)
    else:
        slug_source = (args.slug if (args.slug and len(args.files) == 1)
                       else source.stem)
        slug = _slugify(slug_source, digest)
        if args.slug and len(args.files) > 1:
            print("提示：--slug 只能用于单个文件，已按文件名处理", file=sys.stderr)
        slug = _unique_slug(slug, digest, meta, args.force)
        relative = "%s.wav" % slug
    target = BGM_DIR / relative
    if args.dry_run:
        print("[预览] %s -> %s（%.1f s）" % (source.name, target, duration))
        return True
    _write_wav(target, samples)

    title = args.title if (args.title and len(args.files) == 1) else source.stem
    entry = next((item for item in meta["tracks"]
                  if str(item.get("file", "")) == relative), None)
    if entry is None:
        entry = {"file": relative}
        meta["tracks"].append(entry)
    entry.update({
        "title": title,
        "artist": args.artist,
        "source": args.source or source.name,
        "license": args.license,
        "notes": args.notes,
        "duration": round(len(samples) / 44100.0, 3),
        "sample_rate": 44100,
        "channels": 1,
        "sha256": digest,
        "source_format": source.suffix.lower().lstrip("."),
        "imported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    _write_meta(meta)
    print("[已安装] %-28s -> %s（%.1f s）" % (source.name, target, duration))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="安装内置背景音乐（WAV 44.1k 单声道）")
    parser.add_argument("files", nargs="*", help="音乐文件（WAV/MP3/M4A/OGG/FLAC）")
    parser.add_argument("--title", default=None, help="标题（只用于单个文件）")
    parser.add_argument("--slug", default=None, help="文件名 slug（只用于单个文件）")
    parser.add_argument("--artist", default="", help="作者/演唱者，写进 bgm.json")
    parser.add_argument("--source", default="", help="来源说明，例如下载地址或平台名")
    parser.add_argument("--license", default="", help="授权说明，例如 CC0 / 已购买")
    parser.add_argument("--notes", default="", help="备注")
    parser.add_argument("--peak", type=float, default=0.35,
                        help="归一化峰值（默认 0.35）")
    parser.add_argument("--no-normalize", action="store_true",
                        help="不做峰值归一化（保持原文件响度）")
    parser.add_argument("--force", action="store_true",
                        help="允许覆盖同名文件/重复内容")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    parser.add_argument("--list", action="store_true", help="列出已安装曲目")
    args = parser.parse_args()

    if not (0.0 < args.peak <= 1.0):
        print("--peak 必须在 (0, 1] 之间", file=sys.stderr)
        return 2
    if args.title and len(args.files) > 1:
        print("--title 只能用于单个文件", file=sys.stderr)
        return 2

    if args.list or not args.files:
        _print_entries()
        if not args.files:
            return 0

    meta = _read_meta()
    installed = 0
    for name in args.files:
        try:
            if install(Path(name), args, meta):
                installed += 1
        except audio_import.AudioImportError as error:
            print("[失败] %s：%s" % (name, error), file=sys.stderr)
        except OSError as error:
            print("[失败] %s：%s" % (name, error), file=sys.stderr)
    if installed and not args.dry_run:
        print("已更新 %s；启动游戏后到 自定义 → 音效 → 背景音乐 确认勾选。"
              % META_PATH)
    return 0 if installed else 1


if __name__ == "__main__":
    sys.exit(main())
