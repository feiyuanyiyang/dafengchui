# -*- coding: utf-8 -*-
"""Q 版占位角色烘焙器（伪 3D：方向 × 待机/走路/受击帧）。

游戏当前只烘 n/s 两个基础朝向，左右由运行时镜像；本模块仍支持 8 方向绘制，
方便以后想要更细的转身时直接扩。

这不是最终美术，而是给“方向帧 + 行走动画 + 脚底锚点”管线用的占位角色：
蓝发、大头的 Q 版女仆，特征提取自 DeepSeek 娘的二创风格，但不复制任何现有插画。
真正美术到位后，只要按同样的文件命名替换 PNG 即可，游戏代码不用改。

    from chibi import render_chibi
    img = render_chibi("se", "walk0")   # RGBA，设计尺寸 96x104

约定
    - 设计尺寸 96x104；脚底基线 y=101，脚底 x 中心 = 48。
    - 运行时用 anchor="s"（底边中点）贴到碰撞圆底部，所以帧与帧之间脚不能漂。
    - 角度按屏幕坐标：e=0°，s=90°（朝下=面向镜头），n=270°（朝上=背对镜头）。
"""

from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter

W, H = 96, 104
SS = 4  # 超采样倍率
FEET_Y = 101.0
CENTER_X = 48.0

HAIR = (43, 75, 143)
HAIR_DARK = (29, 50, 101)
HAIR_LIGHT = (92, 140, 200)
SKIN = (255, 224, 206)
SKIN_DARK = (232, 184, 166)
DRESS = (32, 46, 88)
DRESS_LIGHT = (50, 70, 126)
WHITE = (247, 249, 252)
WHITE_DARK = (213, 221, 236)
RIBBON = (79, 166, 232)
EYE = (46, 123, 224)
EYE_DARK = (18, 39, 82)
BLUSH = (255, 150, 160)
SHOE = (24, 36, 68)
TAIL = (52, 88, 155)

ANGLES = {"e": 0.0, "se": 45.0, "s": 90.0, "sw": 135.0, "w": 180.0,
          "nw": 225.0, "n": 270.0, "ne": 315.0}
FRAMES = ("idle", "walk0", "walk1")


class Pen:
    """用设计坐标绘制，内部自动乘 SS。"""

    def __init__(self, img):
        self.img = img
        self.d = ImageDraw.Draw(img)

    def circle(self, cx, cy, r, fill, outline=None, width=1.0):
        self.ellipse(cx, cy, r, r, fill, outline, width)

    def ellipse(self, cx, cy, rx, ry, fill, outline=None, width=1.0):
        box = [(cx - rx) * SS, (cy - ry) * SS, (cx + rx) * SS, (cy + ry) * SS]
        self.d.ellipse(
            box,
            fill=fill,
            outline=outline,
            width=max(1, int(round(width * SS))) if outline else 0,
        )

    def poly(self, points, fill, outline=None, width=1.0):
        pts = [(x * SS, y * SS) for x, y in points]
        self.d.polygon(
            pts,
            fill=fill,
            outline=outline,
            width=max(1, int(round(width * SS))) if outline else 0,
        )

    def rrect(self, x1, y1, x2, y2, radius, fill, outline=None, width=1.0):
        self.d.rounded_rectangle(
            [x1 * SS, y1 * SS, x2 * SS, y2 * SS],
            radius=radius * SS,
            fill=fill,
            outline=outline,
            width=max(1, int(round(width * SS))) if outline else 0,
        )

    def line(self, points, fill, width=2.0, joint="curve"):
        pts = [(x * SS, y * SS) for x, y in points]
        self.d.line(pts, fill=fill, width=max(1, int(round(width * SS))), joint=joint)

    def arc(self, cx, cy, rx, ry, start, end, fill, width=2.0):
        self.d.arc(
            [(cx - rx) * SS, (cy - ry) * SS, (cx + rx) * SS, (cy + ry) * SS],
            start=start,
            end=end,
            fill=fill,
            width=max(1, int(round(width * SS))),
        )


def _rot_ellipse(cx, cy, rx, ry, deg, steps=26):
    """旋转椭圆的采样点，用来画尾巴/发梢这类需要倾斜的形状。"""
    pts = []
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    for index in range(steps):
        t = math.tau * index / steps
        x = rx * math.cos(t)
        y = ry * math.sin(t)
        pts.append((cx + x * ca - y * sa, cy + x * sa + y * ca))
    return pts


def _pose(direction, phase):
    angle = math.radians(ANGLES[direction])
    fx, fy = math.cos(angle), math.sin(angle)
    # front: +1 = 面向镜头（朝下），-1 = 背对镜头（朝上）
    front = fy
    side = fx
    # 走路：身体轻微上下浮动，脚步交替；脚底基线不移动
    if phase == "walk0":
        bob, step = -1.1, 1
    elif phase == "walk1":
        bob, step = -0.4, -1
    else:
        bob, step = 0.0, 0
    return {
        "front": front,
        "side": side,
        "bob": bob,
        "step": step,
        "walk": 0 if phase == "idle" else (1 if phase == "walk0" else -1),
    }


def render_chibi(direction, phase="idle", flash=False, hurt=False):
    pose = _pose(direction, phase)
    front = pose["front"]
    side = pose["side"]
    bob = pose["bob"]
    step = pose["step"]

    img = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    p = Pen(img)

    hx = CENTER_X + side * 2.0
    hy = 38.0 + (2.0 if front < -0.2 else 0.0) + bob
    head_r = 21.0

    # ---------------------------------------------------------------- 远景层
    # 鲸鱼尾巴（在身体后面）
    tail_cx = hx - side * 14 - 6
    p.poly(_rot_ellipse(tail_cx + 2, 90.0, 9.0, 4.8, 26 if side >= 0 else -26), TAIL, HAIR_DARK, 1.0)
    p.poly(_rot_ellipse(tail_cx - 7, 84.0, 8.2, 4.2, -24 if side >= 0 else 24), TAIL, HAIR_DARK, 1.0)

    # 长发后片
    p.ellipse(hx - side * 2, 70.0, 21.0, 27.0, HAIR_DARK)
    p.ellipse(hx - side * 5, 78.0, 7.0, 20.0, HAIR)
    p.ellipse(hx + 13, 76.0, 6.0, 18.0, HAIR_DARK)

    # ---------------------------------------------------------------- 连衣裙
    dress_top = 57.0 + bob
    dress_hem = 94.0
    p.poly(
        [(hx - 12.5, dress_top), (hx + 12.5, dress_top), (hx + 10.0, 69.0),
         (hx + 22.0, dress_hem), (hx - 22.0, dress_hem), (hx - 10.0, 69.0)],
        DRESS,
    )
    # 左侧受光
    p.poly(
        [(hx - 12.5, dress_top), (hx - 4.0, dress_top), (hx - 4.0, 69.0),
         (hx - 11.0, dress_hem), (hx - 22.0, dress_hem), (hx - 10.0, 69.0)],
        DRESS_LIGHT,
    )
    # 裙摆白边 + 褶边
    p.rrect(hx - 22.0, dress_hem - 3.4, hx + 22.0, dress_hem, 1.6, WHITE_DARK)
    for index in range(-5, 6):
        p.circle(hx + index * 4.2, dress_hem - 1.4, 2.2, WHITE)

    if front > 0.08:
        # 前围裙
        apron_dx = side * 1.2
        p.poly(
            [(hx - 7.0 + apron_dx, 64.0 + bob), (hx + 7.0 + apron_dx, 64.0 + bob),
             (hx + 11.0 + apron_dx, 90.0), (hx - 11.0 + apron_dx, 90.0)],
            WHITE,
        )
        p.rrect(hx - 8.5 + apron_dx, 63.0 + bob, hx + 8.5 + apron_dx, 66.0, 1.6, WHITE_DARK)
        # 围裙上的小鲸鱼
        p.ellipse(hx + apron_dx, 80.0, 3.4, 2.6, RIBBON)
        p.poly(
            [(hx + apron_dx + 3.0, 79.4), (hx + apron_dx + 5.6, 77.8), (hx + apron_dx + 4.6, 81.4)],
            RIBBON,
        )
    else:
        # 背面的蝴蝶结
        for sign in (-1, 1):
            p.poly(
                [(hx, 78.0), (hx + sign * 9.0, 74.5), (hx + sign * 8.0, 81.5)],
                WHITE,
            )
        p.circle(hx, 78.0, 2.6, RIBBON)

    shoe_y = 96.0
    left_dx, right_dx = -6.0, 6.0
    left_lift, right_lift = 0.0, 0.0
    if step > 0:
        left_dx -= 1.8
        right_dx += 2.2
        right_lift = -1.4
    elif step < 0:
        right_dx += 1.8
        left_dx -= 2.2
        left_lift = -1.4
    for dx, lift in ((left_dx, left_lift), (right_dx, right_lift)):
        p.rrect(hx + dx - 2.6, 90.0 + lift, hx + dx + 2.6, shoe_y + lift, 1.4, SKIN_DARK)
        p.rrect(hx + dx - 4.0, shoe_y - 3.4 + lift, hx + dx + 4.0, shoe_y + 0.6 + lift, 2.0, SHOE)
        p.line([(hx + dx - 4.0, shoe_y - 1.6 + lift), (hx + dx + 4.0, shoe_y - 1.6 + lift)], WHITE_DARK, 0.9)

    # 领口 + 领结
    p.rrect(hx - 8.5, dress_top - 2.0, hx + 8.5, dress_top + 5.0, 2.4, WHITE)
    for sign in (-1, 1):
        p.poly(
            [(hx, dress_top + 1.5), (hx + sign * 6.0, dress_top - 1.0), (hx + sign * 5.2, dress_top + 4.0)],
            RIBBON,
        )
    p.circle(hx, dress_top + 1.5, 1.8, HAIR_DARK)

    # ---------------------------------------------------------------- 手臂
    swing = pose["walk"] * 2.0
    arms = ((hx - 14.5, 61.0 + bob, hx - 21.0, 76.0 + bob - swing),
            (hx + 14.5, 61.0 + bob, hx + 21.0, 76.0 + bob + swing))
    for x1, y1, x2, y2 in arms:
        p.line([(x1, y1), (x2, y2)], DRESS_LIGHT, 6.0)
        p.circle(x2, y2 - 1.2, 3.0, WHITE)
        p.circle(x2, y2, 2.6, SKIN)

    # ---------------------------------------------------------------- 头部
    # 脖子
    p.rrect(hx - 4.0, 52.0 + bob, hx + 4.0, 60.0 + bob, 2.0, SKIN_DARK)
    # 脸
    p.circle(hx, hy, head_r - 0.5, SKIN)

    # 发帽：在脸上“开一个朝向镜头的脸洞”
    hair = Image.new("RGBA", img.size, (0, 0, 0, 0))
    hp = Pen(hair)
    hp.circle(hx - side * 2.2, hy - 1.6, head_r + 3.6, HAIR)
    if front > -0.35:
        cut = Image.new("L", img.size, 255)
        cd = ImageDraw.Draw(cut)
        open_rx = 12.5 + 3.0 * max(0.0, front)
        open_ry = 14.8 + 2.0 * max(0.0, front)
        face_cx = hx + side * 7.5
        face_cy = hy + 2.5
        cd.ellipse(
            [(face_cx - open_rx) * SS, (face_cy - open_ry) * SS,
             (face_cx + open_rx) * SS, (face_cy + open_ry) * SS],
            fill=0,
        )
        hair.putalpha(ImageChops.multiply(hair.getchannel("A"), cut))
    img.alpha_composite(hair)
    p = Pen(img)

    # 刘海：几团圆弧压在额头
    bang_cx = hx + side * 6.5
    for index in range(-2, 3):
        bx = bang_cx + index * 5.6
        by = hy - 9.0 + abs(index) * 1.8 + bob * 0.0
        p.circle(bx, by, 5.4, HAIR)
    # 两侧发缕
    p.poly(_rot_ellipse(hx - 20.0, hy + 9.0, 4.6, 13.0, 8), HAIR)
    p.poly(_rot_ellipse(hx + 20.0, hy + 9.0, 4.6, 13.0, -8), HAIR)

    # 发箍：白色褶边
    for index in range(-3, 4):
        bx = hx - side * 2.2 + index * 5.2
        by = hy - 16.0 + 3.6 * (abs(index) / 3.0) ** 2
        p.circle(bx, by, 3.4, WHITE)
        p.circle(bx, by, 3.4, None, WHITE_DARK, 0.7)
    # 侧边小蝴蝶结
    bow_x = hx + side * 19.0 + 4.0
    bow_y = hy - 5.0
    for sign in (-1, 1):
        p.poly([(bow_x, bow_y), (bow_x + sign * 5.4, bow_y - 3.0), (bow_x + sign * 5.0, bow_y + 3.0)], RIBBON)
    p.circle(bow_x, bow_y, 1.6, HAIR_DARK)

    # 呆毛
    p.line(
        [(hx + 1.0, hy - 23.0), (hx + 6.0, hy - 29.0), (hx + 2.0, hy - 31.0)],
        HAIR,
        2.2,
    )

    # ---------------------------------------------------------------- 表情
    if front > -0.35:
        face_cx = hx + side * 6.5
        eye_y = hy + 4.0
        eye_dx = 7.4 if front > 0.55 else 5.2
        single = abs(side) > 0.78 and front < 0.45
        eyes = [(face_cx + side * 2.0, eye_y)] if single else [
            (face_cx - eye_dx + side * 2.0, eye_y),
            (face_cx + eye_dx + side * 2.0, eye_y),
        ]
        if hurt:
            # 受击：>_< 的挤眼 + 皱眉 + 痛呼
            for ex, ey in eyes:
                p.line([(ex - 3.2, ey - 2.4), (ex + 3.2, ey + 2.4)], EYE_DARK, 1.9)
                p.line([(ex - 3.2, ey + 2.4), (ex + 3.2, ey - 2.4)], EYE_DARK, 1.9)
                p.line([(ex - 3.4, ey - 4.6), (ex + 2.6, ey - 3.4)], HAIR_DARK, 1.2)
            p.ellipse(face_cx + side * 1.5, hy + 13.4, 3.2, 2.7, (196, 82, 92))
            p.ellipse(face_cx + side * 1.5, hy + 12.6, 1.6, 1.0, (240, 150, 155))
            # 头顶两侧的受击线
            for sign_x in (-1, 1):
                p.line([(hx + sign_x * 26.0, hy - 12.0), (hx + sign_x * 31.0, hy - 16.0)], WHITE, 2.0)
                p.line([(hx + sign_x * 27.0, hy - 5.0), (hx + sign_x * 33.0, hy - 6.0)], WHITE, 1.6)
        else:
            for ex, ey in eyes:
                p.ellipse(ex, ey, 4.3, 5.8, EYE_DARK)
                p.ellipse(ex, ey + 0.6, 3.1, 4.6, EYE)
                p.circle(ex - 1.2, ey - 2.2, 1.6, WHITE)
                p.circle(ex + 1.1, ey + 1.8, 0.9, WHITE)
            # 嘴巴：开心的小张嘴
            p.ellipse(face_cx + side * 1.5, hy + 13.0, 2.5, 2.0, (196, 82, 92))
            p.ellipse(face_cx + side * 1.5, hy + 12.4, 1.3, 0.9, (240, 150, 155))
        # 腮红
        for sign_x in (-1, 1):
            p.ellipse(face_cx + sign_x * 10.0, hy + 10.5, 3.0, 1.8, BLUSH)
    elif front < -0.4:
        # 背面：后脑勺的高光弧 + 小发旋 + 蓝色发带蝴蝶结
        p.arc(hx - side * 2.2, hy - 1.6, 12.0, 10.0, 200, 330, HAIR_LIGHT, 1.6)
        p.circle(hx - side * 2.2, hy - 12.0, 2.0, HAIR_DARK)
        for sign in (-1, 1):
            p.poly([(hx, hy + 2.0), (hx + sign * 8.0, hy - 1.0), (hx + sign * 7.0, hy + 5.0)], RIBBON)
        p.circle(hx, hy + 2.0, 2.2, HAIR_DARK)

    # 轻微环境光：顶部提亮
    light = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = Pen(light)
    ld.ellipse(hx - side * 4, hy - 1.6, 19.0, 17.0, (255, 255, 255, 26))
    light = light.filter(ImageFilter.GaussianBlur(2.0 * SS))
    img.alpha_composite(light)

    if flash:
        img = _flash(img)
    return img


def _flash(img):
    white = Image.new("RGBA", img.size, (255, 255, 255, 0))
    white.putalpha(img.getchannel("A").point(lambda value: int(value * 0.66)))
    return Image.alpha_composite(img, white)
