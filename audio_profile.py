#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自定义音频资源：可写根目录、内置/导入库、运行时 active 清单。

设计（见 docs/自定义音效方案.md）：
    ① 内置音效   assets/audio/                 只读，随游戏发布
    ② 玩家导入   <可写根>/audio/library/       只放玩家导入，带 index.json
    ③ 最终运行   <可写根>/audio/active/        由 profile.json 生成，运行时只读这里

可写根：
    开发时        <项目根>/custom
    打包后        exe 同级/custom（可写时）；否则 %APPDATA%/Dafengchui/custom
    环境变量      DAFENG_RESOURCE_DIR 覆盖（测试用）

启动流程：
    resolve_audio()
      -> 没有可写根：直接用内置
      -> 首次运行：写默认 profile.json，生成 active
      -> 之后：读 profile.json + library/index.json，校验并生成 active
      -> 任何一步失败：回退内置，游戏照常跑
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

GAME_DIR = Path(__file__).resolve().parent
BUILTIN_AUDIO = GAME_DIR / "assets" / "audio"
PROFILE_NAME = "profile.json"
LIBRARY_INDEX = "library/index.json"
MANIFEST_NAME = "active/manifest.json"
MANIFEST_VERSION = 1
PROFILE_VERSION = 2
BGM_DIR_NAME = "bgm"
BGM_META_NAME = "bgm.json"
BGM_MAX_COUNT = 12      # 背景音乐随机池上限（一轮内不重复）
BGM_LEGACY_FILE = "bgm/wind_ambience.wav"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None


def _read_bgm_meta() -> dict[str, dict]:
    """读 assets/audio/bgm/bgm.json 的曲目元数据；缺失/损坏时返回空表。"""
    data = _read_json(BUILTIN_AUDIO / BGM_DIR_NAME / BGM_META_NAME)
    tracks = data.get("tracks") if isinstance(data, dict) else None
    result: dict[str, dict] = {}
    if isinstance(tracks, list):
        for entry in tracks:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("file", "")).replace("\\", "/").strip()
            if name:
                result[name] = entry
    return result


def builtin_bgm_entries() -> list[dict]:
    """扫描 assets/audio/bgm/*.wav，合并 bgm.json 的标题/作者信息。

    直接往 bgm/ 里放 44.1k 单声道 WAV 也能被识别；bgm.json 只负责显示名、
    作者、来源等元数据。顺序固定按文件名排序，保证重复启动结果一致。
    """
    folder = BUILTIN_AUDIO / BGM_DIR_NAME
    if not folder.is_dir():
        return []
    meta = _read_bgm_meta()
    entries: list[dict] = []
    for path in sorted(folder.glob("*.wav"), key=lambda item: item.name.lower()):
        info = meta.get(path.name, {})
        entries.append({
            "relative": "%s/%s" % (BGM_DIR_NAME, path.name),
            "title": str(info.get("title") or path.stem),
            "artist": str(info.get("artist") or ""),
            "source": str(info.get("source") or ""),
            "license": str(info.get("license") or ""),
        })
    return entries


BUILTIN_BGM_ENTRIES = builtin_bgm_entries()
BUILTIN_BGM_FILES = tuple(entry["relative"] for entry in BUILTIN_BGM_ENTRIES)
BUILTIN_BGM_LABELS = {entry["relative"]: entry["title"]
                      for entry in BUILTIN_BGM_ENTRIES}
BUILTIN_BGM_ARTISTS = {entry["relative"]: entry["artist"]
                       for entry in BUILTIN_BGM_ENTRIES}


@dataclass(frozen=True)
class Slot:
    id: str
    label: str
    kind: str            # "random" | "single" | "bgm"
    max_count: int
    builtin: tuple[str, ...]
    default_count: int = 1


SLOTS = (
    Slot("hit", "受击", "random", 8,
         ("sfx/hit_1.wav", "sfx/hit_2.wav", "sfx/hit_3.wav",
          "sfx/hit_4.wav", "sfx/hit_5.wav", "sfx/hit_6.wav",
          "sfx/hit_7.wav", "sfx/hit_8.wav"), default_count=3),
    Slot("wind_change", "风向改变", "single", 1, ("sfx/wind_change.wav",)),
    Slot("game_over", "游戏结束", "single", 1, ("sfx/game_over.wav",)),
    Slot("pickup", "拾取道具", "single", 1, ("sfx/pickup.wav",)),
    Slot("bgm", "背景音乐", "bgm", BGM_MAX_COUNT,
         BUILTIN_BGM_FILES or (BGM_LEGACY_FILE,),
         default_count=BGM_MAX_COUNT),
)
SLOT_BY_ID = {slot.id: slot for slot in SLOTS}
SLOT_IDS = tuple(slot.id for slot in SLOTS)

# 内置音效的显示名（找不到时退回文件名）
BUILTIN_LABELS = {
    "sfx/hit_1.wav": "唉",
    "sfx/hit_2.wav": "呃啊1",
    "sfx/hit_3.wav": "哎呀1",
    "sfx/hit_4.wav": "呃啊2",
    "sfx/hit_5.wav": "啊",
    "sfx/hit_6.wav": "呃啊3",
    "sfx/hit_7.wav": "哎呀2",
    "sfx/hit_8.wav": "哎呀3",
}


def builtin_label(relative: str) -> str:
    if relative in BUILTIN_BGM_LABELS:
        return BUILTIN_BGM_LABELS[relative]
    return BUILTIN_LABELS.get(relative, Path(relative).stem)


def builtin_artist(relative: str) -> str:
    return BUILTIN_BGM_ARTISTS.get(relative, "")


# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #
def game_dir() -> Path:
    """程序所在目录；打包后是 exe 目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return GAME_DIR


def _writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    probe = path / ".dafeng_write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def resource_root(create: bool = True) -> Path | None:
    """返回玩家可写根（.../custom），都不可写时返回 None。"""
    override = os.environ.get("DAFENG_RESOURCE_DIR")
    if override:
        path = Path(override).expanduser()
        if create and not _writable_dir(path):
            return None
        return path

    base = game_dir()
    candidates = [base / "custom"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "Dafengchui" / "custom")

    for candidate in candidates:
        if create:
            if _writable_dir(candidate):
                return candidate
        elif candidate.is_dir() and os.access(candidate, os.W_OK):
            return candidate
    return None


def profile_path(root: Path) -> Path:
    return root / PROFILE_NAME


def library_dir(root: Path) -> Path:
    return root / "audio" / "library"


def library_index_path(root: Path) -> Path:
    return library_dir(root) / "index.json"


def active_dir(root: Path) -> Path:
    return root / "audio" / "active"


def builtin_path(relative: str) -> Path:
    return BUILTIN_AUDIO / relative


def builtin_ref(relative: str) -> str:
    return "builtin:" + relative.replace("\\", "/")


def builtin_files() -> list[Path]:
    return [
        builtin_path(relative)
        for slot in SLOTS
        for relative in slot.builtin
        if builtin_path(relative).is_file()
    ]


def builtin_sound_set() -> dict[str, list[Path]]:
    return {
        slot.id: [builtin_path(relative) for relative in slot.builtin
                  if builtin_path(relative).is_file()]
        for slot in SLOTS
    }


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #
def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


# --------------------------------------------------------------------------- #
# profile（玩家选择）
# --------------------------------------------------------------------------- #
def default_profile() -> dict:
    return {
        "version": PROFILE_VERSION,
        # true：自动把当前全部内置曲目放进 BGM 随机池（玩家只取消勾选某首内置时
        # 会在应用时变成 false，之后不再自动追加新内置曲目）。
        "bgm_auto_builtin": True,
        "audio": {
            slot.id: [builtin_ref(relative) for relative in slot.builtin]
            [:slot.default_count]
            for slot in SLOTS
        },
    }


def _merge_auto_bgm(cleaned: list[str], slot: Slot) -> list[str]:
    """自动模式：当前全部内置曲目 + 玩家已选的导入曲目。"""
    merged = [builtin_ref(relative) for relative in slot.builtin]
    for reference in cleaned:
        if reference.startswith("lib:") and reference not in merged:
            merged.append(reference)
    return merged[:slot.max_count] or cleaned


def _upgrade_bgm_pool(cleaned: list[str], slot: Slot,
                      version: int) -> list[str]:
    """把 v1 的单选 BGM 升级成 v2 的随机池。

    v1 的语义是“只循环一首”，v2 改成“多首随机”。升级时把当前全部内置曲目
    和玩家已选的导入曲目合并；这样新加进 assets/audio/bgm/ 的曲子无需手选
    就会进入随机池。已经保存过 v2 的玩家选择不会被改动。
    """
    if slot.id != "bgm" or version >= PROFILE_VERSION:
        return cleaned
    return _merge_auto_bgm(cleaned, slot)


def load_profile(root: Path) -> dict:
    """读 profile.json；缺失/损坏/空槽都回退默认，保证永远可用。"""
    fallback = default_profile()
    data = _read_json(profile_path(root))
    if not isinstance(data, dict):
        return fallback
    audio = data.get("audio")
    if not isinstance(audio, dict):
        return fallback

    try:
        version = int(data.get("version", 1))
    except (TypeError, ValueError):
        version = 1
    auto_bgm = data.get("bgm_auto_builtin")
    if not isinstance(auto_bgm, bool):
        auto_bgm = True

    result = {}
    for slot in SLOTS:
        references = audio.get(slot.id)
        if not isinstance(references, list):
            references = []
        cleaned = [item for item in references if isinstance(item, str) and item.strip()]
        cleaned = cleaned[:slot.max_count]
        if not cleaned:
            cleaned = [builtin_ref(relative) for relative in slot.builtin]
            cleaned = cleaned[:slot.default_count]
        elif slot.id == "bgm":
            if version < PROFILE_VERSION:
                cleaned = _upgrade_bgm_pool(cleaned, slot, version)
                auto_bgm = True
            elif auto_bgm:
                cleaned = _merge_auto_bgm(cleaned, slot)
        result[slot.id] = cleaned
    # 保留未知字段（以后的 visual 等），只覆盖 version/audio/bgm_auto_builtin
    payload = dict(data)
    payload["version"] = PROFILE_VERSION
    payload["bgm_auto_builtin"] = auto_bgm
    payload["audio"] = result
    return payload


def save_profile(root: Path, profile: dict) -> None:
    # 保留上一版，方便玩家改坏了回滚
    current = profile_path(root)
    if current.is_file():
        backups = root / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(
                current,
                backups / ("profile-%s.json" % time.strftime("%Y%m%d-%H%M%S")))
        except OSError:
            pass
    _atomic_json(current, profile)


# --------------------------------------------------------------------------- #
# library（玩家导入）
# --------------------------------------------------------------------------- #
def load_library_index(root: Path) -> dict:
    """读原始 index.json；缺失/损坏时返回空表，保留可写结构。"""
    data = _read_json(library_index_path(root))
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return {"version": 1, "entries": []}
    entries = [entry for entry in data["entries"] if isinstance(entry, dict)]
    return {"version": 1, "entries": entries}


def save_library_index(root: Path, payload: dict) -> None:
    path = library_index_path(root)
    if path.is_file():
        backups = library_dir(root) / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(
                path,
                backups / ("index-%s.json" % time.strftime("%Y%m%d-%H%M%S")))
        except OSError:
            pass
    _atomic_json(path, payload)


def load_library(root: Path) -> dict[str, dict]:
    """读 library/index.json，返回 {id: entry + path}；路径必须落在库目录内。"""
    data = load_library_index(root)
    base = library_dir(root).resolve()
    entries: dict[str, dict] = {}
    for entry in data.get("entries", []):
        entry_id = entry.get("id")
        relative = entry.get("file")
        if not isinstance(entry_id, str) or not isinstance(relative, str):
            continue
        try:
            candidate = (base / relative).resolve()
        except OSError:
            continue
        if not candidate.is_relative_to(base):
            continue
        if candidate.is_file() and candidate.suffix.lower() == ".wav":
            entries[entry_id] = dict(entry, path=candidate)
    return entries


def remove_library_entry(root: Path, entry_id: str) -> dict | None:
    """从库中删除一条导入音效（同时删文件）。找不到返回 None。

    只删除 index 里登记、且落在 library 目录内的那一个文件；失败会抛 OSError，
    调用方负责提示，不会把半删状态写回 index。
    """
    index = load_library_index(root)
    entries = index["entries"]
    target = next((entry for entry in entries if entry.get("id") == entry_id), None)
    if target is None:
        return None

    relative = str(target.get("file", ""))
    if relative:
        base = library_dir(root).resolve()
        try:
            candidate = (base / relative).resolve()
        except OSError:
            candidate = None
        if (candidate is not None and candidate.is_relative_to(base)
                and candidate.is_file()):
            candidate.unlink()

    entries.remove(target)
    save_library_index(root, index)
    return target


# --------------------------------------------------------------------------- #
# 解析 + 生成 active
# --------------------------------------------------------------------------- #
def _resolve_slot(slot: Slot, references: list[str], root: Path,
                  library: dict[str, dict]) -> list[tuple[str, Path]]:
    resolved: list[tuple[str, Path]] = []
    builtin_root = BUILTIN_AUDIO.resolve()
    for reference in references:
        path = None
        if reference.startswith("builtin:"):
            relative = reference[len("builtin:"):].lstrip("/\\")
            try:
                candidate = (BUILTIN_AUDIO / relative).resolve()
            except OSError:
                candidate = None
            if candidate is not None and candidate.is_relative_to(builtin_root):
                if candidate.is_file() and candidate.suffix.lower() == ".wav":
                    path = candidate
        elif reference.startswith("lib:"):
            entry = library.get(reference[len("lib:"):])
            if entry is not None:
                path = entry["path"]
        if path is not None:
            resolved.append((reference, path))
    return resolved


def _safe_name(value: str, limit: int = 48) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._")
    return (cleaned or "audio")[:limit]


def _fingerprint(plan: list[tuple[str, str, Path]]) -> str:
    digest = hashlib.sha256()
    for slot_id, relative, source in plan:
        try:
            stat = source.stat()
        except OSError:
            continue
        digest.update(
            ("%s|%s|%s|%d|%d\n" % (
                slot_id, relative, source, stat.st_size, stat.st_mtime_ns)
             ).encode("utf-8"))
    return digest.hexdigest()


def _read_manifest(path: Path) -> dict | None:
    data = _read_json(path)
    if not isinstance(data, dict):
        return None
    if data.get("version") != MANIFEST_VERSION:
        return None
    if not isinstance(data.get("slots"), dict):
        return None
    return data


def _cleanup_active(active: Path, keep: set[str]) -> None:
    """active 是生成目录：删掉上一版留下、这一版不再引用的 WAV。"""
    for category in ("sfx", "bgm"):
        base = active / category
        if not base.is_dir():
            continue
        for path in base.rglob("*.wav"):
            relative = path.relative_to(active).as_posix()
            if relative in keep:
                continue
            try:
                path.unlink()
            except OSError:
                pass


def _copy_or_link(source: Path, destination: Path) -> None:
    """active 优先用硬链接，避免多首 BGM 再占一份磁盘；跨盘时退回复制。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        if destination.is_file():
            try:
                same_file = os.path.samefile(source, destination)
            except OSError:
                same_file = False
            source_stat = source.stat()
            destination_stat = destination.stat()
            if same_file:
                return
            if (destination_stat.st_size == source_stat.st_size
                    and destination_stat.st_mtime_ns >= source_stat.st_mtime_ns):
                return
            destination.unlink()
        os.link(source, destination)
        return
    except OSError:
        pass
    shutil.copyfile(source, destination)


def materialize(root: Path, audio_refs: dict[str, list[str]],
                library: dict[str, dict]) -> dict[str, list[Path]]:
    """把选择清单落成 active/ 下的真实文件，返回 {slot: [Path]}。"""
    active = active_dir(root)
    plan: list[tuple[str, str, Path]] = []

    for slot in SLOTS:
        resolved = _resolve_slot(slot, audio_refs.get(slot.id, []), root, library)
        if not resolved:
            resolved = _resolve_slot(
                slot, [builtin_ref(relative) for relative in slot.builtin],
                root, library)
            resolved = resolved[:slot.default_count]
        if slot.max_count > 0:
            resolved = resolved[:slot.max_count]
        for index, (reference, source) in enumerate(resolved, 1):
            safe = _safe_name(reference.replace(":", "_").replace("/", "_"))
            if safe.lower().endswith(".wav"):
                safe = safe[:-4]
            if slot.kind == "bgm":
                relative = "bgm/%02d_%s.wav" % (index, safe)
            else:
                relative = "sfx/%s/%02d_%s.wav" % (slot.id, index, safe)
            plan.append((slot.id, relative, source))

    if not plan:
        raise RuntimeError("没有任何可用的音频文件")

    signature = _fingerprint(plan)
    manifest_path = active / "manifest.json"
    existing = _read_manifest(manifest_path)
    if existing and existing.get("signature") == signature:
        if all((active / relative).is_file() for _s, relative, _p in plan):
            return {
                slot.id: [active / relative for slot_id, relative, _p in plan
                          if slot_id == slot.id]
                for slot in SLOTS
            }

    active.mkdir(parents=True, exist_ok=True)
    for _slot_id, relative, source in plan:
        destination = active / relative
        _copy_or_link(source, destination)

    manifest = {
        "version": MANIFEST_VERSION,
        "signature": signature,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "slots": {},
    }
    for slot in SLOTS:
        files = [relative for slot_id, relative, _p in plan if slot_id == slot.id]
        manifest["slots"][slot.id] = {"kind": slot.kind, "files": files}
    _atomic_json(manifest_path, manifest)
    _cleanup_active(active, {relative for _s, relative, _p in plan})

    return {
        slot.id: [active / relative for slot_id, relative, _p in plan
                  if slot_id == slot.id]
        for slot in SLOTS
    }


def resolve_audio(root: Path | None = None) -> tuple[dict[str, list[Path]], dict]:
    """运行时入口：返回 ({slot: [Path]}, meta)。失败时退回内置。

    root 只给命令行/测试用；游戏运行时传 None，走 resource_root()。
    """
    meta = {"mode": "builtin", "root": None, "profile": None,
            "active": None, "errors": []}

    if root is None:
        root = resource_root()
    if root is None:
        meta["errors"].append("没有可写的资源目录，使用内置音效")
        return builtin_sound_set(), meta
    meta["root"] = str(root)

    path = profile_path(root)
    if not path.is_file():
        try:
            _atomic_json(path, default_profile())
        except OSError as exc:
            meta["errors"].append("写入默认 profile 失败：%s" % exc)
    meta["profile"] = str(path)

    library = library_dir(root)
    try:
        (library / "sfx").mkdir(parents=True, exist_ok=True)
        (library / "bgm").mkdir(parents=True, exist_ok=True)
        index = library / "index.json"
        if not index.is_file():
            _atomic_json(index, {"version": 1, "entries": []})
    except OSError as exc:
        meta["errors"].append("初始化玩家音效库失败：%s" % exc)

    try:
        import audio_import as _audio_import
        _audio_import.rescan_library(root)
    except Exception as exc:  # 扫描失败不影响启动
        meta["errors"].append("扫描玩家音效库失败：%s" % exc)

    profile = load_profile(root)
    library_entries = load_library(root)
    try:
        sound_set = materialize(root, profile["audio"], library_entries)
    except (OSError, RuntimeError, ValueError) as exc:
        meta["errors"].append("生成 active 失败：%s" % exc)
        return builtin_sound_set(), meta

    meta["mode"] = "custom"
    meta["active"] = str(active_dir(root))
    meta["slots"] = {slot: len(paths) for slot, paths in sound_set.items()}
    return sound_set, meta


def inspect() -> dict:
    """给 --check-assets 用：不改变游戏状态，只报告。"""
    sound_set, meta = resolve_audio()
    result = dict(meta)
    result["files"] = {slot: [str(path) for path in paths]
                       for slot, paths in sound_set.items()}
    return result
