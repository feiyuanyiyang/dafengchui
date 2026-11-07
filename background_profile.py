# -*- coding: utf-8 -*-
"""玩家背景偏好：默认明亮草原，可在自定义界面切换。

配置写到 `<自定义根>/background.json`，与音效 profile 分开，避免互相覆盖。
只保存背景 id；贴图仍然在 `assets/sprites/bg/` 里只读加载。
"""

from __future__ import annotations

import json
from pathlib import Path

import audio_profile

CONFIG_NAME = "background.json"
DEFAULT_ID = "meadow"

OPTIONS = (
    {
        "id": "meadow",
        "label": "明亮草原",
        "description": "默认 · 清爽的白天草地",
        "asset": "bg/ground_meadow",
    },
    {
        "id": "courtyard",
        "label": "明亮庭院",
        "description": "苔藓地与蜿蜒石板小径",
        "asset": "bg/ground_courtyard",
    },
)

OPTION_BY_ID = {option["id"]: option for option in OPTIONS}


def normalize(choice):
    """未知/损坏的 id 一律回退到默认背景。"""
    text = str(choice or "").strip()
    return text if text in OPTION_BY_ID else DEFAULT_ID


def asset_name(choice):
    return OPTION_BY_ID[normalize(choice)]["asset"]


def label(choice):
    return OPTION_BY_ID[normalize(choice)]["label"]


def config_path(root):
    return Path(root) / CONFIG_NAME


def _resolve_root(create):
    try:
        return audio_profile.resource_root(create=create)
    except Exception:
        return None


def load_choice(root=None):
    """读取玩家选择；没有配置/没有可写目录时返回默认背景。"""
    root = Path(root) if root is not None else _resolve_root(create=False)
    if root is None:
        return DEFAULT_ID
    try:
        data = json.loads(config_path(root).read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_ID
    if not isinstance(data, dict):
        return DEFAULT_ID
    return normalize(data.get("background"))


def save_choice(choice, root=None):
    """保存选择；没有可写目录时抛 OSError，由界面提示。"""
    choice = normalize(choice)
    root = Path(root) if root is not None else _resolve_root(create=True)
    if root is None:
        raise OSError("没有可写的自定义目录")
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "background": choice,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return choice
