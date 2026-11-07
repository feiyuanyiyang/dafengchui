# -*- coding: utf-8 -*-
"""大风吹（V1.1）——tkinter Canvas 小游戏（源码 + assets 资源目录）。

运行方式
    python game.py                 正常游戏（Windows 10/11，Python 3.8+，Tk 8.6+）
    python game.py --selftest      无窗口自测，输出 PASS/FAIL，退出码 0/1
    python game.py --soak 180      可选浸泡测试：无敌 + HUD，跑 N 秒后自检并退出
    python game.py --check-assets  检查贴图/音频资源是否齐全，然后退出

资源与依赖
    assets/sprites/   B 方案（柔和体积 / 发光氛围）PNG 贴图，资源缺失时自动退回矢量几何
    assets/audio/     WAV 音效与环境 BGM
    Pillow           贴图运行时依赖；缺失时几何渲染照样能玩
    pygame           音频运行时依赖；缺失时改用 waveOut 低延迟音效，再不行退化为 winsound 或静默
    贴图/音频都可以用 tools/ 下的脚本离线重新生成，见各脚本头部说明。

架构（严格遵守分层，便于后续调参与扩展）
    常量区 -> 纯逻辑 GameModel -> 渲染层 Renderer -> 应用壳 GameApp
    - GameModel / Player / Obstacle 不引用任何 tkinter / Canvas / 颜色 / 字体，
      因此 --selftest 可以在不创建 Tk 窗口的情况下推进模拟。
    - Renderer 只读模型、只写 Canvas，不改变任何游戏规则。
    - 应用层只负责窗口、输入、主循环、事件消费。

调参与扩展索引
    1) 玩法数值：全部在下面的“常量区”，按主题分组；改一个数字即可，不要改公式。
    2) 障碍物档位：OBSTACLE_TIERS 是数据表。新增档位 = 加一组常量 + 在表里加一行，
       生成、速度、伤害、颜色、权重都会自动跟随，不需要改生成/碰撞/渲染逻辑。
    3) 风向：WIND_DIRECTIONS 是数据表。新增方向 = 加一行（名称 + 单位向量），
       其余系统（生成、HUD、转向）自动适配 8 个以外的方向。
    4) 难度曲线：sample_duration / spawn_interval 只引用常量，公式集中在一处。
    5) 扩展接入点：文件末尾“扩展预留”区，以及 Renderer 的 PlayerRenderer 独立类；
       后续加贴图、音效、道具时按附录 B 的方式接入，不要改 GameModel 的规则代码。

核心逻辑只依赖 Python 标准库；贴图/音频为可选运行时依赖，缺失时自动降级。
"""

from __future__ import annotations

import argparse
import ast
import ctypes
import math
import os
import random
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from assets import (
    AssetStore,
    HEART_SPRITE_SIZE,
    OBSTACLE_SPRITE_FRAMES,
    SpriteFacing,
    asset_root,
)
from audio import AudioPlayer
import audio_import
import audio_profile
import background_profile

try:  # tkinter 随官方 CPython 提供；缺失时给出友好提示，不影响 --selftest 的纯逻辑部分
    import tkinter as tk
except Exception:  # pragma: no cover - 仅在精简版 Python 上出现
    tk = None


# =============================================================================
# 2. 常量区（唯一数值来源；逻辑与渲染不得出现裸数字）
# =============================================================================

# --- A.1 基础与时间 ---------------------------------------------------------
GAME_TITLE = "大风吹"
# 2026-10-29：窗口从 1000×700 放大到 1.2 倍；HUD/字体/云层/生成边界同步缩放。
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 840
TARGET_FPS = 60
FRAME_MS = 16
FIXED_DT = 1.0 / 120.0
MAX_SUBSTEPS = 6
MAX_FRAME_DT = FIXED_DT * MAX_SUBSTEPS  # 0.05 s；宁可慢动作，也不雪崩
HUD_REFRESH_INTERVAL = 0.1
KEY_RELEASE_DELAY_MS = 40
FLASH_INTERVAL = 0.1
# 贴图分帧预加载：开始界面空闲时一次多补几块，进对局后每帧只补一小块
PRELOAD_IDLE_BUDGET = 0.008  # 开始界面每帧最多花 8ms 补预加载
PRELOAD_PLAY_BUDGET = 0.004  # 对局中每帧最多 4ms，保住 60 帧
WIND_HISTORY_LEN = 5
DEV_MODE = True  # 验收期间保持 True；发布时改成 False 会关闭 F2–F8 调试键

# 浸泡测试（A13）阈值与节流
SOAK_WARMUP_TIME = 5.0
SOAK_REPORT_INTERVAL = 10.0
SOAK_MIN_FPS = 55.0
SOAK_MAX_ITEMS = 80

# --- A.2 玩家 ---------------------------------------------------------------
# 2026-10-28：按原设计 96×104 的 1.3 倍放大：贴图 125×135，碰撞同步。
PLAYER_RADIUS = 23.4
PLAYER_SPEED = 360.0          # 窗口 1.2 倍，移动速度同比缩放，跨屏时间不变
PLAYER_MAX_HEARTS = 3
PLAYER_START_HEARTS = 3
PLAYER_IFRAME = 0.7
# 受击硬直：0.2 s 内无法操控，只沿风向被推一小段；108 × 0.2 ≈ 21.6 px
HIT_STUN_TIME = 0.2
HIT_KNOCKBACK_SPEED = 108.0
# 受伤贴图朝向：按撞击时障碍物在玩家的哪一侧选左/右；|dx| 小于该值时
# 视为正上/正下命中（如风中纵向障碍），沿用玩家最后朝向。
HIT_FACE_DEAD_ZONE = 7.8
# 受击判定：用椭圆近似人物可见身体（含头和裙摆），中心相对脚底向上。
# 随 125×135 贴图按 1.3 倍缩放：21/40/44 -> 27.3/52/57.2。
PLAYER_HIT_RX = 27.3
PLAYER_HIT_RY = 52.0
PLAYER_HIT_CENTER_UP = 57.2
PLAYER_START_X = 600.0
PLAYER_START_Y = 420.0
PLAYER_START_FACE_X = 1.0
PLAYER_START_FACE_Y = 0.0
# 伪 3D 表现：脚底锚点（贴图底边对齐碰撞圆底部）、走路帧率、朝向迟滞
PLAYER_FEET_OFFSET_RATIO = 0.95
PLAYER_WALK_CYCLE_SECONDS = 0.667   # 一个完整奔跑循环的目标时长（16 帧 → 24 fps）
PLAYER_IDLE_CYCLE_SECONDS = 2.25    # idle 动作段+3 帧首尾混合（27 帧 → 12 fps）
PLAYER_IDLE_HOLD_SECONDS = 0.0      # 0 = 不插入静止保持，动作连续循环
PLAYER_IDLE_PINGPONG = False         # idle 只正放；动作段首尾同姿势，不再倒放
# 静止呼吸：没有多帧 idle 时，用渲染层的 1px 正弦上下浮动模拟
PLAYER_BREATH_PERIOD = 3.2
PLAYER_BREATH_PX = 0.8
DEPTH_SORT_ENABLED = True

# --- A.3 风 -----------------------------------------------------------------
# 2026-10-24 调整：初始风速 200 -> 150，逆风净速 150、顺风净速 450，顶风更轻松。
# 2026-10-29：窗口/玩家速度放大 1.2 倍，风速档位同步 ×1.2：150/20/250 -> 180/24/300。
# 新增“风速档位”：每 WIND_SPEED_INTERVAL 秒提高一档（+24），最高 300。
# 换风间隔也整体缩短约 25%，让风向变化更频繁。
WIND_SPEED = 180.0  # 初始风速（第 0 档）
WIND_SPEED_STEP = 24.0
WIND_SPEED_MAX = 300.0
WIND_SPEED_INTERVAL = 30.0
WIND_SPEED_MAX_TIER = int(math.ceil((WIND_SPEED_MAX - WIND_SPEED) / WIND_SPEED_STEP))
WIND_RAMP_TIME = 200.0
WIND_DUR_MIN_START = 45.0
WIND_DUR_MAX_START = 90.0
WIND_DUR_MIN_FLOOR = 12.0
WIND_DUR_MAX_FLOOR = 20.0

# 风向表：名称 + 单位向量（屏幕坐标，y 向下；“吹向”语义，D1 已确认）。
# 新增方向时只需在这里加一行；HUD 文本、生成上游、惯性转向都会自动跟随。
_DIAG = 0.7071067811865476
WIND_DIRECTIONS = (
    ("东", 1.0, 0.0),
    ("东南", _DIAG, _DIAG),
    ("南", 0.0, 1.0),
    ("西南", -_DIAG, _DIAG),
    ("西", -1.0, 0.0),
    ("西北", -_DIAG, -_DIAG),
    ("北", 0.0, -1.0),
    ("东北", _DIAG, -_DIAG),
)
WIND_DIRECTION_NAMES = tuple(d[0] for d in WIND_DIRECTIONS)  # 简单派生，供 HUD 使用

# --- A.4 障碍物与生成 -------------------------------------------------------
# 2026-10-29 调整：小/中/大 11/20/31.5，大号直径 63px ≈ 人物宽度（125px）的一半。
# 速度随窗口 1.2 倍同比增长，跨屏节奏保持不变。
OBSTACLE_SMALL_RADIUS = 11.0
OBSTACLE_SMALL_SPEED = 456.0
OBSTACLE_SMALL_DAMAGE = 1
OBSTACLE_SMALL_WEIGHT = 0.45

OBSTACLE_MEDIUM_RADIUS = 20.0
OBSTACLE_MEDIUM_SPEED = 312.0
OBSTACLE_MEDIUM_DAMAGE = 1
OBSTACLE_MEDIUM_WEIGHT = 0.35

OBSTACLE_LARGE_RADIUS = 31.5
OBSTACLE_LARGE_SPEED = 252.0
OBSTACLE_LARGE_DAMAGE = 1
OBSTACLE_LARGE_WEIGHT = 0.20

OBSTACLE_MAX_ACTIVE = 30
SPAWN_INTERVAL_START = 1.5
SPAWN_INTERVAL_FLOOR = 0.4
SPAWN_RAMP_TIME = 180.0
SPAWN_FIRST_DELAY = 1.2
SPAWN_MARGIN_EXTRA = 48.0
SPAWN_EDGE_INSET = 96.0
SPAWN_CORNER_BAND = 240.0  # 斜向风时，上游角每条边的生成带：距角最大距离（并受 EDGE_INSET 内缩）
SPAWN_MIN_CHORD = 300.0
SPAWN_SAMPLE_ATTEMPTS = 20
DESPAWN_MARGIN = 144.0
OBSTACLE_MAX_AGE = 20.0

# --- A.5 惯性 ---------------------------------------------------------------
# 2026-10-21 调整（第五次）：转向时长按档位独立，方便分别控制转弯半径。
#   小档 1.7 s：半径从约 260 px 回落到约 179 px，不再划出半个窗口的大弧；
#   中档 2.5 s：维持现状，半径约 178 px；
#   大档 2.7 s：配合提速到 210 px/s，半径约 155 px、线速度更快，转弯更醒目。
# 速度下凹 0.25（中段 75% 速度），角度进度仍是“快速起转 + 中段匀速”的梯形曲线。
# 仅 180° 反向改为“先沿原方向减速到 0、再反向加速”的直线倒车，避免空中画半圆；
# 135° 仍然保留弧线（惯性不能丢），但单独缩短时长，让它更快、更弯、更明显。
# 45°/90° 完全不改。
OBSTACLE_BRAKE_TURN_SCALE = 0.6    # 只作用于 180°
OBSTACLE_135_TURN_SCALE = 0.75     # 只作用于 135°，仍是弧线
OBSTACLE_TURN_TIME = 2.5  # 默认值/中档兜底
OBSTACLE_SMALL_TURN_TIME = 1.7
OBSTACLE_MEDIUM_TURN_TIME = 2.5
OBSTACLE_LARGE_TURN_TIME = 2.7
OBSTACLE_TURN_SPEED_DIP = 0.25
OBSTACLE_TURN_PLATEAU = 0.15  # 两端各用 15% 时长加速/收尾，中间 70% 匀速转

# --- A.6 按键与调试键 -------------------------------------------------------
# 上帝模式是正式功能，G 在发布版中同样可用；F8 保留为同一开关的调试别名。
PLAYER_KEY_GOD = ("g", "G")
DEV_KEY_WIND = "F2"
DEV_KEY_HUD = "F3"
DEV_KEY_REVERSE = "F4"
DEV_KEY_DAMAGE = "F6"
DEV_KEY_SPAWN = "F7"
DEV_KEY_GOD = "F8"
DEV_KEY_RESTART = ("r", "R")

# --- A.7 颜色 / 字体 / 布局（UI 常量，不参与规则） ---------------------------
COLOR_BG = "#0F1720"
COLOR_PLAYER = "#4FC3F7"
COLOR_PLAYER_FLASH = "#FFFFFF"
COLOR_EYE = "#0A1118"
COLOR_OUTLINE = "#0A1118"
COLOR_SMALL = "#FFD166"
COLOR_MEDIUM = "#F78C6B"
COLOR_LARGE = "#EF476F"
COLOR_TEXT = "#E6EDF3"
COLOR_DIM = "#8B98A5"
COLOR_HEART_FULL = "#E74C3C"
COLOR_HEART_EMPTY = "#3A4654"
COLOR_GOD = "#FFD166"  # 上帝模式徽标（与最小障碍物同色系，醒目但不刺眼）
COLOR_CLOUD = "#14212C"
COLOR_OVERLAY = "#000000"
OVERLAY_STIPPLE = "gray50"
COLOR_PANEL = "#132430"  # 暂停面板底色
COLOR_BUTTON = "#1B3242"
COLOR_BUTTON_HOVER = "#274A60"
COLOR_BUTTON_BORDER = "#4FC3F7"

FONT_FAMILY = "Microsoft YaHei UI"  # 缺失时 Tk 自动替换
# 以下 UI 数值随窗口 1.25 倍同步缩放
FONT_SMALL = 14
FONT_NORMAL = 19
FONT_BIG = 41
HEART_FONT_SIZE = 29
HEART_GAP = 10
HUD_MARGIN = 29

# HUD 暂停按钮（在生命值左侧；生命值与心数整体右移腾出空间）
HUD_PAUSE_SIZE = 41
HUD_PAUSE_GAP = 17
HUD_PAUSE_BAR_WIDTH = 6
HUD_PAUSE_BAR_HEIGHT = 19
HUD_PAUSE_BAR_GAP = 10
HUD_PAUSE_X1 = HUD_MARGIN
HUD_PAUSE_Y1 = HUD_MARGIN
HUD_PAUSE_X2 = HUD_PAUSE_X1 + HUD_PAUSE_SIZE
HUD_PAUSE_Y2 = HUD_PAUSE_Y1 + HUD_PAUSE_SIZE
HUD_HEART_X = HUD_PAUSE_X2 + HUD_PAUSE_GAP
HUD_PAUSE_BAR_Y1 = HUD_PAUSE_Y1 + (HUD_PAUSE_SIZE - HUD_PAUSE_BAR_HEIGHT) // 2
HUD_PAUSE_BAR_Y2 = HUD_PAUSE_BAR_Y1 + HUD_PAUSE_BAR_HEIGHT
HUD_PAUSE_BAR_TOTAL = 2 * HUD_PAUSE_BAR_WIDTH + HUD_PAUSE_BAR_GAP
HUD_PAUSE_BAR_X = HUD_PAUSE_X1 + (HUD_PAUSE_SIZE - HUD_PAUSE_BAR_TOTAL) // 2

TIMER_Y = 36
GOD_BADGE_Y = TIMER_Y + 36
WIND_ARROW_LEN = 65
WIND_ARROW_Y = 72
WIND_ARROW_WIDTH = 2
WIND_TEXT_Y = 115
HINT_Y = 31
DEV_HUD_MARGIN = 19
OUTLINE_WIDTH = 1
EYE_RADIUS = 2
EYE_FORWARD_RATIO = 0.55
EYE_SIDE_RATIO = 0.45
HEAD_RADIUS_RATIO = 0.55
HEAD_OFFSET_RATIO = 0.7
HEART_COUNT_GAP = 12
OVERLAY_TITLE_Y = 300
OVERLAY_REASON_Y = 360
OVERLAY_SCORE_Y = 408
# 结束界面按钮：竖排，重新开始在上 / 退出游戏在下；R 快捷键仍然可用
OVERLAY_BUTTON_WIDTH = 240
OVERLAY_BUTTON_HEIGHT = 64
OVERLAY_BUTTON_Y_GAP = 22
OVERLAY_BUTTON_Y_START = 464
OVERLAY_HINT_Y = 648

# --- A.8 暂停界面（UI 常量，不参与规则） ------------------------------------
PAUSE_KEY = "Escape"
PAUSE_RESUME_SECONDS = 3
PAUSE_COUNTDOWN_INTERVAL_MS = 1000
PAUSE_LAYER_TAG = "pause_layer"
PAUSE_MASK_STIPPLE = "gray50"  # Canvas 无 alpha，用点阵模拟“整体变暗”
PAUSE_PANEL_WIDTH = 528
PAUSE_PANEL_HEIGHT = 504
PAUSE_BUTTON_WIDTH = 336
PAUSE_BUTTON_HEIGHT = 67
PAUSE_BUTTON_GAP = 22
PAUSE_PANEL_X1 = (WINDOW_WIDTH - PAUSE_PANEL_WIDTH) // 2
PAUSE_PANEL_Y1 = (WINDOW_HEIGHT - PAUSE_PANEL_HEIGHT) // 2
PAUSE_PANEL_X2 = PAUSE_PANEL_X1 + PAUSE_PANEL_WIDTH
PAUSE_PANEL_Y2 = PAUSE_PANEL_Y1 + PAUSE_PANEL_HEIGHT
PAUSE_TITLE_Y = PAUSE_PANEL_Y1 + 72
PAUSE_BUTTON_Y_START = PAUSE_TITLE_Y + 90
PAUSE_BUTTON_YS = tuple(
    PAUSE_BUTTON_Y_START + index * (PAUSE_BUTTON_HEIGHT + PAUSE_BUTTON_GAP)
    for index in range(4)
)
PAUSE_HINT_Y = PAUSE_PANEL_Y2 - 36
# 倒计时只显示暗色遮罩 + 数字 + 说明文字，居中显示。
PAUSE_COUNTDOWN_Y = WINDOW_HEIGHT // 2
PAUSE_COUNTDOWN_SUFFIX_Y = PAUSE_COUNTDOWN_Y + 96
FONT_COUNTDOWN = 86

# 应用层界面（开始 / 自定义）；和模型状态 STATE_* 分开命名
APP_START = "start"
APP_PLAYING = "playing"
APP_CUSTOM = "custom"

START_LAYER_TAG = "start_layer"
START_TITLE_Y = 286
# 副标题放在标题上方：封面里标题正下方是角色飘过来的头发（偏暗），
# 深色小字压上去会看不清；标题上方实测是干净亮天空。
START_SUBTITLE_Y = 200
START_BUTTON_WIDTH = 360
START_BUTTON_HEIGHT = 72
START_BUTTON_GAP = 22
START_BUTTON_Y_START = 454
START_BUTTON_YS = tuple(
    START_BUTTON_Y_START + index * (START_BUTTON_HEIGHT + START_BUTTON_GAP)
    for index in range(3)
)
START_HINT_Y = WINDOW_HEIGHT - 50
FONT_TITLE = 77
FONT_SUBTITLE = 22

# 开始界面封面（assets/sprites/ui/start_cover.png）：亮底，文字因此改用深色；
# 封面缺失时自动退回 COLOR_BG 深色底 + 原来的浅色文字。
START_COVER_NAME = "ui/start_cover"
COLOR_START_TITLE = "#12263D"
COLOR_START_SUBTITLE = "#16304B"
COLOR_START_HINT = "#E6EDF3"

# 自定义界面：底图（assets/sprites/ui/custom_backdrop.png）+ 内嵌设置面板
CUSTOM_BACKDROP_NAME = "ui/custom_backdrop"
CUSTOM_CARD_MARGIN_X = 48
CUSTOM_CARD_MARGIN_Y = 36
COLOR_CARD_BORDER = "#223444"

# 自定义界面：左侧分类栏 + 右侧内容区（tk.Frame 覆盖在 Canvas 上）
CUSTOM_SIDEBAR_WIDTH = 240
CUSTOM_CONTENT_PAD = 26
CUSTOM_ROW_PAD = 4

# 自定义界面（蜡笔版）：整屏合成，坐标全部是 1200x840 下的逻辑像素。
# 视觉基线来自 art_preview/custom_redesign_mock.png：模糊封面 + 暗绿蒙版，
# 白描边镂空按钮、暖纸色选中态，信息结构沿用旧版自定义页。
CRAYON_CUSTOM_BACKDROP_NAME = START_COVER_NAME
CRAYON_CUSTOM_BLUR = 10
CRAYON_CUSTOM_VEIL = (16, 38, 28, 158)
CRAYON_CUSTOM_TITLE_Y = 92
CRAYON_CUSTOM_SUBTITLE_Y = 148
CRAYON_CUSTOM_NAV_X = (80, 300)
CRAYON_CUSTOM_NAV_YS = (258, 326, 394)
CRAYON_CUSTOM_NAV_H = 58
CRAYON_CUSTOM_BACK_Y = 756
CRAYON_CUSTOM_CONTENT_X = (344, 1120)
CRAYON_CUSTOM_HEADER_Y = 213
CRAYON_CUSTOM_DESC_Y = 258
CRAYON_CUSTOM_SLOT_Y = (284, 332)
CRAYON_CUSTOM_SLOT_WIDTHS = (100, 140, 140, 140, 140)
CRAYON_CUSTOM_SLOT_GAP = 12
CRAYON_CUSTOM_SLOT_HINT_Y = 352
CRAYON_CUSTOM_ROWS_TOP = 366
CRAYON_CUSTOM_ROW_H = 52
CRAYON_CUSTOM_ROW_GAP = 8
CRAYON_CUSTOM_ROW_VISIBLE = 6
CRAYON_CUSTOM_SCROLL_X = (1130, 1138)
CRAYON_CUSTOM_ACTION_Y = 758
CRAYON_CUSTOM_ACTION_H = 50
CRAYON_CUSTOM_ACTIONS = (
    ("import", "导入音频…", (618, 756)),
    ("refresh", "刷新", (768, 856)),
    ("default", "恢复默认", (866, 998)),
    ("apply", "应用", (1008, 1120)),
)
CRAYON_CUSTOM_STATUS_Y = 748
CRAYON_CUSTOM_HINT_Y = 808
CRAYON_CUSTOM_BG_CARD_Y = 284
CRAYON_CUSTOM_BG_CARD_SIZE = (360, 350)
CRAYON_CUSTOM_BG_CARD_GAP = 20
CRAYON_CUSTOM_BG_IMAGE_SIZE = (336, 230)
CRAYON_CUSTOM_WARM = (255, 246, 224)
CRAYON_CUSTOM_FRAME = (255, 255, 255, 210)
CRAYON_CUSTOM_FRAME_HOVER = (255, 255, 255, 255)
CRAYON_CUSTOM_WASH = (255, 255, 255, 22)
CRAYON_CUSTOM_CARD_FILL = (255, 255, 255, 14)
CRAYON_CUSTOM_ERROR = (255, 178, 158)

# 静态云块（只创建一次）：(x1, y1, x2, y2)
CLOUD_SHAPES = (
    (96, 84, 312, 168),
    (828, 156, 1104, 252),
    (432, 672, 672, 756),
)

# 贴图 / 音频配置（B 方案）
# 资源缺失或 Pillow 不可用时，渲染层自动退回原来的矢量几何；音频同理静默降级。
ASSETS_ENABLED = True
AUDIO_ENABLED = True
AUDIO_BGM_ENABLED = True
AUDIO_SFX_VOLUME = 0.7
AUDIO_BGM_VOLUME = 0.35
MUTE_KEY = "m"
# 障碍物贴图变体数量（每档 3 种材质，按 oid 轮换，纯视觉差异）
OBSTACLE_SPRITE_VARIANTS = 3
# 小档翻面帧速率（帧/秒）；中/大档是绕中心旋转，按 OBSTACLE_SPIN_PROFILES 的角速度切帧。
# 帧数见 assets.OBSTACLE_SPRITE_FRAMES。
OBSTACLE_FRAME_FPS = {"small": 10.0}
# 中/大档旋转角速度（转/秒）+ 慢速“翻滚起伏”：转速不是恒定马达，而是随正弦
# 轻微忽快忽慢，看起来像在空中翻滚。revs/wobble 都会按 oid 做随机抖动。
#   wobble    = 转速的相对起伏幅度（0.30 = ±30%）
#   wobble_hz = 起伏频率（Hz）
OBSTACLE_SPIN_PROFILES = {
    "medium": {"revs": 0.20, "wobble": 0.30, "wobble_hz": 0.40},
    "large": {"revs": 0.12, "wobble": 0.25, "wobble_hz": 0.30},
}
# 背景暗角（风格 B 的氛围层）；关闭后回到纯色背景
VIGNETTE_ENABLED = True

# --- 逻辑状态 / 事件常量 -----------------------------------------------------
STATE_RUNNING = "RUNNING"
STATE_GAME_OVER = "GAME_OVER"
REASON_BLOWN_AWAY = "BLOWN_AWAY"
REASON_HEARTS_ZERO = "HEARTS_ZERO"
EVENT_HIT = "hit"
EVENT_WIND_CHANGE = "wind_change"
EVENT_GAME_OVER = "game_over"

# 结束原因文案（渲染层使用）
REASON_TEXT = {
    REASON_BLOWN_AWAY: "被吹出窗口",
    REASON_HEARTS_ZERO: "血量归零",
}

# 生成边（内部标识，也用于自测断言）
EDGE_LEFT = "left"
EDGE_RIGHT = "right"
EDGE_TOP = "top"
EDGE_BOTTOM = "bottom"

# 障碍物档位表（数据驱动；新增档位只加常量 + 这里一行）
OBSTACLE_TIERS = (
    {
        "tier": "small",
        "radius": OBSTACLE_SMALL_RADIUS,
        "speed": OBSTACLE_SMALL_SPEED,
        "damage": OBSTACLE_SMALL_DAMAGE,
        "weight": OBSTACLE_SMALL_WEIGHT,
        "color": COLOR_SMALL,
        "turn_time": OBSTACLE_SMALL_TURN_TIME,
    },
    {
        "tier": "medium",
        "radius": OBSTACLE_MEDIUM_RADIUS,
        "speed": OBSTACLE_MEDIUM_SPEED,
        "damage": OBSTACLE_MEDIUM_DAMAGE,
        "weight": OBSTACLE_MEDIUM_WEIGHT,
        "color": COLOR_MEDIUM,
        "turn_time": OBSTACLE_MEDIUM_TURN_TIME,
    },
    {
        "tier": "large",
        "radius": OBSTACLE_LARGE_RADIUS,
        "speed": OBSTACLE_LARGE_SPEED,
        "damage": OBSTACLE_LARGE_DAMAGE,
        "weight": OBSTACLE_LARGE_WEIGHT,
        "color": COLOR_LARGE,
        "turn_time": OBSTACLE_LARGE_TURN_TIME,
    },
)
TIER_BY_NAME = {t["tier"]: t for t in OBSTACLE_TIERS}  # 简单派生

# A1 空中摆动：贴图相对直线/转向轨迹的轻微偏移，让“被风卷着走”的杂物不再像
# 贴在屏幕上平移。只是视觉偏移，不改速度大小，也不参与回收/转向判定。
#   perp  = 垂直运动方向的摆幅（px，左右飘）
#   along = 沿运动方向的起伏（px，看起来像忽快忽慢）
#   freq  = 基础频率（Hz）；实际值按 oid 做 ±15% 抖动，避免同屏动作整齐划一
OBSTACLE_SWAY_PROFILES = {
    "small": {"perp": 3.0, "along": 2.0, "freq": 2.4},   # 树叶/纸片/羽毛：轻、快、爱抖
    "medium": {"perp": 1.7, "along": 1.1, "freq": 1.3},  # 松果/碎石/纸团：带点重量地晃
    "large": {"perp": 0.8, "along": 0.5, "freq": 0.7},   # 树枝/木板/石块：几乎只是轻微飘移
}

# A2 速度个体差异 + 阵风 -----------------------------------------------------
# 生成时每颗障碍物速度 ±8%；飞行途中再叠一层慢速正弦“阵风”，相位按 oid 派生，
# 避免同屏所有杂物同时加速/减速。玩家受风推力不受影响，难度曲线保持原设计。
OBSTACLE_SPEED_JITTER = 0.08
OBSTACLE_GUST_PROFILES = {
    "small": {"amp": 0.070, "freq": 0.55},   # 轻物件最吃风
    "medium": {"amp": 0.050, "freq": 0.42},
    "large": {"amp": 0.030, "freq": 0.30},   # 重的几乎匀速
}

# A3 转向外抛 ----------------------------------------------------------------
# 换风转向时，惯性让物件朝弯道外侧甩出去，并略微滞后于行进方向：
#   side = 垂直速度方向的外抛最大偏移（px）
#   drag = 沿速度方向的滞后最大偏移（px）
# 都乘以 sin(pi * 进度) 的包络，起转/转完为 0，不会在转向结束后留下偏移。
OBSTACLE_TURN_BANK = {
    "small": {"side": 3.2, "drag": 2.2},
    "medium": {"side": 2.4, "drag": 1.5},
    "large": {"side": 1.6, "drag": 1.0},
}


# =============================================================================
# 3. 工具函数
# =============================================================================

EPS = 1e-9


def clamp(value, low, high):
    """把 value 夹到 [low, high]。"""
    if value < low:
        return low
    if value > high:
        return high
    return value


def lerp(a, b, t):
    """线性插值。"""
    return a + (b - a) * t


def smoothstep(t):
    """smoothstep：0 -> 0，1 -> 1，两端导数为 0。"""
    t = clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def turn_progress(s):
    """障碍物转向的角度进度：两端各用 plateau 比例的时长加/减速，中间匀速。

    相比 smoothstep，它更早进入转向中段、角速度在大部分时间里保持恒定，
    轨迹更接近真正的圆弧；配合较大的 OBSTACLE_TURN_TIME，视觉上就是“宽而快”的拐弯。
    """
    s = clamp(s, 0.0, 1.0)
    if s <= 0.0:
        return 0.0
    if s >= 1.0:
        return 1.0
    plateau = OBSTACLE_TURN_PLATEAU
    if plateau <= 0.0:
        return s
    if plateau >= 0.5:
        return smoothstep(s)
    if s < plateau:
        return 0.5 * s * s / (plateau * (1.0 - plateau))
    if s > 1.0 - plateau:
        remaining = 1.0 - s
        return 1.0 - 0.5 * remaining * remaining / (plateau * (1.0 - plateau))
    return (s - 0.5 * plateau) / (1.0 - plateau)


def wrap_to_pi(angle):
    """把角度折到 [-pi, pi)。"""
    wrapped = math.fmod(angle + math.pi, 2.0 * math.pi)
    if wrapped < 0.0:
        wrapped += 2.0 * math.pi
    return wrapped - math.pi


def vec_length(x, y):
    """向量模长。"""
    return math.hypot(x, y)


def unit_of(x, y):
    """归一化；零向量回退为 (0, 0)，由调用方处理。"""
    length = math.hypot(x, y)
    if length < EPS:
        return 0.0, 0.0
    return x / length, y / length


def enable_dpi_awareness():
    """声明进程 DPI 感知（默认不调用，见 run_game 里的说明）。

    注意副作用：声明之后窗口按物理像素呈现，在 125%/150% 缩放的屏幕上
    会比之前“变小”。所以默认关闭，只有打包时想强制高清才设置环境变量
    DAFENGCHUI_DPI_AWARE=1。
    """
    if sys.platform != "win32":
        return False
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # Win8.1+
        return True
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except Exception:
        return False


def process_dpi_awareness():
    """当前进程的 DPI 感知级别：0=不感知，1=系统级，2=每显示器。"""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes.wintypes as wintypes

        value = ctypes.c_int()
        if ctypes.windll.shcore.GetProcessDpiAwareness(
                None, ctypes.byref(value)) == 0:
            return int(value.value)
    except Exception:
        pass
    try:
        return 1 if ctypes.windll.user32.IsProcessDPIAware() else 0
    except Exception:
        return 0


def display_scale():
    """当前显示器的缩放系数（1.0 / 1.25 / 1.5 …）。

    进程不感知 DPI 时，Tk 的 winfo_rootx 是逻辑坐标、抓屏要物理像素，
    抓图前必须换算，否则截图整体偏移（实测 150% 下差 1.5 倍）。
    声明了 DPI 感知则坐标本来就是物理像素，系数取 1.0。
    """
    if sys.platform != "win32":
        return 1.0
    try:
        import ctypes.wintypes as wintypes

        monitor = ctypes.windll.user32.MonitorFromPoint(
            wintypes.POINT(0, 0), 2)  # MONITOR_DEFAULTTONEAREST
        factor = ctypes.c_int()
        if ctypes.windll.shcore.GetScaleFactorForMonitor(
                wintypes.HMONITOR(monitor), ctypes.byref(factor)) == 0:
            if factor.value >= 100:
                return factor.value / 100.0
    except Exception:
        pass
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()  # Win10 1607+
        if dpi:
            return max(1.0, dpi / 96.0)
    except Exception:
        pass
    return 1.0


def enable_high_resolution_timer():
    """Windows 默认定时器精度约 15.6 ms，会让 after(16) 退化到 ~47 FPS。

    用标准库 ctypes 申请 1 ms 精度，保证 FRAME_MS=16 的调度接近 62.5 Hz。
    非 Windows 或调用失败时静默跳过；退出时配对恢复。
    """
    if sys.platform != "win32":
        return False
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
        return True
    except Exception:
        return False


def disable_high_resolution_timer():
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass


def disable_ime_for_window(window):
    """解除 Tk 窗口的 IME 关联，避免中文输入法（搜狗等）在游戏窗口里拦截按键。

    游戏不需要文本输入，窗口不应进入 IME 组合态。Windows 上 IME 上下文挂在
    Tk 的内层窗口和它外层的 wrapper 上，两处都要解除；非 Windows 静默跳过。
    """
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        imm32 = ctypes.windll.imm32
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        imm32.ImmAssociateContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        imm32.ImmAssociateContext.restype = ctypes.c_void_p

        hwnd = int(window.winfo_id())
        if not hwnd:
            return False
        targets = [hwnd]
        parent = user32.GetParent(ctypes.c_void_p(hwnd))
        if parent:
            targets.append(int(parent))
        for target in targets:
            imm32.ImmAssociateContext(ctypes.c_void_p(target), None)
        return True
    except Exception:
        return False


def disable_ime_for_process():
    """在创建任何 Tk 窗口前禁用本进程 IME，作为窗口级处理的进程级保险。

    ImmDisableIME(0) 需在创建窗口前调用；游戏全程不需要文本输入，因此安全。
    """
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.imm32.ImmDisableIME(0))
    except Exception:
        return False


# =============================================================================
# 5. 风向与难度调度（纯函数，便于自测与后续调参）
# =============================================================================

def wind_duration_bounds(elapsed):
    """当前单局时长下，单次风向持续时间的采样下界/上界（smoothstep 包络）。"""
    u = clamp(elapsed / WIND_RAMP_TIME, 0.0, 1.0)
    k = smoothstep(u)
    low = lerp(WIND_DUR_MIN_START, WIND_DUR_MIN_FLOOR, k)
    high = lerp(WIND_DUR_MAX_START, WIND_DUR_MAX_FLOOR, k)
    return low, high


def sample_duration(elapsed, rng):
    """在给定单局时刻采样一次风向持续时间。"""
    low, high = wind_duration_bounds(elapsed)
    return rng.uniform(low, high)


def wind_speed_at(elapsed):
    """返回 (当前风速, 风速档位)：每 WIND_SPEED_INTERVAL 秒升一档，封顶 WIND_SPEED_MAX。"""
    if elapsed <= 0.0:
        return WIND_SPEED, 0
    tier = min(int(elapsed // WIND_SPEED_INTERVAL), WIND_SPEED_MAX_TIER)
    speed = min(WIND_SPEED + WIND_SPEED_STEP * tier, WIND_SPEED_MAX)
    return speed, tier


def spawn_interval(elapsed):
    """当前单局时刻的障碍物生成间隔（180 s 内从 1.5 s 降到 0.4 s）。"""
    u = clamp(elapsed / SPAWN_RAMP_TIME, 0.0, 1.0)
    k = smoothstep(u)
    return lerp(SPAWN_INTERVAL_START, SPAWN_INTERVAL_FLOOR, k)


# =============================================================================
# 4. 数据模型（纯逻辑，不引用 tkinter）
# =============================================================================

LOGICAL_KEYS = ("up", "down", "left", "right")


@dataclass
class InputState:
    """应用层与模型之间的纯数据：逻辑键 -> 是否按住。"""

    pressed: dict = field(default_factory=dict)

    def reset(self):
        self.pressed.clear()

    def vector(self):
        """归一化输入向量；无按键返回 (0, 0)，斜向长度为 1 而不是 1.414。"""
        dx = (1 if self.pressed.get("right") else 0) - (1 if self.pressed.get("left") else 0)
        dy = (1 if self.pressed.get("down") else 0) - (1 if self.pressed.get("up") else 0)
        return unit_of(float(dx), float(dy))


@dataclass
class Player:
    """玩家状态；face 是最近一次输入方向，供渲染层使用。"""

    x: float = PLAYER_START_X
    y: float = PLAYER_START_Y
    radius: float = PLAYER_RADIUS
    hearts: int = PLAYER_START_HEARTS
    invuln_timer: float = 0.0
    face_x: float = PLAYER_START_FACE_X
    face_y: float = PLAYER_START_FACE_Y
    input_active: bool = False  # 本帧是否有方向输入；决定播 walk 还是 idle
    flash_phase: bool = False
    flash_timer: float = 0.0
    stun_timer: float = 0.0   # >0 时无法操控，播放受击贴图并沿风被推开
    hurt_face_x: float = 0.0  # 受伤来源：+1 障碍在右侧、-1 在左侧、0 不确定（沿用最后朝向）


@dataclass
class TurnState:
    """障碍物的惯性转向状态（只在换风时创建）。"""

    elapsed: float
    duration: float
    start_angle: float
    delta_angle: float
    target_angle: float
    start_speed: float
    target_speed: float
    brake_turn: bool = False  # 大角度（135°/180°）：先直线减速到 0，再沿新方向加速


@dataclass
class Obstacle:
    """障碍物；模型只保存 oid，Canvas item id 由渲染层维护。"""

    oid: int
    x: float
    y: float
    vx: float
    vy: float
    radius: float
    tier: str
    damage: int
    base_speed: float
    turn: TurnState = None
    age: float = 0.0
    # A1 空中摆动：x/y 是轨迹位置，sway_x/sway_y 是叠加的视觉偏移（px）。
    # 绘制与碰撞都使用 x+sway_x / y+sway_y，保证“看到哪就撞哪”。
    sway_perp: float = 0.0
    sway_along: float = 0.0
    sway_freq: float = 0.0
    sway_phase: float = 0.0
    sway_phase2: float = 0.0
    sway_x: float = 0.0
    sway_y: float = 0.0
    # A2 阵风：速度围绕 base_speed 做慢速正弦起伏（amp=0 表示禁用，测试构造用）
    gust_amp: float = 0.0
    gust_freq: float = 0.0
    gust_phase: float = 0.0
    # B 中/大档绕贴图中心旋转：角速度（转/秒）、方向（±1）、起始相位（0..1 圈）
    # 以及转速的正弦“翻滚起伏”（wobble 为角度摆幅，单位圈）。
    spin_revs: float = 0.0
    spin_dir: float = 1.0
    spin_phase: float = 0.0
    spin_wobble: float = 0.0
    spin_wobble_freq: float = 0.0
    spin_wobble_phase: float = 0.0
    # 命中玩家并造成伤害后置 True，随后被移出 self.obstacles。
    # 预留给消失动画：渲染/特效层可读取该标记，在 item 删除前播特效。
    dead: bool = False


@dataclass
class Wind:
    """当前风向：吹向单位向量 + 剩余/总时长。"""

    index: int
    ux: float
    uy: float
    remaining: float
    duration: float
    change_count: int = 0

    def name(self):
        return WIND_DIRECTIONS[self.index][0]


class GameModel:
    """纯逻辑游戏模型：可无窗口推进，--selftest 直接驱动。"""

    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.god_mode = False  # G（F8 同效）：免疫风推/伤害/越界，--soak 也会开启
        self.pickups = []  # 扩展预留：回血道具（V1 为空，见附录 B）
        self.reset()

    # ------------------------------------------------------------------ 重置
    def reset(self):
        """完整重置；先归零 elapsed，再采样初始风向时长（避免继承上局难度）。"""
        self.state = STATE_RUNNING
        self.elapsed = 0.0
        self.wind_speed, self.wind_speed_tier = wind_speed_at(0.0)
        self.reason = None
        self.events = []
        self.wind_history = []
        self.obstacle_seq = 0
        self.obstacles = []
        self.player = Player()

        index = self.rng.randrange(len(WIND_DIRECTIONS))
        name, ux, uy = WIND_DIRECTIONS[index]
        duration = sample_duration(0.0, self.rng)
        self.wind = Wind(index=index, ux=ux, uy=uy, remaining=duration, duration=duration)
        self.spawn_timer = SPAWN_FIRST_DELAY

    # -------------------------------------------------------------- 主更新步
    def step(self, dt, input_vec=(0.0, 0.0)):
        """按 §4.4 的顺序推进一个固定模拟步；结束态直接返回（冻结）。"""
        if self.state != STATE_RUNNING:
            return

        # 2. 计时、无敌帧、闪烁相位
        self.elapsed += dt
        # 风速档位：每过一段时间提高一档；放在移动之前，保证本步立即生效。
        self.wind_speed, self.wind_speed_tier = wind_speed_at(self.elapsed)
        player = self.player
        # 先记住本步开始时的硬直状态：计时器在最后一步归零时，本步仍应表现为受击
        stunned_this_step = player.stun_timer > 0.0
        if player.stun_timer > 0.0:
            player.stun_timer = max(0.0, player.stun_timer - dt)
        if player.invuln_timer > 0.0:
            player.invuln_timer = max(0.0, player.invuln_timer - dt)
            player.flash_timer += dt
            if player.flash_timer >= FLASH_INTERVAL:
                player.flash_timer -= FLASH_INTERVAL
                player.flash_phase = not player.flash_phase
        else:
            player.flash_phase = False
            player.flash_timer = 0.0

        # 3. 风倒计时与换风（风先于障碍更新，保证换风当帧就进入转向）
        self.wind.remaining -= dt
        if self.wind.remaining <= 0.0:
            self._change_wind()

        # 4. 生成计时（即使因上限跳过，也要重置计时器）
        self.spawn_timer -= dt
        if self.spawn_timer <= 0.0:
            self.spawn_timer = spawn_interval(self.elapsed)
            if len(self.obstacles) < OBSTACLE_MAX_ACTIVE:
                self._spawn_obstacle()

        # 5. 玩家：输入速度 + 风速度直接叠加（斜向已归一化）
        #    上帝模式：风推力完全失效，只保留玩家自身输入，避免被风推着走/推出窗口。
        ix, iy = input_vec
        stunned = stunned_this_step
        if stunned:
            # 受击硬直：输入无效，只沿“风吹向”被推开一小段
            vx = self.wind.ux * HIT_KNOCKBACK_SPEED
            vy = self.wind.uy * HIT_KNOCKBACK_SPEED
        elif self.god_mode:
            vx = ix * PLAYER_SPEED
            vy = iy * PLAYER_SPEED
        else:
            vx = ix * PLAYER_SPEED + self.wind.ux * self.wind_speed
            vy = iy * PLAYER_SPEED + self.wind.uy * self.wind_speed
        player.x += vx * dt
        player.y += vy * dt
        player.input_active = (not stunned) and (abs(ix) > EPS or abs(iy) > EPS)
        if player.input_active:
            player.face_x, player.face_y = unit_of(ix, iy)
        # 硬直期间夹在窗口内：受击不应直接把人吹出界，避免“一撞就死”
        if self.god_mode or stunned:
            player.x = clamp(player.x, player.radius, WINDOW_WIDTH - player.radius)
            player.y = clamp(player.y, player.radius, WINDOW_HEIGHT - player.radius)

        # 6. 障碍物
        for obstacle in self.obstacles:
            update_obstacle(obstacle, dt)
            obstacle.age += dt

        # 7. 回收：越界 120 px 或存活超过 20 s（倒序语义：重建列表）
        kept = []
        for obstacle in self.obstacles:
            if obstacle.age > OBSTACLE_MAX_AGE:
                continue
            if (
                obstacle.x < -DESPAWN_MARGIN
                or obstacle.x > WINDOW_WIDTH + DESPAWN_MARGIN
                or obstacle.y < -DESPAWN_MARGIN
                or obstacle.y > WINDOW_HEIGHT + DESPAWN_MARGIN
            ):
                continue
            kept.append(obstacle)
        self.obstacles = kept

        # 8. 碰撞：障碍圆 vs 玩家身体椭圆（覆盖可见贴图，头部也有判定）
        #    椭圆中心在脚底上方，半轴按贴图里的角色体积取值；同帧最多结算一次。
        if not self.god_mode:
            feet_y = player.y + player.radius * PLAYER_FEET_OFFSET_RATIO
            hit_cy = feet_y - PLAYER_HIT_CENTER_UP
            for obstacle in self.obstacles:
                radius_x = PLAYER_HIT_RX + obstacle.radius
                radius_y = PLAYER_HIT_RY + obstacle.radius
                obstacle_x, obstacle_y = obstacle_draw_pos(obstacle)
                norm_x = (player.x - obstacle_x) / radius_x
                norm_y = (hit_cy - obstacle_y) / radius_y
                if norm_x * norm_x + norm_y * norm_y < 1.0:
                    if player.invuln_timer <= 0.0:
                        player.hearts = max(0, player.hearts - obstacle.damage)
                        player.invuln_timer = PLAYER_IFRAME
                        player.stun_timer = HIT_STUN_TIME
                        player.flash_timer = 0.0
                        # 受伤贴图朝向来袭障碍物：障碍在右侧 -> 朝右，反之朝左。
                        # 风从左往右吹时障碍物从左侧撞来，因此自然得到“朝左”贴图。
                        dx = obstacle_x - player.x
                        if dx > HIT_FACE_DEAD_ZONE:
                            player.hurt_face_x = 1.0
                        elif dx < -HIT_FACE_DEAD_ZONE:
                            player.hurt_face_x = -1.0
                        else:
                            player.hurt_face_x = 0.0
                        # 造成伤害的障碍物立即消失（无敌帧内命中不掉心，因此也不消失）。
                        obstacle.dead = True
                        self.events.append(EVENT_HIT)
                    break
            # 移出模拟列表；渲染层下一次 sync 会对账删除 Canvas item。
            # 扩展点（消失动画）：以后可在这里给 dead 障碍物记录命中时刻/命中点，
            # 或改为保留一个 dying_timer，让 ObstacleLayer 播放完动画后再删除；
            # 模型规则仍以“dead 即不再参与碰撞/移动”为准。
            if any(obstacle.dead for obstacle in self.obstacles):
                self.obstacles = [obstacle for obstacle in self.obstacles if not obstacle.dead]

        # 9. 结束判定：心数优先于越界
        if player.hearts <= 0:
            self._finish(REASON_HEARTS_ZERO)
            return
        if not self.god_mode:
            if (
                player.x < 0.0
                or player.x > WINDOW_WIDTH
                or player.y < 0.0
                or player.y > WINDOW_HEIGHT
            ):
                self._finish(REASON_BLOWN_AWAY)

    # ------------------------------------------------------------ 风向调度
    def _change_wind(self, reverse=False):
        """自然换风 / F2 随机换风 / F4 反向风：重采样时长并触发全部障碍转向。"""
        if reverse:
            new_index = (self.wind.index + len(WIND_DIRECTIONS) // 2) % len(WIND_DIRECTIONS)
        else:
            candidates = [i for i in range(len(WIND_DIRECTIONS)) if i != self.wind.index]
            new_index = self.rng.choice(candidates)
        self._apply_wind(new_index)

    def _apply_wind(self, new_index):
        name, ux, uy = WIND_DIRECTIONS[new_index]
        duration = sample_duration(self.elapsed, self.rng)
        self.wind.index = new_index
        self.wind.ux = ux
        self.wind.uy = uy
        self.wind.duration = duration
        self.wind.remaining = duration
        self.wind.change_count += 1
        self.wind_history.append(duration)
        if len(self.wind_history) > WIND_HISTORY_LEN:
            self.wind_history.pop(0)
        for obstacle in self.obstacles:
            begin_turn(obstacle, ux, uy)
        self.events.append(EVENT_WIND_CHANGE)

    # ------------------------------------------------------------ 生成/结算
    def _sample_tier(self):
        roll = self.rng.random()
        accumulated = 0.0
        for tier in OBSTACLE_TIERS:
            accumulated += tier["weight"]
            if roll <= accumulated:
                return tier
        return OBSTACLE_TIERS[-1]

    def _spawn_obstacle(self):
        tier = self._sample_tier()
        ux, uy = self.wind.ux, self.wind.uy
        x, y = spawn_point(tier["radius"], ux, uy, self.rng)
        # A2：每颗障碍物速度 ±8%，用独立随机流（oid 派生），不动生成/风向序列。
        motion_rng = obstacle_motion_rng(self.obstacle_seq)
        speed = tier["speed"] * (1.0 + motion_rng.uniform(-OBSTACLE_SPEED_JITTER, OBSTACLE_SPEED_JITTER))
        obstacle = Obstacle(
            oid=self.obstacle_seq,
            x=x,
            y=y,
            vx=ux * speed,
            vy=uy * speed,
            radius=tier["radius"],
            tier=tier["tier"],
            damage=tier["damage"],
            base_speed=speed,
        )
        configure_obstacle_motion(obstacle, motion_rng)
        self.obstacle_seq += 1
        self.obstacles.append(obstacle)
        return obstacle

    def _finish(self, reason):
        if self.state == STATE_RUNNING:
            self.state = STATE_GAME_OVER
            self.reason = reason
            self.events.append(EVENT_GAME_OVER)

    # -------------------------------------------------------------- 调试钩子
    def debug_force_wind(self, reverse=False):
        if self.state != STATE_RUNNING:
            return False
        self._change_wind(reverse=reverse)
        return True

    def debug_spawn(self):
        if self.state != STATE_RUNNING or len(self.obstacles) >= OBSTACLE_MAX_ACTIVE:
            return False
        self._spawn_obstacle()
        return True

    def debug_damage(self, amount=1):
        """F6：绕过无敌帧自伤，并立即结算（便于打通 A11/A12）；上帝模式下无效。"""
        if self.state != STATE_RUNNING or self.god_mode:
            return False
        self.player.hearts = max(0, self.player.hearts - amount)
        self.events.append(EVENT_HIT)
        if self.player.hearts <= 0:
            self._finish(REASON_HEARTS_ZERO)
        return True

    def toggle_god_mode(self):
        """G / F8：切换上帝模式。

        开启后：不受风吹、碰撞不扣心、不会被吹出窗口（仍可用输入自由移动）。
        关闭后：风推与伤害判定立即恢复；无受伤后摇，便于连续开关测试。
        """
        self.god_mode = not self.god_mode
        if self.god_mode:
            self.player.x = clamp(self.player.x, self.player.radius, WINDOW_WIDTH - self.player.radius)
            self.player.y = clamp(self.player.y, self.player.radius, WINDOW_HEIGHT - self.player.radius)
        return self.god_mode

    # 兼容旧调试入口（F8 的既有实现/自测可能仍按旧名调用）
    debug_toggle_god = toggle_god_mode


# =============================================================================
# 6. 生成与惯性（模块级纯函数，便于自测）
# =============================================================================

def spawn_sites(ux, uy):
    """返回上游生成边列表 [(edge, 参数下限, 参数上限), ...]。

    正方向风：上游整条边；
    斜向风：上游角的两条边带（每条距角 SPAWN_CORNER_BAND）。
    """
    inset = SPAWN_EDGE_INSET
    band = SPAWN_CORNER_BAND
    if abs(ux) < EPS:  # 正南/正北
        if uy > 0:
            return [(EDGE_TOP, inset, WINDOW_WIDTH - inset)]
        return [(EDGE_BOTTOM, inset, WINDOW_WIDTH - inset)]
    if abs(uy) < EPS:  # 正东/正西
        if ux > 0:
            return [(EDGE_LEFT, inset, WINDOW_HEIGHT - inset)]
        return [(EDGE_RIGHT, inset, WINDOW_HEIGHT - inset)]

    # 斜向：先定位上游角 (sx, sy)
    sx = 0.0 if ux > 0 else float(WINDOW_WIDTH)
    sy = 0.0 if uy > 0 else float(WINDOW_HEIGHT)
    h_edge = EDGE_LEFT if ux > 0 else EDGE_RIGHT
    v_edge = EDGE_TOP if uy > 0 else EDGE_BOTTOM
    # 注：§5.5 伪代码写作 [inset, inset + band]，但 A7/T8 的验收口径是
    # “距角 <= SPAWN_CORNER_BAND”。这里取 [inset, band]，两者同时满足。
    band_low = inset if inset < band else 0.0
    if sy == 0.0:
        h_lo, h_hi = band_low, band
    else:
        h_lo, h_hi = WINDOW_HEIGHT - band, WINDOW_HEIGHT - band_low
    if sx == 0.0:
        v_lo, v_hi = band_low, band
    else:
        v_lo, v_hi = WINDOW_WIDTH - band, WINDOW_WIDTH - band_low
    return [(h_edge, h_lo, h_hi), (v_edge, v_lo, v_hi)]


def edge_point(edge, param, offset):
    """把边上的参数点沿外法线外移 offset，得到窗外生成点。"""
    if edge == EDGE_LEFT:
        return -offset, param
    if edge == EDGE_RIGHT:
        return WINDOW_WIDTH + offset, param
    if edge == EDGE_TOP:
        return param, -offset
    return param, WINDOW_HEIGHT + offset


def entry_chord(px, py, ux, uy):
    """从窗外点 p 沿单位方向 u 作射线，返回在窗口矩形内的可见弦长（px）。"""
    t_enter = -math.inf
    t_exit = math.inf
    for p, u, low, high in (
        (px, ux, 0.0, float(WINDOW_WIDTH)),
        (py, uy, 0.0, float(WINDOW_HEIGHT)),
    ):
        if abs(u) < EPS:
            if p < low or p > high:
                return 0.0
            continue
        t1 = (low - p) / u
        t2 = (high - p) / u
        if t1 > t2:
            t1, t2 = t2, t1
        t_enter = max(t_enter, t1)
        t_exit = min(t_exit, t2)
    if t_exit <= t_enter:
        return 0.0
    start = max(t_enter, 0.0)
    return max(0.0, t_exit - start)


def spawn_point(radius, ux, uy, rng):
    """在风的上游、窗口外生成一个点；尽量保证入窗弦长 >= SPAWN_MIN_CHORD。"""
    sites = spawn_sites(ux, uy)
    offset = radius + SPAWN_MARGIN_EXTRA
    best_point = None
    best_chord = -1.0
    for _ in range(SPAWN_SAMPLE_ATTEMPTS):
        edge, low, high = rng.choice(sites)
        param = rng.uniform(low, high)
        x, y = edge_point(edge, param, offset)
        chord = entry_chord(x, y, ux, uy)
        if chord > best_chord:
            best_chord = chord
            best_point = (x, y)
        if chord >= SPAWN_MIN_CHORD:
            return x, y
    # 极端情况下 20 次都不足：取弦长最大的候选，避免卡死
    if best_point is not None:
        return best_point
    edge, low, high = sites[0]
    return edge_point(edge, (low + high) * 0.5, offset)


def begin_turn(obstacle, ux, uy):
    """换风时为障碍物建立最短弧 + 35% 速度下凹的转向状态。"""
    speed0 = math.hypot(obstacle.vx, obstacle.vy)
    if speed0 < EPS:
        obstacle.vx = ux * obstacle.base_speed
        obstacle.vy = uy * obstacle.base_speed
        obstacle.turn = None
        return
    angle0 = math.atan2(obstacle.vy, obstacle.vx)
    angle1 = math.atan2(uy, ux)
    delta = wrap_to_pi(angle1 - angle0)
    # 只有 180° 走“减速到 0 再反向”的直线模式；135° 仍走弧线，但时长单独缩短。
    angle_abs = abs(delta)
    brake_turn = abs(angle_abs - math.pi) < 1e-6
    tier = TIER_BY_NAME.get(obstacle.tier)
    turn_time = tier.get("turn_time", OBSTACLE_TURN_TIME) if tier else OBSTACLE_TURN_TIME
    if brake_turn:
        turn_time *= OBSTACLE_BRAKE_TURN_SCALE
    elif abs(angle_abs - 3.0 * math.pi / 4.0) < 1e-6:
        turn_time *= OBSTACLE_135_TURN_SCALE
    obstacle.turn = TurnState(
        elapsed=0.0,
        duration=turn_time,
        start_angle=angle0,
        delta_angle=delta,
        target_angle=angle0 + delta,
        start_speed=speed0,
        target_speed=obstacle.base_speed,
        brake_turn=brake_turn,
    )


def obstacle_draw_pos(obstacle):
    """障碍物实际被看到/被撞到的中心：轨迹位置 + 空中摆动偏移。"""
    return obstacle.x + obstacle.sway_x, obstacle.y + obstacle.sway_y


def obstacle_motion_rng(oid):
    """障碍物外观/运动抖动的独立随机流：由 oid 派生，不消耗游戏随机数。"""
    return random.Random((oid + 1) * 2654435761)


def configure_obstacle_motion(obstacle, rnd=None):
    """按档位给障碍物一套摆动 + 阵风参数；抖动只由 oid 派生。

    这样同一 oid 的视觉参数是确定的，不会影响生成/风向序列，也不会破坏自测。
    """
    if rnd is None:
        rnd = obstacle_motion_rng(obstacle.oid)
    profile = OBSTACLE_SWAY_PROFILES.get(obstacle.tier)
    if profile:
        obstacle.sway_perp = profile["perp"] * rnd.uniform(0.85, 1.15)
        obstacle.sway_along = profile["along"] * rnd.uniform(0.8, 1.2)
        obstacle.sway_freq = profile["freq"] * rnd.uniform(0.85, 1.15)
        obstacle.sway_phase = rnd.uniform(0.0, math.tau)
        obstacle.sway_phase2 = rnd.uniform(0.0, math.tau)
    gust = OBSTACLE_GUST_PROFILES.get(obstacle.tier)
    if gust:
        obstacle.gust_amp = gust["amp"] * rnd.uniform(0.8, 1.2)
        obstacle.gust_freq = gust["freq"] * rnd.uniform(0.85, 1.15)
        obstacle.gust_phase = rnd.uniform(0.0, math.tau)
    spin = OBSTACLE_SPIN_PROFILES.get(obstacle.tier)
    if spin:
        obstacle.spin_revs = spin["revs"] * rnd.uniform(0.85, 1.15)
        obstacle.spin_dir = 1.0 if rnd.random() < 0.5 else -1.0
        obstacle.spin_phase = rnd.random()
        obstacle.spin_wobble = spin["wobble"] * rnd.uniform(0.8, 1.2)
        obstacle.spin_wobble_freq = spin["wobble_hz"] * rnd.uniform(0.85, 1.15)
        obstacle.spin_wobble_phase = rnd.uniform(0.0, math.tau)


def obstacle_gust_factor(obstacle, t):
    """A2 阵风：1.0 附近的慢速正弦速度倍率；amp=0 时恒为 1。"""
    if obstacle.gust_amp <= 0.0 or obstacle.gust_freq <= 0.0:
        return 1.0
    return 1.0 + obstacle.gust_amp * math.sin(
        math.tau * obstacle.gust_freq * t + obstacle.gust_phase
    )


def update_obstacle_sway(obstacle, dt, scale=1.0):
    """A1：顺着当前运动方向叠加“垂直摆动 + 沿程起伏”。

    偏移按速度方向分解，换风转向时会跟着慢慢转过去，看起来像被气流甩动。
    `scale` 用于 180° 刹车转向：在速度归零、方向翻转的瞬间把摆幅收到 0，
    避免偏移方向突然镜像造成一次跳变。
    """
    if obstacle.sway_freq <= 0.0:
        obstacle.sway_x = 0.0
        obstacle.sway_y = 0.0
        return
    speed = math.hypot(obstacle.vx, obstacle.vy)
    if speed < EPS:
        # 速度恰好为 0（180° 转向中点）：摆幅已随 scale 收为 0，这里清干净。
        obstacle.sway_x = 0.0
        obstacle.sway_y = 0.0
        return
    ux, uy = obstacle.vx / speed, obstacle.vy / speed
    perp_x, perp_y = -uy, ux
    t = obstacle.age + dt
    phase = math.tau * obstacle.sway_freq * t + obstacle.sway_phase
    phase_along = math.tau * obstacle.sway_freq * 1.37 * t + obstacle.sway_phase2
    swing = obstacle.sway_perp * math.sin(phase) * scale
    bob = obstacle.sway_along * math.sin(phase_along) * scale
    obstacle.sway_x = perp_x * swing + ux * bob
    obstacle.sway_y = perp_y * swing + uy * bob


def apply_turn_bank(obstacle, turn, progress):
    """A3：转向时朝弯道外侧甩出，并沿原行进方向留下一点滞后。"""
    profile = OBSTACLE_TURN_BANK.get(obstacle.tier)
    if not profile:
        return
    speed = math.hypot(obstacle.vx, obstacle.vy)
    if speed < EPS:
        return
    envelope = math.sin(math.pi * clamp(progress, 0.0, 1.0))
    severity = min(1.0, abs(turn.delta_angle) / (math.pi / 2.0))
    amount = envelope * severity
    if amount <= 0.0:
        return
    ux, uy = obstacle.vx / speed, obstacle.vy / speed
    sign = 1.0 if turn.delta_angle >= 0.0 else -1.0
    side_x, side_y = sign * uy, -sign * ux  # 弯道外侧
    obstacle.sway_x += side_x * profile["side"] * amount - ux * profile["drag"] * amount
    obstacle.sway_y += side_y * profile["side"] * amount - uy * profile["drag"] * amount


def update_obstacle(obstacle, dt):
    """更新一个障碍物：惯性转向或匀速直线；只改模型字段，不碰 Canvas。"""
    turn = obstacle.turn
    t = obstacle.age + dt
    if turn is None:
        # A2：按 base_speed * 阵风倍率重算当前速度，方向保持不变。
        speed_now = math.hypot(obstacle.vx, obstacle.vy)
        if speed_now > EPS:
            factor = obstacle_gust_factor(obstacle, t) * obstacle.base_speed / speed_now
            obstacle.vx *= factor
            obstacle.vy *= factor
        obstacle.x += obstacle.vx * dt
        obstacle.y += obstacle.vy * dt
        update_obstacle_sway(obstacle, dt)
        return

    turn.elapsed += dt
    s = clamp(turn.elapsed / turn.duration, 0.0, 1.0)
    sway_scale = 1.0
    if turn.brake_turn:
        # 仅 180°：沿原方向匀减速到 0，再从静止沿反向匀加速。
        # 速度恰好为 0 的时刻切换方向，因此不会出现侧向位移或半圆弧。
        if s < 0.5:
            angle = turn.start_angle
            speed = turn.start_speed * (1.0 - 2.0 * s)
        else:
            angle = turn.target_angle
            speed = turn.target_speed * (2.0 * s - 1.0)
        # 摆幅在速度归零处收到 0，再随新方向展开，避免偏移镜像跳变。
        sway_scale = abs(2.0 * s - 1.0)
    else:
        eased = turn_progress(s)
        angle = turn.start_angle + turn.delta_angle * eased
        speed = lerp(turn.start_speed, turn.target_speed, eased) * (
            1.0 - OBSTACLE_TURN_SPEED_DIP * math.sin(math.pi * s)
        )
        speed *= obstacle_gust_factor(obstacle, t)
    obstacle.vx = speed * math.cos(angle)
    obstacle.vy = speed * math.sin(angle)
    obstacle.x += obstacle.vx * dt
    obstacle.y += obstacle.vy * dt
    update_obstacle_sway(obstacle, dt, sway_scale)
    if not turn.brake_turn:
        apply_turn_bank(obstacle, turn, s)
    if s >= 1.0:
        factor = obstacle_gust_factor(obstacle, t)
        obstacle.vx = turn.target_speed * factor * math.cos(turn.target_angle)
        obstacle.vy = turn.target_speed * factor * math.sin(turn.target_angle)
        obstacle.turn = None


# =============================================================================
# 8. 渲染层（只读模型、只写 Canvas；不改变规则）
# =============================================================================

def hurt_direction(hurt_face_x, fallback):
    """受伤贴图朝向：优先用来袭障碍物所在的一侧，正上/正下命中沿用最后朝向。

    障碍物在右侧（hurt_face_x > 0）播放右向；在左侧播放左向（由右向贴图
    水平镜像得到）。这样风从左往右吹、障碍物从左边撞来时，贴图自然朝左。
    """
    if hurt_face_x > 0.0:
        return "e"
    if hurt_face_x < 0.0:
        return "w"
    return fallback


class PlayerRenderer:
    """玩家渲染：左/右两基础朝向 × 待机/走路/受击帧，脚底锚点 + 软阴影。

    Tk Canvas 不能旋转图片，所以只烘左/右两张底图；运行时按水平输入选左/右，
    上下移动沿用当前贴图。idle/walk 只看玩家是否有输入——被风吹着走但没按键时，
    仍然使用静止贴图。
    贴图不可用时退回原来的几何图形。
    """

    def __init__(self, canvas, assets=None):
        self.canvas = canvas
        self.assets = assets if (assets is not None and assets.ready) else None
        self.sprite = None
        self.shadow = None
        self._frame = None
        self._shadow_photo = None
        self.facing = SpriteFacing()
        self._walk_clock = 0.0
        self._idle_clock = 0.0
        self._last_time = None
        self.body = None
        self.head = None
        self.eye_left = None
        self.eye_right = None
        self._flash = None
        if self.assets is not None:
            # 先建阴影再建角色，保证同一 y 下角色压在阴影上面
            self.shadow = canvas.create_image(0, 0, anchor="center")
            self.sprite = canvas.create_image(0, 0, anchor="s")
            return
        self.body = canvas.create_oval(
            0, 0, 0, 0, fill=COLOR_PLAYER, outline=COLOR_OUTLINE, width=OUTLINE_WIDTH
        )
        self.head = canvas.create_oval(
            0, 0, 0, 0, fill=COLOR_PLAYER, outline=COLOR_OUTLINE, width=OUTLINE_WIDTH
        )
        self.eye_left = canvas.create_oval(
            0, 0, 0, 0, fill=COLOR_EYE, outline=COLOR_EYE, width=0
        )
        self.eye_right = canvas.create_oval(
            0, 0, 0, 0, fill=COLOR_EYE, outline=COLOR_EYE, width=0
        )

    def sync(self, player):
        if self.sprite is not None:
            self._sync_sprite(player)
        else:
            self._sync_geometry(player)

    # ------------------------------------------------------------ 贴图路径
    def _sync_sprite(self, player):
        now = time.perf_counter()
        if self._last_time is None:
            dt = 0.0
        else:
            dt = min(0.1, max(0.0, now - self._last_time))
        self._last_time = now

        face_x, face_y = unit_of(player.face_x, player.face_y)
        if abs(face_x) < EPS and abs(face_y) < EPS:
            face_x, face_y = PLAYER_START_FACE_X, PLAYER_START_FACE_Y

        # 朝向只看玩家最后一次输入方向；风推不会改它。
        # 动画状态只看“玩家有没有操作”，不看实际是否位移。
        direction = self.facing.update(face_x, face_y)
        hurt = player.stun_timer > 0.0
        walking = bool(player.input_active) and not hurt
        if walking:
            self._walk_clock += dt
            self._idle_clock = 0.0
            count = max(1, self.assets.frame_count(direction, "walk"))
            fps = max(8.0, count / PLAYER_WALK_CYCLE_SECONDS) if count >= 4 else 8.0
            index = int(self._walk_clock * fps) % count
            state = "walk"
        else:
            self._walk_clock = 0.0
            self._idle_clock += dt
            count = max(1, self.assets.frame_count(direction, "idle"))
            fps = max(2.0, count / PLAYER_IDLE_CYCLE_SECONDS)
            motion = count / fps
            cycle = motion + PLAYER_IDLE_HOLD_SECONDS
            phase = self._idle_clock % cycle
            if phase >= motion:
                # 静止保持：停在动作段的第一帧（它与最后一帧几乎一致）
                index = 0
            elif PLAYER_IDLE_PINGPONG and count > 2:
                period = 2 * count - 2
                tick = int(phase * fps) % period
                index = tick if tick < count else period - tick
            else:
                index = int(phase * fps) % count
            state = "idle"

        flash = player.invuln_timer > 0.0 and player.flash_phase
        if hurt:
            hurt_dir = hurt_direction(player.hurt_face_x, direction)
            key = ("hurt", hurt_dir, flash)
            photo = self.assets.hurt_photo(flash, self.canvas, hurt_dir)
        else:
            key = (direction, state, index, flash)
            photo = self.assets.player_photo(direction, state, index, flash, self.canvas)
        if key != self._frame:
            if photo is not None:
                self.canvas.itemconfigure(self.sprite, image=photo)
            self._frame = key

        # 贴图底边中点 = 碰撞圆底部，角色因此“站”在地面上而不是浮在空中
        ground_y = player.y + player.radius * PLAYER_FEET_OFFSET_RATIO
        breath = 0.0
        idle_count = self.assets.frame_count(direction, "idle")
        if not walking and not hurt and idle_count <= 1:
            breath = math.sin(now * math.tau / PLAYER_BREATH_PERIOD) * PLAYER_BREATH_PX
        self.canvas.coords(self.sprite, player.x, ground_y + breath)
        if self.shadow is not None:
            shadow = self.assets.player_shadow_photo(self.canvas)
            if shadow is not None and shadow is not self._shadow_photo:
                self.canvas.itemconfigure(self.shadow, image=shadow)
                self._shadow_photo = shadow
            self.canvas.coords(self.shadow, player.x, ground_y - 3.0)

    # ------------------------------------------------------------ 几何回退
    def _sync_geometry(self, player):
        canvas = self.canvas
        x, y = player.x, player.y
        radius = player.radius
        canvas.coords(self.body, x - radius, y - radius, x + radius, y + radius)

        face_x, face_y = unit_of(player.face_x, player.face_y)
        if abs(face_x) < EPS and abs(face_y) < EPS:
            face_x, face_y = PLAYER_START_FACE_X, PLAYER_START_FACE_Y
        head_radius = radius * HEAD_RADIUS_RATIO
        head_x = x + face_x * radius * HEAD_OFFSET_RATIO
        head_y = y + face_y * radius * HEAD_OFFSET_RATIO
        canvas.coords(
            self.head,
            head_x - head_radius,
            head_y - head_radius,
            head_x + head_radius,
            head_y + head_radius,
        )

        forward = head_radius * EYE_FORWARD_RATIO
        side = head_radius * EYE_SIDE_RATIO
        perp_x, perp_y = -face_y, face_x
        for item, sign in ((self.eye_left, -1.0), (self.eye_right, 1.0)):
            eye_x = head_x + face_x * forward + perp_x * side * sign
            eye_y = head_y + face_y * forward + perp_y * side * sign
            canvas.coords(
                item,
                eye_x - EYE_RADIUS,
                eye_y - EYE_RADIUS,
                eye_x + EYE_RADIUS,
                eye_y + EYE_RADIUS,
            )

        flash = player.invuln_timer > 0.0 and player.flash_phase
        if flash != self._flash:
            canvas.itemconfigure(self.body, fill=COLOR_PLAYER_FLASH if flash else COLOR_PLAYER)
            self._flash = flash

    def reset(self):
        self.facing.reset()
        self._walk_clock = 0.0
        self._idle_clock = 0.0
        self._last_time = None
        self._frame = None
        self.sync(Player())


class ObstacleLayer:
    """障碍物增量对账：oid -> Canvas item id；消失即删，新增即建。

    贴图按“档位 + 变体”映射（变体由 oid 轮换，纯视觉差异），画布锚点用中心；
    贴图不可用时退回原来的圆点几何，两种 item 共用同一套增删逻辑。
    """

    def __init__(self, canvas, assets=None):
        self.canvas = canvas
        self.assets = assets if (assets is not None and assets.ready) else None
        self.items = {}
        self._sprite_oids = set()
        self._frames = {}

    def _sprite_frame(self, obstacle):
        """挑当前显示帧：小档按时间翻面，中/大档按旋转角度切帧。

        都从 oid 取相位，避免同屏障碍物动作同步。
        """
        count = OBSTACLE_SPRITE_FRAMES.get(obstacle.tier, 1)
        if count <= 1:
            return 0
        if obstacle.spin_revs > 0.0:
            # 已转过的圈数：基础转速 × 方向 × 时间 + 慢速正弦起伏（转速忽快忽慢）
            revolutions = (
                obstacle.spin_revs * obstacle.spin_dir * obstacle.age
                + obstacle.spin_phase
            )
            if obstacle.spin_wobble > 0.0 and obstacle.spin_wobble_freq > 0.0:
                # 把“转速相对起伏”积分成角度摆幅，保证瞬时转速在 ±wobble 内变化
                wobble_revs = (
                    obstacle.spin_wobble
                    * obstacle.spin_revs
                    / (math.tau * obstacle.spin_wobble_freq)
                )
                revolutions += wobble_revs * math.sin(
                    math.tau * obstacle.spin_wobble_freq * obstacle.age
                    + obstacle.spin_wobble_phase
                )
            return int(math.floor(revolutions * count)) % count
        fps = OBSTACLE_FRAME_FPS.get(obstacle.tier, 6.0)
        return int(obstacle.age * fps + obstacle.oid % count) % count

    def _sync_frame(self, obstacle, item):
        frame = self._sprite_frame(obstacle)
        if self._frames.get(obstacle.oid) == frame:
            return
        photo = self.assets.obstacle_photo(
            obstacle.tier,
            obstacle.radius,
            obstacle.oid % OBSTACLE_SPRITE_VARIANTS,
            frame,
            self.canvas,
        )
        if photo is not None:
            self.canvas.itemconfigure(item, image=photo)
            self._frames[obstacle.oid] = frame

    def sync(self, obstacles):
        canvas = self.canvas
        alive = set()
        for obstacle in obstacles:
            alive.add(obstacle.oid)
            item = self.items.get(obstacle.oid)
            if item is None:
                item = self._create_item(obstacle)
                self.items[obstacle.oid] = item
            draw_x, draw_y = obstacle_draw_pos(obstacle)
            if obstacle.oid in self._sprite_oids:
                self._sync_frame(obstacle, item)
                canvas.coords(item, draw_x, draw_y)
            else:
                radius = obstacle.radius
                canvas.coords(
                    item,
                    draw_x - radius,
                    draw_y - radius,
                    draw_x + radius,
                    draw_y + radius,
                )
        for oid in list(self.items.keys()):
            if oid not in alive:
                # 扩展点（障碍物消失动画）：目前直接删除 item。
                # 以后可在此读取 item 的最后一帧坐标，生成独立特效 item 并延时删除，
                # 不改动 GameModel 的碰撞规则；命中消失与越界回收可通过 oid 从模型层区分。
                canvas.delete(self.items.pop(oid))
                self._sprite_oids.discard(oid)
                self._frames.pop(oid, None)

    def _create_item(self, obstacle):
        canvas = self.canvas
        if self.assets is not None:
            frame = self._sprite_frame(obstacle)
            photo = self.assets.obstacle_photo(
                obstacle.tier,
                obstacle.radius,
                obstacle.oid % OBSTACLE_SPRITE_VARIANTS,
                frame,
                canvas,
            )
            if photo is not None:
                self._sprite_oids.add(obstacle.oid)
                self._frames[obstacle.oid] = frame
                return canvas.create_image(0, 0, anchor="center", image=photo)
        tier = TIER_BY_NAME.get(obstacle.tier)
        color = tier["color"] if tier else COLOR_SMALL
        return canvas.create_oval(0, 0, 0, 0, fill=color, outline=COLOR_OUTLINE, width=OUTLINE_WIDTH)

    def reset(self):
        for item in self.items.values():
            self.canvas.delete(item)
        self.items.clear()
        self._sprite_oids.clear()
        self._frames.clear()


# --- HUD 蜡笔贴图（tools/art/bake_crayon_ui.py 生成，尺寸固定，运行时不再缩放） ---
WIND_UI_CODES = ("e", "se", "s", "sw", "w", "nw", "n", "ne")
HEART_VARIANTS = ("", "_b", "_c")
CRAYON_UI_ASSETS = (
    ("pause", "pause_hover", "timer_label",
     "digit_dot", "digit_s", "compass_base", "god_badge", "hint")
    + tuple("digit_%d" % index for index in range(10))
    + tuple("compass_arrow_%s" % code for code in WIND_UI_CODES)
    + tuple("wind_%s" % code for code in WIND_UI_CODES)
    + tuple(name + suffix
            for suffix in HEART_VARIANTS
            for name in ("heart_full", "heart_empty"))
)

HUD_CRAYON_PAUSE_X = (HUD_PAUSE_X1 + HUD_PAUSE_X2) // 2
HUD_CRAYON_PAUSE_Y = (HUD_PAUSE_Y1 + HUD_PAUSE_Y2) // 2
HUD_CRAYON_HEART_X = HUD_PAUSE_X2 + HUD_PAUSE_GAP + 21
HUD_CRAYON_HEART_Y = HUD_MARGIN + 20
HUD_CRAYON_HEART_STEP = 48
HUD_CRAYON_TIMER_Y = 49
HUD_CRAYON_TIMER_GAP_LABEL = 12
HUD_CRAYON_TIMER_GAP_UNIT = 9
HUD_CRAYON_TIMER_SLOTS = 5          # 够放 999.9
HUD_CRAYON_GOD_X = HUD_MARGIN
HUD_CRAYON_GOD_Y = 104
HUD_CRAYON_COMPASS_X = WINDOW_WIDTH - HUD_MARGIN - 48
HUD_CRAYON_COMPASS_Y = 134
HUD_CRAYON_WIND_NAME_Y = 198


class HudRenderer:
    """生命、计时、风向、提示、调试 HUD、结束遮罩；文本按 10 Hz 节流。

    HUD 有两套实现，接口一致：
        crayon —— 贴 assets/sprites/ui/crayon/ 里预烘好的图（默认）
        legacy —— 缺贴图或没装 Pillow 时回退 Canvas 文字/图形
    渲染层只调 update()/set_pause_hover()/reset_view()，不关心用的是哪套。
    """

    def __init__(self, canvas, assets=None):
        self.canvas = canvas
        self.assets = assets if (assets is not None and assets.ready) else None
        self.font_small = (FONT_FAMILY, FONT_SMALL)
        self.font_normal = (FONT_FAMILY, FONT_NORMAL)
        self.font_big = (FONT_FAMILY, FONT_BIG)
        self.font_heart = (FONT_FAMILY, HEART_FONT_SIZE)

        self._crayon = bool(
            self.assets is not None and self.assets.has_ui(CRAYON_UI_ASSETS)
        )
        if self._crayon:
            self._build_crayon()
        else:
            self._build_legacy()
        self._build_overlay()

        self._last_update = 0.0
        self._hearts = None
        self._wind_index = None
        self._wind_speed = None
        self._god_active = None
        self._overlay_visible = None
        self._overlay_crayon = False
        self._overlay_reason_text = None
        self._overlay_score_text = None
        self._timer_key = None
        self._pause_hover = False

    # ------------------------------------------------------------ 蜡笔贴图版
    def _ui(self, name):
        return self.assets.ui_photo(name, self.canvas)

    def _ui_size(self, name):
        return self.assets.ui_size(name)

    def _build_crayon(self):
        c = self.canvas
        # 暂停按钮：既是可点区域，也是所有游戏层的深度锚点（必须最先生成）
        self.pause_photo = self._ui("pause")
        self.pause_hover_photo = self._ui("pause_hover")
        self.pause_bg = c.create_image(
            HUD_CRAYON_PAUSE_X,
            HUD_CRAYON_PAUSE_Y,
            anchor="center",
            image=self.pause_photo,
        )

        self.heart_items = []
        self._heart_sprites = True
        heart_photo = self._ui("heart_full")
        for index in range(PLAYER_MAX_HEARTS):
            self.heart_items.append(
                c.create_image(
                    HUD_CRAYON_HEART_X + index * HUD_CRAYON_HEART_STEP,
                    HUD_CRAYON_HEART_Y,
                    anchor="center",
                    image=heart_photo,
                )
            )

        # 计时：中文标签 + 逐位数字（固定格子保证等宽）+ 单位，整体右对齐
        self.timer_label = c.create_image(
            0, HUD_CRAYON_TIMER_Y, anchor="center", image=self._ui("timer_label"))
        self.timer_slots = [
            c.create_image(0, HUD_CRAYON_TIMER_Y, anchor="center", state="hidden")
            for _ in range(HUD_CRAYON_TIMER_SLOTS)
        ]
        self.timer_unit = c.create_image(
            0, HUD_CRAYON_TIMER_Y, anchor="center", image=self._ui("digit_s"))

        # 风向：右上角罗盘，方向只体现在指针上
        self.compass_base = c.create_image(
            HUD_CRAYON_COMPASS_X, HUD_CRAYON_COMPASS_Y,
            anchor="center", image=self._ui("compass_base"))
        self.compass_arrow = c.create_image(
            HUD_CRAYON_COMPASS_X, HUD_CRAYON_COMPASS_Y,
            anchor="center", image=self._ui("compass_arrow_e"))
        self.wind_name = c.create_image(
            HUD_CRAYON_COMPASS_X, HUD_CRAYON_WIND_NAME_Y,
            anchor="center", image=self._ui("wind_e"))

        # 上帝模式徽标：属于“玩家状态”，放在生命值下面
        self.god_badge = c.create_image(
            HUD_CRAYON_GOD_X, HUD_CRAYON_GOD_Y,
            anchor="w", image=self._ui("god_badge"), state="hidden")

        self.hint = c.create_image(
            WINDOW_WIDTH // 2, WINDOW_HEIGHT - HINT_Y,
            anchor="center", image=self._ui("hint"))

        self.dev_hud = c.create_text(
            DEV_HUD_MARGIN,
            WINDOW_HEIGHT - DEV_HUD_MARGIN,
            text="",
            anchor="sw",
            justify="left",
            font=self.font_small,
            fill=COLOR_DIM,
            state="hidden",
        )

    # ------------------------------------------------------------ 旧版回退
    def _build_legacy(self):
        canvas = self.canvas
        # 暂停按钮：画成“两条竖杠”，避免依赖字体里的 ⏸ 字形。
        self.pause_bg = canvas.create_rectangle(
            HUD_PAUSE_X1,
            HUD_PAUSE_Y1,
            HUD_PAUSE_X2,
            HUD_PAUSE_Y2,
            fill=COLOR_BUTTON,
            outline=COLOR_BUTTON_BORDER,
            width=OUTLINE_WIDTH,
        )
        self.pause_bar_left = canvas.create_rectangle(
            HUD_PAUSE_BAR_X,
            HUD_PAUSE_BAR_Y1,
            HUD_PAUSE_BAR_X + HUD_PAUSE_BAR_WIDTH,
            HUD_PAUSE_BAR_Y2,
            fill=COLOR_TEXT,
            outline="",
        )
        self.pause_bar_right = canvas.create_rectangle(
            HUD_PAUSE_BAR_X + HUD_PAUSE_BAR_WIDTH + HUD_PAUSE_BAR_GAP,
            HUD_PAUSE_BAR_Y1,
            HUD_PAUSE_BAR_X + HUD_PAUSE_BAR_TOTAL,
            HUD_PAUSE_BAR_Y2,
            fill=COLOR_TEXT,
            outline="",
        )

        self.heart_items = []
        self._heart_sprites = False
        heart_y = HUD_MARGIN + max(0, (HEART_FONT_SIZE - HEART_SPRITE_SIZE) // 2)
        for index in range(PLAYER_MAX_HEARTS):
            x = HUD_HEART_X + index * (HEART_FONT_SIZE + HEART_GAP)
            photo = self.assets.heart_photo(True, canvas) if self.assets is not None else None
            if photo is not None:
                self.heart_items.append(
                    canvas.create_image(x, heart_y, anchor="nw", image=photo)
                )
                self._heart_sprites = True
                continue
            self.heart_items.append(
                canvas.create_text(
                    x,
                    HUD_MARGIN,
                    text="♥",
                    anchor="nw",
                    font=self.font_heart,
                    fill=COLOR_HEART_FULL,
                )
            )
        heart_slot = HEART_SPRITE_SIZE if self._heart_sprites else HEART_FONT_SIZE
        self.heart_count = canvas.create_text(
            HUD_HEART_X
            + PLAYER_MAX_HEARTS * (heart_slot + HEART_GAP)
            + HEART_COUNT_GAP,
            HUD_MARGIN,
            text="%d/%d" % (PLAYER_START_HEARTS, PLAYER_MAX_HEARTS),
            anchor="nw",
            font=self.font_normal,
            fill=COLOR_TEXT,
        )
        self.timer = canvas.create_text(
            WINDOW_WIDTH - HUD_MARGIN,
            TIMER_Y,
            text="存活 0.0 s",
            anchor="ne",
            font=self.font_normal,
            fill=COLOR_TEXT,
        )
        # 上帝模式徽标：只在开启时显示，给玩家明确的开关反馈。
        self.god_badge = canvas.create_text(
            WINDOW_WIDTH - HUD_MARGIN,
            GOD_BADGE_Y,
            text="★ 上帝模式 · 按 G 关闭",
            anchor="ne",
            font=self.font_small,
            fill=COLOR_GOD,
            state="hidden",
        )
        self.wind_arrow = canvas.create_line(
            0, 0, 0, 0, arrow=tk.LAST, fill=COLOR_TEXT, width=WIND_ARROW_WIDTH
        )
        self.wind_text = canvas.create_text(
            WINDOW_WIDTH // 2,
            WIND_TEXT_Y,
            text="风（吹向）：东",
            anchor="n",
            font=self.font_small,
            fill=COLOR_TEXT,
        )
        self.hint = canvas.create_text(
            WINDOW_WIDTH // 2,
            WINDOW_HEIGHT - HINT_Y,
            text="WASD / 方向键移动 · 顶住风 · G 上帝模式 · M 静音 · R 重新开始",
            anchor="s",
            font=self.font_small,
            fill=COLOR_DIM,
        )
        self.dev_hud = canvas.create_text(
            DEV_HUD_MARGIN,
            WINDOW_HEIGHT - DEV_HUD_MARGIN,
            text="",
            anchor="sw",
            justify="left",
            font=self.font_small,
            fill=COLOR_DIM,
            state="hidden",
        )

    # ------------------------------------------------------------ 结束遮罩
    def _build_overlay(self):
        canvas = self.canvas
        # 结束遮罩：stipple 模拟半透明（Canvas 图形没有真正的 alpha）
        self.overlay_items = []
        self.overlay_rect = canvas.create_rectangle(
            0,
            0,
            WINDOW_WIDTH,
            WINDOW_HEIGHT,
            fill=COLOR_OVERLAY,
            outline="",
            stipple=OVERLAY_STIPPLE,
            state="hidden",
        )
        self.overlay_items.append(self.overlay_rect)
        self.overlay_title = canvas.create_text(
            WINDOW_WIDTH // 2,
            OVERLAY_TITLE_Y,
            text="游戏结束",
            anchor="center",
            font=self.font_big,
            fill=COLOR_TEXT,
            state="hidden",
        )
        self.overlay_reason = canvas.create_text(
            WINDOW_WIDTH // 2,
            OVERLAY_REASON_Y,
            text="",
            anchor="center",
            font=self.font_normal,
            fill=COLOR_TEXT,
            state="hidden",
        )
        self.overlay_score = canvas.create_text(
            WINDOW_WIDTH // 2,
            OVERLAY_SCORE_Y,
            text="",
            anchor="center",
            font=self.font_normal,
            fill=COLOR_TEXT,
            state="hidden",
        )
        self.overlay_hint = canvas.create_text(
            WINDOW_WIDTH // 2,
            OVERLAY_HINT_Y,
            text="按 R 也可重新开始",
            anchor="center",
            font=self.font_small,
            fill=COLOR_DIM,
            state="hidden",
        )
        self.overlay_items.extend(
            [self.overlay_title, self.overlay_reason, self.overlay_score, self.overlay_hint]
        )

        # 结束界面两个按钮：行为由应用层注入（HUD 只负责界面与标签绑定）。
        # 竖排：重新开始在上，退出游戏在下；标签绑定只在该层可见时生效。
        self._overlay_button_rects = {}
        self._overlay_callbacks = {}
        self.overlay_buttons = []
        center_x = WINDOW_WIDTH // 2
        for index, (tag, label, action) in enumerate(
            (
                ("overlay_btn_restart", "重新开始", "restart"),
                ("overlay_btn_quit", "退出游戏", "quit"),
            )
        ):
            y = OVERLAY_BUTTON_Y_START + index * (
                OVERLAY_BUTTON_HEIGHT + OVERLAY_BUTTON_Y_GAP
            )
            self.overlay_buttons.extend(
                self._create_overlay_button(tag, center_x, y, label, action)
            )
        self.overlay_items.extend(self.overlay_buttons)

    # ------------------------------------------------------------ 刷新
    def update(self, model, fps, input_speed, dev_visible, item_count, force=False):
        now = time.perf_counter()
        if force or (now - self._last_update) >= HUD_REFRESH_INTERVAL:
            self._last_update = now
            self._sync_timer(model.elapsed)
            self._sync_hearts(model.player.hearts)
            self._sync_wind(model.wind, model.wind_speed)
            self._sync_god(model.god_mode)
            self._sync_dev_hud(model, fps, input_speed, dev_visible, item_count)
        self._sync_overlay(model)

    def set_pause_hover(self, hovering):
        """HUD 暂停按钮悬停反馈；只换图，不接管光标（光标由暂停菜单管理）。"""
        if hovering == self._pause_hover:
            return
        self._pause_hover = hovering
        if self._crayon:
            self.canvas.itemconfigure(
                self.pause_bg,
                image=self.pause_hover_photo if hovering else self.pause_photo,
            )
            return
        self.canvas.itemconfigure(
            self.pause_bg, fill=COLOR_BUTTON_HOVER if hovering else COLOR_BUTTON
        )

    # ------------------------------------------------------------ 计时
    def _sync_timer(self, elapsed):
        text = "%.1f" % max(0.0, elapsed)
        if self._crayon and len(text) > HUD_CRAYON_TIMER_SLOTS:
            # 活过 1000 秒就退化成整数，避免超出数字槽位
            text = "%.0f" % elapsed
        if text == self._timer_key:
            return
        self._timer_key = text
        if not self._crayon:
            self.canvas.itemconfigure(self.timer, text="存活 %.1f s" % elapsed)
            return
        self._layout_timer(text)

    def _layout_timer(self, text):
        """右对齐排布：标签 + 逐位数字 + 单位；数字格子等宽，秒数跳动时不抖。"""
        label_w, _label_h = self._ui_size("timer_label")
        unit_w, _unit_h = self._ui_size("digit_s")
        glyphs = []
        for char in text:
            name = "digit_dot" if char == "." else "digit_%d" % int(char)
            glyphs.append((name, self._ui_size(name)[0]))

        total = label_w + HUD_CRAYON_TIMER_GAP_LABEL
        total += sum(width for _name, width in glyphs)
        total += HUD_CRAYON_TIMER_GAP_UNIT + unit_w

        x = WINDOW_WIDTH - HUD_MARGIN - total
        y = HUD_CRAYON_TIMER_Y
        self.canvas.coords(self.timer_label, x + label_w / 2.0, y)
        x += label_w + HUD_CRAYON_TIMER_GAP_LABEL
        for index, item in enumerate(self.timer_slots):
            if index < len(glyphs):
                name, width = glyphs[index]
                self.canvas.itemconfigure(
                    item, image=self._ui(name), state="normal")
                self.canvas.coords(item, x + width / 2.0, y)
                x += width
            else:
                self.canvas.itemconfigure(item, state="hidden")
        x += HUD_CRAYON_TIMER_GAP_UNIT
        self.canvas.coords(self.timer_unit, x + unit_w / 2.0, y)

    # ------------------------------------------------------------ 生命
    def _sync_hearts(self, hearts):
        if hearts == self._hearts:
            return
        self._hearts = hearts
        for index, item in enumerate(self.heart_items):
            full = index < hearts
            if self._crayon:
                suffix = HEART_VARIANTS[min(index, len(HEART_VARIANTS) - 1)]
                self.canvas.itemconfigure(
                    item,
                    image=self._ui(("heart_full" if full else "heart_empty") + suffix))
                continue
            if self._heart_sprites and self.assets is not None:
                photo = self.assets.heart_photo(full, self.canvas)
                if photo is not None:
                    self.canvas.itemconfigure(item, image=photo)
                    continue
            color = COLOR_HEART_FULL if full else COLOR_HEART_EMPTY
            self.canvas.itemconfigure(item, fill=color)
        if not self._crayon:
            self.canvas.itemconfigure(
                self.heart_count, text="%d/%d" % (hearts, PLAYER_MAX_HEARTS)
            )

    # ------------------------------------------------------------ 风向
    def _sync_wind(self, wind, speed):
        if wind.index == self._wind_index and speed == self._wind_speed:
            return
        self._wind_index = wind.index
        self._wind_speed = speed
        if self._crayon:
            code = WIND_UI_CODES[wind.index % len(WIND_UI_CODES)]
            self.canvas.itemconfigure(
                self.compass_arrow, image=self._ui("compass_arrow_%s" % code))
            self.canvas.itemconfigure(
                self.wind_name, image=self._ui("wind_%s" % code))
            return
        center_x = WINDOW_WIDTH // 2
        center_y = WIND_ARROW_Y
        half = WIND_ARROW_LEN * 0.5
        self.canvas.coords(
            self.wind_arrow,
            center_x - wind.ux * half,
            center_y - wind.uy * half,
            center_x + wind.ux * half,
            center_y + wind.uy * half,
        )
        self.canvas.itemconfigure(
            self.wind_text,
            text="风（吹向）：%s  风速 %.0f" % (wind.name(), speed),
        )

    # ------------------------------------------------------------ 上帝模式
    def _sync_god(self, active):
        if active == self._god_active:
            return
        self._god_active = active
        self.canvas.itemconfigure(self.god_badge, state="normal" if active else "hidden")

    def _sync_dev_hud(self, model, fps, input_speed, dev_visible, item_count):
        state = "normal" if dev_visible else "hidden"
        self.canvas.itemconfigure(self.dev_hud, state=state)
        if not dev_visible:
            return
        history = ", ".join("%.1f" % value for value in model.wind_history) or "-"
        text = "\n".join(
            (
                "FPS %.0f  输入 %.0f px/s  items %d  障碍 %d"
                % (fps, input_speed, item_count, len(model.obstacles)),
                "生成间隔 %.2fs  风 %s (%.2f, %.2f)  风速 %.0f（第 %d 档）  剩余 %.1fs"
                % (
                    spawn_interval(model.elapsed),
                    model.wind.name(),
                    model.wind.ux,
                    model.wind.uy,
                    model.wind_speed,
                    model.wind_speed_tier,
                    model.wind.remaining,
                ),
                "最近时长 [%s]  换风 %d" % (history, model.wind.change_count),
            )
        )
        self.canvas.itemconfigure(self.dev_hud, text=text)

    # ------------------------------------------------------------ 结束遮罩
    def set_overlay_crayon(self, enabled):
        """由蜡笔版结束遮罩接管时，HUD 不再绘制旧的点阵遮罩。

        接管是永久的：旧遮罩的 Canvas 元素直接删除，而不是只置隐藏。
        否则一旦某次 reset_view() 把接管标记清掉，旧遮罩会重新画出来，
        和蜡笔遮罩叠成两层（旧 UI 先出现、新界面盖在上面）。
        """
        self._overlay_crayon = bool(enabled)
        if not enabled:
            return
        self._hide_overlay()
        for item in self.overlay_items:
            self.canvas.delete(item)
        self.overlay_items = []
        self.overlay_buttons = []
        self._overlay_button_rects = {}
        self._overlay_visible = False

    def set_game_over_callbacks(self, on_restart, on_quit):
        """注入结束界面按钮行为；回调由应用层决定重开/退出。"""
        self._overlay_callbacks = {"restart": on_restart, "quit": on_quit}

    def _create_overlay_button(self, tag, center_x, center_y, label, action):
        half_width = OVERLAY_BUTTON_WIDTH // 2
        half_height = OVERLAY_BUTTON_HEIGHT // 2
        rect = self.canvas.create_rectangle(
            center_x - half_width,
            center_y - half_height,
            center_x + half_width,
            center_y + half_height,
            fill=COLOR_BUTTON,
            outline=COLOR_BUTTON_BORDER,
            width=OUTLINE_WIDTH,
            tags=(tag,),
            state="hidden",
        )
        text = self.canvas.create_text(
            center_x,
            center_y,
            text=label,
            anchor="center",
            font=self.font_normal,
            fill=COLOR_TEXT,
            tags=(tag,),
            state="hidden",
        )
        self._overlay_button_rects[tag] = rect
        self.canvas.tag_bind(
            tag, "<Button-1>", lambda event, a=action: self._activate_overlay_button(a)
        )
        self.canvas.tag_bind(
            tag, "<Enter>", lambda event, t=tag: self._set_overlay_hover(t, True)
        )
        self.canvas.tag_bind(
            tag, "<Leave>", lambda event, t=tag: self._set_overlay_hover(t, False)
        )
        return [rect, text]

    def _activate_overlay_button(self, action):
        if not self._overlay_visible:
            return
        callback = self._overlay_callbacks.get(action)
        if callback is not None:
            callback()

    def _set_overlay_hover(self, tag, hovering):
        rect = self._overlay_button_rects.get(tag)
        if rect is None:
            return
        self.canvas.itemconfigure(
            rect, fill=COLOR_BUTTON_HOVER if hovering else COLOR_BUTTON
        )
        try:
            self.canvas.configure(cursor="hand2" if hovering else "")
        except Exception:
            pass

    def _hide_overlay(self):
        for item in self.overlay_items:
            self.canvas.itemconfigure(item, state="hidden")
        for rect in self._overlay_button_rects.values():
            self.canvas.itemconfigure(rect, fill=COLOR_BUTTON)
        try:
            self.canvas.configure(cursor="")
        except Exception:
            pass

    def _sync_overlay(self, model):
        if self._overlay_crayon:
            return
        visible = model.state == STATE_GAME_OVER
        if visible != self._overlay_visible:
            self._overlay_visible = visible
            if visible:
                for item in self.overlay_items:
                    self.canvas.itemconfigure(item, state="normal")
            else:
                self._hide_overlay()
        if not visible:
            return
        reason_text = REASON_TEXT.get(model.reason, "")
        score_text = "本局得分：%.1f 秒" % model.elapsed
        if reason_text != self._overlay_reason_text:
            self._overlay_reason_text = reason_text
            self.canvas.itemconfigure(self.overlay_reason, text=reason_text)
        if score_text != self._overlay_score_text:
            self._overlay_score_text = score_text
            self.canvas.itemconfigure(self.overlay_score, text=score_text)

    def reset_view(self):
        self._last_update = 0.0
        self._hearts = None
        self._wind_index = None
        self._wind_speed = None
        self._god_active = None
        self._overlay_visible = False
        # 不重置 _overlay_crayon：它表示“结束遮罩由谁接管”，是应用层启动时
        # 定下的模式，不属于单局状态；清掉会让旧点阵遮罩在重开后重新出现。
        self._overlay_reason_text = None
        self._overlay_score_text = None
        self._timer_key = None
        self._hide_overlay()
        self.canvas.itemconfigure(self.dev_hud, state="hidden")
        self.canvas.itemconfigure(self.god_badge, state="hidden")
        if self._crayon:
            for item in self.timer_slots:
                self.canvas.itemconfigure(item, state="hidden")


class ScaledCanvas:
    """把绘制坐标乘以 UI_SCALE 的 Canvas 代理。

    模型、物理、布局仍然全部用 1200×840 逻辑坐标；只有真正落到画布上的
    坐标被放大，这样“把游戏放大”不需要改任何游戏逻辑。
    """

    __slots__ = ("_canvas", "scale")

    def __init__(self, canvas, scale):
        self._canvas = canvas
        self.scale = scale

    @property
    def widget(self):
        """底层真实的 Tk widget。

        本代理不是 widget：tkinter 的 filedialog / messagebox 会把 parent
        直接交给 Tcl，非 widget 会被转成 repr 字符串，报
        “bad window path name "<__main__.ScaledCanvas object at 0x...>"”。
        凡是需要 parent= 的地方一律传 self.canvas.widget。
        """
        return self._canvas

    def __getattr__(self, name):
        return getattr(self._canvas, name)

    def _scaled(self, values):
        scale = self.scale
        return tuple(v * scale for v in values)

    @staticmethod
    def _kwargs(kwargs, scale):
        width = kwargs.get("width")
        if isinstance(width, (int, float)):
            kwargs["width"] = max(1, int(round(width * scale)))
        return kwargs

    def create_text(self, x, y, **kwargs):
        return self._canvas.create_text(*self._scaled((x, y)), **kwargs)

    def create_image(self, x, y, **kwargs):
        return self._canvas.create_image(*self._scaled((x, y)), **kwargs)

    def create_oval(self, *coords, **kwargs):
        return self._canvas.create_oval(
            *self._scaled(coords), **self._kwargs(kwargs, self.scale))

    def create_rectangle(self, *coords, **kwargs):
        return self._canvas.create_rectangle(
            *self._scaled(coords), **self._kwargs(kwargs, self.scale))

    def create_line(self, *coords, **kwargs):
        return self._canvas.create_line(
            *self._scaled(coords), **self._kwargs(kwargs, self.scale))

    def create_polygon(self, *coords, **kwargs):
        return self._canvas.create_polygon(
            *self._scaled(coords), **self._kwargs(kwargs, self.scale))

    def create_window(self, x, y, **kwargs):
        return self._canvas.create_window(*self._scaled((x, y)), **kwargs)

    def coords(self, item, *values):
        if values:
            return self._canvas.coords(item, *self._scaled(values))
        return self._canvas.coords(item)


UI_SCALE = 1.0


def ui_scale():
    """界面放大系数：默认跟随显示器缩放，可用 DAFENGCHUI_UI_SCALE 覆盖。

    放大之后必须以物理像素渲染（DPI 感知），否则 Tk 会再缩一次，等于双重放大。
    """
    override = os.environ.get("DAFENGCHUI_UI_SCALE")
    if override:
        try:
            value = float(override)
            if 0.5 <= value <= 3.0:
                return value
        except ValueError:
            pass
    if sys.platform != "win32":
        return 1.0
    try:
        return max(1.0, min(3.0, display_scale()))
    except Exception:
        return 1.0


def px(value):
    """逻辑像素 → 画布物理像素（供不能走 ScaledCanvas 的 Tk widget 布局）。"""
    if UI_SCALE == 1.0:
        return int(round(value))
    return int(round(value * UI_SCALE))


def prepare_ui_scale():
    """创建 Tk 之前确定放大系数；需要放大时开启 DPI 感知。"""
    global UI_SCALE
    UI_SCALE = ui_scale()
    if UI_SCALE > 1.0:
        enable_dpi_awareness()
    try:
        import assets as assets_module

        assets_module.set_ui_scale(UI_SCALE)
    except Exception:
        pass
    return UI_SCALE


class Renderer:
    """渲染总装：地面背景（或旧版云/暗角）+ 玩家 + 障碍物 + HUD。"""

    def __init__(self, canvas, assets=None):
        self.canvas = canvas
        self.assets = assets
        canvas.configure(bg=COLOR_BG, highlightthickness=0)
        self.background = None
        self._background_photo = None
        self.background_choice = background_profile.DEFAULT_ID
        # 旧版纯色背景的暗角与云：默认走地面贴图时它们全程隐藏，
        # 所以推迟到真的回退纯色背景时再建（省启动时 ~0.1 秒）。
        self.vignette = None
        self.clouds = []
        self._cloud_photos = []
        self._fallback_ready = False
        self.pending_preload = []
        self.player = PlayerRenderer(canvas, assets)
        self.obstacles = ObstacleLayer(canvas, assets)
        self.hud = HudRenderer(canvas, assets)
        self.preload()
        self.apply_background(background_profile.load_choice())

    def apply_background(self, choice):
        """切换地面背景；失败时回退到旧的纯色 + 云 + 暗角。"""
        choice = background_profile.normalize(choice)
        photo = None
        if self.assets is not None and self.assets.ready:
            photo = self.assets.photo(
                background_profile.asset_name(choice),
                (WINDOW_WIDTH, WINDOW_HEIGHT),
                self.canvas,
            )
        if photo is None:
            if self.background is not None:
                try:
                    self.canvas.delete(self.background)
                except Exception:
                    pass
                self.background = None
            self._background_photo = None
            self.background_choice = choice
            self._ensure_backdrop_fallback()
            self._apply_background_visibility()
            return False
        if self.background is None:
            self.background = self.canvas.create_image(
                0, 0, anchor="nw", image=photo)
        else:
            self.canvas.itemconfigure(self.background, image=photo)
        self._background_photo = photo
        self.background_choice = choice
        self.canvas.tag_lower(self.background)
        self._apply_background_visibility()
        return True

    def _ensure_backdrop_fallback(self):
        """建旧版的云与暗角（只在背景贴图不可用时才需要）。

        每建一个就 tag_lower 到最底层：暗角最后建，压在云之下、游戏层之下。
        """
        if self._fallback_ready:
            return
        self._fallback_ready = True
        assets = self.assets
        for index, (x1, y1, x2, y2) in enumerate(CLOUD_SHAPES):
            size = (x2 - x1, y2 - y1)
            photo = (
                assets.cloud_photo(index, size, self.canvas)
                if assets is not None and assets.ready
                else None
            )
            if photo is not None:
                self._cloud_photos.append(photo)
                item = self.canvas.create_image(
                    (x1 + x2) // 2, (y1 + y2) // 2, anchor="center", image=photo
                )
            else:
                item = self.canvas.create_oval(
                    x1, y1, x2, y2, fill=COLOR_CLOUD, outline=""
                )
            self.clouds.append(item)
            self.canvas.tag_lower(item)
        if VIGNETTE_ENABLED and assets is not None and assets.ready:
            photo = assets.vignette_photo(self.canvas)
            if photo is not None:
                self.vignette = self.canvas.create_image(
                    0, 0, anchor="nw", image=photo)
                self.canvas.tag_lower(self.vignette)

    def _apply_background_visibility(self):
        """使用地面图时隐藏旧版的云和暗角，避免叠在亮背景上。"""
        active = self.background is not None
        if self.vignette is not None:
            self.canvas.itemconfigure(
                self.vignette, state="hidden" if active else "normal")
        for item in self.clouds:
            self.canvas.itemconfigure(
                item, state="hidden" if active else "normal")

    def preload(self):
        """先把马上要用的贴图转成 PhotoImage，剩下的重活交给主循环分帧补。"""
        self.pending_preload = []
        if self.assets is None or not self.assets.ready:
            return
        self.pending_preload = self.assets.preload(
            self.canvas,
            cloud_sizes=[(x2 - x1, y2 - y1) for x1, y1, x2, y2 in CLOUD_SHAPES],
            obstacle_sizes=[(tier["tier"], tier["radius"]) for tier in OBSTACLE_TIERS],
        )

    def load_more_assets(self, budget):
        """分帧补预加载（按时间预算）；返回 True 表示还有剩余。"""
        if not self.pending_preload or self.assets is None:
            return False
        self.pending_preload = self.assets.load_more(
            self.pending_preload, self.canvas, budget)
        return bool(self.pending_preload)

    def render(self, model, fps, input_speed, dev_visible):
        item_count = len(self.canvas.find_all())
        self.player.sync(model.player)
        self.obstacles.sync(model.obstacles)
        self._sort_depth(model)
        self.hud.update(model, fps, input_speed, dev_visible, item_count)

    def _sort_depth(self, model):
        """按“脚底 y”重排角色与障碍，营造伪 3D 的前后遮挡。

        所有游戏层 item 都压到 HUD 层之下（HUD 第一项是暂停按钮），
        因此不会出现障碍物盖住生命值/文字的问题。
        """
        if not DEPTH_SORT_ENABLED:
            return
        if self.assets is None or self.player.sprite is None:
            return
        feet = model.player.y + model.player.radius * PLAYER_FEET_OFFSET_RATIO
        entries = []
        if self.player.shadow is not None:
            entries.append((feet - 0.01, self.player.shadow))
        entries.append((feet, self.player.sprite))
        for obstacle in model.obstacles:
            item = self.obstacles.items.get(obstacle.oid)
            if item is not None:
                entries.append((obstacle.y + obstacle.radius, item))
        if len(entries) <= 2:
            return
        entries.sort(key=lambda entry: entry[0])
        anchor = self.hud.pause_bg
        for _ground, item in entries:
            self.canvas.tag_lower(item, anchor)

    def reset_view(self):
        self.obstacles.reset()
        self.player.reset()
        self.hud.reset_view()


# =============================================================================
# 9. 应用层：窗口、输入、主循环、事件消费、调试键
# =============================================================================

KEY_MAP = {
    "w": "up",
    "W": "up",
    "Up": "up",
    "s": "down",
    "S": "down",
    "Down": "down",
    "a": "left",
    "A": "left",
    "Left": "left",
    "d": "right",
    "D": "right",
    "Right": "right",
}

DEBUG_KEYS = (
    DEV_KEY_WIND,
    DEV_KEY_HUD,
    DEV_KEY_REVERSE,
    DEV_KEY_DAMAGE,
    DEV_KEY_SPAWN,
    DEV_KEY_GOD,
)


# --- 暂停遮罩（蜡笔版）布局：整屏合成，按钮是白描边镂空长条 ---
CRAYON_PAUSE_TITLE_Y = 252
CRAYON_PAUSE_HINT_Y = 736
CRAYON_PAUSE_BUTTON_SIZE = (420, 74)
CRAYON_PAUSE_BUTTON_X = (WINDOW_WIDTH - CRAYON_PAUSE_BUTTON_SIZE[0]) // 2
CRAYON_PAUSE_BUTTON_YS = (378, 468, 558, 648)
CRAYON_PAUSE_SINGLE_Y = 560
CRAYON_PAUSE_SETTINGS_HINT_Y = 640
CRAYON_PAUSE_RADIUS = 16
CRAYON_PAUSE_FRAME_WIDTH = 3
CRAYON_PAUSE_FRAME = (255, 255, 255, 230)
CRAYON_PAUSE_FRAME_HOVER = (255, 255, 255, 255)
CRAYON_PAUSE_WASH = (255, 255, 255, 38)
CRAYON_PAUSE_VEIL = (16, 38, 28, 112)
CRAYON_PAUSE_BLUR = 16
CRAYON_PAUSE_ASSETS = (
    "pause_title", "settings_title", "pause_hint", "settings_hint",
    "btn_continue", "btn_restart", "btn_settings", "btn_quit", "btn_back",
)

# --- 结束遮罩（蜡笔版）：同一套整屏合成，标题 + 死因 + 得分 + 两个按钮 ---
CRAYON_OVER_TITLE_Y = 250
CRAYON_OVER_REASON_Y = 320
CRAYON_OVER_SCORE_Y = 378
CRAYON_OVER_BUTTON_YS = (476, 566)
CRAYON_OVER_HINT_Y = 664
CRAYON_OVER_REASON_ASSETS = {
    REASON_BLOWN_AWAY: "over_reason_blown",
    REASON_HEARTS_ZERO: "over_reason_hearts",
}
CRAYON_OVER_ASSETS = (
    "over_title", "over_reason_blown", "over_reason_hearts",
    "score_label", "score_unit", "over_hint",
    "btn_restart", "btn_quit",
) + tuple("digit_%d" % i for i in range(10)) + ("digit_dot",)

# --- 开始界面（蜡笔版）：和暂停/结束同一套整屏合成，但按钮更短更高 ---
CRAYON_START_TITLE_Y = 274
CRAYON_START_HINT_Y = 790
CRAYON_START_BUTTON_SIZE = (344, 96)
CRAYON_START_BUTTON_X = (WINDOW_WIDTH - CRAYON_START_BUTTON_SIZE[0]) // 2
CRAYON_START_BUTTON_YS = (456, 572, 688)
CRAYON_START_RADIUS = 20
CRAYON_START_FRAME_WIDTH = 3
CRAYON_START_FRAME = (255, 255, 255, 230)
CRAYON_START_FRAME_HOVER = (255, 255, 255, 255)
# 亮封面上纯白淡洗几乎看不见，hover 改用暖纸色
CRAYON_START_WASH = (255, 246, 224, 82)
# 封面很亮，字和框要压一层同色系薄雾才站得住；是暂停遮罩的一半浓度
CRAYON_START_VEIL = (16, 38, 28, 58)
CRAYON_START_HOVER_LIFT = 2
CRAYON_START_ASSETS = (
    "start_title", "start_hint",
    "start_btn_start", "start_btn_custom", "start_btn_quit",
)



class CrayonPauseView:
    """暂停/设置遮罩（蜡笔版）：抓一帧画面 → 模糊压暗 → 整屏合成一张图。

    为什么必须整屏合成：Canvas 图片的透明像素只透出画布底色，不透出下方
    item，"模糊背景上浮着镂空按钮"没法分层画。合成好之后 Canvas 只贴一张图，
    hover 时重画这一张（约 10ms，只在鼠标进出按钮时发生）。

    命中测试是纯坐标矩形，由应用层把 <Motion>/<Button-1> 转发进来。
    """

    def __init__(self, canvas, assets, on_continue, on_restart, on_settings,
                 on_quit, on_back):
        self.canvas = canvas
        self.assets = assets
        self.scale = UI_SCALE
        self._callbacks = {
            "continue": on_continue,
            "restart": on_restart,
            "settings": on_settings,
            "quit": on_quit,
            "back": on_back,
        }
        self.mode = None
        self.visible = False
        self._backdrop = None
        self._photo = None
        self._hit_rects = []
        self._hover = None
        self._menu_buttons = (
            ("continue", "btn_continue", CRAYON_PAUSE_BUTTON_YS[0]),
            ("restart", "btn_restart", CRAYON_PAUSE_BUTTON_YS[1]),
            ("settings", "btn_settings", CRAYON_PAUSE_BUTTON_YS[2]),
            ("quit", "btn_quit", CRAYON_PAUSE_BUTTON_YS[3]),
        )

        self.image_item = canvas.create_image(0, 0, anchor="nw", state="hidden")
        self.countdown_number = canvas.create_image(
            WINDOW_WIDTH // 2,
            PAUSE_COUNTDOWN_Y,
            anchor="center",
            state="hidden",
        )
        self.countdown_suffix = canvas.create_image(
            WINDOW_WIDTH // 2,
            PAUSE_COUNTDOWN_SUFFIX_Y,
            anchor="center",
            image=assets.ui_photo("countdown_suffix", canvas) if assets else None,
            state="hidden",
        )

    # ------------------------------------------------------------ 对外接口
    def show_menu(self):
        self._ensure_backdrop()
        self.mode = "menu"
        self.visible = True
        self._hover = None
        self._hide_countdown()
        self._render()

    def show_settings(self):
        self._ensure_backdrop()
        self.mode = "settings"
        self.visible = True
        self._hover = None
        self._hide_countdown()
        self._render()

    def show_countdown(self, seconds):
        self._ensure_backdrop()
        self.mode = "countdown"
        self.visible = True
        self._hit_rects = []
        self._render()
        photo = self.assets.ui_photo(
            "countdown_%d" % min(3, max(1, seconds)), self.canvas)
        self.canvas.itemconfigure(self.countdown_number, image=photo, state="normal")
        self.canvas.itemconfigure(self.countdown_suffix, state="normal")

    def hide(self):
        self.mode = None
        self.visible = False
        self._hover = None
        self._hit_rects = []
        self._backdrop = None
        self._hide_countdown()
        self.canvas.itemconfigure(self.image_item, state="hidden")

    def on_motion(self, x, y):
        """返回 True 表示这次移动被暂停界面消费掉了。"""
        if not self.visible or self.mode == "countdown":
            return False
        key = self._hit_test(x, y)
        if key != self._hover:
            self._hover = key
            self._render()
        return True

    def on_click(self, x, y):
        if not self.visible or self.mode == "countdown":
            return False
        key = self._hit_test(x, y)
        if key is None:
            return False
        callback = self._callbacks.get(key)
        if callback is not None:
            callback()
        return True

    # ------------------------------------------------------------ 内部
    def _hide_countdown(self):
        self.canvas.itemconfigure(self.countdown_number, state="hidden")
        self.canvas.itemconfigure(self.countdown_suffix, state="hidden")

    def _hit_test(self, x, y):
        for key, (x1, y1, x2, y2) in self._hit_rects:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return key
        return None

    def _ensure_backdrop(self):
        """一次暂停只抓一次图：设置页/倒计时都复用同一张底。"""
        if self._backdrop is not None:
            return
        self._backdrop = self._capture()
        if self._backdrop is None:
            from PIL import Image
            self._backdrop = Image.new(
                "RGBA", (WINDOW_WIDTH, WINDOW_HEIGHT), CRAYON_PAUSE_VEIL)

    def _capture(self):
        try:
            from PIL import Image, ImageFilter, ImageGrab
        except Exception:
            return None
        self.canvas.update_idletasks()
        # 不感知 DPI 时 Tk 报逻辑坐标、抓屏要物理像素，必须按缩放系数换算
        # （实测 150% 缩放下直接抓会整体偏移约 (x*0.5, y*0.5)）。
        scale = 1.0 if process_dpi_awareness() else display_scale()
        left = int(round(self.canvas.winfo_rootx() * scale))
        top = int(round(self.canvas.winfo_rooty() * scale))
        width = int(round(WINDOW_WIDTH * self.scale))
        height = int(round(WINDOW_HEIGHT * self.scale))
        try:
            shot = ImageGrab.grab(
                bbox=(left, top, left + width, top + height))
        except Exception:
            return None
        if shot.size != (width, height):
            try:
                shot = shot.resize((width, height))
            except Exception:
                return None
        blurred = shot.convert("RGB").filter(
            ImageFilter.GaussianBlur(CRAYON_PAUSE_BLUR))
        veil = Image.new("RGBA", blurred.size, CRAYON_PAUSE_VEIL)
        return Image.alpha_composite(blurred.convert("RGBA"), veil)

    def _layout(self):
        if self.mode == "settings":
            return (
                "settings_title",
                "settings_hint",
                (("back", "btn_back", CRAYON_PAUSE_SINGLE_Y),),
                CRAYON_PAUSE_SETTINGS_HINT_Y,
            )
        return ("pause_title", "pause_hint", self._menu_buttons, CRAYON_PAUSE_HINT_Y)

    def _render(self):
        from PIL import Image, ImageDraw, ImageTk

        base = self._backdrop
        if base is None:
            return
        if self.mode == "countdown":
            self._hit_rects = []
            self._photo = ImageTk.PhotoImage(base, master=self.canvas)
            self.canvas.itemconfigure(self.image_item, image=self._photo, state="normal")
            return

        title, hint, buttons, hint_y = self._layout()
        scale = self.scale
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        half_height = CRAYON_PAUSE_BUTTON_SIZE[1] // 2
        rects = []
        for key, _asset, center_y in buttons:
            box = (
                round(CRAYON_PAUSE_BUTTON_X * scale),
                round((center_y - half_height) * scale),
                round((CRAYON_PAUSE_BUTTON_X + CRAYON_PAUSE_BUTTON_SIZE[0]) * scale),
                round((center_y + half_height) * scale),
            )
            hover = key == self._hover
            if hover:
                draw.rounded_rectangle(
                    box, radius=round(CRAYON_PAUSE_RADIUS * scale),
                    fill=CRAYON_PAUSE_WASH)
            draw.rounded_rectangle(
                box,
                radius=round(CRAYON_PAUSE_RADIUS * scale),
                outline=CRAYON_PAUSE_FRAME_HOVER if hover else CRAYON_PAUSE_FRAME,
                width=max(1, round((CRAYON_PAUSE_FRAME_WIDTH + (1 if hover else 0)) * scale)),
            )
            # 命中矩形保持逻辑坐标（事件转发前已经换算过）
            rects.append((key, (
                CRAYON_PAUSE_BUTTON_X,
                center_y - half_height,
                CRAYON_PAUSE_BUTTON_X + CRAYON_PAUSE_BUTTON_SIZE[0],
                center_y + half_height,
            )))

        composed = Image.alpha_composite(base.copy(), overlay)
        self._paste(composed, title, (WINDOW_WIDTH // 2, CRAYON_PAUSE_TITLE_Y))
        for _key, asset, center_y in buttons:
            self._paste(composed, asset, (WINDOW_WIDTH // 2, center_y))
        self._paste(composed, hint, (WINDOW_WIDTH // 2, hint_y))

        self._hit_rects = rects
        self._photo = ImageTk.PhotoImage(composed, master=self.canvas)
        self.canvas.itemconfigure(self.image_item, image=self._photo, state="normal")

    def _paste(self, base, name, center):
        if self.assets is None:
            return
        image = self.assets.ui_pixels(name, self.scale)
        if image is None:
            return
        x = int(round(center[0] * self.scale - image.width / 2))
        y = int(round(center[1] * self.scale - image.height / 2))
        base.alpha_composite(image, (x, y))


def _crayon_backdrop(canvas, scale):
    """抓一帧画布 → 高斯模糊 → 压暗；失败返回 None。"""
    try:
        from PIL import Image, ImageFilter, ImageGrab
    except Exception:
        return None
    canvas.update_idletasks()
    # 不感知 DPI 时 Tk 报逻辑坐标、抓屏要物理像素，必须按缩放系数换算。
    shot_scale = 1.0 if process_dpi_awareness() else display_scale()
    left = int(round(canvas.winfo_rootx() * shot_scale))
    top = int(round(canvas.winfo_rooty() * shot_scale))
    width = int(round(WINDOW_WIDTH * scale))
    height = int(round(WINDOW_HEIGHT * scale))
    try:
        shot = ImageGrab.grab(bbox=(left, top, left + width, top + height))
    except Exception:
        return None
    if shot.size != (width, height):
        try:
            shot = shot.resize((width, height))
        except Exception:
            return None
    blurred = shot.convert("RGB").filter(
        ImageFilter.GaussianBlur(CRAYON_PAUSE_BLUR))
    veil = Image.new("RGBA", blurred.size, CRAYON_PAUSE_VEIL)
    return Image.alpha_composite(blurred.convert("RGBA"), veil)


class CrayonGameOverView:
    """结束遮罩（蜡笔版）：模糊背景上直接浮标题、死因、得分与镂空按钮。

    与暂停遮罩同一套做法：整屏合成一张图，命中测试用坐标矩形，
    由应用层把 <Motion>/<Button-1> 转发进来。
    """

    def __init__(self, canvas, assets, on_restart, on_quit):
        self.canvas = canvas
        self.assets = assets
        self.scale = UI_SCALE
        self._callbacks = {"restart": on_restart, "quit": on_quit}
        self.visible = False
        self._backdrop = None
        self._photo = None
        self._hover = None
        self._hit_rects = []
        self._model = None
        self.image_item = canvas.create_image(
            0, 0, anchor="nw", state="hidden")
        canvas.tag_raise(self.image_item)

    # ------------------------------------------------------------ 对外接口
    def show(self, model):
        self._model = model
        if self._backdrop is None:
            self._backdrop = _crayon_backdrop(self.canvas, self.scale)
        if self._backdrop is None:
            from PIL import Image

            self._backdrop = Image.new(
                "RGBA",
                (int(round(WINDOW_WIDTH * self.scale)),
                 int(round(WINDOW_HEIGHT * self.scale))),
                CRAYON_PAUSE_VEIL,
            )
        self.visible = True
        self._hover = None
        self._render()

    def hide(self):
        self.visible = False
        self._hover = None
        self._model = None
        self._backdrop = None
        self.canvas.itemconfigure(self.image_item, state="hidden")
        try:
            self.canvas.configure(cursor="")
        except Exception:
            pass

    def on_motion(self, x, y):
        key = self._hit(x, y)
        if key != self._hover:
            self._hover = key
            self._render()

    def on_click(self, x, y):
        key = self._hit(x, y)
        if key is None:
            return False
        callback = self._callbacks.get(key)
        if callback is not None:
            callback()
        return True

    # ------------------------------------------------------------ 内部
    def _hit(self, x, y):
        for key, box in self._hit_rects:
            if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                return key
        return None

    def _buttons(self):
        return (
            ("restart", "btn_restart", CRAYON_OVER_BUTTON_YS[0]),
            ("quit", "btn_quit", CRAYON_OVER_BUTTON_YS[1]),
        )

    def _render(self):
        from PIL import Image, ImageDraw, ImageTk

        base = self._backdrop
        if base is None:
            return
        scale = self.scale
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        half_height = CRAYON_PAUSE_BUTTON_SIZE[1] // 2
        rects = []
        for key, _asset, center_y in self._buttons():
            box = (
                round(CRAYON_PAUSE_BUTTON_X * scale),
                round((center_y - half_height) * scale),
                round((CRAYON_PAUSE_BUTTON_X + CRAYON_PAUSE_BUTTON_SIZE[0]) * scale),
                round((center_y + half_height) * scale),
            )
            hover = key == self._hover
            if hover:
                draw.rounded_rectangle(
                    box, radius=round(CRAYON_PAUSE_RADIUS * scale),
                    fill=CRAYON_PAUSE_WASH)
            draw.rounded_rectangle(
                box,
                radius=round(CRAYON_PAUSE_RADIUS * scale),
                outline=CRAYON_PAUSE_FRAME_HOVER if hover else CRAYON_PAUSE_FRAME,
                width=max(1, round(
                    (CRAYON_PAUSE_FRAME_WIDTH + (1 if hover else 0)) * scale)),
            )
            # 命中矩形保持逻辑坐标（事件转发前已换算）
            rects.append((key, (
                CRAYON_PAUSE_BUTTON_X,
                center_y - half_height,
                CRAYON_PAUSE_BUTTON_X + CRAYON_PAUSE_BUTTON_SIZE[0],
                center_y + half_height,
            )))

        composed = Image.alpha_composite(base.copy(), overlay)
        self._paste(composed, "over_title",
                    (WINDOW_WIDTH // 2, CRAYON_OVER_TITLE_Y))
        model = self._model
        if model is not None:
            reason_asset = CRAYON_OVER_REASON_ASSETS.get(
                model.reason, "over_reason_hearts")
            self._paste(composed, reason_asset,
                        (WINDOW_WIDTH // 2, CRAYON_OVER_REASON_Y))
            self._paste_score(composed, model.elapsed)
        for _key, asset, center_y in self._buttons():
            self._paste(composed, asset, (WINDOW_WIDTH // 2, center_y))
        self._paste(composed, "over_hint",
                    (WINDOW_WIDTH // 2, CRAYON_OVER_HINT_Y))

        self._hit_rects = rects
        self._photo = ImageTk.PhotoImage(composed, master=self.canvas)
        self.canvas.itemconfigure(
            self.image_item, image=self._photo, state="normal")
        try:
            self.canvas.configure(cursor="hand2" if self._hover else "")
        except Exception:
            pass

    def _paste_score(self, base, elapsed):
        """本局得分 + 等宽数字 + 单位，整组居中。"""
        import assets as assets_module  # noqa: F401  (仅用于类型提示)

        label = self.assets.ui_pixels("score_label", self.scale)
        unit = self.assets.ui_pixels("score_unit", self.scale)
        text = "%.1f" % max(0.0, elapsed)
        if len(text) > HUD_CRAYON_TIMER_SLOTS:
            text = "%.0f" % max(0.0, elapsed)
        glyphs = []
        for char in text:
            name = "digit_dot" if char == "." else "digit_%s" % char
            image = self.assets.ui_pixels(name, self.scale)
            if image is not None:
                glyphs.append(image)
        gap = int(round(6 * self.scale))
        total = (
            (label.width if label is not None else 0)
            + gap
            + sum(image.width for image in glyphs)
            + gap
            + (unit.width if unit is not None else 0)
        )
        x = WINDOW_WIDTH * self.scale / 2.0 - total / 2.0
        center_y = CRAYON_OVER_SCORE_Y * self.scale
        if label is not None:
            base.alpha_composite(
                label, (int(x), int(center_y - label.height / 2.0)))
            x += label.width + gap
        for image in glyphs:
            base.alpha_composite(
                image, (int(x), int(center_y - image.height / 2.0)))
            x += image.width
        x += gap
        if unit is not None:
            base.alpha_composite(
                unit, (int(x), int(center_y - unit.height / 2.0)))

    def _paste(self, base, name, center):
        image = self.assets.ui_pixels(name, self.scale)
        if image is None:
            return
        x = int(round(center[0] * self.scale - image.width / 2.0))
        y = int(round(center[1] * self.scale - image.height / 2.0))
        base.alpha_composite(image, (x, y))


class PauseView:
    """暂停遮罩层：整屏变暗 + 面板按钮；只写 Canvas，不改模型与规则。

    视图不持有游戏状态：按钮只回调 GameApp，由应用层决定暂停/恢复/退出。
    """

    def __init__(self, canvas, on_continue, on_restart, on_settings, on_quit, on_back):
        self.canvas = canvas
        self.font_small = (FONT_FAMILY, FONT_SMALL)
        self.font_normal = (FONT_FAMILY, FONT_NORMAL)
        self.font_big = (FONT_FAMILY, FONT_BIG)
        self.font_countdown = (FONT_FAMILY, FONT_COUNTDOWN)
        self.mode = None  # None / "menu" / "settings" / "countdown"
        self._button_rects = {}

        layer = (PAUSE_LAYER_TAG,)
        center_x = WINDOW_WIDTH // 2
        self.mask = canvas.create_rectangle(
            0,
            0,
            WINDOW_WIDTH,
            WINDOW_HEIGHT,
            fill=COLOR_OVERLAY,
            outline="",
            stipple=PAUSE_MASK_STIPPLE,
            tags=layer,
        )
        self.panel = canvas.create_rectangle(
            PAUSE_PANEL_X1,
            PAUSE_PANEL_Y1,
            PAUSE_PANEL_X2,
            PAUSE_PANEL_Y2,
            fill=COLOR_PANEL,
            outline=COLOR_BUTTON_BORDER,
            width=OUTLINE_WIDTH,
            tags=layer,
        )
        self.title = canvas.create_text(
            center_x,
            PAUSE_TITLE_Y,
            text="游戏暂停",
            anchor="center",
            font=self.font_big,
            fill=COLOR_TEXT,
            tags=layer,
        )
        self.hint = canvas.create_text(
            center_x,
            PAUSE_HINT_Y,
            text="",
            anchor="center",
            font=self.font_small,
            fill=COLOR_DIM,
            tags=layer,
        )
        self.countdown_number = canvas.create_text(
            center_x,
            PAUSE_COUNTDOWN_Y,
            text="",
            anchor="center",
            font=self.font_countdown,
            fill=COLOR_GOD,
            tags=layer,
        )
        self.countdown_suffix = canvas.create_text(
            center_x,
            PAUSE_COUNTDOWN_SUFFIX_Y,
            text="秒后回到游戏",
            anchor="center",
            font=self.font_normal,
            fill=COLOR_TEXT,
            tags=layer,
        )

        self.menu_buttons = []
        for tag, y, label, callback in (
            ("pause_btn_continue", PAUSE_BUTTON_YS[0], "继续游戏", on_continue),
            ("pause_btn_restart", PAUSE_BUTTON_YS[1], "重新开始", on_restart),
            ("pause_btn_settings", PAUSE_BUTTON_YS[2], "设置", on_settings),
            ("pause_btn_quit", PAUSE_BUTTON_YS[3], "退出游戏", on_quit),
        ):
            self.menu_buttons.extend(self._create_button(tag, y, label, callback))
        self.back_button = self._create_button(
            "pause_btn_back", PAUSE_BUTTON_YS[-1], "返回", on_back
        )

        self.all_items = [
            self.mask,
            self.panel,
            self.title,
            self.hint,
            self.countdown_number,
            self.countdown_suffix,
        ] + self.menu_buttons + self.back_button
        self.hide()

    # ---------------------------------------------------------------- 内部
    def _create_button(self, tag, y, label, callback):
        center_x = WINDOW_WIDTH // 2
        rect = self.canvas.create_rectangle(
            center_x - PAUSE_BUTTON_WIDTH // 2,
            y - PAUSE_BUTTON_HEIGHT // 2,
            center_x + PAUSE_BUTTON_WIDTH // 2,
            y + PAUSE_BUTTON_HEIGHT // 2,
            fill=COLOR_BUTTON,
            outline=COLOR_BUTTON_BORDER,
            width=OUTLINE_WIDTH,
            tags=(PAUSE_LAYER_TAG, tag),
        )
        text = self.canvas.create_text(
            center_x,
            y,
            text=label,
            anchor="center",
            font=self.font_normal,
            fill=COLOR_TEXT,
            tags=(PAUSE_LAYER_TAG, tag),
        )
        self._button_rects[tag] = rect
        self.canvas.tag_bind(tag, "<Button-1>", lambda event, cb=callback: cb())
        self.canvas.tag_bind(tag, "<Enter>", lambda event, t=tag: self._set_hover(t, True))
        self.canvas.tag_bind(tag, "<Leave>", lambda event, t=tag: self._set_hover(t, False))
        return [rect, text]

    def _set_hover(self, tag, hovering):
        rect = self._button_rects.get(tag)
        if rect is None:
            return
        self.canvas.itemconfigure(
            rect, fill=COLOR_BUTTON_HOVER if hovering else COLOR_BUTTON
        )
        try:
            self.canvas.configure(cursor="hand2" if hovering else "")
        except Exception:
            pass

    def _hide_all(self):
        for item in self.all_items:
            self.canvas.itemconfigure(item, state="hidden")
        self.mode = None

    def _show(self, mode, items):
        self._hide_all()
        for item in items:
            self.canvas.itemconfigure(item, state="normal")
        # 暂停层必须压在所有动态生成的障碍物/HUD 之上
        self.canvas.tag_raise(PAUSE_LAYER_TAG)
        self.mode = mode

    # ---------------------------------------------------------------- 视图
    def show_menu(self):
        self.canvas.itemconfigure(self.title, text="游戏暂停")
        self.canvas.itemconfigure(self.hint, text="Esc 继续游戏，R 重新开始，或点击下方按钮")
        self._show(
            "menu",
            [self.mask, self.panel, self.title, self.hint] + self.menu_buttons,
        )

    def show_settings(self):
        self.canvas.itemconfigure(self.title, text="设置")
        self.canvas.itemconfigure(self.hint, text="设置功能尚未实现，将在后续版本提供")
        self._show(
            "settings",
            [self.mask, self.panel, self.title, self.hint] + self.back_button,
        )

    def show_countdown(self, seconds):
        # 倒计时期间保留暗色遮罩，但不显示面板/标题/按钮；
        # 数字与“秒后回到游戏”居中显示，玩家仍能透过遮罩看到场上情况。
        self.canvas.itemconfigure(self.countdown_number, text=str(seconds))
        self._show(
            "countdown",
            [self.mask, self.countdown_number, self.countdown_suffix],
        )

    def hide(self):
        self._hide_all()
        try:
            self.canvas.configure(cursor="")
        except Exception:
            pass



def load_ui_photo(name, size, master):
    """读取 assets/sprites/<name>.png 生成 PhotoImage；缺图或缺 Pillow 时返回 None。

    和 AssetStore 分开：封面/底图不参与 REQUIRED_SPRITES 校验，贴图不全时也能显示。
    返回的 PhotoImage 必须由调用方长期持有，否则会被 GC 回收成空白。
    """
    try:
        from PIL import Image, ImageTk

        path = asset_root() / "sprites" / (name + ".png")
        if not path.is_file():
            return None
        target = (max(1, int(round(size[0] * UI_SCALE))),
                  max(1, int(round(size[1] * UI_SCALE))))
        with Image.open(path) as source:
            image = source.convert("RGB")
            if image.size != target:
                image = image.resize(target, Image.LANCZOS)
        return ImageTk.PhotoImage(image, master=master)
    except Exception:
        return None


class StartView:
    """开始界面：标题 + 开始 / 自定义 / 退出。只写 Canvas，行为交给 GameApp。"""

    def __init__(self, canvas, on_start, on_custom, on_quit):
        self.canvas = canvas
        self._button_rects = {}
        layer = (START_LAYER_TAG,)
        center_x = WINDOW_WIDTH // 2

        # 封面可用时铺满画布并用深色文字；缺图时退回深色底 + 浅色文字。
        self.cover_photo = load_ui_photo(
            START_COVER_NAME, (WINDOW_WIDTH, WINDOW_HEIGHT), canvas)
        if self.cover_photo is not None:
            self.background = canvas.create_image(
                0, 0, anchor="nw", image=self.cover_photo, tags=layer)
            self.mask = None
            title_fill = COLOR_START_TITLE
            subtitle_fill = COLOR_START_SUBTITLE
            hint_fill = COLOR_START_HINT
        else:
            self.background = None
            self.mask = canvas.create_rectangle(
                0, 0, WINDOW_WIDTH, WINDOW_HEIGHT,
                fill=COLOR_BG, outline="", tags=layer,
            )
            title_fill = COLOR_TEXT
            subtitle_fill = COLOR_DIM
            hint_fill = COLOR_DIM
        self.title = canvas.create_text(
            center_x, START_TITLE_Y, text=GAME_TITLE, anchor="center",
            font=(FONT_FAMILY, FONT_TITLE), fill=title_fill, tags=layer,
        )
        self.subtitle = canvas.create_text(
            center_x, START_SUBTITLE_Y,
            text="八向风力 · Q 版角色 · 60 FPS", anchor="center",
            font=(FONT_FAMILY, FONT_SUBTITLE), fill=subtitle_fill, tags=layer,
        )
        self.hint = canvas.create_text(
            center_x, START_HINT_Y, text="回车 / 空格 开始游戏", anchor="center",
            font=(FONT_FAMILY, FONT_SMALL), fill=hint_fill, tags=layer,
        )
        self.buttons = []
        for tag, y, label, callback in (
            ("start_btn_start", START_BUTTON_YS[0], "开始游戏", on_start),
            ("start_btn_custom", START_BUTTON_YS[1], "自定义", on_custom),
            ("start_btn_quit", START_BUTTON_YS[2], "退出", on_quit),
        ):
            self.buttons.extend(self._create_button(tag, y, label, callback))
        base = self.background if self.background is not None else self.mask
        self.all_items = [base, self.title, self.subtitle, self.hint] + self.buttons
        self.hide()

    def _create_button(self, tag, y, label, callback):
        center_x = WINDOW_WIDTH // 2
        rect = self.canvas.create_rectangle(
            center_x - START_BUTTON_WIDTH // 2, y - START_BUTTON_HEIGHT // 2,
            center_x + START_BUTTON_WIDTH // 2, y + START_BUTTON_HEIGHT // 2,
            fill=COLOR_BUTTON, outline=COLOR_BUTTON_BORDER, width=OUTLINE_WIDTH,
            tags=(START_LAYER_TAG, tag),
        )
        text = self.canvas.create_text(
            center_x, y, text=label, anchor="center",
            font=(FONT_FAMILY, FONT_NORMAL), fill=COLOR_TEXT,
            tags=(START_LAYER_TAG, tag),
        )
        self._button_rects[tag] = rect
        self.canvas.tag_bind(tag, "<Button-1>", lambda event, cb=callback: cb())
        self.canvas.tag_bind(tag, "<Enter>", lambda event, t=tag: self._set_hover(t, True))
        self.canvas.tag_bind(tag, "<Leave>", lambda event, t=tag: self._set_hover(t, False))
        return [rect, text]

    def _set_hover(self, tag, hovering):
        rect = self._button_rects.get(tag)
        if rect is None:
            return
        self.canvas.itemconfigure(
            rect, fill=COLOR_BUTTON_HOVER if hovering else COLOR_BUTTON)
        try:
            self.canvas.configure(cursor="hand2" if hovering else "")
        except Exception:
            pass

    def show(self):
        for item in self.all_items:
            self.canvas.itemconfigure(item, state="normal")
        self.canvas.tag_raise(START_LAYER_TAG)
        try:
            self.canvas.configure(cursor="")
        except Exception:
            pass

    def hide(self):
        for item in self.all_items:
            self.canvas.itemconfigure(item, state="hidden")
        try:
            self.canvas.configure(cursor="")
        except Exception:
            pass


class CrayonStartView:
    """开始界面（蜡笔版）：封面 + 薄雾 + 白描边镂空按钮，整屏合成一张图。

    为什么整屏合成：Canvas 图片的透明像素只透出画布底色、不透出下方 item，
    “封面之上浮着镂空按钮”没法分层画。底图只在第一次使用时读一次，之后
    hover 只是重画这张合成图（约 10ms，只在鼠标进出按钮时发生）。

    命中测试是纯坐标矩形，由应用层把 <Motion>/<Button-1> 转发进来。
    """

    def __init__(self, canvas, assets, on_start, on_custom, on_quit):
        self.canvas = canvas
        self.assets = assets
        self.scale = UI_SCALE
        self._callbacks = {
            "start": on_start,
            "custom": on_custom,
            "quit": on_quit,
        }
        self._buttons = (
            ("start", "start_btn_start", CRAYON_START_BUTTON_YS[0]),
            ("custom", "start_btn_custom", CRAYON_START_BUTTON_YS[1]),
            ("quit", "start_btn_quit", CRAYON_START_BUTTON_YS[2]),
        )
        self.visible = False
        self._hover = None
        self._photo = None
        self._hit_rects = []
        self._backdrop = self._load_backdrop()
        self.ready = self._backdrop is not None
        self.image_item = canvas.create_image(
            0, 0, anchor="nw", state="hidden", tags=(START_LAYER_TAG,))

    # ------------------------------------------------------------ 对外接口
    def show(self):
        self.visible = True
        self._hover = None
        self._render()
        self.canvas.tag_raise(START_LAYER_TAG)
        try:
            self.canvas.configure(cursor="")
        except Exception:
            pass

    def hide(self):
        self.visible = False
        self._hover = None
        self._hit_rects = []
        self.canvas.itemconfigure(self.image_item, state="hidden")
        try:
            self.canvas.configure(cursor="")
        except Exception:
            pass

    def destroy(self):
        try:
            self.canvas.delete(self.image_item)
        except Exception:
            pass

    def on_motion(self, x, y):
        """返回 True 表示这次移动被开始界面消费掉了。"""
        if not self.visible:
            return False
        key = self._hit_test(x, y)
        if key != self._hover:
            self._hover = key
            self._render()
            try:
                self.canvas.configure(cursor="hand2" if key else "")
            except Exception:
                pass
        return True

    def on_click(self, x, y):
        if not self.visible:
            return False
        key = self._hit_test(x, y)
        if key is None:
            return False
        callback = self._callbacks.get(key)
        if callback is not None:
            callback()
        return True

    # ------------------------------------------------------------ 内部
    def _hit_test(self, x, y):
        for key, (x1, y1, x2, y2) in self._hit_rects:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return key
        return None

    def _load_backdrop(self):
        """封面缩到画布尺寸并压一层薄雾；缺图 / 缺 Pillow 时返回 None。"""
        try:
            from PIL import Image

            path = asset_root() / "sprites" / (START_COVER_NAME + ".png")
            if not path.is_file():
                return None
            target = (max(1, int(round(WINDOW_WIDTH * self.scale))),
                      max(1, int(round(WINDOW_HEIGHT * self.scale))))
            with Image.open(path) as source:
                cover = source.convert("RGB")
            if cover.size != target:
                # 1.33 倍下采样，BOX 就是区域均值，和 LANCZOS 肉眼无差但快一半
                cover = cover.resize(target, Image.BOX)
            return cover.convert("RGBA")
        except Exception:
            return None

    def _render(self):
        from PIL import Image, ImageDraw, ImageTk

        base = self._backdrop
        if base is None:
            return
        scale = self.scale
        width, height = CRAYON_START_BUTTON_SIZE
        half = height // 2
        lift = CRAYON_START_HOVER_LIFT
        # 雾层直接并进 overlay：省掉启动时一次 1800×1260 的全图 alpha_composite
        overlay = Image.new("RGBA", base.size, CRAYON_START_VEIL)
        draw = ImageDraw.Draw(overlay)
        rects = []
        for key, _asset, center_y in self._buttons:
            hover = key == self._hover
            top = center_y - half - (lift if hover else 0)
            box = (
                round(CRAYON_START_BUTTON_X * scale),
                round(top * scale),
                round((CRAYON_START_BUTTON_X + width) * scale),
                round((top + height) * scale),
            )
            if hover:
                draw.rounded_rectangle(
                    box, radius=round(CRAYON_START_RADIUS * scale),
                    fill=CRAYON_START_WASH)
            draw.rounded_rectangle(
                box,
                radius=round(CRAYON_START_RADIUS * scale),
                outline=(CRAYON_START_FRAME_HOVER if hover
                         else CRAYON_START_FRAME),
                width=max(1, round(
                    (CRAYON_START_FRAME_WIDTH + (1 if hover else 0)) * scale)),
            )
            # 命中矩形保持逻辑坐标（事件转发前已经换算过）
            rects.append((key, (
                CRAYON_START_BUTTON_X,
                center_y - half,
                CRAYON_START_BUTTON_X + width,
                center_y + half,
            )))

        composed = Image.alpha_composite(base.copy(), overlay)
        self._paste(composed, "start_title", (WINDOW_WIDTH // 2, CRAYON_START_TITLE_Y))
        for key, asset, center_y in self._buttons:
            offset = -lift if key == self._hover else 0
            self._paste(composed, asset, (WINDOW_WIDTH // 2, center_y + offset))
        self._paste(composed, "start_hint", (WINDOW_WIDTH // 2, CRAYON_START_HINT_Y))

        self._hit_rects = rects
        self._photo = ImageTk.PhotoImage(composed, master=self.canvas)
        self.canvas.itemconfigure(
            self.image_item, image=self._photo, state="normal")

    def _paste(self, base, name, center):
        if self.assets is None:
            return
        image = self.assets.ui_pixels(name, self.scale)
        if image is None:
            return
        x = int(round(center[0] * self.scale - image.width / 2))
        y = int(round(center[1] * self.scale - image.height / 2))
        base.alpha_composite(image, (x, y))


def create_start_view(canvas, assets, on_start, on_custom, on_quit):
    """优先蜡笔版开始界面；贴图或封面缺失时退回旧版（雅黑 + 深色按钮）。"""
    if assets is not None and assets.has_ui(CRAYON_START_ASSETS):
        view = CrayonStartView(canvas, assets, on_start, on_custom, on_quit)
        if view.ready:
            return view
        view.destroy()
    return StartView(
        canvas, on_start=on_start, on_custom=on_custom, on_quit=on_quit)


class CustomScreen:
    """自定义界面：左侧板块栏 + 右侧内容区，已接入音效与背景。

    用 tk.Frame 覆盖在 Canvas 上，列表/勾选/文件对话框都比手写 Canvas 好用；
    音效数据仍然走 audio_profile / audio_import，不碰 GameModel。
    """

    CATEGORIES = (
        ("audio", "音效", True),
        ("background", "背景", True),
        ("character", "人物", False),
    )

    def __init__(self, parent, root_path, audio, on_back, assets=None,
                 on_background_changed=None):
        self.parent = parent
        self.root_path = Path(root_path) if root_path is not None else None
        self.audio = audio
        self.on_back = on_back
        self.assets = assets
        self.on_background_changed = on_background_changed
        self.profile = audio_profile.default_profile()
        self.library = {}
        self.selections = {}
        self.slot_id = "hit"
        self.category = "audio"
        self.background_choice = background_profile.DEFAULT_ID
        self._background_photos = {}
        self.dirty = False
        self._durations = {}
        # 条目行缓存：点击勾选/切换槽位时只改已有行，不销毁重建，避免整表闪烁。
        self._rows = []
        self._current_options = []
        self._empty_label = None
        self._status_text = ""
        # 底图 + 内嵌面板：Tk 帧不能透明，所以用“底图画布 + 略内缩的不透明面板”
        # 实现“封面作背景”的效果；底图缺失时退回原来的全窗口深色布局。
        self.backdrop = tk.Canvas(
            parent, width=px(WINDOW_WIDTH), height=px(WINDOW_HEIGHT),
            highlightthickness=0, bd=0, bg=COLOR_BG,
        )
        self.backdrop_photo = load_ui_photo(
            CUSTOM_BACKDROP_NAME, (WINDOW_WIDTH, WINDOW_HEIGHT), self.backdrop)
        if self.backdrop_photo is not None:
            self.backdrop.create_image(
                0, 0, anchor="nw", image=self.backdrop_photo)
        self.frame = tk.Frame(
            parent, bg=COLOR_BG, highlightthickness=1,
            highlightbackground=COLOR_CARD_BORDER,
        )
        self._build()
        self.frame.place_forget()
        self.backdrop.place_forget()

    # ------------------------------------------------------------ 构建
    def _build(self):
        self.sidebar = tk.Frame(
            self.frame, bg=COLOR_PANEL, width=CUSTOM_SIDEBAR_WIDTH)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        tk.Label(
            self.sidebar, text="自定义", bg=COLOR_PANEL, fg=COLOR_TEXT,
            font=(FONT_FAMILY, 24), anchor="w",
        ).pack(fill="x", padx=16, pady=(24, 2))
        tk.Label(
            self.sidebar, text="游戏资源", bg=COLOR_PANEL, fg=COLOR_DIM,
            font=(FONT_FAMILY, FONT_SMALL), anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 18))

        self.category_buttons = {}
        for key, label, enabled in self.CATEGORIES:
            text = label if enabled else label + "（即将）"
            button = self._make_button(
                self.sidebar, text,
                lambda k=key: self._select_category(k),
                hover=False, state="normal" if enabled else "disabled",
            )
            button.pack(fill="x", padx=14, pady=4)
            self.category_buttons[key] = button

        self._make_button(
            self.sidebar, "返回主菜单", self.request_close,
        ).pack(side="bottom", fill="x", padx=14, pady=18)

        self.content = tk.Frame(self.frame, bg=COLOR_BG)
        self.content.pack(
            side="left", fill="both", expand=True,
            padx=CUSTOM_CONTENT_PAD, pady=18,
        )

        header = tk.Frame(self.content, bg=COLOR_BG)
        header.pack(fill="x")
        tk.Label(
            header, text="音效", bg=COLOR_BG, fg=COLOR_TEXT,
            font=(FONT_FAMILY, 28), anchor="w",
        ).pack(side="left")
        self.list_count = tk.Label(
            header, text="", bg=COLOR_BG, fg=COLOR_DIM,
            font=(FONT_FAMILY, FONT_SMALL),
        )
        self.list_count.pack(side="right", pady=(12, 0))

        tk.Label(
            self.content,
            text="选择游戏内实际播放的音效；背景音乐可多选随机播放；改动点“应用”后生效。",
            bg=COLOR_BG, fg=COLOR_DIM, font=(FONT_FAMILY, FONT_SMALL),
            anchor="w",
        ).pack(fill="x", pady=(4, 10))

        self.tabs = tk.Frame(self.content, bg=COLOR_BG)
        self.tabs.pack(fill="x")
        self.slot_buttons = {}
        for slot in audio_profile.SLOTS:
            button = self._make_button(
                self.tabs, slot.label,
                lambda sid=slot.id: self._select_slot(sid), hover=False,
            )
            button.pack(side="left", padx=(0, 8), pady=2)
            self.slot_buttons[slot.id] = button

        self.slot_hint = tk.Label(
            self.content, text="", bg=COLOR_BG, fg=COLOR_DIM,
            font=(FONT_FAMILY, FONT_SMALL), anchor="w",
        )
        self.slot_hint.pack(fill="x", pady=(10, 6))

        list_wrap = tk.Frame(self.content, bg=COLOR_BG)
        list_wrap.pack(fill="both", expand=True)
        self.list_canvas = tk.Canvas(
            list_wrap, bg=COLOR_BG, highlightthickness=0)
        from tkinter import ttk

        style = ttk.Style(self.frame)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "Dafeng.Vertical.TScrollbar",
            background=COLOR_PANEL, troughcolor=COLOR_BG,
            bordercolor=COLOR_BG, arrowcolor=COLOR_DIM,
            darkcolor=COLOR_PANEL, lightcolor=COLOR_PANEL, relief="flat",
        )
        style.map(
            "Dafeng.Vertical.TScrollbar",
            background=[("active", COLOR_BUTTON_HOVER)],
        )
        self.scrollbar = ttk.Scrollbar(
            list_wrap, orient="vertical", command=self.list_canvas.yview,
            style="Dafeng.Vertical.TScrollbar",
        )
        self.list_canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.list_canvas.pack(side="left", fill="both", expand=True)
        self.list_inner = tk.Frame(self.list_canvas, bg=COLOR_BG)
        self._list_window = self.list_canvas.create_window(
            (0, 0), window=self.list_inner, anchor="nw")
        self.list_inner.bind("<Configure>", self._on_list_configure)
        self.list_canvas.bind("<Configure>", self._on_canvas_configure)
        self.list_canvas.bind("<Enter>", self._bind_wheel)
        self.list_canvas.bind("<Leave>", self._unbind_wheel)

        action = tk.Frame(self.content, bg=COLOR_BG)
        action.pack(fill="x", pady=(10, 0))
        self.status_label = tk.Label(
            action, text="", bg=COLOR_BG, fg=COLOR_DIM,
            font=(FONT_FAMILY, FONT_SMALL), anchor="w",
        )
        self.status_label.pack(side="left")
        self.apply_button = self._make_button(action, "应用", self._on_apply)
        self.apply_button.pack(side="right", padx=(8, 0))
        self.default_button = self._make_button(
            action, "恢复默认", self._on_default)
        self.default_button.pack(side="right", padx=(8, 0))
        self.refresh_button = self._make_button(action, "刷新", self._on_refresh)
        self.refresh_button.pack(side="right", padx=(8, 0))
        self.import_button = self._make_button(
            action, "导入音频…", self._on_import)
        self.import_button.pack(side="right")

        self.background_content = tk.Frame(self.frame, bg=COLOR_BG)
        self._build_background_page()

    def _build_background_page(self):
        content = self.background_content
        header = tk.Frame(content, bg=COLOR_BG)
        header.pack(fill="x")
        tk.Label(
            header, text="背景", bg=COLOR_BG, fg=COLOR_TEXT,
            font=(FONT_FAMILY, 28), anchor="w",
        ).pack(side="left")
        tk.Label(
            header, text="地面背景", bg=COLOR_BG, fg=COLOR_DIM,
            font=(FONT_FAMILY, FONT_SMALL),
        ).pack(side="right", pady=(12, 0))
        tk.Label(
            content,
            text="点选想用的地面背景，立即生效并保存。",
            bg=COLOR_BG, fg=COLOR_DIM, font=(FONT_FAMILY, FONT_SMALL),
            anchor="w",
        ).pack(fill="x", pady=(4, 12))

        cards = tk.Frame(content, bg=COLOR_BG)
        cards.pack(fill="both", expand=True)
        self._bg_cards = {}
        for option in background_profile.OPTIONS:
            card = tk.Frame(
                cards, bg=COLOR_PANEL, highlightthickness=2,
                highlightbackground=COLOR_PANEL, bd=0,
            )
            card.pack(side="left", anchor="n", padx=(0, 18), pady=(0, 8))
            photo = None
            if self.assets is not None and self.assets.ready:
                photo = self.assets.photo(
                    option["asset"], (360, 252), self.frame)
            if photo is not None:
                self._background_photos[option["id"]] = photo
                image_label = tk.Label(card, image=photo, bg=COLOR_PANEL, bd=0)
                image_label.pack(padx=10, pady=(10, 8))
            else:
                image_label = tk.Label(
                    card, text="背景贴图不可用", width=40, height=10,
                    bg=COLOR_PANEL, fg=COLOR_DIM,
                    font=(FONT_FAMILY, FONT_SMALL),
                )
                image_label.pack(padx=10, pady=(10, 8))
            title = tk.Label(
                card, text=option["label"], bg=COLOR_PANEL, fg=COLOR_TEXT,
                font=(FONT_FAMILY, FONT_NORMAL), anchor="w",
            )
            title.pack(fill="x", padx=12)
            description = tk.Label(
                card, text=option["description"], bg=COLOR_PANEL, fg=COLOR_DIM,
                font=(FONT_FAMILY, FONT_SMALL), anchor="w",
            )
            description.pack(fill="x", padx=12, pady=(0, 10))
            for widget in (card, image_label, title, description):
                widget.configure(cursor="hand2")
                widget.bind(
                    "<Button-1>",
                    lambda event, oid=option["id"]: self._select_background(oid),
                )
            self._bg_cards[option["id"]] = card

        action = tk.Frame(content, bg=COLOR_BG)
        action.pack(fill="x", pady=(12, 0))
        self.bg_status_label = tk.Label(
            action, text="", bg=COLOR_BG, fg=COLOR_DIM,
            font=(FONT_FAMILY, FONT_SMALL), anchor="w",
        )
        self.bg_status_label.pack(side="left")
        self.bg_default_button = self._make_button(
            action, "恢复默认", self._on_background_default)
        self.bg_default_button.pack(side="right", padx=(8, 0))

    def _make_button(self, parent, text, command, width=None,
                     hover=True, state="normal", font_size=FONT_NORMAL):
        button = tk.Button(
            parent, text=text, command=command,
            font=(FONT_FAMILY, font_size), bg=COLOR_BUTTON, fg=COLOR_TEXT,
            activebackground=COLOR_BUTTON_HOVER, activeforeground=COLOR_TEXT,
            relief="flat", bd=0, highlightthickness=0, padx=10, pady=4,
        )
        if width is not None:
            button.configure(width=width)
        if state == "disabled":
            button.configure(
                state="disabled", disabledforeground=COLOR_DIM, cursor="arrow")
        else:
            button.configure(cursor="hand2")
            if hover:
                button.bind(
                    "<Enter>",
                    lambda event, b=button: b.configure(bg=COLOR_BUTTON_HOVER))
                button.bind(
                    "<Leave>",
                    lambda event, b=button: b.configure(bg=COLOR_BUTTON))
        return button

    # ------------------------------------------------------------ 数据
    def _options(self, slot):
        options = []
        for relative in slot.builtin:
            path = audio_profile.builtin_path(relative)
            if not path.is_file():
                continue
            meta = "%.2fs" % self._duration(path)
            if slot.kind == "bgm":
                artist = audio_profile.builtin_artist(relative)
                if artist:
                    meta += " · " + artist
            options.append({
                "ref": audio_profile.builtin_ref(relative),
                "label": "内置 · " + audio_profile.builtin_label(relative),
                "meta": meta,
                "path": path,
                "builtin": True,
            })
        entries = sorted(
            self.library.values(),
            key=lambda item: str(item.get("display_name", "")))
        for entry in entries:
            relative = str(entry.get("file", ""))
            category = relative.split("/")[0]
            hint = str(entry.get("slot_hint", "")).strip()
            if slot.kind == "bgm":
                if category != "bgm":
                    continue
                if hint and hint != "bgm":
                    continue
            else:
                if category != "sfx":
                    continue
                if hint and hint != slot.id:
                    continue
            identifier = str(entry.get("id", ""))
            if not identifier:
                continue
            label = str(entry.get("display_name", "未命名"))
            if len(label) > 30:
                label = label[:29] + "…"
            options.append({
                "ref": "lib:" + identifier,
                "label": label,
                "meta": "%.2fs · 我的" % float(entry.get("duration", 0.0)),
                "path": Path(entry["path"]),
                "builtin": False,
            })
        return options

    def _duration(self, path):
        key = str(path)
        if key not in self._durations:
            import wave

            try:
                with wave.open(key, "rb") as handle:
                    duration = handle.getnframes() / float(handle.getframerate())
            except Exception:
                duration = 0.0
            self._durations[key] = duration
        return self._durations[key]

    def _validate(self, slot, references):
        options = self._options(slot)
        valid = {option["ref"] for option in options}
        cleaned = []
        for reference in references:
            if reference in valid and reference not in cleaned:
                cleaned.append(reference)
        cleaned = cleaned[:slot.max_count]
        if not cleaned:
            cleaned = [option["ref"] for option in options
                       if option["builtin"]][:slot.default_count]
        return cleaned

    # ------------------------------------------------------------ 打开/关闭
    def open(self):
        self._durations.clear()
        if self.root_path is None:
            self.profile = audio_profile.default_profile()
            self.library = {}
        else:
            try:
                audio_import.rescan_library(self.root_path)
            except Exception:
                pass
            self.profile = audio_profile.load_profile(self.root_path)
            self.library = audio_profile.load_library(self.root_path)
        self.selections = {
            slot.id: self._validate(
                slot, list(self.profile.get("audio", {}).get(slot.id, [])))
            for slot in audio_profile.SLOTS
        }
        self.slot_id = "hit"
        self.category = "audio"
        self.background_choice = background_profile.load_choice(self.root_path)
        self.dirty = False
        self._status_text = ""
        self.status_label.configure(text="", fg=COLOR_DIM)
        if self.backdrop_photo is not None:
            self.backdrop.place(
                x=0, y=0, width=px(WINDOW_WIDTH), height=px(WINDOW_HEIGHT))
            self.frame.place(
                x=px(CUSTOM_CARD_MARGIN_X), y=px(CUSTOM_CARD_MARGIN_Y),
                width=px(WINDOW_WIDTH - 2 * CUSTOM_CARD_MARGIN_X),
                height=px(WINDOW_HEIGHT - 2 * CUSTOM_CARD_MARGIN_Y),
            )
        else:
            self.frame.place(
                x=0, y=0, width=px(WINDOW_WIDTH), height=px(WINDOW_HEIGHT))
        self.frame.lift()
        self.frame.update_idletasks()
        self._render_all()
        self._show_category()
        self._render_background_choices()
        self._set_bg_status(
            "当前背景：%s。点选其他卡片会立即切换并保存。"
            % background_profile.label(self.background_choice))
        if self.root_path is None:
            self._set_status("没有可写的自定义目录；只能查看内置音效。", error=True)
            self._set_bg_status("没有可写的自定义目录，无法保存背景。", error=True)
        else:
            self._set_status("选择音效后点“应用”生效。")

    def close(self):
        self.audio.stop_preview()
        try:
            self.list_canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass
        self.frame.place_forget()
        self.backdrop.place_forget()

    # ------------------------------------------------------------ 渲染
    def _render_all(self):
        self._render_categories()
        self._render_slot_tabs()
        self._render_entries()
        if self.root_path is None:
            for button in (self.import_button, self.refresh_button,
                           self.default_button, self.apply_button):
                button.configure(state="disabled", disabledforeground=COLOR_DIM)
        else:
            for button in (self.import_button, self.refresh_button,
                           self.default_button):
                button.configure(state="normal")
            self._update_apply_state()

    def _render_categories(self):
        for key, button in self.category_buttons.items():
            button.configure(
                bg=COLOR_BUTTON_HOVER if key == self.category else COLOR_PANEL)

    def _show_category(self):
        options = dict(side="left", fill="both", expand=True,
                       padx=CUSTOM_CONTENT_PAD, pady=18)
        if self.category == "background":
            self.content.pack_forget()
            self.background_content.pack(**options)
        else:
            self.background_content.pack_forget()
            self.content.pack(**options)
        self._render_categories()

    def _render_background_choices(self):
        for choice, card in getattr(self, "_bg_cards", {}).items():
            selected = choice == self.background_choice
            card.configure(
                highlightbackground=(
                    COLOR_BUTTON_BORDER if selected else COLOR_PANEL))

    def _set_bg_status(self, text, error=False):
        if not hasattr(self, "bg_status_label"):
            return
        self.bg_status_label.configure(
            text=text, fg=COLOR_LARGE if error else COLOR_DIM)

    def _select_background(self, choice):
        """点选卡片即保存并立即应用，不再需要单独的“应用”按钮。"""
        choice = background_profile.normalize(choice)
        if choice == self.background_choice:
            self._set_bg_status(
                "当前背景：%s。" % background_profile.label(choice))
            return
        if self.root_path is None:
            self._set_bg_status("没有可写的自定义目录，无法保存背景。", error=True)
            return
        try:
            background_profile.save_choice(choice, self.root_path)
        except OSError as error:
            self._set_bg_status("保存失败：%s" % error, error=True)
            return
        self.background_choice = choice
        self._render_background_choices()
        ok = True
        if self.on_background_changed is not None:
            ok = self.on_background_changed(choice)
        if ok is False:
            self._set_bg_status(
                "已保存；但背景贴图不可用，当前回退到旧版背景。", error=True)
        else:
            self._set_bg_status(
                "已切换背景：%s" % background_profile.label(choice))

    def _on_background_default(self):
        self._select_background(background_profile.DEFAULT_ID)

    def _render_slot_tabs(self):
        for slot in audio_profile.SLOTS:
            self.slot_buttons[slot.id].configure(
                bg=COLOR_BUTTON_HOVER if slot.id == self.slot_id else COLOR_BUTTON)

    def _render_entries(self):
        """刷新条目列表。

        行控件按索引复用、只更新内容：勾选条目或切换槽位时不再销毁整张
        列表，避免 Tk 反复销毁/重建导致条目区闪一下。只有“没有可用条目”
        的空状态提示是独立控件，按需显示。
        """
        slot = audio_profile.SLOT_BY_ID[self.slot_id]
        options = self._options(slot)
        selected = set(self.selections.get(slot.id, []))
        self._current_options = options
        self.list_count.configure(text="%d 条可用" % len(options))
        if slot.kind == "bgm":
            hint = ("可多选（已选 %d / 最多 %d 首），内置与“我的”一起随机播放，"
                    "一轮内不重复；只选一首时循环播放。" % (
                        len(selected), slot.max_count))
        elif slot.kind == "random":
            hint = "可多选（已选 %d / 最多 %d 条），游戏中随机播放。" % (
                len(selected), slot.max_count)
        else:
            hint = "单选；未启用时使用内置默认。"
        self.slot_hint.configure(text=hint)

        if self._empty_label is None:
            self._empty_label = tk.Label(
                self.list_inner, text="没有可用条目；点“导入音频…”添加。",
                bg=COLOR_BG, fg=COLOR_DIM, font=(FONT_FAMILY, FONT_NORMAL),
                anchor="w",
            )
        if not options:
            if not self._empty_label.winfo_manager():
                self._empty_label.pack(fill="x", pady=12)
        elif self._empty_label.winfo_manager():
            self._empty_label.pack_forget()

        for index, option in enumerate(options):
            row = self._ensure_row(index)
            self._apply_row(row, option, option["ref"] in selected)

        # 当前槽位用不到的行先藏起来（保留控件，下次复用）。
        for row in self._rows[len(options):]:
            if row["frame"].winfo_manager():
                row["frame"].pack_forget()

        self._sync_scrollregion()
        self._update_scrollbar()

    def _ensure_row(self, index):
        while len(self._rows) <= index:
            self._rows.append(self._create_row(len(self._rows)))
        return self._rows[index]

    def _create_row(self, index):
        row = tk.Frame(self.list_inner, bg=COLOR_PANEL)
        row.pack(fill="x", pady=CUSTOM_ROW_PAD)
        variable = tk.BooleanVar(value=False)
        check = tk.Checkbutton(
            row, text="", variable=variable,
            command=lambda i=index: self._toggle(i),
            indicatoron=False, selectcolor=COLOR_PANEL,
            bg=COLOR_PANEL, activebackground=COLOR_PANEL,
            fg=COLOR_TEXT, activeforeground=COLOR_TEXT,
            font=(FONT_FAMILY, FONT_NORMAL), anchor="w",
            relief="flat", bd=0, highlightthickness=0,
            padx=10, pady=6,
        )
        check.pack(side="left", fill="x", expand=True)
        meta = tk.Label(
            row, text="", bg=COLOR_PANEL, fg=COLOR_DIM,
            font=(FONT_FAMILY, FONT_SMALL),
        )
        meta.pack(side="left", padx=8)
        preview = self._make_button(
            row, "▶", lambda i=index: self._preview(i),
            width=2, font_size=FONT_SMALL,
        )
        preview.pack(side="right", padx=6, pady=4)
        delete = self._make_button(
            row, "删除", lambda i=index: self._delete(i),
            font_size=FONT_SMALL,
        )
        delete.pack(side="right", padx=(0, 4), pady=4)
        return {
            "frame": row,
            "check": check,
            "variable": variable,
            "meta": meta,
            "preview": preview,
            "delete": delete,
            "ref": None,
        }

    def _apply_row(self, row, option, is_selected):
        row_bg = COLOR_BUTTON_HOVER if is_selected else COLOR_PANEL
        glyph = "☑" if is_selected else "☐"
        check = row["check"]
        check.configure(
            text=glyph + "  " + option["label"],
            bg=row_bg, activebackground=row_bg, selectcolor=row_bg,
        )
        row["frame"].configure(bg=row_bg)
        row["meta"].configure(bg=row_bg, text=option["meta"])
        variable = row["variable"]
        if bool(variable.get()) != is_selected:
            variable.set(is_selected)
        # 内置条目只显示试听；“我的”条目才多一个删除按钮。
        delete = row["delete"]
        if option["builtin"]:
            if delete.winfo_manager():
                delete.pack_forget()
        elif not delete.winfo_manager():
            delete.pack(side="right", padx=(0, 4), pady=4)
        if not row["frame"].winfo_manager():
            row["frame"].pack(fill="x", pady=CUSTOM_ROW_PAD)
        row["ref"] = option["ref"]

    def _sync_scrollregion(self):
        content = self.list_inner.winfo_reqheight()
        width = max(self.list_canvas.winfo_width(), 1)
        self.list_canvas.configure(scrollregion=(0, 0, width, content))

    def _update_scrollbar(self):
        content = self.list_inner.winfo_reqheight()
        view = self.list_canvas.winfo_height()
        if view <= 1:
            # 首次打开、画布尚未完成布局时补一次几何计算；之后的交互刷新
            # 不再强制 update_idletasks()，避免整窗重绘造成闪烁。
            self.list_canvas.update_idletasks()
            view = self.list_canvas.winfo_height()
        if content > view + 2:
            if not self.scrollbar.winfo_manager():
                self.scrollbar.pack(side="right", fill="y", before=self.list_canvas)
        elif self.scrollbar.winfo_manager():
            self.scrollbar.pack_forget()

    def _update_apply_state(self):
        state = "normal" if (self.dirty and self.root_path is not None) else "disabled"
        self.apply_button.configure(state=state, disabledforeground=COLOR_DIM)

    def _set_status(self, text, error=False):
        self._status_text = text
        self.status_label.configure(
            text=text, fg=COLOR_LARGE if error else COLOR_DIM)

    # ------------------------------------------------------------ 交互
    def _select_category(self, key):
        if key == "character":
            self._set_status("“人物”自定义将在后续版本接入。")
            return
        if key == self.category:
            return
        self.category = key
        self.audio.stop_preview()
        self._show_category()
        if key == "background":
            self._render_background_choices()
            self._set_bg_status(
                "当前：%s。点选其他卡片会立即切换并保存。"
                % background_profile.label(self.background_choice))
        else:
            self._set_status("选择音效后点“应用”生效。")

    def _select_slot(self, slot_id):
        if slot_id == self.slot_id:
            return
        self.audio.stop_preview()
        self.slot_id = slot_id
        self._render_slot_tabs()
        self._render_entries()
        self.list_canvas.yview_moveto(0.0)

    def _toggle(self, index):
        if not 0 <= index < len(self._current_options):
            return
        option = self._current_options[index]
        slot = audio_profile.SLOT_BY_ID[self.slot_id]
        current = list(self.selections.get(slot.id, []))
        reference = option["ref"]
        if slot.max_count <= 1:
            current = [reference]
        elif reference in current:
            if len(current) <= 1:
                self._set_status("至少要保留 1 条。", error=True)
                self._render_entries()
                return
            current.remove(reference)
        else:
            if len(current) >= slot.max_count:
                self._set_status(
                    "最多启用 %d 条，请先取消一条。" % slot.max_count, error=True)
                self._render_entries()
                return
            current.append(reference)
        self.selections[slot.id] = current
        self.dirty = True
        self._set_status("有未应用的更改；点“应用”生效。")
        self._render_entries()
        self._update_apply_state()

    def _preview(self, index):
        if not 0 <= index < len(self._current_options):
            return
        option = self._current_options[index]
        if not self.audio.preview(option["path"]):
            if self.audio.muted:
                self._set_status("当前静音（按 M 取消静音后再试听）。", error=True)
            else:
                self._set_status("无法试听：%s" % option["label"], error=True)
            return
        self._set_status("试听：%s" % option["label"])

    def _delete(self, index):
        if not 0 <= index < len(self._current_options):
            return
        option = self._current_options[index]
        if option["builtin"] or self.root_path is None:
            return
        from tkinter import messagebox

        if not messagebox.askyesno(
            "删除音效",
            "从音效库删除“%s”？\n已启用它的位置会回退到内置默认。"
            % option["label"],
            parent=self.frame,
        ):
            return
        entry_id = option["ref"][len("lib:"):]
        try:
            removed = audio_profile.remove_library_entry(self.root_path, entry_id)
        except OSError as error:
            self._set_status("删除失败：%s" % error, error=True)
            return
        if removed is None:
            self._set_status("找不到这条音效，可能已经被删除。", error=True)
            self._render_entries()
            return

        changed = False
        for slot in audio_profile.SLOTS:
            current = list(self.selections.get(slot.id, []))
            if option["ref"] not in current:
                continue
            current = [reference for reference in current
                       if reference != option["ref"]]
            if not current:
                current = [candidate["ref"] for candidate in self._options(slot)
                           if candidate["builtin"]][:slot.default_count]
            self.selections[slot.id] = current
            changed = True
        self.library = audio_profile.load_library(self.root_path)
        if changed:
            self.dirty = True
        self._render_entries()
        self._update_apply_state()
        name = str(removed.get("display_name", entry_id))
        if changed:
            self._set_status("已删除“%s”；点“应用”生效。" % name)
        else:
            self._set_status("已删除“%s”。" % name)

    # ------------------------------------------------------------ 按钮
    def _on_import(self):
        if self.root_path is None:
            return
        from tkinter import filedialog, messagebox

        paths = filedialog.askopenfilenames(
            parent=self.frame, title="导入音效",
            filetypes=[
                ("音频文件", "*.wav *.mp3 *.ogg *.flac"),
                ("所有文件", "*.*"),
            ],
        )
        if not paths:
            return
        slot = audio_profile.SLOT_BY_ID[self.slot_id]
        self._set_status("正在导入…")
        self.frame.update_idletasks()
        imported = 0
        duplicates = 0
        skipped = 0
        errors = []
        for filename in paths:
            try:
                result = audio_import.import_audio(
                    self.root_path, Path(filename),
                    audio_import.ImportOptions(slot=slot.id))
            except audio_import.AudioImportError as error:
                errors.append("%s：%s" % (Path(filename).name, error))
                continue
            if result.created:
                imported += 1
            else:
                duplicates += 1
            reference = "lib:" + str(result.entry["id"])
            current = list(self.selections.get(slot.id, []))
            if slot.max_count <= 1:
                current = [reference]
            elif reference not in current:
                if len(current) < slot.max_count:
                    current.append(reference)
                else:
                    skipped += 1
            self.selections[slot.id] = current
        if imported or duplicates:
            self.library = audio_profile.load_library(self.root_path)
            self.dirty = True
            self._render_entries()
            self._update_apply_state()
        message = "已导入 %d 条" % imported
        if duplicates:
            message += "；%d 条已存在" % duplicates
        if skipped:
            message += "；%d 条因槽位已满未启用" % skipped
        self._set_status(message)
        if errors:
            messagebox.showerror(
                "导入失败", "\n".join(errors[:8]), parent=self.frame)

    def _on_refresh(self):
        if self.root_path is None:
            return
        try:
            audio_import.rescan_library(self.root_path)
        except Exception as error:
            self._set_status("刷新失败：%s" % error, error=True)
            return
        self.library = audio_profile.load_library(self.root_path)
        self._durations.clear()
        self._render_entries()
        self._set_status("已刷新音效库。")

    def _on_default(self):
        slot = audio_profile.SLOT_BY_ID[self.slot_id]
        options = self._options(slot)
        self.selections[slot.id] = [
            option["ref"] for option in options if option["builtin"]
        ][:slot.default_count]
        self.dirty = True
        self._set_status("已恢复该槽位的默认选择；点“应用”生效。")
        self._render_entries()
        self._update_apply_state()

    def _on_apply(self):
        if self.root_path is None:
            return
        selections = {
            slot.id: self._validate(slot, self.selections.get(slot.id, []))
            for slot in audio_profile.SLOTS
        }
        self.selections = selections
        profile = dict(self.profile) if isinstance(self.profile, dict) else {}
        profile["version"] = audio_profile.PROFILE_VERSION
        profile["audio"] = selections
        # 玩家没有取消任何内置曲目时保持“自动收录新内置曲目”模式；一旦
        # 手动取消过某首内置，就记住这个选择，之后不自动加回来。
        bgm_slot = audio_profile.SLOT_BY_ID["bgm"]
        builtin_bgm = {option["ref"] for option in self._options(bgm_slot)
                       if option["builtin"]}
        profile["bgm_auto_builtin"] = builtin_bgm.issubset(
            set(selections.get("bgm", [])))
        try:
            audio_profile.save_profile(self.root_path, profile)
        except OSError as error:
            self._set_status("保存失败：%s" % error, error=True)
            return
        self.profile = profile
        ok = self.audio.reload()
        self.dirty = False
        self._render_entries()
        self._update_apply_state()
        self._set_status(
            "已应用。" if ok else "已保存；音频后端未就绪，重启游戏后生效。",
            error=not ok,
        )

    def request_close(self):
        if not self._confirm_discard():
            return
        self.close()
        self.on_back()

    def _confirm_discard(self):
        if not self.dirty:
            return True
        from tkinter import messagebox

        return messagebox.askyesno(
            "放弃更改",
            "有未应用的音效更改，返回主菜单会丢弃它们。继续？",
            parent=self.frame,
        )

    # ------------------------------------------------------------ 滚动
    def _on_list_configure(self, event):
        self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.list_canvas.itemconfigure(self._list_window, width=event.width)

    def _bind_wheel(self, event):
        self.list_canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, event):
        self.list_canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event):
        self.list_canvas.yview_scroll(int(-event.delta / 120), "units")


def create_custom_screen(canvas, parent, root_path, audio, on_back, assets=None,
                         on_background_changed=None):
    """优先蜡笔版自定义界面；字体/封面缺失时退回旧的 Tk 面板。"""
    try:
        import crayon_text
    except Exception:
        crayon_text = None
    if crayon_text is not None and crayon_text.available():
        root = assets.root if assets is not None else asset_root()
        cover = Path(root) / "sprites" / (START_COVER_NAME + ".png")
        if cover.is_file():
            view = CrayonCustomScreen(
                canvas, root_path, audio, on_back, assets=assets,
                on_background_changed=on_background_changed,
                text_module=crayon_text,
            )
            if view.ready:
                return view
            view.destroy()
    return CustomScreen(
        parent, root_path, audio, on_back, assets=assets,
        on_background_changed=on_background_changed,
    )


class CrayonCustomScreen:
    """自定义界面（蜡笔版）：整屏合成，交互靠坐标命中。

    视觉与 art_preview/custom_redesign_mock.png 一致：模糊封面 + 暗绿蒙版，
    白描边镂空按钮、暖纸色选中态；信息结构沿用旧版 CustomScreen。

    为什么要整屏合成：Canvas 图片的透明像素只透出画布底色、不透出下方
    item，没法分层贴“镂空按钮 + 蜡笔字”。这里每次状态变化重画一张图，
    命中测试是纯坐标矩形，由应用层把鼠标事件转发进来。

    音效数据仍走 audio_profile / audio_import，不碰 GameModel；
    动态文本（条目名、时长、状态）用 crayon_text 运行时烘焙并缓存。
    """

    CATEGORIES = CustomScreen.CATEGORIES

    def __init__(self, canvas, root_path, audio, on_back, assets=None,
                 on_background_changed=None, text_module=None):
        self.canvas = canvas
        self.root_path = Path(root_path) if root_path is not None else None
        self.audio = audio
        self.on_back = on_back
        self.assets = assets
        self.on_background_changed = on_background_changed
        self._ct = text_module
        self.scale = UI_SCALE if UI_SCALE else 1.0

        self.profile = audio_profile.default_profile()
        self.library = {}
        self.selections = {}
        self.slot_id = "hit"
        self.category = "audio"
        self.background_choice = background_profile.DEFAULT_ID
        self.dirty = False
        self.scroll = 0
        self._durations = {}
        self._current_options = []
        self.status_text = ""
        self.status_error = False
        self.bg_status_text = ""
        self.bg_status_error = False
        self.visible = False
        self._hover = None
        self._hover_row = None
        self._hit_rects = []
        self._text_cache = {}
        self._star_cache = {}
        self._photo = None
        self._backdrop = self._load_backdrop()
        self.ready = self._backdrop is not None
        self.image_item = canvas.create_image(
            0, 0, anchor="nw", state="hidden", tags=("custom_layer",))

    # ------------------------------------------------------------ 底图
    def _load_backdrop(self):
        from PIL import Image, ImageFilter

        root = self.assets.root if self.assets is not None else asset_root()
        path = Path(root) / "sprites" / (CRAYON_CUSTOM_BACKDROP_NAME + ".png")
        if not path.is_file():
            return None
        target = (max(1, int(round(WINDOW_WIDTH * self.scale))),
                  max(1, int(round(WINDOW_HEIGHT * self.scale))))
        try:
            with Image.open(path) as source:
                cover = source.convert("RGB")
            if cover.size != target:
                cover = cover.resize(target, Image.LANCZOS)
            blurred = cover.filter(
                ImageFilter.GaussianBlur(CRAYON_CUSTOM_BLUR * self.scale))
            base = blurred.convert("RGBA")
            veil = Image.new("RGBA", base.size, CRAYON_CUSTOM_VEIL)
            return Image.alpha_composite(base, veil)
        except Exception:
            return None

    def destroy(self):
        try:
            self.canvas.delete(self.image_item)
        except Exception:
            pass

    # ------------------------------------------------------------ 对外接口
    def open(self):
        self._durations.clear()
        if self.root_path is None:
            self.profile = audio_profile.default_profile()
            self.library = {}
        else:
            try:
                audio_import.rescan_library(self.root_path)
            except Exception:
                pass
            self.profile = audio_profile.load_profile(self.root_path)
            self.library = audio_profile.load_library(self.root_path)
        self.selections = {
            slot.id: self._validate(
                slot, list(self.profile.get("audio", {}).get(slot.id, [])))
            for slot in audio_profile.SLOTS
        }
        self.slot_id = "hit"
        self.category = "audio"
        self.scroll = 0
        self.background_choice = background_profile.load_choice(self.root_path)
        self.dirty = False
        self._hover = None
        self._hover_row = None
        self.status_error = False
        self.bg_status_error = False
        if self.root_path is None:
            self._set_status("没有可写的自定义目录；只能查看内置音效。", error=True)
            self._set_bg_status("没有可写的自定义目录，无法保存背景。", error=True)
        else:
            self._set_status("选择音效后点「应用」生效。")
            self._set_bg_status(
                "当前背景：%s。点选其他卡片会立即切换并保存。"
                % background_profile.label(self.background_choice))
        self.visible = True
        self._render()
        self.canvas.tag_raise(self.image_item)
        try:
            self.canvas.configure(cursor="")
        except Exception:
            pass

    def close(self):
        self.audio.stop_preview()
        self.visible = False
        self._hover = None
        self._hover_row = None
        self._hit_rects = []
        try:
            self.canvas.itemconfigure(self.image_item, state="hidden")
            self.canvas.configure(cursor="")
        except Exception:
            pass

    def request_close(self):
        if not self._confirm_discard():
            return
        self.close()
        self.on_back()

    def on_motion(self, x, y):
        if not self.visible:
            return False
        key = self._hit_test(x, y)
        row = self._row_at(x, y)
        if key != self._hover or row != self._hover_row:
            self._hover = key
            self._hover_row = row
            self._render()
            try:
                self.canvas.configure(cursor="hand2" if key else "")
            except Exception:
                pass
        return True

    def on_click(self, x, y):
        if not self.visible:
            return False
        key = self._hit_test(x, y)
        if key is None:
            return True
        kind = key[0]
        if kind == "cat":
            self._select_category(key[1])
        elif kind == "slot":
            self._select_slot(key[1])
        elif kind == "row":
            self._toggle(key[1])
        elif kind == "preview":
            self._preview(key[1])
        elif kind == "delete":
            self._delete(key[1])
        elif kind == "back":
            self.request_close()
        elif kind == "action":
            self._run_action(key[1])
        elif kind == "bg":
            self._select_background(key[1])
        elif kind == "bgdefault":
            self._on_background_default()
        return True

    def on_wheel(self, delta):
        if not self.visible or self.category != "audio":
            return False
        total = len(self._current_options)
        if total <= CRAYON_CUSTOM_ROW_VISIBLE:
            return True
        step = int(-delta / 120.0) or (-1 if delta > 0 else 1)
        limit = total - CRAYON_CUSTOM_ROW_VISIBLE
        new = max(0, min(limit, self.scroll + step))
        if new != self.scroll:
            self.scroll = new
            self._render()
        return True

    # ------------------------------------------------------------ 数据
    def _options(self, slot):
        options = []
        for relative in slot.builtin:
            path = audio_profile.builtin_path(relative)
            if not path.is_file():
                continue
            meta = "%.2fs" % self._duration(path)
            if slot.kind == "bgm":
                artist = audio_profile.builtin_artist(relative)
                if artist:
                    meta += " · " + artist
            options.append({
                "ref": audio_profile.builtin_ref(relative),
                "label": "内置 · " + audio_profile.builtin_label(relative),
                "meta": meta,
                "path": path,
                "builtin": True,
            })
        entries = sorted(
            self.library.values(),
            key=lambda item: str(item.get("display_name", "")))
        for entry in entries:
            relative = str(entry.get("file", ""))
            category = relative.split("/")[0]
            hint = str(entry.get("slot_hint", "")).strip()
            if slot.kind == "bgm":
                if category != "bgm":
                    continue
                if hint and hint != "bgm":
                    continue
            else:
                if category != "sfx":
                    continue
                if hint and hint != slot.id:
                    continue
            identifier = str(entry.get("id", ""))
            if not identifier:
                continue
            label = str(entry.get("display_name", "未命名"))
            if len(label) > 30:
                label = label[:29] + "…"
            options.append({
                "ref": "lib:" + identifier,
                "label": label,
                "meta": "%.2fs · 我的" % float(entry.get("duration", 0.0)),
                "path": Path(entry["path"]),
                "builtin": False,
            })
        return options

    def _duration(self, path):
        key = str(path)
        if key not in self._durations:
            import wave

            try:
                with wave.open(key, "rb") as handle:
                    duration = handle.getnframes() / float(handle.getframerate())
            except Exception:
                duration = 0.0
            self._durations[key] = duration
        return self._durations[key]

    def _validate(self, slot, references):
        options = self._options(slot)
        valid = {option["ref"] for option in options}
        cleaned = []
        for reference in references:
            if reference in valid and reference not in cleaned:
                cleaned.append(reference)
        cleaned = cleaned[:slot.max_count]
        if not cleaned:
            cleaned = [option["ref"] for option in options
                       if option["builtin"]][:slot.default_count]
        return cleaned

    # ------------------------------------------------------------ 状态
    def _set_status(self, text, error=False):
        self.status_text = text
        self.status_error = error

    def _set_bg_status(self, text, error=False):
        self.bg_status_text = text
        self.bg_status_error = error

    def _slot_hint(self, slot, selected_count):
        if slot.kind == "bgm":
            return ("可多选（已选 %d / 最多 %d 首），内置与“我的”一起随机播放，"
                    "一轮内不重复；只选一首时循环播放。" % (
                        selected_count, slot.max_count))
        if slot.kind == "random":
            return "可多选（已选 %d / 最多 %d 条），游戏中随机播放。" % (
                selected_count, slot.max_count)
        return "单选；未启用时使用内置默认。"

    def _apply_enabled(self):
        return self.dirty and self.root_path is not None

    # ------------------------------------------------------------ 交互
    def _select_category(self, key):
        if key == "character":
            self._set_status("“人物”自定义将在后续版本接入。")
            self._render()
            return
        if key == self.category:
            return
        self.category = key
        self.audio.stop_preview()
        self.scroll = 0
        if key == "background":
            self._set_bg_status(
                "当前：%s。点选其他卡片会立即切换并保存。"
                % background_profile.label(self.background_choice))
        else:
            self._set_status("选择音效后点「应用」生效。")
        self._render()

    def _select_slot(self, slot_id):
        if slot_id == self.slot_id:
            return
        self.audio.stop_preview()
        self.slot_id = slot_id
        self.scroll = 0
        self._render()

    def _toggle(self, index):
        if not 0 <= index < len(self._current_options):
            return
        option = self._current_options[index]
        slot = audio_profile.SLOT_BY_ID[self.slot_id]
        current = list(self.selections.get(slot.id, []))
        reference = option["ref"]
        if slot.max_count <= 1:
            current = [reference]
        elif reference in current:
            if len(current) <= 1:
                self._set_status("至少要保留 1 条。", error=True)
                self._render()
                return
            current.remove(reference)
        else:
            if len(current) >= slot.max_count:
                self._set_status(
                    "最多启用 %d 条，请先取消一条。" % slot.max_count, error=True)
                self._render()
                return
            current.append(reference)
        self.selections[slot.id] = current
        self.dirty = True
        self._set_status("有未应用的更改；点「应用」生效。")
        self._render()

    def _preview(self, index):
        if not 0 <= index < len(self._current_options):
            return
        option = self._current_options[index]
        if not self.audio.preview(option["path"]):
            if self.audio.muted:
                self._set_status("当前静音（按 M 取消静音后再试听）。", error=True)
            else:
                self._set_status("无法试听：%s" % option["label"], error=True)
            self._render()
            return
        self._set_status("试听：%s" % option["label"])
        self._render()

    def _delete(self, index):
        if not 0 <= index < len(self._current_options):
            return
        option = self._current_options[index]
        if option["builtin"] or self.root_path is None:
            return
        from tkinter import messagebox

        if not messagebox.askyesno(
            "删除音效",
            "从音效库删除“%s”？\n已启用它的位置会回退到内置默认。"
            % option["label"],
            parent=self.canvas.widget,
        ):
            return
        entry_id = option["ref"][len("lib:"):]
        try:
            removed = audio_profile.remove_library_entry(self.root_path, entry_id)
        except OSError as error:
            self._set_status("删除失败：%s" % error, error=True)
            self._render()
            return
        if removed is None:
            self._set_status("找不到这条音效，可能已经被删除。", error=True)
            self._render()
            return

        changed = False
        for slot in audio_profile.SLOTS:
            current = list(self.selections.get(slot.id, []))
            if option["ref"] not in current:
                continue
            current = [reference for reference in current
                       if reference != option["ref"]]
            if not current:
                current = [candidate["ref"] for candidate in self._options(slot)
                           if candidate["builtin"]][:slot.default_count]
            self.selections[slot.id] = current
            changed = True
        self.library = audio_profile.load_library(self.root_path)
        if changed:
            self.dirty = True
        name = str(removed.get("display_name", entry_id))
        if changed:
            self._set_status("已删除“%s”；点「应用」生效。" % name)
        else:
            self._set_status("已删除“%s”。" % name)
        self._render()

    def _run_action(self, name):
        if name == "import":
            self._on_import()
        elif name == "refresh":
            self._on_refresh()
        elif name == "default":
            self._on_default()
        elif name == "apply":
            self._on_apply()

    # ------------------------------------------------------------ 按钮动作
    def _on_import(self):
        if self.root_path is None:
            return
        from tkinter import filedialog, messagebox

        paths = filedialog.askopenfilenames(
            parent=self.canvas.widget, title="导入音效",
            filetypes=[
                ("音频文件", "*.wav *.mp3 *.ogg *.flac"),
                ("所有文件", "*.*"),
            ],
        )
        if not paths:
            return
        slot = audio_profile.SLOT_BY_ID[self.slot_id]
        self._set_status("正在导入…")
        self._render()
        self.canvas.update_idletasks()
        imported = 0
        duplicates = 0
        skipped = 0
        errors = []
        for filename in paths:
            try:
                result = audio_import.import_audio(
                    self.root_path, Path(filename),
                    audio_import.ImportOptions(slot=slot.id))
            except audio_import.AudioImportError as error:
                errors.append("%s：%s" % (Path(filename).name, error))
                continue
            if result.created:
                imported += 1
            else:
                duplicates += 1
            reference = "lib:" + str(result.entry["id"])
            current = list(self.selections.get(slot.id, []))
            if slot.max_count <= 1:
                current = [reference]
            elif reference not in current:
                if len(current) < slot.max_count:
                    current.append(reference)
                else:
                    skipped += 1
            self.selections[slot.id] = current
        if imported or duplicates:
            self.library = audio_profile.load_library(self.root_path)
            self.dirty = True
        message = "已导入 %d 条" % imported
        if duplicates:
            message += "；%d 条已存在" % duplicates
        if skipped:
            message += "；%d 条因槽位已满未启用" % skipped
        self._set_status(message)
        self._render()
        if errors:
            messagebox.showerror(
                "导入失败", "\n".join(errors[:8]), parent=self.canvas.widget)

    def _on_refresh(self):
        if self.root_path is None:
            return
        try:
            audio_import.rescan_library(self.root_path)
        except Exception as error:
            self._set_status("刷新失败：%s" % error, error=True)
            self._render()
            return
        self.library = audio_profile.load_library(self.root_path)
        self._durations.clear()
        self._set_status("已刷新音效库。")
        self._render()

    def _on_default(self):
        slot = audio_profile.SLOT_BY_ID[self.slot_id]
        options = self._options(slot)
        self.selections[slot.id] = [
            option["ref"] for option in options if option["builtin"]
        ][:slot.default_count]
        self.dirty = True
        self._set_status("已恢复该槽位的默认选择；点「应用」生效。")
        self._render()

    def _on_apply(self):
        if self.root_path is None:
            return
        selections = {
            slot.id: self._validate(slot, self.selections.get(slot.id, []))
            for slot in audio_profile.SLOTS
        }
        self.selections = selections
        profile = dict(self.profile) if isinstance(self.profile, dict) else {}
        profile["version"] = audio_profile.PROFILE_VERSION
        profile["audio"] = selections
        bgm_slot = audio_profile.SLOT_BY_ID["bgm"]
        builtin_bgm = {option["ref"] for option in self._options(bgm_slot)
                       if option["builtin"]}
        profile["bgm_auto_builtin"] = builtin_bgm.issubset(
            set(selections.get("bgm", [])))
        try:
            audio_profile.save_profile(self.root_path, profile)
        except OSError as error:
            self._set_status("保存失败：%s" % error, error=True)
            self._render()
            return
        self.profile = profile
        ok = self.audio.reload()
        self.dirty = False
        self._set_status(
            "已应用。" if ok else "已保存；音频后端未就绪，重启游戏后生效。",
            error=not ok,
        )
        self._render()

    def _confirm_discard(self):
        if not self.dirty:
            return True
        from tkinter import messagebox

        return messagebox.askyesno(
            "放弃更改",
            "有未应用的音效更改，返回主菜单会丢弃它们。继续？",
            parent=self.canvas.widget,
        )

    # ------------------------------------------------------------ 背景
    def _select_background(self, choice):
        choice = background_profile.normalize(choice)
        if choice == self.background_choice:
            self._set_bg_status(
                "当前背景：%s。" % background_profile.label(choice))
            self._render()
            return
        if self.root_path is None:
            self._set_bg_status("没有可写的自定义目录，无法保存背景。", error=True)
            self._render()
            return
        try:
            background_profile.save_choice(choice, self.root_path)
        except OSError as error:
            self._set_bg_status("保存失败：%s" % error, error=True)
            self._render()
            return
        self.background_choice = choice
        ok = True
        if self.on_background_changed is not None:
            ok = self.on_background_changed(choice)
        if ok is False:
            self._set_bg_status(
                "已保存；但背景贴图不可用，当前回退到旧版背景。", error=True)
        else:
            self._set_bg_status(
                "已切换背景：%s" % background_profile.label(choice))
        self._render()

    def _on_background_default(self):
        self._select_background(background_profile.DEFAULT_ID)

    # ------------------------------------------------------------ 命中
    def _hit_test(self, x, y):
        for key, (x1, y1, x2, y2) in self._hit_rects:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return key
        return None

    def _row_at(self, x, y):
        if not (CRAYON_CUSTOM_CONTENT_X[0] <= x <= CRAYON_CUSTOM_CONTENT_X[1]):
            return None
        top = CRAYON_CUSTOM_ROWS_TOP
        bottom = top + CRAYON_CUSTOM_ROW_VISIBLE * (
            CRAYON_CUSTOM_ROW_H + CRAYON_CUSTOM_ROW_GAP)
        if not (top <= y <= bottom):
            return None
        index = int((y - top) // (CRAYON_CUSTOM_ROW_H + CRAYON_CUSTOM_ROW_GAP))
        index += self.scroll
        if 0 <= index < len(self._current_options):
            return index
        return None

    # ------------------------------------------------------------ 绘制工具
    def _s(self, value):
        return value * self.scale

    def _rrect(self, draw, x1, y1, x2, y2, radius, fill=None,
               outline=None, width=1.0):
        draw.rounded_rectangle(
            [round(self._s(x1)), round(self._s(y1)),
             round(self._s(x2)), round(self._s(y2))],
            radius=max(1, round(self._s(radius))),
            fill=fill, outline=outline,
            width=max(1, round(self._s(width))),
        )

    def _text_image(self, text, px, fill=(255, 255, 255),
                    outline=(34, 54, 45), outline_px=1.6, strength=0.78,
                    bold_px=None, latin=False, opacity=1.0):
        from PIL import Image

        seed = 0
        for index, char in enumerate(text):
            seed = (seed * 131 + ord(char) * (index + 3)) & 0xFFFF
        key = (text, px, fill, outline, outline_px, strength, bold_px,
               latin, opacity, self.scale)
        cached = self._text_cache.get(key)
        if cached is not None:
            return cached
        scaled_px = max(8, int(round(px * self.scale)))
        font = self._ct.FONT_LATIN if latin else self._ct.FONT_CJK
        image = self._ct.text_png(
            [(text, font, scaled_px)], seed=seed, fill=fill,
            outline=outline, outline_px=outline_px, strength=strength,
            bold_px=bold_px)
        if image is None:
            image = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        if opacity < 1.0:
            image = image.copy()
            image.putalpha(image.getchannel("A").point(
                lambda v: int(v * opacity)))
        if len(self._text_cache) > 600:
            self._text_cache.clear()
        self._text_cache[key] = image
        return image

    def _put(self, ui, text, px, cx=None, cy=None, x=None, right=None,
             fill=(255, 255, 255), outline=(34, 54, 45), outline_px=1.6,
             strength=0.78, bold_px=None, latin=False, opacity=1.0):
        if not text:
            return
        image = self._text_image(
            text, px, fill=fill, outline=outline, outline_px=outline_px,
            strength=strength, bold_px=bold_px, latin=latin, opacity=opacity)
        if cx is not None:
            left = round(self._s(cx) - image.width / 2.0)
        elif x is not None:
            left = round(self._s(x))
        else:
            left = round(self._s(right) - image.width)
        top = round(self._s(cy) - image.height / 2.0)
        ui.alpha_composite(image, (left, top))

    def _star(self, size):
        key = round(size * self.scale)
        if key in self._star_cache:
            return self._star_cache[key]
        image = None
        if self.assets is not None:
            image = self.assets.image("ui/crayon/star", (key, key))
        self._star_cache[key] = image
        return image

    def _star_at(self, ui, cx, cy, size=24):
        image = self._star(size)
        if image is None:
            return
        ui.alpha_composite(
            image, (round(self._s(cx) - image.width / 2.0),
                    round(self._s(cy) - image.height / 2.0)))

    def _button(self, draw, ui, rects, key, x1, x2, cy, height, label,
                primary=False, disabled=False, font_px=20, radius=12):
        hover = self._hover == key
        text_opacity = 1.0
        if disabled:
            if primary:
                fill = CRAYON_CUSTOM_WARM + (95,)
                outline = (255, 255, 255, 120)
                ink = (255, 255, 255)
                text_opacity = 0.62
            else:
                fill = (255, 255, 255, 6)
                outline = (255, 255, 255, 90)
                ink = (255, 255, 255)
                text_opacity = 0.45
            border = 2.0
        elif primary:
            fill = CRAYON_CUSTOM_WARM + (238,)
            outline = (255, 255, 255, 245)
            ink = (34, 54, 45)
            border = 2.6
        else:
            fill = (255, 255, 255, 26 if hover else 12)
            outline = CRAYON_CUSTOM_FRAME_HOVER if hover else CRAYON_CUSTOM_FRAME
            ink = (255, 255, 255)
            border = 2.2
        y1 = cy - height / 2.0
        y2 = cy + height / 2.0
        self._rrect(draw, x1, y1, x2, y2, radius, fill=fill,
                    outline=outline, width=border)
        self._put(
            ui, label, font_px, cx=(x1 + x2) / 2.0, cy=cy, fill=ink,
            outline=None if primary and not disabled else (34, 54, 45),
            outline_px=0 if primary and not disabled else 1.5,
            strength=0.72, opacity=text_opacity)
        if not disabled:
            rects.append((key, (x1, y1, x2, y2)))

    def _checkbox(self, draw, cx, cy, checked):
        box = 12
        if checked:
            self._rrect(draw, cx - box, cy - box, cx + box, cy + box, 5,
                        fill=CRAYON_CUSTOM_WARM + (232,),
                        outline=(255, 255, 255, 235), width=2.0)
            points = [(cx - 6.2, cy + 0.6), (cx - 1.6, cy + 5.0),
                      (cx + 6.4, cy - 5.4)]
            draw.line(
                [(round(self._s(px)), round(self._s(py)))
                 for px, py in points],
                fill=(34, 54, 45, 255),
                width=max(1, round(self._s(3.2))), joint="curve")
        else:
            self._rrect(draw, cx - box, cy - box, cx + box, cy + box, 5,
                        fill=(255, 255, 255, 10),
                        outline=(255, 255, 255, 175), width=2.0)

    def _play_icon(self, draw, cx, cy, hover):
        radius = 15
        draw.ellipse(
            [round(self._s(cx - radius)), round(self._s(cy - radius)),
             round(self._s(cx + radius)), round(self._s(cy + radius))],
            outline=(255, 255, 255, 240 if hover else 195),
            width=max(1, round(self._s(2.0))))
        triangle = [(cx - 4.2, cy - 6.4), (cx - 4.2, cy + 6.4),
                    (cx + 6.8, cy)]
        draw.polygon(
            [(round(self._s(px)), round(self._s(py))) for px, py in triangle],
            fill=(255, 255, 255, 250 if hover else 220))

    def _fit(self, text, px, max_width, latin=False):
        font = self._ct.FONT_LATIN if latin else self._ct.FONT_CJK
        if self._ct.measure(text, font, px) <= max_width:
            return text
        while text and self._ct.measure(text + "…", font, px) > max_width:
            text = text[:-1]
        return text + "…"

    def _wrap(self, text, px, max_width, max_lines=2):
        font = self._ct.FONT_CJK
        lines = []
        current = ""
        for char in text:
            if self._ct.measure(current + char, font, px) <= max_width:
                current += char
            else:
                lines.append(current)
                current = char
                if len(lines) >= max_lines:
                    break
        if len(lines) < max_lines and current:
            lines.append(current)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
        if lines and self._ct.measure("".join(lines), font, px) < self._ct.measure(text, font, px):
            while lines[-1] and self._ct.measure(lines[-1] + "…", font, px) > max_width:
                lines[-1] = lines[-1][:-1]
            lines[-1] += "…"
        return lines

    # ------------------------------------------------------------ 绘制
    def _render(self):
        if not self.ready:
            return
        from PIL import Image, ImageDraw, ImageTk

        base = self._backdrop.copy()
        ui = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(ui)
        rects = []
        self._draw_nav(draw, ui, rects)
        self._draw_title(ui)
        if self.category == "background":
            self._draw_background_page(draw, ui, rects)
        else:
            self._draw_audio_page(draw, ui, rects)
        composed = Image.alpha_composite(base, ui)
        self._hit_rects = rects
        self._photo = ImageTk.PhotoImage(composed, master=self.canvas)
        self.canvas.itemconfigure(
            self.image_item, image=self._photo, state="normal")

    def _draw_title(self, ui):
        self._put(ui, "自定义", 72, cx=WINDOW_WIDTH / 2.0,
                  cy=CRAYON_CUSTOM_TITLE_Y, outline=(24, 20, 18),
                  outline_px=2.6, strength=0.42, bold_px=0.45)
        self._put(ui, "游戏资源 · 选好后点「应用」生效", 19,
                  cx=WINDOW_WIDTH / 2.0, cy=CRAYON_CUSTOM_SUBTITLE_Y,
                  opacity=0.92)

    def _draw_nav(self, draw, ui, rects):
        self._put(ui, "分类", 17, x=84, cy=194, opacity=0.72)
        for index, (key, label, enabled) in enumerate(self.CATEGORIES):
            display = label if enabled else label + "（即将）"
            self._button(
                draw, ui, rects, ("cat", key),
                CRAYON_CUSTOM_NAV_X[0], CRAYON_CUSTOM_NAV_X[1],
                CRAYON_CUSTOM_NAV_YS[index], CRAYON_CUSTOM_NAV_H, display,
                primary=(key == self.category), disabled=not enabled,
                font_px=21,
            )
        self._button(
            draw, ui, rects, ("back",),
            CRAYON_CUSTOM_NAV_X[0], CRAYON_CUSTOM_NAV_X[1],
            CRAYON_CUSTOM_BACK_Y, CRAYON_CUSTOM_NAV_H, "返回主菜单",
            font_px=21,
        )

    def _draw_status(self, draw, ui):
        lines = self._wrap(self.status_text, 15, 262)
        colour = CRAYON_CUSTOM_ERROR if self.status_error else (255, 255, 255)
        for index, line in enumerate(lines[:2]):
            self._put(ui, line, 15, x=344,
                      cy=CRAYON_CUSTOM_STATUS_Y + index * 21,
                      fill=colour, opacity=1.0, outline_px=1.4,
                      strength=0.7)

    def _draw_actions(self, draw, ui, rects):
        for key, label, (x1, x2) in CRAYON_CUSTOM_ACTIONS:
            disabled = self.root_path is None
            if key == "apply":
                disabled = not self._apply_enabled()
            self._button(
                draw, ui, rects, ("action", key), x1, x2,
                CRAYON_CUSTOM_ACTION_Y, CRAYON_CUSTOM_ACTION_H, label,
                primary=(key == "apply"), disabled=disabled, font_px=20,
            )

    def _draw_audio_page(self, draw, ui, rects):
        slot = audio_profile.SLOT_BY_ID[self.slot_id]
        options = self._options(slot)
        self._current_options = options
        selected = set(self.selections.get(slot.id, []))

        self._star_at(ui, 356, CRAYON_CUSTOM_HEADER_Y)
        self._put(ui, "音效", 40, x=378, cy=CRAYON_CUSTOM_HEADER_Y,
                  outline=(24, 20, 18), outline_px=1.8, strength=0.55)
        self._put(ui, "%d 条可用" % len(options), 17, right=1120, cy=216,
                  opacity=0.78)
        self._put(ui, "选择游戏内实际播放的音效；背景音乐可多选随机播放。",
                  18, x=344, cy=CRAYON_CUSTOM_DESC_Y, opacity=0.88)

        x = CRAYON_CUSTOM_CONTENT_X[0]
        for index, slot_info in enumerate(audio_profile.SLOTS):
            width = CRAYON_CUSTOM_SLOT_WIDTHS[index]
            x2 = x + width
            key = ("slot", slot_info.id)
            hover = self._hover == key
            is_selected = slot_info.id == self.slot_id
            if is_selected:
                fill = CRAYON_CUSTOM_WARM + (232,)
                outline = (255, 255, 255, 240)
                ink = (34, 54, 45)
                border = 2.2
            else:
                fill = (255, 255, 255, 26 if hover else 10)
                outline = (255, 255, 255, 240 if hover else 200)
                ink = (255, 255, 255)
                border = 2.0
            self._rrect(draw, x, CRAYON_CUSTOM_SLOT_Y[0], x2,
                        CRAYON_CUSTOM_SLOT_Y[1], 11, fill=fill,
                        outline=outline, width=border)
            self._put(ui, slot_info.label, 19, cx=(x + x2) / 2.0,
                      cy=(CRAYON_CUSTOM_SLOT_Y[0] + CRAYON_CUSTOM_SLOT_Y[1]) / 2.0,
                      fill=ink,
                      outline=None if is_selected else (34, 54, 45),
                      outline_px=0 if is_selected else 1.5, strength=0.75)
            rects.append((key, (x, CRAYON_CUSTOM_SLOT_Y[0], x2,
                                CRAYON_CUSTOM_SLOT_Y[1])))
            x = x2 + CRAYON_CUSTOM_SLOT_GAP

        self._put(ui, self._slot_hint(slot, len(selected)), 16, x=344,
                  cy=CRAYON_CUSTOM_SLOT_HINT_Y, opacity=0.82)

        visible = CRAYON_CUSTOM_ROW_VISIBLE
        total = len(options)
        for offset in range(visible):
            index = self.scroll + offset
            if index >= total:
                break
            cy = (CRAYON_CUSTOM_ROWS_TOP + offset * (
                CRAYON_CUSTOM_ROW_H + CRAYON_CUSTOM_ROW_GAP)
                + CRAYON_CUSTOM_ROW_H / 2.0)
            y1 = cy - CRAYON_CUSTOM_ROW_H / 2.0
            y2 = cy + CRAYON_CUSTOM_ROW_H / 2.0
            option = options[index]
            hover = self._hover_row == index
            checked = option["ref"] in selected
            self._rrect(
                draw, 344, y1, 1120, y2, 13,
                fill=(255, 255, 255, 34 if hover else 14),
                outline=(255, 255, 255, 245 if hover else 150),
                width=2.4 if hover else 1.8,
            )
            self._checkbox(draw, 372, cy, checked)
            name = self._fit(option["label"], 20, 500)
            self._put(ui, name, 20, x=400, cy=cy,
                      outline_px=1.5, strength=0.75,
                      opacity=1.0 if hover else 0.95)
            has_delete = not option["builtin"]
            meta_right = 968 if has_delete else 1024
            self._put(ui, option["meta"], 16, right=meta_right, cy=cy,
                      opacity=0.85, outline_px=1.4, strength=0.7)
            if has_delete:
                self._delete_button(draw, ui, rects, index, cy, hover)
            self._play_icon(draw, 1076, cy, hover)
            rects.append((("preview", index),
                          (1061, cy - 15, 1091, cy + 15)))
            rects.append((("row", index), (344, y1, 1120, y2)))

        if total > visible:
            track_top = CRAYON_CUSTOM_ROWS_TOP
            track_bottom = (CRAYON_CUSTOM_ROWS_TOP
                            + visible * (CRAYON_CUSTOM_ROW_H
                                         + CRAYON_CUSTOM_ROW_GAP) - 12)
            self._rrect(draw, CRAYON_CUSTOM_SCROLL_X[0], track_top,
                        CRAYON_CUSTOM_SCROLL_X[1], track_bottom, 4,
                        fill=(255, 255, 255, 34))
            height = track_bottom - track_top
            thumb = height * visible / float(total)
            top = track_top + (height - thumb) * (
                self.scroll / float(max(1, total - visible)))
            self._rrect(draw, CRAYON_CUSTOM_SCROLL_X[0], top,
                        CRAYON_CUSTOM_SCROLL_X[1], top + thumb, 4,
                        fill=(255, 255, 255, 110))

        self._draw_status(draw, ui)
        self._draw_actions(draw, ui, rects)
        self._put(ui, "Esc 返回主菜单 · 滚轮 浏览音效列表", 18,
                  cx=WINDOW_WIDTH / 2.0, cy=CRAYON_CUSTOM_HINT_Y,
                  opacity=0.9)

    def _delete_button(self, draw, ui, rects, index, cy, hover):
        x1, x2 = 980, 1036
        edge = (255, 255, 255, 235 if hover else 170)
        self._rrect(draw, x1, cy - 15, x2, cy + 15, 8,
                    fill=(255, 255, 255, 16), outline=edge, width=1.4)
        self._put(ui, "删除", 15, cx=(x1 + x2) / 2.0, cy=cy,
                  outline_px=1.2, strength=0.68)
        rects.append((("delete", index), (x1, cy - 15, x2, cy + 15)))

    def _draw_background_page(self, draw, ui, rects):
        self._star_at(ui, 356, CRAYON_CUSTOM_HEADER_Y)
        self._put(ui, "背景", 40, x=378, cy=CRAYON_CUSTOM_HEADER_Y,
                  outline=(24, 20, 18), outline_px=1.8, strength=0.55)
        self._put(ui, "地面背景", 17, right=1120, cy=216, opacity=0.78)
        self._put(ui, "点选想用的地面背景，立即生效并保存。", 18, x=344,
                  cy=CRAYON_CUSTOM_DESC_Y, opacity=0.88)

        width, height = CRAYON_CUSTOM_BG_CARD_SIZE
        image_width, image_height = CRAYON_CUSTOM_BG_IMAGE_SIZE
        for index, option in enumerate(background_profile.OPTIONS):
            x1 = CRAYON_CUSTOM_CONTENT_X[0] + index * (
                width + CRAYON_CUSTOM_BG_CARD_GAP)
            y1 = CRAYON_CUSTOM_BG_CARD_Y
            x2, y2 = x1 + width, y1 + height
            key = ("bg", option["id"])
            hover = self._hover == key
            is_selected = option["id"] == self.background_choice
            self._rrect(
                draw, x1, y1, x2, y2, 14,
                fill=(255, 255, 255, 30 if hover else CRAYON_CUSTOM_CARD_FILL[3]),
                outline=(255, 255, 255, 255 if is_selected else 200),
                width=2.8 if is_selected else 1.8,
            )
            picture = None
            if self.assets is not None:
                picture = self.assets.image(
                    option["asset"],
                    (round(self._s(image_width)),
                     round(self._s(image_height))))
            if picture is not None:
                ui.alpha_composite(
                    picture, (round(self._s(x1 + 12)),
                              round(self._s(y1 + 10))))
            else:
                self._rrect(draw, x1 + 12, y1 + 10, x1 + 12 + image_width,
                            y1 + 10 + image_height, 10,
                            fill=(255, 255, 255, 20))
                self._put(ui, "背景贴图不可用", 16, cx=x1 + width / 2.0,
                          cy=y1 + 10 + image_height / 2.0, opacity=0.8)
            self._put(ui, option["label"], 22, x=x1 + 16,
                      cy=y1 + 10 + image_height + 28)
            self._put(ui, option["description"], 15, x=x1 + 16,
                      cy=y1 + 10 + image_height + 58, opacity=0.8,
                      outline_px=1.3, strength=0.7)
            if is_selected:
                pill_x1, pill_x2 = x2 - 92, x2 - 12
                pill_y = y1 + 18
                self._rrect(draw, pill_x1, pill_y, pill_x2, pill_y + 28, 12,
                            fill=CRAYON_CUSTOM_WARM + (240,),
                            outline=(255, 255, 255, 240), width=2.0)
                self._put(ui, "使用中", 15, cx=(pill_x1 + pill_x2) / 2.0,
                          cy=pill_y + 14, fill=(34, 54, 45),
                          outline=None, outline_px=0, strength=0.7)
            rects.append((key, (x1, y1, x2, y2)))

        lines = self._wrap(self.bg_status_text, 15, 620)
        colour = CRAYON_CUSTOM_ERROR if self.bg_status_error else (255, 255, 255)
        for index, line in enumerate(lines[:2]):
            self._put(ui, line, 15, x=344,
                      cy=CRAYON_CUSTOM_STATUS_Y + index * 21,
                      fill=colour, opacity=0.95, outline_px=1.4,
                      strength=0.7)
        self._button(
            draw, ui, rects, ("bgdefault",), 1008, 1120,
            CRAYON_CUSTOM_ACTION_Y, CRAYON_CUSTOM_ACTION_H, "恢复默认",
            disabled=self.root_path is None, font_px=20,
        )
        self._put(ui, "Esc 返回主菜单 · 点卡片立即切换", 18,
                  cx=WINDOW_WIDTH / 2.0, cy=CRAYON_CUSTOM_HINT_Y,
                  opacity=0.9)


class DeferredAudio:
    """启动期占位音频：真正的 AudioPlayer 在后台线程里构造。

    pygame 的 import + mixer 初始化在本机要 ~0.7–1 秒，占了启动时间的四分之一；
    窗口和开始界面没理由等它。后台线程构造完成后，这里转发所有调用；
    只有“每帧都会调”的 update/play 在未就绪时静默跳过，
    用户明确触发的操作（切歌、试听、静音）才同步等它就绪。
    """

    def __init__(self, factory):
        self._factory = factory
        self._audio = None
        self._lock = threading.Lock()
        self._worker = None
        self._done = threading.Event()
        self._closed = False

    # ------------------------------------------------------------ 后台构造
    def warm(self):
        """在后台线程构造真实播放器；重复调用只会起一个线程。"""
        with self._lock:
            if self._worker is not None or self._audio is not None:
                return
            self._worker = threading.Thread(
                target=self._build, name="audio-warmup", daemon=True)
        self._worker.start()

    def _build(self):
        audio = None
        try:
            audio = self._factory()
        except Exception:  # pragma: no cover - AudioPlayer 自己会吞掉大部分异常
            audio = None
        with self._lock:
            self._audio = audio
            closed = self._closed
        if closed and audio is not None:
            # 构造期间用户已经关掉窗口：别留一个还在响的 mixer
            try:
                audio.close()
            except Exception:
                pass
        self._done.set()

    def _ensure(self):
        """拿到真实对象；后台没就绪就等它（只用于用户主动触发的操作）。"""
        audio = self._audio
        if audio is not None:
            return audio
        if self._worker is None:
            self.warm()
        self._done.wait(timeout=10.0)
        return self._audio

    # ------------------------------------------------------------ 状态
    @property
    def ready(self):
        return self._audio is not None

    @property
    def muted(self):
        audio = self._audio
        return bool(audio.muted) if audio is not None else False

    @property
    def backend(self):
        audio = self._audio
        return audio.backend if audio is not None else "warming"

    @property
    def last_played(self):
        audio = self._audio
        return audio.last_played if audio is not None else None

    # ------------------------------------------------------------ 转发
    def update(self):
        """主循环每帧调用：还没就绪就跳过，绝不能在这里阻塞。"""
        audio = self._audio
        if audio is not None:
            audio.update()

    def play(self, event_name):
        audio = self._audio
        return audio.play(event_name) if audio is not None else False

    def preview(self, path):
        audio = self._ensure()
        return audio.preview(path) if audio is not None else False

    def stop_preview(self):
        audio = self._audio
        if audio is not None:
            audio.stop_preview()

    def reload(self):
        audio = self._ensure()
        return audio.reload() if audio is not None else False

    def toggle_mute(self):
        audio = self._ensure()
        return audio.toggle_mute() if audio is not None else False

    def set_paused(self, paused):
        audio = self._audio
        if audio is not None:
            audio.set_paused(paused)

    def start_bgm(self):
        audio = self._ensure()
        if audio is not None:
            audio.start_bgm()

    def pause_bgm(self):
        audio = self._audio
        if audio is not None:
            audio.pause_bgm()

    def resume_bgm(self):
        audio = self._ensure()
        if audio is not None:
            audio.resume_bgm()

    def stop_bgm(self):
        audio = self._audio
        if audio is not None:
            audio.stop_bgm()

    def close(self):
        with self._lock:
            self._closed = True
            audio = self._audio
        if audio is not None:
            audio.close()


class GameApp:
    """Tk 应用壳：只负责编排，不实现风/碰撞/难度公式。"""

    def __init__(self, root, model=None, soak_seconds=None, autostart=False):
        self.root = root
        self.model = model if model is not None else GameModel()
        self.soak_seconds = soak_seconds
        self.exit_code = 0

        root.title(GAME_TITLE)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._center_window()

        self.canvas = ScaledCanvas(
            tk.Canvas(
                root,
                width=int(round(WINDOW_WIDTH * UI_SCALE)),
                height=int(round(WINDOW_HEIGHT * UI_SCALE)),
                highlightthickness=0,
            ),
            UI_SCALE,
        )
        self.canvas.configure(bg=COLOR_BG)
        self.canvas.pack()

        self.assets = AssetStore() if ASSETS_ENABLED else None
        # pygame 的 import + mixer 初始化 ~1 秒，放到后台线程和贴图预加载并行，
        # 窗口和开始界面先出来；真正要发声时 DeferredAudio 再等它就绪。
        self.audio = DeferredAudio(
            lambda: AudioPlayer(
                enabled=AUDIO_ENABLED,
                sfx_volume=AUDIO_SFX_VOLUME,
                bgm_volume=AUDIO_BGM_VOLUME,
            )
        )
        # 但要等首屏画完再热身：pygame import + mixer.init 会跟冒烟期的贴图预加载抢 GIL，
        # 放到第一个事件循环之后，主线程先把窗口和开始界面交出去。
        self.root.after(0, self.audio.warm)
        if self.assets is not None and not self.assets.ready:
            print("提示：%s（已退回矢量几何渲染）" % self.assets.status(), file=sys.stderr)

        self.renderer = Renderer(self.canvas, self.assets)
        self.renderer.hud.set_game_over_callbacks(
            self._on_game_over_restart_clicked,
            self._on_game_over_quit_clicked,
        )
        pause_options = dict(
            on_continue=self._on_continue_clicked,
            on_restart=self._on_restart_clicked,
            on_settings=self._on_settings_clicked,
            on_quit=self._on_quit_clicked,
            on_back=self._on_back_clicked,
        )
        if self.assets is not None and self.assets.has_ui(CRAYON_OVER_ASSETS):
            self.over_view = CrayonGameOverView(
                self.canvas,
                self.assets,
                self._on_game_over_restart_clicked,
                self._on_game_over_quit_clicked,
            )
            self.renderer.hud.set_overlay_crayon(True)
        else:
            self.over_view = None
        if self.assets is not None and self.assets.has_ui(CRAYON_PAUSE_ASSETS):
            self.pause_view = CrayonPauseView(self.canvas, self.assets, **pause_options)
        else:
            self.pause_view = PauseView(self.canvas, **pause_options)
        self.start_view = create_start_view(
            self.canvas,
            self.assets,
            on_start=self.start_game,
            on_custom=self._on_custom_clicked,
            on_quit=self._on_start_quit_clicked,
        )
        # 自定义界面（封面模糊 + 一屏卡片）要 ~0.5 秒：先不建，开始界面
        # 空下来之后在 _drain_pending_preload 里预热，避免占启动时间
        self.custom_screen = None
        self._custom_prebuild_scheduled = False
        self.screen = APP_START
        self.autostart = bool(autostart) or soak_seconds is not None
        self.input_state = InputState()

        # 暂停状态：只属于应用层，模型不感知（暂停时主循环不推进模型）
        self.paused = False
        self.pause_settings = False
        self.pause_countdown = 0
        self._pause_job = None

        self._keys_down = set()
        self._release_jobs = {}
        self._edge_down = set()
        self._wind_sfx_guard_until = 0.0  # 受伤后短暂抑制风变呼啸，避免叠音
        # 结束界面是否已经暂停 BGM：暂停只做一次，重开时再恢复。
        self._bgm_over_paused = False

        self.dev_hud = soak_seconds is not None
        self.running = True
        self.last_time = time.perf_counter()
        self._next_frame_at = self.last_time + FRAME_MS / 1000.0
        self.accumulator = 0.0
        self.fps = float(TARGET_FPS)
        self._fps_frames = 0
        self._fps_start = time.perf_counter()

        self._soak_start = self.last_time
        self._soak_last_report = self.last_time - 10.0
        self._soak_min_fps = None
        self._soak_peak_items = 0
        self._soak_reports = []

        self.root.bind_all("<KeyPress>", self.on_key_press)
        self.root.bind_all("<KeyRelease>", self.on_key_release)
        self.root.bind_all("<FocusOut>", self.on_focus_out)
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<MouseWheel>", self._on_canvas_wheel)
        self.canvas.bind("<Leave>", self._on_canvas_leave)

        # 中文输入法在游戏窗口里会吞键/卡顿：窗口创建后立刻解除 IME 关联，
        # 并在窗口每次重新获得焦点时再确认一次（搜狗等可能会重新挂接）。
        self._ime_disabled = disable_ime_for_window(self.root)
        self.root.bind("<FocusIn>", self._on_focus_in, add="+")
        self.canvas.focus_set()

        self._after_id = root.after(FRAME_MS, self.loop)
        self.renderer.render(self.model, float(TARGET_FPS), 0.0, self.dev_hud)
        if self.autostart:
            self.start_game()
        else:
            self.start_view.show()

    # ------------------------------------------------------------ 窗口与焦点
    def _center_window(self):
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = int(round(WINDOW_WIDTH * UI_SCALE))
        height = int(round(WINDOW_HEIGHT * UI_SCALE))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.root.geometry("%dx%d+%d+%d" % (width, height, x, y))

    def _on_focus_in(self, event):
        disable_ime_for_window(self.root)

    # ---------------------------------------------------------------- 输入
    @staticmethod
    def _normalize_key(keysym):
        return keysym.lower() if len(keysym) == 1 else keysym

    def _recompute_input(self):
        for logical in LOGICAL_KEYS:
            self.input_state.pressed[logical] = any(
                KEY_MAP.get(physical) == logical for physical in self._keys_down
            )

    def _cancel_release_job(self, physical):
        job = self._release_jobs.pop(physical, None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass

    def _clear_input(self):
        self._keys_down.clear()
        self._edge_down.clear()
        for physical in list(self._release_jobs.keys()):
            self._cancel_release_job(physical)
        self.input_state.reset()

    def on_key_press(self, event):
        if not self.running:
            return "break"
        if self.soak_seconds is not None:
            return "break"  # 浸泡模式不接受操作
        keysym = event.keysym

        if keysym in (MUTE_KEY, MUTE_KEY.upper()):
            if keysym in self._edge_down:
                return "break"
            self._edge_down.add(keysym)
            self.audio.toggle_mute()
            return "break"

        if self.screen == APP_START:
            if keysym in ("Return", "KP_Enter", "space"):
                if keysym not in self._edge_down:
                    self._edge_down.add(keysym)
                    self.start_game()
            return "break"

        if self.screen == APP_CUSTOM:
            if keysym == PAUSE_KEY:
                if keysym not in self._edge_down:
                    self._edge_down.add(keysym)
                    self._ensure_custom_screen().request_close()
            return "break"

        if self.paused:
            self._handle_paused_key(keysym)
            return "break"

        if keysym == PAUSE_KEY:
            if keysym in self._edge_down:
                return "break"
            self._edge_down.add(keysym)
            if self.model.state == STATE_RUNNING:
                self.pause_game()
            return "break"

        if DEV_MODE and keysym in DEBUG_KEYS:
            if keysym in self._edge_down:
                return "break"
            self._edge_down.add(keysym)
            self._handle_debug_key(keysym)
            return "break"

        if keysym in DEV_KEY_RESTART:
            if keysym in self._edge_down:
                return "break"
            self._edge_down.add(keysym)
            if self.model.state == STATE_GAME_OVER:
                self.restart_game()
            return "break"

        if keysym in PLAYER_KEY_GOD:
            if keysym in self._edge_down:
                return "break"
            self._edge_down.add(keysym)
            self.model.toggle_god_mode()
            return "break"

        logical = KEY_MAP.get(keysym)
        if logical is not None:
            physical = self._normalize_key(keysym)
            self._cancel_release_job(physical)
            self._keys_down.add(physical)
            self._recompute_input()
            return "break"
        return None

    def on_key_release(self, event):
        keysym = event.keysym
        self._edge_down.discard(keysym)
        if keysym in DEV_KEY_RESTART:
            self._edge_down.discard(keysym)
        logical = KEY_MAP.get(keysym)
        if logical is None:
            return None
        physical = self._normalize_key(keysym)
        if physical in self._keys_down:
            self._cancel_release_job(physical)
            self._release_jobs[physical] = self.root.after(
                KEY_RELEASE_DELAY_MS,
                lambda p=physical: self._release_key(p),
            )
        return "break"

    def _release_key(self, physical):
        self._release_jobs.pop(physical, None)
        self._keys_down.discard(physical)
        self._recompute_input()

    def on_focus_out(self, event):
        self._clear_input()

    def _input_speed(self):
        vector_x, vector_y = self.input_state.vector()
        return vec_length(vector_x, vector_y) * PLAYER_SPEED

    # ------------------------------------------------------------ 鼠标 / HUD 按钮
    def _hud_pause_hit(self, x, y):
        return (
            HUD_PAUSE_X1 <= x <= HUD_PAUSE_X2
            and HUD_PAUSE_Y1 <= y <= HUD_PAUSE_Y2
        )

    def _pointer(self, event):
        """屏幕坐标 → 逻辑坐标（画布是放大后的物理像素）。"""
        if UI_SCALE == 1.0:
            return event.x, event.y
        return event.x / UI_SCALE, event.y / UI_SCALE

    def _on_canvas_click(self, event):
        self.canvas.focus_set()
        x, y = self._pointer(event)
        over_view = getattr(self, "over_view", None)
        if over_view is not None and over_view.visible and over_view.on_click(x, y):
            return
        if self.screen == APP_CUSTOM:
            self._ensure_custom_screen().on_click(x, y)
            return
        if self.paused and getattr(self.pause_view, "visible", False):
            if self.pause_view.on_click(x, y):
                return
        if self.screen == APP_START:
            on_click = getattr(self.start_view, "on_click", None)
            if on_click is not None and on_click(x, y):
                return
        if self.screen != APP_PLAYING:
            return
        if self.paused or self.soak_seconds is not None:
            return
        if self._hud_pause_hit(x, y):
            self.pause_game()

    def _on_canvas_wheel(self, event):
        if self.screen == APP_CUSTOM:
            self._ensure_custom_screen().on_wheel(event.delta)
            return "break"

    def _on_canvas_leave(self, event):
        if self.screen == APP_CUSTOM:
            screen = self._ensure_custom_screen()
            if screen.visible:
                screen.on_motion(-1, -1)

    def _on_canvas_motion(self, event):
        x, y = self._pointer(event)
        over_view = getattr(self, "over_view", None)
        if over_view is not None and over_view.visible:
            over_view.on_motion(x, y)
            return
        if self.screen == APP_CUSTOM:
            self._ensure_custom_screen().on_motion(x, y)
            return
        if self.screen == APP_START:
            on_motion = getattr(self.start_view, "on_motion", None)
            if on_motion is not None and on_motion(x, y):
                return
        if self.paused and getattr(self.pause_view, "visible", False):
            self.pause_view.on_motion(x, y)
            return
        hovering = (
            self.screen == APP_PLAYING
            and not self.paused
            and self.soak_seconds is None
            and self.model.state == STATE_RUNNING
            and self._hud_pause_hit(x, y)
        )
        self.renderer.hud.set_pause_hover(hovering)

    # ------------------------------------------------------------ 调试按键
    def _handle_debug_key(self, keysym):
        if keysym == DEV_KEY_HUD:
            self.dev_hud = not self.dev_hud
        elif keysym == DEV_KEY_WIND:
            self.model.debug_force_wind(reverse=False)
        elif keysym == DEV_KEY_REVERSE:
            self.model.debug_force_wind(reverse=True)
        elif keysym == DEV_KEY_DAMAGE:
            self.model.debug_damage()
        elif keysym == DEV_KEY_SPAWN:
            self.model.debug_spawn()
        elif keysym == DEV_KEY_GOD:
            self.model.toggle_god_mode()

    # ------------------------------------------------------------ 暂停/恢复
    def _handle_paused_key(self, keysym):
        """暂停期间响应 R（重新开始）与 Esc（设置页返回菜单 / 菜单页启动倒计时）。"""
        if keysym in DEV_KEY_RESTART:
            if keysym in self._edge_down:
                return
            self._edge_down.add(keysym)
            self.restart_game()
            return
        if keysym != PAUSE_KEY or self._pause_job is not None:
            return
        if keysym in self._edge_down:
            return
        self._edge_down.add(keysym)
        if self.pause_settings:
            self._on_back_clicked()
        else:
            self._start_resume_countdown()

    def pause_game(self):
        """暂停：清空输入、冻结模拟、压暗画面并弹出暂停菜单。"""
        if self.screen != APP_PLAYING or self.paused:
            return False
        if self.model.state != STATE_RUNNING:
            return False
        self.paused = True
        self.pause_settings = False
        self.pause_countdown = 0
        self._clear_input()
        self.accumulator = 0.0
        self.last_time = time.perf_counter()
        self.pause_view.show_menu()
        self.audio.set_paused(True)
        return True

    def _start_resume_countdown(self):
        """继续游戏：先显示 3，再每秒递减，到 0 后恢复；倒计时期间保持暂停。"""
        if not self.paused or self._pause_job is not None:
            return False
        self.pause_settings = False
        self.pause_countdown = PAUSE_RESUME_SECONDS
        self.pause_view.show_countdown(self.pause_countdown)
        self._pause_job = self.root.after(
            PAUSE_COUNTDOWN_INTERVAL_MS, self._pause_countdown_tick
        )
        return True

    def _pause_countdown_tick(self):
        self._pause_job = None
        if not self.paused:
            return
        self.pause_countdown -= 1
        if self.pause_countdown > 0:
            self.pause_view.show_countdown(self.pause_countdown)
            self._pause_job = self.root.after(
                PAUSE_COUNTDOWN_INTERVAL_MS, self._pause_countdown_tick
            )
        else:
            self.resume_game()

    def resume_game(self):
        """结束暂停：隐藏遮罩（画面恢复原亮度）、重新对齐主循环时间基准。"""
        if not self.paused:
            return False
        self._cancel_pause_job()
        self.paused = False
        self.pause_settings = False
        self.pause_countdown = 0
        self.pause_view.hide()
        self.last_time = time.perf_counter()
        self.accumulator = 0.0
        self._fps_frames = 0
        self._fps_start = self.last_time
        self.canvas.focus_set()
        self.audio.set_paused(False)
        return True

    def _cancel_pause_job(self):
        job = self._pause_job
        self._pause_job = None
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass

    # 暂停菜单按钮回调（PauseView 只负责界面，行为都在应用层）
    def _on_continue_clicked(self):
        if self.paused:
            self._start_resume_countdown()

    def _on_restart_clicked(self):
        if not self.paused:
            return
        self.restart_game()

    def _on_settings_clicked(self):
        if not self.paused or self._pause_job is not None:
            return
        self.pause_settings = True
        self.pause_view.show_settings()

    def _on_back_clicked(self):
        if not self.paused or self._pause_job is not None:
            return
        self.pause_settings = False
        self.pause_view.show_menu()

    def _on_quit_clicked(self):
        # 暂停菜单的“退出游戏”只结束当前对局并回到开始界面，不关闭窗口；
        # 真正退出程序由开始界面的“退出”按钮负责。
        if not self.paused:
            return
        self.end_game_to_start()

    # 结束界面按钮回调（HUD 只负责界面，行为都在应用层）
    def _on_game_over_restart_clicked(self):
        if self.screen != APP_PLAYING or self.model.state != STATE_GAME_OVER:
            return
        self.restart_game()

    def _on_game_over_quit_clicked(self):
        if self.screen != APP_PLAYING or self.model.state != STATE_GAME_OVER:
            return
        self.end_game_to_start()

    def restart_game(self):
        """重新开始：重置当前对局并立即恢复运行（暂停菜单按钮 / 暂停中按 R）。"""
        if self.screen != APP_PLAYING:
            return False
        self._cancel_pause_job()
        was_paused = self.paused
        self.paused = False
        self.pause_settings = False
        self.pause_countdown = 0
        self.pause_view.hide()
        if was_paused:
            self.audio.set_paused(False)
        elif AUDIO_BGM_ENABLED:
            # 从结束界面重开：BGM 在结束时被单独暂停，这里承接下来继续播放。
            self.audio.resume_bgm()
        self._bgm_over_paused = False
        # reset_game 会清空输入、模型与渲染缓存，并把时间基准对齐到当前时刻。
        self.reset_game()
        self._fps_frames = 0
        self._fps_start = self.last_time
        self.accumulator = 0.0
        return True

    def end_game_to_start(self):
        """结束当前对局并回到开始界面（窗口与主循环保持运行）。"""
        if self.screen != APP_PLAYING:
            return False
        self._cancel_pause_job()
        # 先停掉本局 BGM（结束界面可能只暂停了它），再解除全局暂停，
        # 避免恢复瞬间又响一下；下一局从 start_game 重新起播。
        if AUDIO_BGM_ENABLED:
            self.audio.stop_bgm()
        self.audio.set_paused(False)
        self._bgm_over_paused = False
        self.paused = False
        self.pause_settings = False
        self.pause_countdown = 0
        self.pause_view.hide()
        self.screen = APP_START
        # 结束即清空本局状态：模型回到初始对局、渲染缓存与障碍物一并复位。
        self.reset_game()
        self.start_view.show()
        self.last_time = time.perf_counter()
        self.accumulator = 0.0
        self.canvas.focus_set()
        return True

    # ------------------------------------------------------------ 开始/自定义界面
    def start_game(self):
        """从开始界面进入游戏：重置模型、启动 BGM、让主循环开始推进。"""
        if self.screen == APP_PLAYING:
            return False
        self._cancel_pause_job()
        self.screen = APP_PLAYING
        self.start_view.hide()
        if self.custom_screen is not None:
            self.custom_screen.close()
        self.pause_view.hide()
        self.paused = False
        self.pause_settings = False
        self.pause_countdown = 0
        self.audio.set_paused(False)
        self._bgm_over_paused = False
        # 每次从开始界面进入对局都回到正常模式，避免上一局开的上帝模式延续下来
        self.model.god_mode = False
        self.reset_game()
        self._wind_sfx_guard_until = 0.0
        self._next_frame_at = time.perf_counter() + FRAME_MS / 1000.0
        if AUDIO_BGM_ENABLED:
            self.audio.start_bgm()
        return True

    def _ensure_custom_screen(self):
        """懒建自定义界面：挂到第一次点“自定义”上，不占启动时间。"""
        if self.custom_screen is None:
            self.custom_screen = create_custom_screen(
                self.canvas, self.root, audio_profile.resource_root(), self.audio,
                on_back=self._on_custom_back_clicked,
                assets=self.assets,
                on_background_changed=self._on_background_changed,
            )
        return self.custom_screen

    def _on_custom_clicked(self):
        if self.screen != APP_START:
            return
        self.screen = APP_CUSTOM
        self.start_view.hide()
        self._clear_input()
        self._ensure_custom_screen().open()
        self.canvas.focus_set()

    def _on_custom_back_clicked(self):
        if self.screen != APP_CUSTOM:
            return
        self.screen = APP_START
        self.start_view.show()
        self.canvas.focus_set()

    def _on_background_changed(self, choice):
        """自定义界面切换背景后立即更新渲染层；返回是否成功加载贴图。"""
        return self.renderer.apply_background(choice)

    def _on_start_quit_clicked(self):
        if self.running:
            self.root.after(0, self.close)

    # ------------------------------------------------------------ 主循环
    def loop(self):
        if not self.running:
            return
        try:
            self._drain_pending_preload()
            self._tick()
        except Exception:
            traceback.print_exc()
            self.exit_code = 1
            self.close()
            return
        if self.running:
            # 自校正调度：以目标帧时刻为准，避免 after 回调耗时被累加成漂移；
            # 卡顿后不补发一串 0 ms 回调，而是从当前时刻重新对齐。
            now = time.perf_counter()
            delay_ms = int(round((self._next_frame_at - now) * 1000.0))
            if delay_ms < 0:
                delay_ms = 0
            self._next_frame_at += FRAME_MS / 1000.0
            if self._next_frame_at <= now:
                self._next_frame_at = now + FRAME_MS / 1000.0
            self._after_id = self.root.after(delay_ms, self.loop)

    def _drain_pending_preload(self):
        """把中/大障碍与闪白贴图分帧补完，避免一次性压在启动路径上。

        开始界面每帧多补一点；进对局后少补一点，不抢帧预算。
        补完之后趁开始界面还闲，把自定义界面提前建好（首次点它不再卡半秒）。
        """
        if not getattr(self.renderer, "pending_preload", None):
            self._schedule_custom_prebuild()
            return
        budget = (
            PRELOAD_PLAY_BUDGET if self.screen == APP_PLAYING else PRELOAD_IDLE_BUDGET
        )
        if not self.renderer.load_more_assets(budget):
            self._schedule_custom_prebuild()

    def _schedule_custom_prebuild(self):
        """开始界面闲置时预热自定义界面；只调度一次，对局中不预热。"""
        if self._custom_prebuild_scheduled or self.screen != APP_START:
            return
        self._custom_prebuild_scheduled = True
        self.root.after(1, self._prebuild_custom_screen)

    def _prebuild_custom_screen(self):
        if self.custom_screen is not None or self.screen != APP_START:
            return
        self._ensure_custom_screen()

    def _tick(self):
        # 开始/自定义界面：冻结模拟与渲染，只保留 after 调度。
        if self.screen != APP_PLAYING:
            self.last_time = time.perf_counter()
            return
        # 暂停：冻结模拟与渲染，只保留 after 调度；恢复时重新对齐时间基准。
        if self.paused:
            self.last_time = time.perf_counter()
            return
        now = time.perf_counter()
        dt = now - self.last_time
        self.last_time = now
        if dt < 0.0:
            dt = 0.0
        if dt > MAX_FRAME_DT:
            dt = MAX_FRAME_DT
        self.accumulator += dt

        steps = 0
        while self.accumulator >= FIXED_DT and steps < MAX_SUBSTEPS:
            self.model.step(FIXED_DT, self.input_state.vector())
            self.accumulator -= FIXED_DT
            steps += 1
            self._consume_events()
        if steps >= MAX_SUBSTEPS:
            self.accumulator = 0.0

        self._fps_frames += 1
        fps_elapsed = now - self._fps_start
        if fps_elapsed >= 1.0:
            self.fps = self._fps_frames / fps_elapsed
            self._fps_frames = 0
            self._fps_start = now
            if (
                self.soak_seconds is not None
                and (now - self._soak_start) >= SOAK_WARMUP_TIME
                and (self._soak_min_fps is None or self.fps < self._soak_min_fps)
            ):
                self._soak_min_fps = self.fps

        self.renderer.render(self.model, self.fps, self._input_speed(), self.dev_hud)
        self._sync_over_view()
        # 对局结束：只暂停 BGM（game_over 音效还能正常播完）；重开时接着放，
        # 回到开始界面则由 end_game_to_start 停掉、下一局从头播放。
        if self.model.state == STATE_GAME_OVER and not self._bgm_over_paused:
            self._bgm_over_paused = True
            if AUDIO_BGM_ENABLED:
                self.audio.pause_bgm()
        # 背景音乐随机池：当前曲目播完时切下一首（检查内部已节流到 100 ms）。
        if AUDIO_BGM_ENABLED:
            self.audio.update()

        if self.soak_seconds is not None:
            self._soak_tick(now)

    def _consume_events(self):
        if not self.model.events:
            return
        events = list(self.model.events)
        now = time.perf_counter()
        has_hit = EVENT_HIT in events
        if has_hit:
            # 受伤声优先：同帧里即使先产生了换风事件，也不起呼啸；
            # 已经在响的风变音不再硬停（waveOut 中途 reset 会产生“啪”的爆音），
            # 只保证受伤后的 0.35 s 内不新起风变音。
            self._wind_sfx_guard_until = now + 0.35
        for event_name in events:
            if event_name == EVENT_WIND_CHANGE:
                if has_hit or now < self._wind_sfx_guard_until:
                    continue
            self.audio_play(event_name)
        self.model.events.clear()

    def audio_play(self, event_name):
        """消费模型语义事件并播放对应音效。

        事件含 EVENT_HIT / EVENT_WIND_CHANGE / EVENT_GAME_OVER，
        以及后续道具用的 "pickup"。EVENT_HIT 会在三条受击变体里随机抽一条；
        音频不可用时静默返回，模型层完全无感。
        """
        ok = self.audio.play(event_name)
        if os.environ.get("DAFENG_AUDIO_DEBUG"):
            print(
                "[audio] %.3f %-10s -> %-8s backend=%s ok=%s"
                % (
                    time.perf_counter(),
                    event_name,
                    self.audio.last_played or event_name,
                    self.audio.backend,
                    ok,
                ),
                file=sys.stderr,
                flush=True,
            )

    # ------------------------------------------------------------ 重开与退出
    def reset_game(self):
        over_view = getattr(self, "over_view", None)
        if over_view is not None:
            over_view.hide()
        self.model.reset()
        self.renderer.reset_view()
        self._clear_input()
        self._wind_sfx_guard_until = 0.0
        self.last_time = time.perf_counter()
        self.accumulator = 0.0
        self.canvas.focus_set()

    def _sync_over_view(self):
        """模型进入结束态时弹出蜡笔结束遮罩；重开后收起。"""
        over_view = getattr(self, "over_view", None)
        if over_view is None:
            return
        if self.model.state == STATE_GAME_OVER:
            if not over_view.visible:
                # 双保险：旧点阵遮罩必须在截图之前让位，否则它会被
                # _crayon_backdrop 一起截进模糊背景里，看起来就是
                # “旧 UI 先出现、新界面又叠在上面”。
                self.renderer.hud.set_overlay_crayon(True)
                over_view.show(self.model)
        elif over_view.visible:
            over_view.hide()

    def close(self):
        if not self.running:
            self.audio.close()
            try:
                self.root.destroy()
            except Exception:
                pass
            return
        self.running = False
        self._cancel_pause_job()
        self.audio.close()
        try:
            if self._after_id is not None:
                self.root.after_cancel(self._after_id)
        except Exception:
            pass
        for physical in list(self._release_jobs.keys()):
            self._cancel_release_job(physical)
        try:
            self.root.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------ 浸泡测试
    def _soak_tick(self, now):
        elapsed = now - self._soak_start
        if now - self._soak_last_report >= SOAK_REPORT_INTERVAL:
            self._soak_last_report = now
            items = len(self.canvas.find_all())
            obstacles = len(self.model.obstacles)
            print(
                "t=%.1fs fps=%.1f canvas_items=%d obstacles=%d"
                % (elapsed, self.fps, items, obstacles),
                flush=True,
            )
            self._soak_reports.append((elapsed, self.fps, items, obstacles))
            self._soak_peak_items = max(self._soak_peak_items, items)

        if elapsed >= float(self.soak_seconds):
            self._finish_soak(elapsed)

    def _finish_soak(self, elapsed):
        items = len(self.canvas.find_all())
        obstacles = len(self.model.obstacles)
        min_fps = self._soak_min_fps if self._soak_min_fps is not None else self.fps
        peak_items = max(self._soak_peak_items, items)
        obstacles_ok = obstacles <= OBSTACLE_MAX_ACTIVE
        fps_ok = min_fps >= SOAK_MIN_FPS
        items_ok = peak_items <= SOAK_MAX_ITEMS
        bounded_ok = items <= SOAK_MAX_ITEMS
        ok = fps_ok and items_ok and obstacles_ok and bounded_ok and self.exit_code == 0
        print(
            "soak summary: duration=%.1fs min_fps=%.1f peak_items=%d "
            "final_items=%d final_obstacles=%d"
            % (elapsed, min_fps, peak_items, items, obstacles),
            flush=True,
        )
        print(
            "soak checks: fps>=%.0f %s | items<=%d %s | obstacles<=%d %s | final_bounded %s"
            % (
                SOAK_MIN_FPS,
                "PASS" if fps_ok else "FAIL",
                SOAK_MAX_ITEMS,
                "PASS" if items_ok else "FAIL",
                OBSTACLE_MAX_ACTIVE,
                "PASS" if obstacles_ok else "FAIL",
                "PASS" if bounded_ok else "FAIL",
            ),
            flush=True,
        )
        print("soak result: %s" % ("PASS" if ok else "FAIL"), flush=True)
        self.exit_code = 0 if ok else 1
        self.running = False
        try:
            if self._after_id is not None:
                self.root.after_cancel(self._after_id)
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


# =============================================================================
# 10. 扩展预留（只保留接入说明与最小字段，不写半成品实现）
# =============================================================================
# - 回血道具：GameModel.pickups、update_pickups(dt)、resolve_pickup_collisions()、
#   Renderer.draw_pickups()；生成可复用 spawn_sites/spawn_point；拾取回 1 心并夹到上限。
# - 射击清障：InputState 增加 shoot 边缘触发；GameModel.projectiles、update_projectiles()；
#   命中后给障碍标记 dead（与撞玩家消失共用同一移除路径）；不引入第三方库。
# - 障碍物消失动画：GameModel 已在撞玩家时置 Obstacle.dead；
#   在 ObstacleLayer.sync 的删除分支里接管 item（记录末帧坐标 → 生成特效 item → 延时删除），
#   或给 Obstacle 增加 dying_timer 后由单独的 EffectLayer 播放，均不需要改碰撞规则。
# - 角色/障碍贴图：已接入（assets.py + tools/art/）。玩家是 n/s × idle/walk0/walk1 + 受击，
#   脚底锚点 + 脚下阴影 + 按脚底 y 深度排序；朝向用 SpriteFacing（左/右两张）映射。
#   真美术替换 = 保持文件名与 125×135 规格；模型层完全不动。
#   贴图引用由 AssetStore 持有，防止 PhotoImage 被 GC 回收成空白。
# - 音乐/音效：已接入（audio.py + tools/audio/bake_audio.py）。模型只发语义事件
#   （EVENT_HIT / EVENT_WIND_CHANGE / EVENT_GAME_OVER），应用层消费后播放。
# - 统计/记分扩展：GameModel 已有 wind_history / change_count；新增只读字段即可。
# - 新增障碍档位：加一组常量并在 OBSTACLE_TIERS 加一行；生成/碰撞/颜色自动跟随。
# - 新增风向：在 WIND_DIRECTIONS 加一行（名称 + 单位向量）；HUD/生成/转向自动适配。


# =============================================================================
# 11. 入口与自测
# =============================================================================

def _obstacle_for_test(vx, vy, base_speed, tier="small"):
    return Obstacle(
        oid=0,
        x=PLAYER_START_X,
        y=PLAYER_START_Y,
        vx=vx,
        vy=vy,
        radius=TIER_BY_NAME[tier]["radius"],
        tier=tier,
        damage=TIER_BY_NAME[tier]["damage"],
        base_speed=base_speed,
    )


def _angle_degrees(vx, vy):
    return math.degrees(math.atan2(vy, vx))


def test_t1_input_and_speed():
    state = InputState()
    assert state.vector() == (0.0, 0.0)

    state.pressed["up"] = True
    state.pressed["right"] = True
    ux, uy = state.vector()
    assert abs(vec_length(ux, uy) - 1.0) < 1e-9
    assert abs(ux - _DIAG) < 1e-9
    assert abs(uy + _DIAG) < 1e-9

    straight = PLAYER_SPEED * 1.0
    diagonal = PLAYER_SPEED * vec_length(ux, uy)
    assert abs(straight - PLAYER_SPEED) < 1e-9
    assert abs(diagonal - straight) < 1e-9

    wind_x, wind_y = WIND_SPEED, 0.0
    against = vec_length(-PLAYER_SPEED + wind_x, wind_y)
    along = vec_length(PLAYER_SPEED + wind_x, wind_y)
    side = vec_length(0.0 + wind_x, -PLAYER_SPEED + wind_y)
    assert abs(against - (PLAYER_SPEED - WIND_SPEED)) < 0.5
    assert abs(along - (PLAYER_SPEED + WIND_SPEED)) < 0.5
    assert abs(side - math.hypot(PLAYER_SPEED, WIND_SPEED)) < 0.5

    combined_x = ux * PLAYER_SPEED + wind_x
    combined_y = uy * PLAYER_SPEED + wind_y
    assert abs(combined_x - (_DIAG * PLAYER_SPEED + WIND_SPEED)) < 0.5
    assert abs(combined_y + _DIAG * PLAYER_SPEED) < 0.5
    assert abs(vec_length(combined_x, combined_y) - math.hypot(_DIAG * PLAYER_SPEED + WIND_SPEED, _DIAG * PLAYER_SPEED)) < 0.5


def test_t2_wind_scheduler():
    model = GameModel(seed=20261021)
    model.god_mode = True
    previous_index = model.wind.index
    changes = []
    steps = int(round(600.0 / FIXED_DT))
    for _ in range(steps):
        model.step(FIXED_DT, (0.0, 0.0))
        model.events.clear()
        if model.wind.index != previous_index:
            changes.append((model.wind.index, model.wind.duration))
            previous_index = model.wind.index

    assert len(changes) >= 10
    assert len({index for index, _ in changes}) >= 6
    assert model.wind_speed == WIND_SPEED_MAX
    for position in range(1, len(changes)):
        assert changes[position][0] != changes[position - 1][0]
    for _, duration in changes:
        assert duration >= WIND_DUR_MIN_FLOOR

    for elapsed in (0.0, 60.0, 120.0, 180.0, 240.0):
        low, high = wind_duration_bounds(elapsed)
        rng = random.Random(1000 + int(elapsed))
        for _ in range(100):
            value = sample_duration(elapsed, rng)
            assert low - 1e-9 <= value <= high + 1e-9

    rng_early = random.Random(7)
    rng_late = random.Random(7)
    early_mean = sum(sample_duration(0.0, rng_early) for _ in range(2000)) / 2000.0
    late_mean = sum(sample_duration(300.0, rng_late) for _ in range(2000)) / 2000.0
    assert early_mean > late_mean + 5.0


def test_t3_difficulty_curve():
    assert wind_duration_bounds(0.0) == (WIND_DUR_MIN_START, WIND_DUR_MAX_START)
    low, high = wind_duration_bounds(WIND_RAMP_TIME)
    assert abs(low - WIND_DUR_MIN_FLOOR) < 1e-9
    assert abs(high - WIND_DUR_MAX_FLOOR) < 1e-9

    assert abs(spawn_interval(0.0) - SPAWN_INTERVAL_START) < 1e-9
    assert abs(spawn_interval(SPAWN_RAMP_TIME) - SPAWN_INTERVAL_FLOOR) < 1e-9
    previous = spawn_interval(0.0)
    for moment in range(1, 181):
        value = spawn_interval(float(moment))
        assert value <= previous + 1e-12
        assert value >= SPAWN_INTERVAL_FLOOR - 1e-9
        previous = value

    # 风速档位：初始最低，每 WIND_SPEED_INTERVAL 秒升一档，封顶 WIND_SPEED_MAX
    assert wind_speed_at(0.0) == (WIND_SPEED, 0)
    assert wind_speed_at(WIND_SPEED_INTERVAL - 1e-9)[1] == 0
    speed_one, tier_one = wind_speed_at(WIND_SPEED_INTERVAL)
    assert tier_one == 1
    assert abs(speed_one - (WIND_SPEED + WIND_SPEED_STEP)) < 1e-9
    previous_speed = wind_speed_at(0.0)[0]
    for moment in range(1, 400):
        value, _ = wind_speed_at(float(moment))
        assert value >= previous_speed - 1e-9
        assert value <= WIND_SPEED_MAX + 1e-9
        previous_speed = value
    assert wind_speed_at(10000.0)[0] == WIND_SPEED_MAX
    assert wind_speed_at(10000.0)[1] == WIND_SPEED_MAX_TIER


def _run_turn(target_x, target_y, start_speed):
    obstacle = _obstacle_for_test(start_speed, 0.0, start_speed)
    begin_turn(obstacle, target_x, target_y)
    turn_time = TIER_BY_NAME["small"].get("turn_time", OBSTACLE_TURN_TIME)
    samples = []
    steps = int(math.ceil((turn_time + 0.05) / FIXED_DT))
    for _ in range(steps):
        update_obstacle(obstacle, FIXED_DT)
        samples.append((obstacle.vx, obstacle.vy, obstacle.turn is None))
    return obstacle, samples


def test_t4_inertia():
    speed = OBSTACLE_LARGE_SPEED

    # 角度模式判定：180° 才走刹车-倒车模式；135°/90°/45° 都走弧线
    small_turn_time = TIER_BY_NAME["small"]["turn_time"]
    probe_180 = _obstacle_for_test(speed, 0.0, speed)
    begin_turn(probe_180, -1.0, 0.0)
    assert probe_180.turn.brake_turn is True
    assert abs(
        probe_180.turn.duration - small_turn_time * OBSTACLE_BRAKE_TURN_SCALE
    ) < 1e-9
    probe_135 = _obstacle_for_test(speed, 0.0, speed)
    begin_turn(probe_135, -_DIAG, _DIAG)
    assert probe_135.turn.brake_turn is False
    assert abs(
        probe_135.turn.duration - small_turn_time * OBSTACLE_135_TURN_SCALE
    ) < 1e-9
    probe_90 = _obstacle_for_test(speed, 0.0, speed)
    begin_turn(probe_90, 0.0, 1.0)
    assert probe_90.turn.brake_turn is False
    probe_45 = _obstacle_for_test(speed, 0.0, speed)
    begin_turn(probe_45, _DIAG, _DIAG)
    assert probe_45.turn.brake_turn is False

    # 135° 仍需保持弧线平滑（航向逐步变化，而不是瞬间折向）
    _, samples_135_arc = _run_turn(-_DIAG, _DIAG, speed)
    angles_135 = [_angle_degrees(vx, vy) for vx, vy, _ in samples_135_arc]
    max_step_135 = max(
        abs(wrap_to_pi(math.radians(angles_135[i] - angles_135[i - 1])))
        for i in range(1, len(angles_135))
    )
    assert math.degrees(max_step_135) <= 3.2

    # 180°：应沿原方向直线减速到 0，再沿反向加速；不产生侧向位移，也不画半圆
    obstacle, samples = _run_turn(-1.0, 0.0, speed)
    projection = [vx * -1.0 + vy * 0.0 for vx, vy, _ in samples]
    q_values = [abs(value) for value in projection]
    assert min(q_values) < 0.05 * speed
    sign_changes = 0
    previous_sign = 0
    for value in projection:
        if abs(value) < 1e-9:
            continue
        sign = 1 if value > 0 else -1
        if previous_sign != 0 and sign != previous_sign:
            sign_changes += 1
        previous_sign = sign
    assert sign_changes == 1

    final_vx, final_vy, finished = samples[-1]
    assert finished
    final_angle = math.atan2(final_vy, final_vx)
    assert abs(math.degrees(wrap_to_pi(final_angle - math.pi))) < 1.0

    speeds = [vec_length(vx, vy) for vx, vy, _ in samples]
    assert min(speeds) < 0.05 * speed
    assert max(speeds) > 0.95 * speed
    assert abs(speeds[-1] - speed) < 1e-6
    minimum_index = min(range(len(speeds)), key=speeds.__getitem__)
    for index in range(1, minimum_index + 1):
        assert speeds[index] <= speeds[index - 1] + 1e-9
    for index in range(minimum_index + 1, len(speeds)):
        assert speeds[index] >= speeds[index - 1] - 1e-9

    # 横向位移必须为 0（真正的一维减速-反向，而不是圆弧）
    straight = _obstacle_for_test(speed, 0.0, speed)
    begin_turn(straight, -1.0, 0.0)
    start_y = straight.y
    max_cross_track = 0.0
    turn_time = TIER_BY_NAME["small"].get("turn_time", OBSTACLE_TURN_TIME)
    for _ in range(int(math.ceil((turn_time + 0.05) / FIXED_DT))):
        update_obstacle(straight, FIXED_DT)
        max_cross_track = max(max_cross_track, abs(straight.y - start_y))
    assert max_cross_track < 1e-9

    before = (obstacle.vx, obstacle.vy)
    for _ in range(120):
        update_obstacle(obstacle, FIXED_DT)
    assert abs(obstacle.vx - before[0]) < 1e-9
    assert abs(obstacle.vy - before[1]) < 1e-9

    # 45°：沿新风向的投影存在低于两端的局部极小
    _, samples_45 = _run_turn(_DIAG, _DIAG, speed)
    p45 = [vx * _DIAG + vy * _DIAG for vx, vy, _ in samples_45]
    assert any(
        p45[i] < p45[0] - 1e-6 and p45[i] < p45[-1] - 1e-6
        for i in range(1, len(p45) - 1)
    )

    # 90°：p 单调不减，且非反向转向的航向变化仍需平滑
    _, samples_90 = _run_turn(0.0, 1.0, speed)
    p90 = [vx * 0.0 + vy * 1.0 for vx, vy, _ in samples_90]
    for i in range(1, len(p90)):
        assert p90[i] >= p90[i - 1] - 1e-6
    angles_90 = [_angle_degrees(vx, vy) for vx, vy, _ in samples_90]
    max_step_90 = max(
        abs(wrap_to_pi(math.radians(angles_90[i] - angles_90[i - 1])))
        for i in range(1, len(angles_90))
    )
    assert math.degrees(max_step_90) <= 3.2

    # 135°：q 唯一零极小后回升，p 只过零一次
    _, samples_135 = _run_turn(-_DIAG, _DIAG, speed)
    p135 = [vx * -_DIAG + vy * _DIAG for vx, vy, _ in samples_135]
    q135 = [abs(value) for value in p135]
    assert min(q135) < 0.05 * speed
    crossings = 0
    previous_sign = 0
    for value in p135:
        if abs(value) < 1e-9:
            continue
        sign = 1 if value > 0 else -1
        if previous_sign != 0 and sign != previous_sign:
            crossings += 1
        previous_sign = sign
    assert crossings == 1


def test_t5_collision_and_iframe():
    model = GameModel(seed=11)
    model.obstacles = []
    obstacle = Obstacle(
        oid=0,
        x=model.player.x,
        y=model.player.y,
        vx=0.0,
        vy=0.0,
        radius=OBSTACLE_SMALL_RADIUS,
        tier="small",
        damage=1,
        base_speed=OBSTACLE_SMALL_SPEED,
    )
    model.obstacles.append(obstacle)

    model.step(FIXED_DT, (0.0, 0.0))
    assert model.player.hearts == PLAYER_START_HEARTS - 1
    # 造成伤害后障碍物消失
    assert obstacle.dead is True
    assert obstacle not in model.obstacles

    # 无敌帧内重叠新障碍物：不扣心，也不会消失
    blocker = Obstacle(
        oid=1,
        x=model.player.x,
        y=model.player.y,
        vx=0.0,
        vy=0.0,
        radius=OBSTACLE_SMALL_RADIUS,
        tier="small",
        damage=1,
        base_speed=OBSTACLE_SMALL_SPEED,
    )
    model.obstacles = [blocker]
    for _ in range(int(0.6 / FIXED_DT)):
        model.player.x, model.player.y = blocker.x, blocker.y
        model.step(FIXED_DT, (0.0, 0.0))
    assert model.player.hearts == PLAYER_START_HEARTS - 1
    assert blocker.dead is False
    assert blocker in model.obstacles

    # 无敌帧结束后再次命中：扣心并消失
    for _ in range(int(0.2 / FIXED_DT)):
        model.player.x, model.player.y = blocker.x, blocker.y
        model.step(FIXED_DT, (0.0, 0.0))
    assert model.player.hearts <= PLAYER_START_HEARTS - 2
    assert 0 <= model.player.hearts <= PLAYER_MAX_HEARTS
    assert blocker not in model.obstacles


def test_t6_game_over():
    model = GameModel(seed=12)
    model.player.hearts = 0
    model.step(FIXED_DT, (0.0, 0.0))
    assert model.state == STATE_GAME_OVER
    assert model.reason == REASON_HEARTS_ZERO
    frozen = (model.elapsed, model.player.hearts)
    model.step(1.0, (1.0, 0.0))
    assert (model.elapsed, model.player.hearts) == frozen

    model.reset()
    model.player.x = -5.0
    model.player.y = PLAYER_START_Y
    model.step(FIXED_DT, (0.0, 0.0))
    assert model.state == STATE_GAME_OVER
    assert model.reason == REASON_BLOWN_AWAY

    model.reset()
    model.player.y = WINDOW_HEIGHT + 5.0
    model.step(FIXED_DT, (0.0, 0.0))
    assert model.state == STATE_GAME_OVER
    assert model.reason == REASON_BLOWN_AWAY


def test_t7_reset():
    model = GameModel(seed=13)
    for _ in range(240):
        model.step(FIXED_DT, (1.0, 0.0))
        model.events.clear()
    model.debug_spawn()
    model.reset()

    assert model.state == STATE_RUNNING
    assert model.elapsed == 0.0
    assert model.reason is None
    assert model.events == []
    assert model.player.hearts == PLAYER_START_HEARTS
    assert model.player.invuln_timer == 0.0
    assert model.obstacles == []
    assert model.obstacle_seq == 0
    assert model.wind_history == []
    assert model.spawn_timer == SPAWN_FIRST_DELAY
    assert 0 <= model.wind.index < len(WIND_DIRECTIONS)
    assert WIND_DUR_MIN_START <= model.wind.duration <= WIND_DUR_MAX_START
    assert model.wind.remaining == model.wind.duration
    assert model.wind.change_count == 0
    assert model.wind_speed == WIND_SPEED
    assert model.wind_speed_tier == 0

    fresh = GameModel(seed=999)
    for attribute in ("x", "y", "radius", "hearts", "face_x", "face_y"):
        assert getattr(model.player, attribute) == getattr(fresh.player, attribute)


def test_t8_spawn_and_recycle():
    model = GameModel(seed=15)
    model.god_mode = True
    seen = set()
    spawns = []
    max_count = 0
    steps = int(round(180.0 / FIXED_DT))
    for _ in range(steps):
        model.step(FIXED_DT, (0.0, 0.0))
        model.events.clear()
        max_count = max(max_count, len(model.obstacles))
        for obstacle in model.obstacles:
            if obstacle.oid not in seen:
                seen.add(obstacle.oid)
                # 立即快照：对象随后会被继续更新，不能留引用做延后检查。
                spawns.append(
                    (
                        obstacle.x - obstacle.vx * FIXED_DT,
                        obstacle.y - obstacle.vy * FIXED_DT,
                        obstacle.radius,
                        obstacle.tier,
                        obstacle.damage,
                        vec_length(obstacle.vx, obstacle.vy),
                        model.wind.ux,
                        model.wind.uy,
                    )
                )

    assert spawns
    assert max_count <= OBSTACLE_MAX_ACTIVE

    for spawn_x, spawn_y, radius, tier_name, damage, speed, ux, uy in spawns:
        sites = spawn_sites(ux, uy)
        margin = radius + SPAWN_MARGIN_EXTRA
        matched = False
        for edge, low, high in sites:
            if edge == EDGE_LEFT:
                edge_ok = abs(spawn_x + margin) < 1e-6 and low - 1e-6 <= spawn_y <= high + 1e-6
            elif edge == EDGE_RIGHT:
                edge_ok = (
                    abs(spawn_x - (WINDOW_WIDTH + margin)) < 1e-6
                    and low - 1e-6 <= spawn_y <= high + 1e-6
                )
            elif edge == EDGE_TOP:
                edge_ok = abs(spawn_y + margin) < 1e-6 and low - 1e-6 <= spawn_x <= high + 1e-6
            else:
                edge_ok = (
                    abs(spawn_y - (WINDOW_HEIGHT + margin)) < 1e-6
                    and low - 1e-6 <= spawn_x <= high + 1e-6
                )
            if edge_ok:
                matched = True
                break
        assert matched
        assert entry_chord(spawn_x, spawn_y, ux, uy) >= SPAWN_MIN_CHORD - 1e-6
        tier = TIER_BY_NAME[tier_name]
        # A2：生成速度带 ±8% 个体差异，首步又叠了一层阵风，因此按组合上/下界检查。
        gust_max = OBSTACLE_GUST_PROFILES[tier_name]["amp"] * 1.2
        speed_low = tier["speed"] * (1.0 - OBSTACLE_SPEED_JITTER) * (1.0 - gust_max)
        speed_high = tier["speed"] * (1.0 + OBSTACLE_SPEED_JITTER) * (1.0 + gust_max)
        assert speed_low - 1e-6 <= speed <= speed_high + 1e-6, (tier_name, speed)
        assert damage == tier["damage"] == 1

    # 越界回收
    model2 = GameModel(seed=16)
    model2.god_mode = True
    model2.obstacles = [
        Obstacle(
            oid=0,
            x=WINDOW_WIDTH + DESPAWN_MARGIN + 1.0,
            y=100.0,
            vx=0.0,
            vy=0.0,
            radius=OBSTACLE_SMALL_RADIUS,
            tier="small",
            damage=1,
            base_speed=OBSTACLE_SMALL_SPEED,
        )
    ]
    model2.step(FIXED_DT, (0.0, 0.0))
    assert model2.obstacles == []

    # 超龄兜底回收
    model3 = GameModel(seed=17)
    model3.god_mode = True
    model3.obstacles = [
        Obstacle(
            oid=0,
            x=WINDOW_WIDTH * 0.5,
            y=WINDOW_HEIGHT * 0.5,
            vx=0.0,
            vy=0.0,
            radius=OBSTACLE_SMALL_RADIUS,
            tier="small",
            damage=1,
            base_speed=OBSTACLE_SMALL_SPEED,
            age=OBSTACLE_MAX_AGE + 0.1,
        )
    ]
    model3.step(FIXED_DT, (0.0, 0.0))
    assert model3.obstacles == []

    # 上限是硬限制，且达上限时仍重置生成计时
    model4 = GameModel(seed=18)
    model4.god_mode = True
    model4.obstacles = [
        Obstacle(
            oid=index,
            x=50.0 + index * 10.0,
            y=50.0,
            vx=0.0,
            vy=0.0,
            radius=OBSTACLE_SMALL_RADIUS,
            tier="small",
            damage=1,
            base_speed=OBSTACLE_SMALL_SPEED,
        )
        for index in range(OBSTACLE_MAX_ACTIVE)
    ]
    model4.spawn_timer = 0.0001
    model4.step(FIXED_DT, (0.0, 0.0))
    assert len(model4.obstacles) <= OBSTACLE_MAX_ACTIVE
    assert model4.spawn_timer > 0.0


def test_t9_import_whitelist():
    if getattr(sys, "frozen", False):
        return  # 打包后没有源码文件可做 AST 检查；源码运行时才校验
    assert __file__
    with open(__file__, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    allowed = set(getattr(sys, "stdlib_module_names", ()))
    allowed |= {
        "__future__",
        "tkinter",
        "math",
        "random",
        "time",
        "sys",
        "ast",
        "argparse",
        "dataclasses",
        "ctypes",
        "traceback",
    }
    # 本地模块 + 允许的可选第三方运行时依赖（缺失时自动降级，不影响逻辑层）
    allowed |= {"assets", "audio", "audio_profile", "audio_import",
                "background_profile", "crayon_text", "PIL", "pygame",
                "winsound"}
    extra = modules - allowed
    assert not extra, "非标准库 import: %s" % (sorted(extra),)


def test_t10_headless_layering():
    if tk is not None:
        assert getattr(tk, "_default_root", None) is None
    model = GameModel(seed=19)
    model.god_mode = True
    for _ in range(1200):
        model.step(FIXED_DT, (0.0, 0.0))
        model.events.clear()
    for entity in [model, model.player] + list(model.obstacles):
        assert not hasattr(entity, "canvas")
        assert not hasattr(entity, "item_id")


def test_t11_boundary_values():
    for _, ux, uy in WIND_DIRECTIONS:
        assert abs(vec_length(ux, uy) - 1.0) < 1e-9

    assert smoothstep(0.0) == 0.0
    assert smoothstep(1.0) == 1.0
    assert abs(smoothstep(0.5) - 0.5) < 1e-12

    assert abs(wrap_to_pi(math.pi * 1.5) + math.pi * 0.5) < 1e-9
    for angle in (math.pi, -math.pi, 5.0 * math.pi, -5.0 * math.pi):
        wrapped = wrap_to_pi(angle)
        assert -math.pi - 1e-9 <= wrapped <= math.pi + 1e-9
        assert abs(abs(wrapped) - math.pi) < 1e-9

    model = GameModel(seed=20)
    model.god_mode = True
    for _ in range(600):
        model.step(FIXED_DT, (1.0, 0.0))
        model.events.clear()
    values = [model.player.x, model.player.y, model.wind.ux, model.wind.uy]
    for obstacle in model.obstacles:
        values.extend([obstacle.x, obstacle.y, obstacle.vx, obstacle.vy])
    assert all(math.isfinite(value) for value in values)


def test_t12_god_mode():
    model = GameModel(seed=21)
    assert model.god_mode is False

    assert model.toggle_god_mode() is True
    # 上帝模式下无输入：完全不受风推，位置保持不变
    start_x, start_y = model.player.x, model.player.y
    for _ in range(int(1.0 / FIXED_DT)):
        model.step(FIXED_DT, (0.0, 0.0))
        model.events.clear()
    assert abs(model.player.x - start_x) < 1e-9
    assert abs(model.player.y - start_y) < 1e-9
    assert model.player.hearts == PLAYER_START_HEARTS

    # 仍然可以用输入自由移动
    model.player.x, model.player.y = PLAYER_START_X, PLAYER_START_Y
    for _ in range(int(0.5 / FIXED_DT)):
        model.step(FIXED_DT, (1.0, 0.0))
        model.events.clear()
    assert model.player.x > PLAYER_START_X + 100.0

    # 无敌：贴身障碍物不扣心，F6 自伤也无效
    model.obstacles = []
    model.obstacles.append(_obstacle_for_test(0.0, 0.0, OBSTACLE_SMALL_SPEED))
    model.player.x, model.player.y = PLAYER_START_X, PLAYER_START_Y
    model.step(FIXED_DT, (0.0, 0.0))
    assert model.player.hearts == PLAYER_START_HEARTS
    assert model.debug_damage() is False
    assert model.player.hearts == PLAYER_START_HEARTS

    # 不会被吹出窗口：越界位置会被夹回可玩区域
    model.player.x = -50.0
    model.player.y = WINDOW_HEIGHT + 50.0
    model.step(FIXED_DT, (0.0, 0.0))
    assert model.state == STATE_RUNNING
    assert model.player.radius <= model.player.x <= WINDOW_WIDTH - model.player.radius
    assert model.player.radius <= model.player.y <= WINDOW_HEIGHT - model.player.radius

    # 关闭后恢复风推：无输入也会被吹动
    assert model.toggle_god_mode() is False
    model.player.x, model.player.y = PLAYER_START_X, PLAYER_START_Y
    moved_from = (model.player.x, model.player.y)
    for _ in range(int(0.3 / FIXED_DT)):
        model.step(FIXED_DT, (0.0, 0.0))
        model.events.clear()
    assert vec_length(model.player.x - moved_from[0], model.player.y - moved_from[1]) > 1.0


def test_t13_obstacle_removed_on_hit():
    model = GameModel(seed=23)
    model.obstacles = []
    hit = _obstacle_for_test(0.0, 0.0, OBSTACLE_SMALL_SPEED)
    bystander = _obstacle_for_test(0.0, 0.0, OBSTACLE_SMALL_SPEED)
    bystander.oid = 1
    bystander.x = PLAYER_START_X + 300.0
    bystander.y = PLAYER_START_Y
    model.obstacles = [hit, bystander]
    model.events.clear()

    model.step(FIXED_DT, (0.0, 0.0))
    assert model.player.hearts == PLAYER_START_HEARTS - 1
    assert hit.dead is True
    assert hit not in model.obstacles, "命中玩家的障碍物未移出模拟"
    assert bystander in model.obstacles, "未命中的障碍物不应消失"
    assert len(model.obstacles) == 1
    assert EVENT_HIT in model.events

    # 重置后不残留 dead 障碍
    model.reset()
    assert model.obstacles == []


def test_t15_sprite_facing():
    """左/右两张贴图：斜向映射到左/右，上下移动保持当前贴图不变。"""
    from assets import SpriteFacing

    facing = SpriteFacing()
    assert facing.update(1.0, 0.0) == "e"
    assert facing.update(0.707, 0.707) == "e"    # 右下
    assert facing.update(0.707, -0.707) == "e"   # 右上
    assert facing.update(-0.707, 0.707) == "w"   # 左下
    assert facing.update(-0.707, -0.707) == "w"  # 左上
    assert facing.update(0.0, 1.0) == "w"        # 向下：保持左
    assert facing.update(0.0, -1.0) == "w"       # 向上：保持左
    assert facing.update(0.1, 0.0) == "w"        # 死区内不改
    assert facing.update(1.0, 0.0) == "e"
    facing.reset()
    assert facing.direction == "e"


def test_t16_hit_stun_and_knockback():
    """受击硬直：0.2s 内输入无效，沿风吹向短距离位移，且不会被吹出界。"""
    model = GameModel(seed=23)
    model.wind.ux, model.wind.uy = 1.0, 0.0
    model.player.x, model.player.y = 400.0, 350.0
    model.obstacles = [
        Obstacle(
            oid=0,
            x=400.0,
            y=350.0,
            vx=0.0,
            vy=0.0,
            radius=OBSTACLE_SMALL_RADIUS,
            tier="small",
            damage=1,
            base_speed=OBSTACLE_SMALL_SPEED,
        )
    ]
    model.step(FIXED_DT, (0.0, 0.0))
    assert model.player.hearts == PLAYER_START_HEARTS - 1
    assert abs(model.player.stun_timer - HIT_STUN_TIME) < 1e-9

    start_x = model.player.x
    for _ in range(int(0.12 / FIXED_DT)):
        model.step(FIXED_DT, (-1.0, 0.0))  # 硬直期间按住“左”
    assert model.player.x > start_x + 5.0  # 仍被风往右推
    assert model.player.stun_timer > 0.0

    for _ in range(int(0.15 / FIXED_DT)):
        model.step(FIXED_DT, (0.0, 0.0))
    assert model.player.stun_timer == 0.0
    before_x = model.player.x
    model.step(FIXED_DT, (-1.0, 0.0))
    assert model.player.x < before_x  # 操控恢复

    # 靠右墙受击：硬直位移被夹在窗口内，不会直接判负
    wall = GameModel(seed=29)
    wall.wind.ux, wall.wind.uy = 1.0, 0.0
    wall.player.x = WINDOW_WIDTH - wall.player.radius - 1.0
    wall.player.y = 350.0
    wall.obstacles = [
        Obstacle(
            oid=0,
            x=wall.player.x,
            y=wall.player.y,
            vx=0.0,
            vy=0.0,
            radius=OBSTACLE_SMALL_RADIUS,
            tier="small",
            damage=1,
            base_speed=OBSTACLE_SMALL_SPEED,
        )
    ]
    wall.step(FIXED_DT, (0.0, 0.0))
    for _ in range(int(0.5 / FIXED_DT)):
        if wall.player.stun_timer <= 0.0:
            break
        wall.step(FIXED_DT, (1.0, 0.0))
        # 硬直期间始终被夹在窗口内
        assert wall.player.x <= WINDOW_WIDTH - wall.player.radius + 1e-9
    assert wall.state == STATE_RUNNING

    # 受伤贴图朝向：障碍物在右侧 -> 朝右；在左侧 -> 朝左；正上/正下沿用最后朝向
    def hit_face_x(obstacle_dx):
        probe = GameModel(seed=37)
        probe.wind.ux, probe.wind.uy = 0.0, 0.0
        probe.player.x, probe.player.y = 400.0, 350.0
        probe.obstacles = [
            Obstacle(
                oid=0,
                x=probe.player.x + obstacle_dx,
                y=probe.player.y,
                vx=0.0,
                vy=0.0,
                radius=OBSTACLE_SMALL_RADIUS,
                tier="small",
                damage=1,
                base_speed=OBSTACLE_SMALL_SPEED,
            )
        ]
        probe.step(FIXED_DT, (0.0, 0.0))
        return probe.player.hurt_face_x

    assert hit_face_x(14.0) == 1.0
    assert hit_face_x(-14.0) == -1.0
    assert hit_face_x(0.0) == 0.0
    assert hurt_direction(1.0, "w") == "e"
    assert hurt_direction(-1.0, "e") == "w"
    assert hurt_direction(0.0, "w") == "w"  # 纵向命中沿用最后朝向


def test_t17_hitbox_matches_sprite():
    """受击判定覆盖角色上半身：头部高度有判定，脚底下方的障碍不命中。"""
    model = GameModel(seed=31)
    model.wind.ux, model.wind.uy = 0.0, 0.0
    model.wind.remaining = 999.0
    feet_y = model.player.y + model.player.radius * PLAYER_FEET_OFFSET_RATIO

    model.obstacles = [
        Obstacle(
            oid=0,
            x=model.player.x,
            y=feet_y - 70.0,  # 头部高度
            vx=0.0,
            vy=0.0,
            radius=OBSTACLE_SMALL_RADIUS,
            tier="small",
            damage=1,
            base_speed=OBSTACLE_SMALL_SPEED,
        )
    ]
    model.step(FIXED_DT, (0.0, 0.0))
    assert model.player.hearts == PLAYER_START_HEARTS - 1

    miss = GameModel(seed=32)
    miss.wind.ux, miss.wind.uy = 0.0, 0.0
    miss.wind.remaining = 999.0
    miss_feet = miss.player.y + miss.player.radius * PLAYER_FEET_OFFSET_RATIO
    miss.obstacles = [
        Obstacle(
            oid=0,
            x=miss.player.x,
            y=miss_feet + 70.0,  # 脚下很远，不应命中
            vx=0.0,
            vy=0.0,
            radius=OBSTACLE_SMALL_RADIUS,
            tier="small",
            damage=1,
            base_speed=OBSTACLE_SMALL_SPEED,
        )
    ]
    miss.step(FIXED_DT, (0.0, 0.0))
    assert miss.player.hearts == PLAYER_START_HEARTS


def test_t14_assets_present():
    """资源完整性：非玩家贴图、每个方向的 idle/walk 帧、内置音效与 BGM 都存在。"""
    from assets import PLAYER_DIRECTIONS, PLAYER_STATES, REQUIRED_SPRITES, player_frame_paths
    from audio_profile import SLOTS, builtin_files, builtin_sound_set

    for slot in SLOTS:
        assert builtin_sound_set()[slot.id], "内置音频槽位为空：%s" % slot.id

    base = Path(__file__).resolve().parent
    sprite_dir = base / "assets" / "sprites"
    missing = [name for name in REQUIRED_SPRITES if not (sprite_dir / (name + ".png")).is_file()]
    for direction in PLAYER_DIRECTIONS:
        for state in PLAYER_STATES:
            if not player_frame_paths(base / "assets", direction, state):
                missing.append("player/player_%s_%s*" % (direction, state))
    assert not missing, "缺少贴图：%s" % (missing[:5],)

    import wave

    for path in builtin_files():
        assert path.is_file(), "缺少音频：%s" % (path,)
        with wave.open(str(path), "rb") as handle:
            assert handle.getnchannels() == 1, path
            assert handle.getsampwidth() == 2, path
            assert handle.getframerate() == 44100, path
            assert handle.getnframes() > 0, path


def test_t18_custom_audio_profile():
    """自定义音效：profile -> active -> 加载；坏配置回退；删 active 能重建。"""
    import tempfile
    import audio_profile

    with tempfile.TemporaryDirectory() as temporary:
        old = os.environ.get("DAFENG_RESOURCE_DIR")
        os.environ["DAFENG_RESOURCE_DIR"] = temporary
        try:
            sound_set, meta = audio_profile.resolve_audio()
            assert meta["mode"] == "custom", meta
            assert (Path(temporary) / "profile.json").is_file()
            active = Path(temporary) / "audio" / "active"
            assert (active / "manifest.json").is_file()
            for slot, paths in sound_set.items():
                assert paths, "槽位为空：%s" % slot
                assert all(path.is_file() for path in paths), slot
                assert all(active in path.parents for path in paths), slot

            # 损坏的 profile 应该回退默认，而不是抛异常
            (Path(temporary) / "profile.json").write_text("{bad", encoding="utf-8")
            sound_set2, meta2 = audio_profile.resolve_audio()
            assert meta2["mode"] == "custom"
            assert all(sound_set2[slot] for slot in sound_set2)

            # 删掉一个 active 文件，下次解析应重建
            victim = sound_set2["hit"][0]
            victim.unlink()
            sound_set3, _ = audio_profile.resolve_audio()
            assert victim.is_file()
        finally:
            if old is None:
                os.environ.pop("DAFENG_RESOURCE_DIR", None)
            else:
                os.environ["DAFENG_RESOURCE_DIR"] = old


def test_t19_audio_import():
    """P1：WAV 导入 -> 库 -> active；去重；静音拒绝。"""
    import tempfile
    import wave

    import audio_import
    import audio_profile

    def write_wav(path, rate, channels, seconds, amp=0.5):
        import array
        import math

        count = int(rate * seconds)
        data = array.array("h")
        for index in range(count):
            value = int(amp * 32767 * math.sin(2 * math.pi * 440 * index / rate))
            for _ in range(channels):
                data.append(value)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(channels)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(data.tobytes())

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "tone48.wav"
        write_wav(source, 48000, 2, 0.3)

        options = audio_import.ImportOptions(slot="hit", display_name="自测")
        result = audio_import.import_audio(root, source, options)
        assert result.created
        assert result.path.is_file()
        with wave.open(str(result.path), "rb") as handle:
            assert handle.getframerate() == 44100
            assert handle.getnchannels() == 1
            assert handle.getsampwidth() == 2

        duplicate = audio_import.import_audio(root, source, options)
        assert not duplicate.created
        assert duplicate.entry["id"] == result.entry["id"]
        assert len(audio_profile.load_library_index(root)["entries"]) == 1

        audio_import.set_slot_entry(root, "hit", result.entry["id"], "only")
        sound_set, meta = audio_profile.resolve_audio(root)
        assert meta["mode"] == "custom"
        assert len(sound_set["hit"]) == 1
        assert sound_set["hit"][0].is_file()

        removed = audio_profile.remove_library_entry(root, result.entry["id"])
        assert removed is not None
        assert not result.path.exists()
        assert len(audio_profile.load_library_index(root)["entries"]) == 0

        silent = root / "silent.wav"
        write_wav(silent, 44100, 1, 0.2, amp=0.0)
        try:
            audio_import.import_audio(root, silent, audio_import.ImportOptions(slot="hit"))
        except audio_import.AudioImportError:
            pass
        else:
            raise AssertionError("静音文件应被拒绝")


def test_t20_bgm_playlist():
    """BGM 随机池：一轮不重复、只选一首循环、v1 单选迁移、内置+导入进 active。"""
    import json
    import random as random_module
    import tempfile
    import wave

    import audio
    import audio_import
    import audio_profile

    sequence = audio.BgmSequence(3, random_module.Random(2026))
    rounds = [[sequence.next_index() for _ in range(3)] for _ in range(3)]
    for round_indices in rounds:
        assert sorted(round_indices) == [0, 1, 2], round_indices
    for previous, current in zip(rounds, rounds[1:]):
        assert previous[-1] != current[0], (previous, current)
    single = audio.BgmSequence(1)
    assert [single.next_index() for _ in range(3)] == [0, 0, 0]
    assert audio.BgmSequence(0).next_index() is None

    slot = audio_profile.SLOT_BY_ID["bgm"]
    assert slot.max_count > 1
    assert slot.builtin, "没有内置背景音乐"
    default = audio_profile.default_profile()
    assert default["bgm_auto_builtin"] is True
    assert len(default["audio"]["bgm"]) == min(len(slot.builtin), slot.max_count)

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        reference_path = audio_profile.profile_path(root)

        # 手动取消过内置曲目：auto=false，加载时原样保留玩家子集。
        manual_ref = audio_profile.builtin_ref(slot.builtin[0])
        reference_path.write_text(json.dumps({
            "version": audio_profile.PROFILE_VERSION,
            "bgm_auto_builtin": False,
            "audio": {"bgm": [manual_ref]},
        }), encoding="utf-8")
        manual = audio_profile.load_profile(root)
        assert manual["bgm_auto_builtin"] is False
        assert manual["audio"]["bgm"] == [manual_ref]

        # auto=true：当前全部内置 + 已选导入，新增内置曲目会自动进池。
        reference_path.write_text(json.dumps({
            "version": audio_profile.PROFILE_VERSION,
            "bgm_auto_builtin": True,
            "audio": {"bgm": ["lib:deadbeef"]},
        }), encoding="utf-8")
        auto = audio_profile.load_profile(root)
        auto_refs = auto["audio"]["bgm"]
        assert "lib:deadbeef" in auto_refs
        for relative in slot.builtin:
            assert audio_profile.builtin_ref(relative) in auto_refs

        # v1 单选 BGM -> 全部内置 + 已选导入，并打开 auto 模式。
        legacy = {
            "version": 1,
            "audio": {"bgm": [manual_ref]},
        }
        reference_path.write_text(json.dumps(legacy), encoding="utf-8")
        upgraded = audio_profile.load_profile(root)
        assert upgraded["version"] == audio_profile.PROFILE_VERSION
        assert upgraded["bgm_auto_builtin"] is True
        refs = upgraded["audio"]["bgm"]
        assert refs[0] == manual_ref
        assert len(refs) == min(len(slot.builtin), slot.max_count)

        # 导入一首 BGM，追加进随机池，解析后应同时包含内置与导入。
        import array
        import math

        source = root / "song.wav"
        samples = array.array("h")
        for index in range(int(44100 * 0.6)):
            samples.append(int(0.4 * 32767
                               * math.sin(2 * math.pi * 330 * index / 44100)))
        with wave.open(str(source), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(44100)
            handle.writeframes(samples.tobytes())

        result = audio_import.import_audio(
            root, source, audio_import.ImportOptions(slot="bgm"))
        assert result.created
        audio_import.set_slot_entry(root, "bgm", result.entry["id"], "add")
        sound_set, meta = audio_profile.resolve_audio(root)
        assert meta["mode"] == "custom", meta
        assert len(sound_set["bgm"]) >= 2
        assert any("lib_" in path.name for path in sound_set["bgm"])
        assert all(path.is_file() for path in sound_set["bgm"])


def test_t21_air_motion_and_frames():
    """A2/A3/B：速度个体差异、阵风、转向外抛生效且有界；帧贴图齐全。"""
    model = GameModel(seed=41)
    model.god_mode = True
    seen = set()
    spawn_speeds = {}
    max_sway = 0.0
    for _ in range(int(25.0 / FIXED_DT)):
        model.step(FIXED_DT, (0.0, 0.0))
        model.events.clear()
        for obstacle in model.obstacles:
            max_sway = max(max_sway, math.hypot(obstacle.sway_x, obstacle.sway_y))
            if obstacle.oid in seen:
                continue
            seen.add(obstacle.oid)
            spawn_speeds.setdefault(obstacle.tier, []).append(obstacle.base_speed)
            assert obstacle.gust_amp > 0.0, "真实生成的障碍物应带阵风参数"
            if obstacle.tier in OBSTACLE_SPIN_PROFILES:
                assert obstacle.spin_revs > 0.0, "中/大档应按角度旋转"
            else:
                assert obstacle.spin_revs == 0.0, "小档仍用翻面帧"
    assert max_sway > 0.5, "A1 摆动没有生效"

    total = 0
    for tier_name, speeds in spawn_speeds.items():
        nominal = TIER_BY_NAME[tier_name]["speed"]
        low = nominal * (1.0 - OBSTACLE_SPEED_JITTER) - 1e-6
        high = nominal * (1.0 + OBSTACLE_SPEED_JITTER) + 1e-6
        assert all(low <= value <= high for value in speeds), (tier_name, speeds)
        total += len(speeds)
    assert total >= 3 and any(len(values) >= 2 for values in spawn_speeds.values())
    assert any(
        abs(values[0] - values[1]) > 1e-9
        for values in spawn_speeds.values()
        if len(values) >= 2
    ), "同档障碍物速度应有个体差异"

    # A3：转向中外抛 > 0，转完后归零（不残留偏移）
    probe = _obstacle_for_test(OBSTACLE_LARGE_SPEED, 0.0, OBSTACLE_LARGE_SPEED, tier="large")
    begin_turn(probe, -_DIAG, _DIAG)
    peak = 0.0
    while probe.turn is not None:
        update_obstacle(probe, FIXED_DT)
        peak = max(peak, math.hypot(probe.sway_x, probe.sway_y))
    assert peak > 0.3, "A3 转向外抛没有生效"
    assert math.hypot(probe.sway_x, probe.sway_y) < 1e-6, "转向结束后偏移应归零"

    # B：每个档位至少有额外帧文件（帧 0 仍旧名）
    from assets import obstacle_sprite_name

    base = Path(__file__).resolve().parent / "assets" / "sprites"
    for tier_name, count in OBSTACLE_SPRITE_FRAMES.items():
        assert count >= 2, tier_name
        for variant in range(OBSTACLE_SPRITE_VARIANTS):
            for frame in range(1, count):
                path = base / (obstacle_sprite_name(tier_name, variant, frame) + ".png")
                assert path.is_file(), "缺少动画帧：%s" % (path,)

    # B：中/大档的帧号要跟旋转角度走（而不是纯时间翻面）
    layer = ObstacleLayer.__new__(ObstacleLayer)
    medium = _obstacle_for_test(OBSTACLE_MEDIUM_SPEED, 0.0, OBSTACLE_MEDIUM_SPEED, tier="medium")
    count = OBSTACLE_SPRITE_FRAMES["medium"]
    medium.spin_revs = 1.0
    medium.spin_dir = 1.0
    medium.spin_phase = 0.0
    medium.age = 5.0 / (1.0 * count)
    assert layer._sprite_frame(medium) == 5
    medium.spin_dir = -1.0
    assert layer._sprite_frame(medium) == (-5) % count


def test_t22_bgm_pause_resume():
    """BGM 结束暂停/重开续播：暂停只做一次且保留位置，静音时不出声。"""
    import tempfile

    import audio

    class _FakeBgmOut:
        def __init__(self):
            self.calls = []

        def set_bgm_paused(self, paused):
            self.calls.append(bool(paused))

        def start_bgm(self, path, loop=False):
            self.calls.append("start")
            return True

        def stop_bgm(self):
            self.calls.append("stop")

    player = audio.AudioPlayer(enabled=False)
    fake = _FakeBgmOut()
    player.enabled = True
    player.backend = "waveout"
    player._waveout = fake
    player._bgm_paths = [Path("dummy.wav")]
    player._bgm_active = True
    player._bgm_wanted = True
    player._bgm_current = 0

    # 结束界面：只暂停 BGM 并保留位置；重复调用不重复驱动后端
    assert player.pause_bgm() is True
    assert player._bgm_paused is True
    assert fake.calls == [True]
    assert player.pause_bgm() is True
    assert fake.calls == [True]

    # 重开：从原位置继续播放
    assert player.resume_bgm() is True
    assert player._bgm_paused is False
    assert fake.calls == [True, False]

    # 静音中重开：硬件保持暂停，取消静音后才续播
    assert player.pause_bgm() is True
    player.muted = True
    assert player.resume_bgm() is True
    assert fake.calls == [True, False, True]
    player.toggle_mute()
    assert player.muted is False
    assert fake.calls == [True, False, True, False]

    # 回到开始界面：停止并清掉标记，下一局重新起播
    player.stop_bgm()
    assert player._bgm_active is False
    assert player._bgm_paused is False
    assert player._bgm_wanted is False
    assert fake.calls[-1] == "stop"

    # 新一局 start_bgm：清掉残留暂停标记，从随机池第一首重新开始
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "bgm.wav"
        path.write_bytes(b"RIFF")
        player._bgm_paths = [path]
        player._bgm_sequence = audio.BgmSequence(1)
        player._bgm_paused = True
        assert player.start_bgm() is True
        assert player._bgm_paused is False
        assert fake.calls[-1] == "start"
        assert player._bgm_current == 0


SELFTESTS = (
    ("T1 输入与速度", test_t1_input_and_speed),
    ("T2 风向调度", test_t2_wind_scheduler),
    ("T3 难度曲线", test_t3_difficulty_curve),
    ("T4 惯性转向", test_t4_inertia),
    ("T5 碰撞与无敌", test_t5_collision_and_iframe),
    ("T6 结束条件", test_t6_game_over),
    ("T7 重置", test_t7_reset),
    ("T8 生成与回收", test_t8_spawn_and_recycle),
    ("T9 import 白名单", test_t9_import_whitelist),
    ("T10 无界面分层", test_t10_headless_layering),
    ("T11 边界数值", test_t11_boundary_values),
    ("T12 上帝模式", test_t12_god_mode),
    ("T13 命中即消失", test_t13_obstacle_removed_on_hit),
    ("T14 资源完整性", test_t14_assets_present),
    ("T15 朝向映射", test_t15_sprite_facing),
    ("T16 受击硬直", test_t16_hit_stun_and_knockback),
    ("T17 受击判定范围", test_t17_hitbox_matches_sprite),
    ("T18 自定义音效", test_t18_custom_audio_profile),
    ("T19 音频导入", test_t19_audio_import),
    ("T20 BGM 随机池", test_t20_bgm_playlist),
    ("T21 空中动作与帧", test_t21_air_motion_and_frames),
    ("T22 BGM 暂停续播", test_t22_bgm_pause_resume),
)


def run_selftest():
    passed = 0
    failed = 0
    print("大风吹 V1 自测（无窗口）")
    for label, test in SELFTESTS:
        try:
            test()
        except Exception as error:
            failed += 1
            print("[%s] ...... FAIL: %s" % (label, error))
            traceback.print_exc()
        else:
            passed += 1
            print("[%s] ...... PASS" % label)
    print("%d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


def run_check_assets():
    """检查贴图与音频资源是否齐全、格式是否正确；不创建窗口，也不初始化声卡。"""
    from assets import AssetStore, REQUIRED_SPRITES  # noqa: F401

    base = Path(__file__).resolve().parent
    store = AssetStore()
    print("资源目录：%s" % (base / "assets"))
    print("贴图：%s" % store.status())
    for name in store.missing:
        print("  缺失 assets/sprites/%s" % name)
    ok = store.ready
    if store.ready:
        from PIL import Image

        names = list(REQUIRED_SPRITES)
        for frames in store.player_frames.values():
            names.extend(frames)
        for name in names:
            path = base / "assets" / "sprites" / (name + ".png")
            try:
                with Image.open(path) as img:
                    img.verify()
            except Exception as error:
                print("  贴图损坏 %s：%s" % (path, error))
                ok = False
        print("  %d 张 PNG 均可读取（idle %d / walk %d）" % (
            len(names), store.frame_count("e", "idle"), store.frame_count("e", "walk")))

    import wave
    from audio_profile import (
        SLOTS, builtin_bgm_entries, builtin_files, inspect)

    bgm_entries = builtin_bgm_entries()
    print("内置背景音乐：%d 首（游戏中随机播放）" % len(bgm_entries))
    for entry in bgm_entries:
        artist = entry.get("artist") or ""
        print("  %-46s %s%s" % (
            entry["relative"], entry["title"],
            (" · " + artist) if artist else ""))

    print("内置音频：")
    for path in builtin_files():
        if not path.is_file():
            print("  缺失 %s" % path)
            ok = False
            continue
        try:
            with wave.open(str(path), "rb") as handle:
                seconds = handle.getnframes() / float(handle.getframerate())
                print(
                    "  %-46s %5.2fs  %d Hz  %d ch"
                    % (path.relative_to(base), seconds,
                       handle.getframerate(), handle.getnchannels())
                )
        except Exception as error:
            print("  音频损坏 %s：%s" % (path, error))
            ok = False

    try:
        report = inspect()
    except Exception as error:
        print("  自定义音效检查失败：%s" % error)
        ok = False
    else:
        print("自定义资源根：%s" % (report.get("root") or "无（使用内置）"))
        print("  模式：%s" % report.get("mode"))
        for slot in SLOTS:
            entries = report.get("files", {}).get(slot.id, [])
            print("  %-10s %d 条" % (slot.id, len(entries)))
        for entries in report.get("files", {}).values():
            for name in entries:
                path = Path(name)
                if not path.is_file():
                    print("  缺失 active 文件：%s" % path)
                    ok = False
                    continue
                try:
                    with wave.open(str(path), "rb") as handle:
                        assert handle.getnchannels() == 1
                        assert handle.getsampwidth() == 2
                        assert handle.getframerate() == 44100
                except Exception as error:
                    print("  active 音频损坏 %s：%s" % (path, error))
                    ok = False
        for error in report.get("errors", []):
            print("  注意：%s" % error)

    try:
        import pygame  # noqa: F401

        print("音频后端：pygame 可用（BGM + 多音效混音）")
    except Exception:
        print("音频后端：pygame 未安装（改用 waveOut 低延迟音效；不可用时退化为 winsound 或静默）")
    return 0 if ok else 1


def run_game():
    if tk is None:
        print("当前 Python 缺少 tkinter，无法启动游戏。请安装官方 CPython（含 Tcl/Tk）。", file=sys.stderr)
        return 2
    app = None
    disable_ime_for_process()
    # 按显示缩放把整个界面放大（150% → 1.5 倍），同时声明 DPI 感知，
    # 这样画布直接按物理像素渲染，而不是被系统拉伸变糊。
    prepare_ui_scale()
    enable_high_resolution_timer()
    try:
        root = tk.Tk()
        app = GameApp(root)
        root.mainloop()
    finally:
        disable_high_resolution_timer()
    return app.exit_code if app is not None else 2


def run_soak(seconds):
    if tk is None:
        print("当前 Python 缺少 tkinter，无法执行浸泡测试。", file=sys.stderr)
        return 2
    app = None
    disable_ime_for_process()
    prepare_ui_scale()
    enable_high_resolution_timer()
    try:
        root = tk.Tk()
        app = GameApp(root, soak_seconds=seconds)
        app.model.god_mode = True
        app.dev_hud = True
        root.mainloop()
    finally:
        disable_high_resolution_timer()
    return app.exit_code if app is not None else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="大风吹 V1")
    parser.add_argument("--selftest", action="store_true", help="无窗口自测，输出 PASS/FAIL")
    parser.add_argument("--soak", type=int, default=None, metavar="SECONDS", help="浸泡测试秒数")
    parser.add_argument("--check-assets", action="store_true", help="检查贴图/音频资源后退出")
    arguments = parser.parse_args(argv)
    if arguments.selftest:
        return run_selftest()
    if arguments.check_assets:
        return run_check_assets()
    if arguments.soak is not None:
        return run_soak(max(1, arguments.soak))
    return run_game()


if __name__ == "__main__":
    sys.exit(main())

