# -*- coding: utf-8 -*-
"""从原始视频/立绘重新生成高分辨率玩家帧（含抠图修正）。

    python tools/art/rebuild_player.py
    python tools/art/rebuild_player.py --idle 待机动作.mp4 --walk 角色原地向右奔跑循环.mp4 --hurt 受击.png

和 import_player.py 的区别
    - 输出按“设计尺寸 125×135 的 2 倍”落盘（250×270），运行时缩到 125×135，
      源是 720p 视频/1440×2560 立绘，不存在把 96px 小图放大导致的模糊；
    - 抠图后先做“颜色去污染”再缩放，边缘不会带绿；
    - idle / walk 各自按本组 bbox 统一缩放，同组帧之间不会忽大忽小；
    - walk 自动挑选首尾最接近的 16 帧，保证 0.667 s 循环接缝尽量小；
    - idle 采样 27 帧并做 3 帧首尾交叉淡化。

抠图为什么这么写（2026-10 修正“前胸/眼睛露出背景”）
    旧版叠了两条“全局颜色”规则：
      * greenish：g > 1.12 * max(r, b) 的像素一律当背景；
      * 亮度 25–160 且低饱和度的像素一律当脚下的灰色阴影。
    视频是 4:2:0 色度抽样，绿幕的绿会糊进角色的深色线稿，线稿于是变成“暗绿”，
    正好被 greenish 判据吃掉；白色围裙暗部、眼白、高光的低饱和像素又被阴影判据
    吃掉。结果就是人物沿着线稿被抠出 1–2 px 的透明缝，缩小后变成半透明绿线，
    看起来像“前胸、眼睛、发缝露出背景”。
    现在改成三条更窄的判据：
      1) 只把“离背景色足够近”的像素算背景（本素材背景很均匀，标准差≈2）；
      2) 地面阴影按“同一条背景色的明暗线”单独识别（k∈[0.45,0.98] 且离 k*base
         很近），不再按“低饱和灰”全局删，白围裙/眼白/高光都能留下；
      3) 抠完把不透明像素的颜色灌进透明区、把贴边污染色换成最近内部色，缩放时
         RGB 与 alpha 分开处理，绿色不会从边缘渗进来。
    另外距离计算一律用 float32，避免旧版 int16 平方在极暗像素上溢出。

依赖：Pillow、numpy、scipy、PyAV（均已在开发环境）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import av
import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "art"))
import import_player as ip  # noqa: E402

PLAYER = ROOT / "assets" / "sprites" / "player"

# 设计尺寸（游戏里显示的大小）与落盘倍数
TARGET_W, TARGET_H = 125, 135
FILE_SCALE = 2
OUT_W, OUT_H = TARGET_W * FILE_SCALE, TARGET_H * FILE_SCALE
INNER_W, INNER_H = OUT_W - 4, OUT_H - 4  # 留 2px 透明边，避免缩放裁掉发梢

IDLE_COUNT = 27
WALK_COUNT = 16
WALK_STRIDE = 1        # 24 fps 连续帧
IDLE_STRIDE = 2        # 24 fps -> 12 fps

# --- 抠图参数 ---------------------------------------------------------------
MATTE_TOL = 42.0       # 与背景色的 RGB 距离阈值（背景 std≈2，留足压缩噪声余量）
SHADOW_K_MIN = 0.45    # 地面阴影：同色明暗线系数下界（角色暗线稿 k≈0.2–0.4）
SHADOW_RESID = 24.0    # 地面阴影：偏离 k*base 的残差上限
EDGE_BAND = 4          # 距透明区多少像素内的颜色算“脏边”，用内部色覆盖
ALPHA_ERODE = 2        # 源分辨率下再收 2px，切掉半透明外沿
GREEN_SPILL = 30       # 只有绿得“很明确”的像素才压绿色通道
CROP_BOTTOM = 80       # 即梦视频底部水印


def read_frames(path, crop_bottom=CROP_BOTTOM):
    container = av.open(str(path))
    stream = container.streams.video[0]
    frames = []
    for frame in container.decode(stream):
        image = frame.to_image().convert("RGB")
        if crop_bottom:
            image = image.crop((0, 0, image.width, max(1, image.height - crop_bottom)))
        frames.append(image)
    return frames


def estimate_base(rgb):
    """取画面四周 6px 环的中位色当背景色（比四角均值更抗噪）。"""
    ring = np.concatenate(
        [
            rgb[:6].reshape(-1, 3),
            rgb[-6:].reshape(-1, 3),
            rgb[:, :6].reshape(-1, 3),
            rgb[:, -6:].reshape(-1, 3),
        ]
    )
    return np.median(ring, axis=0).astype(np.float32)


def video_base(frames, sample=9):
    """用多帧估同一个背景色，避免逐帧浮动让抠图边界抖动。"""
    picks = frames[:: max(1, len(frames) // sample)][:sample] or frames
    bases = [estimate_base(np.asarray(frame).astype(np.float32)) for frame in picks]
    return np.median(np.stack(bases), axis=0)


def matte(img, base, tolerance=MATTE_TOL, k_min=SHADOW_K_MIN, shadow_resid=SHADOW_RESID):
    """绿幕抠图：删背景 + 地面阴影，保留角色内部的白/蓝/肤色和深色线稿。"""
    source = np.asarray(img.convert("RGB")).astype(np.float32)
    diff = source - base
    direct = (diff * diff).sum(axis=2) <= tolerance * tolerance
    b2 = float((base * base).sum())
    k = (source * base).sum(axis=2) / b2
    resid = np.linalg.norm(source - k[..., None] * base, axis=2)
    # 绿幕上的地面阴影是“更暗的同一个绿色”，和背景色差很大，只靠 direct 会漏掉；
    # 但它仍落在 base 的同一明暗线上（k≈0.5–0.7），角色最暗的线稿 k<0.45。
    shadow = (k > k_min) & (k < 0.98) & (resid < shadow_resid)
    alpha = np.where(direct | shadow, 0, 255).astype(np.uint8)
    return Image.fromarray(np.dstack([source.astype(np.uint8), alpha]))


def clean_edges(img, band=EDGE_BAND, erode=ALPHA_ERODE, spill=GREEN_SPILL):
    """颜色去污染 + 轻微收边：去掉绿幕反光和发丝边缘的绿色描边。"""
    arr = np.asarray(img).astype(np.uint8).copy()
    rgb = arr[..., :3].astype(np.float32)
    alpha = arr[..., 3]
    transparent = alpha < 8
    if transparent.any() and not transparent.all():
        # 1) 透明区的颜色 = 最近的不透明色：之后缩放时不会把背景色混进边缘
        iy, ix = ndimage.distance_transform_edt(
            transparent, return_distances=False, return_indices=True
        )
        rgb[transparent] = rgb[iy[transparent], ix[transparent]]
        # 2) 贴着透明区的“脏边”：换成更深处的干净内部色
        dist = ndimage.distance_transform_edt(~transparent)
        safe = dist > band
        if safe.any():
            sy, sx = ndimage.distance_transform_edt(
                ~safe, return_distances=False, return_indices=True
            )
            rim = (alpha > 0) & ~safe
            rgb[rim] = rgb[sy[rim], sx[rim]]
    # 3) 绿幕溢色：只压“绿得明确”的像素，避免把抗锯齿灰边整体去饱和
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    spill_mask = (g > np.maximum(r, b) + spill) & (alpha > 0)
    rgb[..., 1] = np.where(spill_mask, np.maximum(r, b), g)
    out = np.dstack([np.clip(rgb, 0, 255).astype(np.uint8), alpha])
    if erode:
        out[..., 3] = ndimage.minimum_filter(alpha, size=2 * erode + 1)
    return Image.fromarray(out)


def bbox(img, threshold=8):
    mask = np.asarray(img.getchannel("A")) > threshold
    if not mask.any():
        return None
    ys, xs = np.nonzero(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def union_box(images):
    boxes = [bbox(img) for img in images]
    boxes = [box for box in boxes if box]
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def fit(img, box, scale, canvas_size):
    """按给定缩放把 bbox 内容放到画布底部居中；scale 是“输出像素 / 源像素”。

    RGB 与 alpha 分开缩放：alpha 走 LANCZOS 得到柔和边缘，RGB 走已去污染的颜色，
    不会出现“透明区的绿色被混进半透明边缘”的绿边。
    """
    piece = img.crop(box)
    size = (max(1, round(piece.width * scale)), max(1, round(piece.height * scale)))
    alpha = piece.getchannel("A").resize(size, Image.LANCZOS)
    color = piece.convert("RGB").resize(size, Image.LANCZOS)
    piece = color.convert("RGBA")
    piece.putalpha(alpha)
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    x = (canvas_size[0] - piece.width) // 2
    y = canvas_size[1] - piece.height
    canvas.alpha_composite(piece, (x, y))
    return canvas


def frame_diff(a, b, size=(96, 104)):
    aa = np.asarray(a.resize(size, Image.LANCZOS).convert("RGB"), dtype=np.float32)
    bb = np.asarray(b.resize(size, Image.LANCZOS).convert("RGB"), dtype=np.float32)
    return float(np.mean(np.abs(aa - bb)))


def pick_walk_start(frames):
    best, best_score = 0, 1e9
    for start in range(0, len(frames) - WALK_COUNT):
        score = frame_diff(frames[start], frames[start + WALK_COUNT])
        if score < best_score:
            best, best_score = start, score
    return best, best_score


def pick_idle_start(frames):
    stride = IDLE_STRIDE
    span = stride * IDLE_COUNT
    best, best_score = 0, 1e9
    for start in range(0, len(frames) - span):
        score = frame_diff(frames[start], frames[start + span])
        if score < best_score:
            best, best_score = start, score
    return best, best_score


def crossfade(frame, anchor, weight):
    return Image.blend(frame, anchor, weight)


def save_pair(img, name, swap):
    """swap=True：视频帧是反方向，e 取镜像（待机视频）；否则 e 用原图（奔跑视频）。"""
    e = img.transpose(Image.FLIP_LEFT_RIGHT) if swap else img
    w = img if swap else img.transpose(Image.FLIP_LEFT_RIGHT)
    e.save(PLAYER / ("player_e_%s.png" % name))
    w.save(PLAYER / ("player_w_%s.png" % name))


def audit(images, label):
    """自检：可见的残留绿色像素 + 半透明边缘占比（只报告，不阻断构建）。

    alpha≤32 的像素在游戏里等同于透明，不计入“可见残留绿”，否则会把外沿
    未完全收干净的 1–2 个像素也算进去，读数没有参考价值。
    """
    green = semi = 0
    total = 0
    for img in images:
        arr = np.asarray(img)
        alpha = arr[..., 3]
        rgb = arr[..., :3].astype(np.int16)
        total += alpha.size
        visible = alpha > 32
        green += int(((rgb[..., 1] > np.maximum(rgb[..., 0], rgb[..., 2]) + 25) & visible).sum())
        semi += int((visible & (alpha < 250)).sum())
    print("  %s 自检：可见残留绿 %d px，半透明边 %.1f%%（越低越好）"
          % (label, green, 100.0 * semi / max(1, total)))
    return green


def build(idle_path, walk_path, hurt_path):
    idle_raw = read_frames(idle_path)
    walk_raw = read_frames(walk_path)
    idle_start, idle_score = pick_idle_start(idle_raw)
    walk_start, walk_score = pick_walk_start(walk_raw)
    print("idle 起点 %d（接缝差 %.1f），walk 起点 %d（接缝差 %.1f）"
          % (idle_start, idle_score, walk_start, walk_score))

    idle_base = video_base(idle_raw)
    walk_base = video_base(walk_raw)
    print("背景色 idle %s / walk %s" % (idle_base.round(1).tolist(), walk_base.round(1).tolist()))

    idle_frames = [
        clean_edges(matte(idle_raw[idle_start + i * IDLE_STRIDE], idle_base))
        for i in range(IDLE_COUNT)
    ]
    walk_frames = [
        clean_edges(matte(walk_raw[walk_start + i * WALK_STRIDE], walk_base))
        for i in range(WALK_COUNT)
    ]

    idle_box = union_box(idle_frames)
    walk_box = union_box(walk_frames)
    idle_scale = min(
        INNER_W / max(1, idle_box[2] - idle_box[0]),
        INNER_H / max(1, idle_box[3] - idle_box[1]),
    )
    walk_scale = min(
        INNER_W / max(1, walk_box[2] - walk_box[0]),
        INNER_H / max(1, walk_box[3] - walk_box[1]),
    )
    print("idle bbox %s，缩放 %.4f；walk bbox %s，缩放 %.4f"
          % (idle_box, idle_scale, walk_box, walk_scale))

    idle_out = [fit(img, idle_box, idle_scale, (OUT_W, OUT_H)) for img in idle_frames]
    # 首尾 3 帧向开头交叉淡化，循环时不会“跳一下”
    for k, idx in enumerate((IDLE_COUNT - 3, IDLE_COUNT - 2, IDLE_COUNT - 1)):
        idle_out[idx] = crossfade(idle_out[idx], idle_out[k], 0.2 + 0.15 * k)
    walk_out = [fit(img, walk_box, walk_scale, (OUT_W, OUT_H)) for img in walk_frames]

    for index, img in enumerate(idle_out):
        save_pair(img, "idle" if index == 0 else "idle%d" % index, swap=True)
    for index, img in enumerate(walk_out):
        save_pair(img, "walk%d" % index, swap=False)

    # 受击图单独按高度归一，和 idle / walk 的视觉身高一致
    hurt = Image.open(hurt_path)
    hurt_source = np.asarray(hurt.convert("RGB")).astype(np.float32)
    hurt_color = clean_edges(matte(hurt, estimate_base(hurt_source)))
    hurt_color = ip.keep_largest_component(hurt_color, min_ratio=0.01, min_area=200)
    hurt_box = bbox(hurt_color)
    hurt_scale = min(
        INNER_W / max(1, hurt_box[2] - hurt_box[0]),
        INNER_H / max(1, hurt_box[3] - hurt_box[1]),
    )
    hurt_img = fit(hurt_color, hurt_box, hurt_scale, (OUT_W, OUT_H))
    hurt_img.save(PLAYER / "player_hurt.png")
    print("已写入 %d idle / %d walk / 1 hurt → %s" % (len(idle_out), len(walk_out), PLAYER))
    print("设计尺寸 %dx%d，文件 %dx%d" % (TARGET_W, TARGET_H, OUT_W, OUT_H))
    audit(idle_out, "idle")
    audit(walk_out, "walk")
    audit([hurt_img], "hurt")


def main(argv=None):
    parser = argparse.ArgumentParser(description="从源视频/立绘重建高分辨率玩家帧")
    parser.add_argument("--idle", default="D:/desktop/待机动作.mp4")
    parser.add_argument("--walk", default="D:/desktop/角色原地向右奔跑循环.mp4")
    parser.add_argument("--hurt", default="D:/desktop/受击.png")
    args = parser.parse_args(argv)
    build(Path(args.idle), Path(args.walk), Path(args.hurt))


if __name__ == "__main__":
    main()
