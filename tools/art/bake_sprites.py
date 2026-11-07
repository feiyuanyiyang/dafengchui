# -*- coding: utf-8 -*-
"""《大风吹》B 方案（柔和体积 / 发光氛围）贴图烘焙脚本。

仅在开发机上运行，用 Pillow 把矢量绘制结果烘成运行时 PNG：

    python tools/art/bake_sprites.py

产出（相对项目根）：
    assets/sprites/player/player_<dir>_<frame>.png        左/右 2 朝向（均为正面像）× idle/walk0/walk1
    assets/sprites/player/player_hurt.png                 受击表情
                                                        （受击闪白由运行时从正常帧生成）
    assets/sprites/obstacles/obstacle_<tier>_<i>.png      3 档 × 3 材质（绘制逻辑见 obstacle_art.py）
    assets/sprites/ui/heart_full.png / heart_empty.png / pickup_heart.png
    assets/sprites/ui/player_shadow.png                   脚底软阴影
    assets/sprites/bg/vignette.png / cloud_0.png / cloud_1.png
    art_preview/style_b_sprites.png                       评审用预览（不进游戏）

设计约定
    - 玩家是“伪 3D”的 Q 版占位角色：左/右两朝向（正面像）× 待机/走路两帧 + 受击帧，设计尺寸 125×135，
      脚底基线 y=101；运行时 anchor="s" 贴到碰撞圆底部。
    - 心 28px；障碍物按半径算（见 assets.py 的 obstacle_sprite_size）。
    - 烘焙文件统一是设计尺寸的 2 倍，方便以后上高 DPI。
    - 透明底、PNG RGBA；不使用 JPEG。

所有绘制先在 RENDER_SCALE 倍超采样画布上完成，再 LANCZOS 缩到 2 倍图，
避免小尺寸贴图出现锯齿。
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).parent))
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from assets import OBSTACLE_SPRITE_FRAMES, obstacle_sprite_name  # noqa: E402
from chibi import render_chibi  # noqa: E402
from obstacle_art import OBSTACLE_FRAMES, OBSTACLE_SPIN_FRAMES, obstacle_sprite_size  # noqa: E402
from obstacle_art import render_obstacle as render_obstacle_art  # noqa: E402

ASSET = ROOT / "assets" / "sprites"
PREVIEW = ROOT / "art_preview" / "style_b_sprites.png"

# 烘焙分辨率 = 设计尺寸 * FILE_SCALE；绘制分辨率 = 设计尺寸 * RENDER_SCALE
FILE_SCALE = 2
RENDER_SCALE = 4

# --- 调色板（与 game.py 的 COLOR_* 对齐） -----------------------------------
BG = (15, 23, 32)
PLAYER = (79, 195, 247)
PLAYER_LIGHT = (168, 232, 255)
DARK = (10, 17, 24)
WHITE = (255, 255, 255)
SMALL = (255, 209, 102)
MEDIUM = (247, 140, 107)
LARGE = (239, 71, 111)
HEART = (231, 76, 60)
HEART_EMPTY = (58, 70, 84)

# 玩家设计画布 125×135（脚底基线在底边），帧名与 assets.py 保持一致
PLAYER_W, PLAYER_H = 125, 135
PLAYER_FRAMES = ("idle", "walk0", "walk1")
# 只烘 2 个基础朝向：w（向左）与 e（向右）
PLAYER_DIRECTIONS = ("e", "w")

# 障碍物档位（尺寸公式见 obstacle_art.obstacle_sprite_size）
TIERS = (
    ("small", 11.0, SMALL),
    ("medium", 20.0, MEDIUM),
    ("large", 31.5, LARGE),
)
VARIANTS = 3

# 8 方向角度表（渲染器支持；当前游戏只烘 n/s，其余方向留给以后扩展）
DIRECTIONS = (
    ("e", 0.0),
    ("se", 45.0),
    ("s", 90.0),
    ("sw", 135.0),
    ("w", 180.0),
    ("nw", 225.0),
    ("n", 270.0),
    ("ne", 315.0),
)


def _canvas(size, scale=RENDER_SCALE):
    return Image.new("RGBA", (int(size * scale), int(size * scale)), (0, 0, 0, 0))


def _save(img, path, logical_size):
    """超采样图 -> FILE_SCALE 倍图，写入 PNG。"""
    file_size = int(round(logical_size * FILE_SCALE))
    img = img.resize((file_size, file_size), Image.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return img


def _save_rect(img, path, size):
    """非正方形贴图：按 (w, h) 设计尺寸写 FILE_SCALE 倍 PNG。"""
    out = img.resize(
        (int(round(size[0] * FILE_SCALE)), int(round(size[1] * FILE_SCALE))), Image.LANCZOS
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path)
    return out


def _tint(color, factor, white=0.0):
    out = []
    for channel in color:
        value = channel * factor + 255.0 * white
        out.append(int(max(0, min(255, value))))
    return tuple(out)


def render_player_shadow(width=83, height=31):
    """脚底软阴影：独立一层，不随角色转向旋转。"""
    img = Image.new("RGBA", (width * RENDER_SCALE, height * RENDER_SCALE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w, h = img.size
    d.ellipse([w * 0.10, h * 0.18, w * 0.90, h * 0.90], fill=(0, 0, 0, 120))
    img = img.filter(ImageFilter.GaussianBlur(max(2, 2.4 * RENDER_SCALE)))
    return img


def _soft_disc(size, color, radius, alpha, blur):
    """模糊圆盘（光晕 / 软阴影），返回与画布同尺寸的 RGBA。"""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse(
        [size / 2 - radius, size / 2 - radius, size / 2 + radius, size / 2 + radius],
        fill=color + (alpha,),
    )
    return layer.filter(ImageFilter.GaussianBlur(blur))


def _sphere(size, cx, cy, radius, color, seed=0, rim=0.55, highlight=(0.36, 0.30)):
    """柔和体积球体：径向明暗 + 上缘高光 + 边缘光 + 暗描边。

    返回 RGBA 图，调用方负责合成到画布。
    """
    rnd = random.Random(seed)
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # 主体：由外向内画同心圆近似径向渐变（外深内亮，亮部偏左上）
    steps = max(18, int(radius * 1.6))
    hx, hy = highlight
    for i in range(steps):
        t = i / (steps - 1)
        rr = radius * (1.0 - 0.42 * t)
        ox = (hx - 0.5) * radius * 0.35 * t
        oy = (hy - 0.5) * radius * 0.35 * t
        color_step = _tint(color, 0.55 + 0.45 * t, 0.30 * t * t)
        d.ellipse(
            [cx + ox - rr, cy + oy - rr, cx + ox + rr, cy + oy + rr],
            fill=color_step + (255,),
        )

    # 左下暗部：让球体有“背光”过渡
    shade = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    sr = radius * 1.05
    sd.ellipse(
        [cx + radius * 0.30 - sr, cy + radius * 0.36 - sr,
         cx + radius * 0.30 + sr, cy + radius * 0.36 + sr],
        fill=DARK + (110,),
    )
    shade = shade.filter(ImageFilter.GaussianBlur(max(1.0, radius * 0.55)))

    # 高光：左上一小团软白
    hi = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hi)
    hx = cx - radius * (0.5 - highlight[0]) * 1.2
    hy = cy - radius * (0.5 - highlight[1]) * 1.2
    hr = radius * (0.42 + rnd.uniform(-0.05, 0.05))
    hd.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=WHITE + (int(105 * rim) + 40,))
    hi = hi.filter(ImageFilter.GaussianBlur(max(1.0, radius * 0.22)))

    # 边缘光：右下细弧，模拟环境光反射
    rim_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rim_layer)
    rd.arc(
        [cx - radius + 1.2, cy - radius + 1.2, cx + radius - 1.2, cy + radius - 1.2],
        start=300,
        end=110,
        fill=WHITE + (int(95 * rim),),
        width=max(1, int(radius * 0.09)),
    )
    rim_layer = rim_layer.filter(ImageFilter.GaussianBlur(max(0.6, radius * 0.05)))

    # 暗描边：略向外扩一圈再模糊，保形但不发死
    outline = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    od = ImageDraw.Draw(outline)
    od.ellipse(
        [cx - radius - 1.0, cy - radius - 1.0, cx + radius + 1.0, cy + radius + 1.0],
        outline=DARK + (170,),
        width=max(1, int(radius * 0.10)),
    )
    outline = outline.filter(ImageFilter.GaussianBlur(max(0.5, radius * 0.03)))

    body = layer
    body.alpha_composite(shade)
    body.alpha_composite(hi)
    body.alpha_composite(rim_layer)
    body.alpha_composite(outline)

    # 裁回圆形：防止阴影/高光溢出球体轮廓（边缘光保留在圆内）
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius], fill=255
    )
    clipped = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    clipped.paste(body, (0, 0), mask)
    # 把描边和边缘光再叠回一圈（被裁掉的外沿）
    clipped.alpha_composite(outline)
    clipped.alpha_composite(rim_layer)
    return clipped


def render_obstacle(tier, radius, color, variant, size=None, frame=0):
    """障碍物：风卷杂物（树叶/纸片/羽毛 -> 松果/碎石/纸团 -> 树枝/木板/石块）。

    绘制逻辑在 obstacle_art.py；这里的 color 参数只为兼容旧调用点，
    材质本身自带配色，档位色只保留为很淡的外发光。
    """
    if size is None:
        size = obstacle_sprite_size(radius)
    return render_obstacle_art(tier, variant, radius, size, RENDER_SCALE, frame=frame)


def _heart_mask(size, scale):
    """参数化心形 -> 超采样 L 掩膜。"""
    w = int(size * scale)
    mask = Image.new("L", (w, w), 0)
    d = ImageDraw.Draw(mask)
    points = []
    steps = 220
    for i in range(steps):
        t = math.tau * i / steps
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        points.append((w / 2 + x * (w * 0.026), w / 2 - y * (w * 0.026) - w * 0.04))
    d.polygon(points, fill=255)
    return mask


def render_heart(full=True, size=34):
    scale = RENDER_SCALE
    s = size * scale
    canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    mask = _heart_mask(size, scale)

    color = HEART if full else HEART_EMPTY
    if full:
        canvas.alpha_composite(
            _soft_disc(s, HEART, radius=s * 0.30, alpha=70, blur=max(2, s * 0.06))
        )
    body = Image.new("RGBA", (s, s), color + (255,))
    # 左上高光
    hi = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hi)
    hd.ellipse([s * 0.16, s * 0.16, s * 0.52, s * 0.46], fill=(255, 255, 255, 110 if full else 34))
    hi = hi.filter(ImageFilter.GaussianBlur(max(1, s * 0.035)))
    body.alpha_composite(hi)
    # 右下暗部
    shade = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(shade).ellipse(
        [s * 0.38, s * 0.48, s * 1.05, s * 1.05], fill=DARK + (80 if full else 40,)
    )
    body.alpha_composite(shade.filter(ImageFilter.GaussianBlur(max(1, s * 0.05))))
    body.putalpha(mask)

    # 软描边
    outline = Image.new("RGBA", (s, s), DARK + (150,))
    outline.putalpha(mask.filter(ImageFilter.MaxFilter(3)))
    canvas.alpha_composite(outline)
    canvas.alpha_composite(body)
    return canvas


def render_pickup(size=43):
    """拾取道具：心形 + 青色光环，为后续玩法预留。"""
    canvas = render_heart(True, size=size)
    s = canvas.size[0]
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    d.ellipse([s * 0.08, s * 0.08, s * 0.92, s * 0.92], outline=PLAYER + (150,), width=max(1, int(s * 0.025)))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(max(1, s * 0.02))))
    return canvas


def render_cloud(width=300, height=110, seed=0):
    """风格 B 的软云：几团圆叠出轮廓，轻微模糊 + 冷色外发光。"""
    scale = RENDER_SCALE
    w, h = int(width * scale), int(height * scale)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        [w * 0.08, h * 0.28, w * 0.92, h * 1.0], fill=(79, 195, 247, 18)
    )
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(max(2.0, h * 0.10))))

    rnd = random.Random(seed)
    base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(base)
    # 半径按“高度”取比例，避免宽画布时糊成一条横杠
    blobs = (
        (0.24, 0.70, 0.34),
        (0.40, 0.52, 0.46),
        (0.58, 0.56, 0.38),
        (0.75, 0.70, 0.28),
    )
    for cx, cy, rr in blobs:
        jitter = rnd.uniform(-0.02, 0.02)
        r = height * (rr + jitter) * scale
        px, py = cx * w, cy * h
        d.ellipse([px - r, py - r, px + r, py + r], fill=(24, 40, 54, 225))
    # 压扁的底部让云贴住“地平线”，而不是一个球
    d.ellipse(
        [w * 0.07, h * 0.62, w * 0.93, h * 0.94],
        fill=(24, 40, 54, 225),
    )
    base = base.filter(ImageFilter.GaussianBlur(max(1.2, 1.6 * scale)))
    canvas.alpha_composite(base)

    # 顶缘一丝冷光，避免云和背景糊成一块
    rim = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rim)
    for cx, cy, rr in blobs:
        r = height * rr * scale * 0.9
        px, py = cx * w, cy * h
        rd.arc([px - r, py - r, px + r, py + r], 195, 330, fill=(120, 205, 240, 26), width=max(2, int(scale * 3)))
    canvas.alpha_composite(rim.filter(ImageFilter.GaussianBlur(max(1.5, h * 0.03))))
    return canvas


def render_vignette(width=1200, height=840):
    """风格 B 的暗角：中心完全透明、边缘压暗，叠在最底层。

    只在低分辨率画布上逐像素算径向 alpha，再放大 + 模糊：
    直接叠多层半透明椭圆会让中心也被反复压暗（错误做法）。
    """
    sw, sh = 200, 140
    mask = Image.new("L", (sw, sh), 0)
    px = mask.load()
    for y in range(sh):
        ny = (y + 0.5) / sh - 0.5
        for x in range(sw):
            nx = (x + 0.5) / sw - 0.5
            # 椭圆归一化距离：横向留更多余量，避免 16:10 画面左右先黑
            d = math.hypot(nx / 0.62, ny / 0.60)
            t = min(1.0, max(0.0, (d - 0.42) / 0.78))
            alpha = int(118 * (t * t * (3.0 - 2.0 * t)))
            px[x, y] = alpha
    mask = mask.resize((width, height), Image.LANCZOS)
    mask = mask.filter(ImageFilter.GaussianBlur(26))
    img = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    img.putalpha(mask)
    return img


def main(argv=None):
    parser = argparse.ArgumentParser(description="烘焙 B 方案占位贴图")
    parser.add_argument("--force-player", action="store_true", help="即使已有多帧真实美术也覆盖玩家帧")
    parser.add_argument("--skip-player", action="store_true", help="只烘焙背景/障碍/UI，不动玩家帧")
    args = parser.parse_args(argv)

    ASSET.mkdir(parents=True, exist_ok=True)
    written = []

    # 玩家：左/右 2 基础朝向，均为“正面像”；左向由正面水平镜像得到
    has_real_frames = (ASSET / "player" / "player_e_walk2.png").exists()
    skip_player = args.skip_player or (has_real_frames and not args.force_player)
    if skip_player:
        print("检测到多帧玩家美术，跳过玩家帧烘焙（--force-player 可强制覆盖）")
    else:
        for name in PLAYER_DIRECTIONS:
            for frame in PLAYER_FRAMES:
                img = render_chibi("s", frame)
                if name == "w":
                    img = img.transpose(Image.FLIP_LEFT_RIGHT)
                path = ASSET / "player" / ("player_%s_%s.png" % (name, frame))
                written.append(_save_rect(img, path, (PLAYER_W, PLAYER_H)))
        # 受击帧也属于玩家美术：只在没有真实多帧美术（或 --force-player）时才烘，
        # 否则会把 import_player.py --hurt 导入的自定义受击图覆盖成占位图。
        written.append(
            _save_rect(
                render_chibi("s", "idle", hurt=True),
                ASSET / "player" / "player_hurt.png",
                (PLAYER_W, PLAYER_H),
            )
        )

    # 障碍物 3 档 × 3 变体 × N 帧（帧数由 assets.OBSTACLE_SPRITE_FRAMES 决定）
    for tier, radius, color in TIERS:
        draw_frames = OBSTACLE_SPIN_FRAMES.get(tier)
        if draw_frames is None:
            draw_frames = len(OBSTACLE_FRAMES.get(tier, ()))
        assert draw_frames == OBSTACLE_SPRITE_FRAMES[tier], (
            "帧数不一致：%s 绘制 %d / 资源 %d"
            % (tier, draw_frames, OBSTACLE_SPRITE_FRAMES[tier])
        )
        size = obstacle_sprite_size(radius)
        for variant in range(VARIANTS):
            for frame in range(OBSTACLE_SPRITE_FRAMES[tier]):
                img = render_obstacle(tier, radius, color, variant, size=size, frame=frame)
                written.append(
                    _save(
                        img,
                        ASSET / (obstacle_sprite_name(tier, variant, frame) + ".png"),
                        size,
                    )
                )

    # UI
    written.append(_save(render_heart(True), ASSET / "ui" / "heart_full.png", 34))
    written.append(_save(render_heart(False), ASSET / "ui" / "heart_empty.png", 34))
    written.append(_save(render_pickup(), ASSET / "ui" / "pickup_heart.png", 43))
    written.append(_save_rect(render_player_shadow(83, 31), ASSET / "ui" / "player_shadow.png", (83, 31)))

    # 背景软云（两种形状，运行时按 CLOUD_SHAPES 拉伸）
    for index in range(2):
        written.append(
            _save_rect(
                render_cloud(seed=900 + index),
                ASSET / "bg" / ("cloud_%d.png" % index),
                (300, 110),
            )
        )

    # 背景暗角（原生尺寸，不放大）
    vig = render_vignette()
    (ASSET / "bg").mkdir(parents=True, exist_ok=True)
    vig.save(ASSET / "bg" / "vignette.png")
    written.append(vig)

    _preview()
    print("baked %d images -> %s" % (len(written), ASSET))
    print("preview -> %s" % PREVIEW)


def _preview():
    """评审图：素材一览 + 真实尺寸的场景模拟。"""
    W, H = 1180, 860
    sheet = Image.new("RGBA", (W, H), (8, 13, 19, 255))
    d = ImageDraw.Draw(sheet)
    font_path = "C:/Windows/Fonts/msyh.ttc"
    try:
        from PIL import ImageFont

        f_title = ImageFont.truetype(font_path, 22)
        f_sub = ImageFont.truetype(font_path, 15)
    except Exception:  # pragma: no cover
        f_title = f_sub = None

    def label(x, y, text):
        d.text((x, y), text, font=f_sub, fill=(150, 165, 180))

    d.text((28, 20), "B 方案贴图 · 柔和体积 / 发光氛围", font=f_title, fill=(230, 237, 243))
    d.text((28, 50), "运行时显示尺寸：玩家 125×135（左/右 ×3 帧 + 受击）· 障碍 32/56/88px · 心 34px", font=f_sub, fill=(139, 152, 165))

    # 玩家：左/右 2 基础朝向 + 受击
    label(28, 84, "玩家：左/右正面像 + 受击（125×135）")
    tiles = (
        ("w 左向", "player_w_idle.png"),
        ("e 右向", "player_e_idle.png"),
        ("受击", "player_hurt.png"),
    )
    for i, (caption, filename) in enumerate(tiles):
        img = Image.open(ASSET / "player" / filename).convert("RGBA").resize(
            (77, 83), Image.LANCZOS
        )
        x = 28 + i * 112
        sheet.alpha_composite(img, (x, 104))
        d.text((x + 12, 190), caption, font=f_sub, fill=(139, 152, 165))

    # 走路帧（e）
    label(430, 84, "e：idle / walk0 / walk1")
    for j, frame in enumerate(PLAYER_FRAMES):
        path = ASSET / "player" / ("player_e_%s.png" % frame)
        img = Image.open(path).convert("RGBA").resize((48, 52), Image.LANCZOS)
        sheet.alpha_composite(img, (430 + j * 54, 106))

    # 障碍物
    label(28, 224, "障碍物 · 3 档 × 3 变体（实际显示尺寸）")
    x = 28
    for tier, radius, _color in TIERS:
        size = obstacle_sprite_size(radius)
        for variant in range(VARIANTS):
            path = ASSET / "obstacles" / ("obstacle_%s_%d.png" % (tier, variant))
            img = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
            sheet.alpha_composite(img, (x, 252 + (50 - size) // 2))
            x += size + 14
        x += 24
    d.text((28, 300), "small 9.1 / medium 15.6 / large 23.4 半径（碰撞圆与贴图同步缩放）", font=f_sub, fill=(139, 152, 165))

    # UI
    label(28, 340, "UI · 生命与道具")
    heart_full = Image.open(ASSET / "ui" / "heart_full.png").convert("RGBA").resize((34, 34), Image.LANCZOS)
    heart_empty = Image.open(ASSET / "ui" / "heart_empty.png").convert("RGBA").resize((34, 34), Image.LANCZOS)
    pickup = Image.open(ASSET / "ui" / "pickup_heart.png").convert("RGBA").resize((43, 43), Image.LANCZOS)
    for i in range(3):
        sheet.alpha_composite(heart_full, (28 + i * 44, 360))
    sheet.alpha_composite(heart_empty, (28 + 3 * 44 + 8, 360))
    sheet.alpha_composite(pickup, (236, 356))
    d.text((292, 368), "回血道具（预留）", font=f_sub, fill=(139, 152, 165))

    # 场景模拟：用真实贴图 + 暗角，按 1200x840 逻辑尺寸渲染后缩到 0.46
    scene = Image.new("RGBA", (1200, 840), BG + (255,))
    scene.alpha_composite(render_vignette().resize((1200, 840), Image.LANCZOS))
    sd = ImageDraw.Draw(scene)
    # 云
    for (cx, cy, s_) in ((192, 156, 1.2), (840, 204, 0.9)):
        for (ox, oy, rr) in ((-0.62, 0.12, 0.52), (-0.2, -0.06, 0.7), (0.28, -0.02, 0.62), (0.66, 0.16, 0.44)):
            sd.ellipse(
                [cx + ox * 40 * s_ - 22 * s_ * rr, cy + oy * 26 * s_ - 22 * s_ * rr,
                 cx + ox * 40 * s_ + 22 * s_ * rr, cy + oy * 26 * s_ + 22 * s_ * rr],
                fill=(24, 40, 54, 255),
            )
    # 障碍
    for (tier, radius, _c, variant, ox, oy) in (
        ("small", 9.1, SMALL, 0, 250, 250),
        ("medium", 15.6, MEDIUM, 1, 720, 320),
        ("large", 23.4, LARGE, 2, 300, 540),
        ("small", 9.1, SMALL, 2, 800, 560),
    ):
        size = obstacle_sprite_size(radius)
        img = Image.open(ASSET / "obstacles" / ("obstacle_%s_%d.png" % (tier, variant)))
        img = img.convert("RGBA").resize((size, size), Image.LANCZOS)
        scene.alpha_composite(img, (ox - size // 2, oy - size // 2))
    # 玩家（脚底锚点：贴图底边中点在脚底）
    shadow = Image.open(ASSET / "ui" / "player_shadow.png").convert("RGBA")
    scene.alpha_composite(shadow, (600 - 41, 432 - 15))
    p = Image.open(ASSET / "player" / "player_e_idle.png").convert("RGBA")
    p = p.resize((125, 135), Image.LANCZOS)
    scene.alpha_composite(p, (600 - 62, 432 - 134))
    # 生命值
    for i in range(3):
        scene.alpha_composite(heart_full, (1032 + i * 43, 29))

    scene = scene.resize((int(1200 * 0.46), int(840 * 0.46)), Image.LANCZOS)
    sheet.alpha_composite(scene, (28, 430))
    d.rectangle([28, 430, 28 + scene.width, 430 + scene.height], outline=(38, 56, 72), width=1)
    d.text(
        (scene.width + 48, 430),
        "场景模拟（0.46 倍预览）\n\n深色底 + 暗角 + 风卷杂物\n"
        "玩家/障碍物/生命值均已替换为 PNG\n\n"
        "实际运行：python game.py\n"
        "资源缺失时自动退回矢量几何",
        font=f_sub,
        fill=(139, 152, 165),
    )
    PREVIEW.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(PREVIEW)


if __name__ == "__main__":
    main()
