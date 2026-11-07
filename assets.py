# -*- coding: utf-8 -*-
"""《大风吹》资源加载层：路径解析、PNG 读取、缩放缓存、Tk 图片对象缓存。

设计原则
    - 资源是“数据”，不是模块依赖：逻辑层（GameModel）永远不 import 本文件。
    - Pillow 在这里是**可选**的：缺失或资源目录不完整时 ready=False，
      渲染层会自动退回矢量几何绘制，游戏仍可运行（`--selftest` 也照常无窗口跑）。
    - PhotoImage 必须由持有者长期引用，否则会被 GC 回收成空白；
      本模块把 ImageTk.PhotoImage 缓存在实例里，Renderer 只需持有 AssetStore。

路径
    开发态：<项目根>/assets
    打包态：PyInstaller 单文件运行时资源解压在 sys._MEIPASS/assets
"""

from __future__ import annotations

import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# --- 设计尺寸约定（烘焙脚本 tools/art/bake_sprites.py 必须一致） --------------
PLAYER_SPRITE_SIZE = (125, 135)  # 玩家画布：脚底基线在贴图底边，运行时 anchor="s"
PLAYER_DIRECTIONS = ("e", "w")   # 只烘左/右两个基础朝向（正面像）；上/下移动保留当前左右贴图
PLAYER_STATES = ("idle", "walk")
PLAYER_SHADOW_SIZE = (83, 31)
HEART_SPRITE_SIZE = 34
PICKUP_SPRITE_SIZE = 43
VIGNETTE_SIZE = (1200, 840)
OBSTACLE_PAD_MIN = 5.0
OBSTACLE_PAD_RATIO = 0.4

# 障碍物帧动画：每档的烘焙帧数（帧 0 是基准姿态，额外帧按 _1/_2/... 命名）
#   small  : 4 帧翻面（旋转 + 压扁）
#   medium : 144 帧绕贴图中心旋转（2.5°/帧）
#   large  : 180 帧绕贴图中心旋转（2°/帧）
# 绘制侧的帧参数见 tools/art/obstacle_art.py，两边必须一致。
OBSTACLE_SPRITE_FRAMES = {"small": 4, "medium": 144, "large": 180}
OBSTACLE_VARIANTS = 3

# 非玩家贴图必须齐全；玩家帧按目录动态发现（walk 可以是 2 帧、8 帧、16 帧……）
def obstacle_sprite_name(tier, variant, frame=0):
    """障碍物贴图名（不含扩展名）：帧 0 沿用旧名，额外帧加 _<n> 后缀。"""
    base = "obstacles/obstacle_%s_%d" % (tier, variant % OBSTACLE_VARIANTS)
    if frame <= 0:
        return base
    return base + "_%d" % frame


REQUIRED_SPRITES = tuple(
    ["player/player_hurt"]
    + [
        obstacle_sprite_name(tier, variant, frame)
        for tier, count in OBSTACLE_SPRITE_FRAMES.items()
        for variant in range(OBSTACLE_VARIANTS)
        for frame in range(count)
    ]
    + ["ui/heart_full", "ui/heart_empty", "ui/pickup_heart", "ui/player_shadow", "bg/vignette"]
    + ["bg/cloud_0", "bg/cloud_1"]
)


def asset_root():
    """资源根目录：打包后指向 _MEIPASS，否则指向源码同级目录。"""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "assets"
    return Path(__file__).resolve().parent / "assets"


def obstacle_sprite_size(radius):
    """障碍物贴图的设计尺寸（正方形边长），随半径自动适配。"""
    pad = max(OBSTACLE_PAD_MIN, radius * OBSTACLE_PAD_RATIO)
    return int(round((radius + pad) * 2))


def _frame_index(path):
    match = re.search(r"(\d+)$", path.stem)
    return int(match.group(1)) if match else 0


def player_frame_paths(root, direction, state):
    """发现 player_<dir>_<state>*.png，按文件名末尾数字排序。

    支持 player_e_idle.png、player_e_walk0.png、player_e_walk00.png…walk15.png，
    帧数完全由文件数量决定。
    """
    directory = Path(root) / "sprites" / "player"
    pattern = "player_%s_%s*.png" % (direction, state)
    return sorted(directory.glob(pattern), key=lambda item: (_frame_index(item), item.name))


class SpriteFacing:
    """伪 3D 朝向映射：只有左/右两张基础贴图。

    规则（对应需求）：
        - 左/左上/左下：使用左向贴图
        - 右/右上/右下：使用右向贴图
        - 纯上/下：贴图保持不变（沿用最近一次的左/右）

    0.15 的水平死区用于过滤风向漂移造成的左右抖动。
    """

    DEAD_ZONE = 0.15

    def __init__(self):
        self.direction = "e"

    def update(self, face_x, face_y):
        if face_x < -self.DEAD_ZONE:
            self.direction = "w"
        elif face_x > self.DEAD_ZONE:
            self.direction = "e"
        return self.direction

    def reset(self):
        self.direction = "e"


_UI_SCALE = 1.0

# 预加载：PNG 解码/resize 放线程池（Pillow 会释放 GIL），PhotoImage 仍回主线程创建。
# 分块是为了压住峰值内存——一次只留几十张解好的图，而不是上千张。
_PRELOAD_WORKERS = max(2, min(8, os.cpu_count() or 4))
_PRELOAD_CHUNK = 64
# 每档帧数不超过这个值的障碍物同步加载（对局早期就会出现），其余分帧补
_PRELOAD_EAGER_FRAMES = 8

# 闪白蒙版：透明度 0.66 的查表，比 lambda 逐像素算快一点（预加载时要用 172 次）。
_FLASH_TABLE = [int(value * 0.66) for value in range(256)]


def set_ui_scale(scale):
    """设置界面放大系数（游戏启动时调用）。

    Tk 的 PhotoImage 不支持非整数缩放，所以这里先用 Pillow 把位图放大，
    再交给 Tk——HUD 的烘焙图因此保持锐利，而不是被窗口拉伸。
    """
    global _UI_SCALE
    try:
        _UI_SCALE = max(1.0, float(scale))
    except (TypeError, ValueError):
        _UI_SCALE = 1.0


def ui_scale():
    return _UI_SCALE


def crisp(image):
    """按 UI 缩放放大位图；1.0 时原样返回。"""
    if _UI_SCALE <= 1.0 or image is None:
        return image
    size = (int(round(image.width * _UI_SCALE)),
            int(round(image.height * _UI_SCALE)))
    if size == image.size:
        return image
    try:
        from PIL import Image
    except Exception:
        return image
    return image.resize(size, Image.LANCZOS)


class AssetStore:
    """PNG 贴图仓库；线程不安全，只在 Tk 主线程使用。"""

    def __init__(self, root=None):
        self.root = Path(root) if root is not None else asset_root()
        self.ready = False
        self.error = ""
        self.missing = ()
        self._Image = None
        self._ImageTk = None
        self._images = {}
        self._photos = {}
        self._ui_manifest = self._load_ui_manifest()
        self._warned = set()

        try:
            from PIL import Image, ImageTk  # noqa: F401  (运行时可选依赖)
        except Exception as exc:  # pragma: no cover - 仅在没装 Pillow 的机器上
            self.error = "Pillow 不可用：%s" % (exc,)
            return

        if not (self.root / "sprites").is_dir():
            self.error = "找不到贴图目录：%s" % (self.root / "sprites")
            return

        # 一次 os.walk 建好现有贴图集合，再逐个判缺失：
        # 比 984 次 is_file() 快一个数量级（每次 stat 在 Windows 上要 ~0.15ms）。
        available = set()
        try:
            for base, _dirs, files in os.walk(self.root / "sprites"):
                base_path = Path(base)
                for filename in files:
                    if filename.lower().endswith(".png"):
                        available.add(
                            (base_path / filename)
                            .relative_to(self.root / "sprites")
                            .with_suffix("")
                            .as_posix()
                        )
        except OSError:
            available = set()

        missing = [
            name for name in REQUIRED_SPRITES if name not in available
        ]

        self.player_frames = {}
        for direction in PLAYER_DIRECTIONS:
            for state in PLAYER_STATES:
                paths = player_frame_paths(self.root, direction, state)
                if paths:
                    self.player_frames[(direction, state)] = [
                        path.relative_to(self.root / "sprites").with_suffix("").as_posix()
                        for path in paths
                    ]
                else:
                    missing.append("player/player_%s_%s*" % (direction, state))

        if missing:
            self.missing = tuple(missing)
            self.error = "缺少 %d 个贴图（例：%s）" % (len(missing), missing[0])
            return

        self._Image = Image
        self._ImageTk = ImageTk
        self.ready = True

    # ------------------------------------------------------------ 基础加载
    def _scaled_size(self, size):
        """设计尺寸 → 物理像素尺寸（UI 放大后 PhotoImage 必须按物理像素建）。"""
        if _UI_SCALE <= 1.0:
            return size
        return (max(1, int(round(size[0] * _UI_SCALE))),
                max(1, int(round(size[1] * _UI_SCALE))))

    def _resample(self, source_size, target_size):
        """选重采样滤波器。

        - 精确 2× 下采样：BOX 就是 2×2 均值，和 LANCZOS 几乎无差，快约 25%
        - 放大：BICUBIC 比 LANCZOS 快，背景这种大图肉眼也看不出区别
        - 其余下采样：保持 LANCZOS，避免缩小后边缘发虚
        """
        if (source_size[0] == target_size[0] * 2
                and source_size[1] == target_size[1] * 2):
            return self._Image.BOX
        if target_size[0] > source_size[0] or target_size[1] > source_size[1]:
            return self._Image.BICUBIC
        return self._Image.LANCZOS

    def _load_image(self, name, size, target=None):
        """读 PNG 并一次性缩放到 target（默认 size）；不进缓存，失败记录一次。"""
        path = self.root / "sprites" / (name + ".png")
        target = size if target is None else target
        try:
            with self._Image.open(path) as source:
                img = source.convert("RGBA")
            if img.size != target:
                img = img.resize(target, self._resample(img.size, target))
        except Exception as exc:
            self._warn_once("image:%s" % name, "贴图加载失败 %s：%s" % (path, exc))
            return None
        return img

    def image(self, name, size):
        """按名字读取 PNG，缩放到 size=(w,h)；失败返回 None 并记录一次。"""
        if not self.ready:
            return None
        key = (name, size)
        cached = self._images.get(key)
        if cached is not None:
            return cached
        img = self._load_image(name, size)
        if img is not None:
            self._images[key] = img
        return img

    def photo(self, name, size, master, keep_image=True):
        """返回 ImageTk.PhotoImage（带缓存）；必须由本对象持有引用。

        直接按物理像素尺寸解码，不再“先缩到设计尺寸再按 UI 缩放放大”：
        150% 缩放下这样少一次重采样，也少一份中间图。
        keep_image=False 时不保留中间 PIL 图（障碍物角度帧几百张，省内存）。
        """
        if not self.ready:
            return None
        key = (name, size)
        cached = self._photos.get(key)
        if cached is not None:
            return cached
        target = self._scaled_size(size)
        img = self._load_image(name, size, target)
        if img is None:
            return None
        try:
            photo = self._ImageTk.PhotoImage(img, master=master)
        except Exception as exc:  # pragma: no cover - 仅在 Tk 异常时
            self._warn_once("photo:%s" % name, "PhotoImage 创建失败 %s：%s" % (name, exc))
            return None
        self._photos[key] = photo
        if keep_image and target == size:
            # 只有 1.0 缩放时中间图和设计尺寸一致，缓存才有复用价值
            self._images[key] = img
        return photo

    # ------------------------------------------------------------ 语义化接口
    def _variant_photo(self, name, size, flash, mirror, master, base=None):
        """闪白/镜像在运行时生成，缓存键包含闪白位与镜像位。

        base 传入已解码好的物理像素图时不再重复读盘（预加载走这条路）。
        """
        key = (name, size, bool(flash), bool(mirror))
        cached = self._photos.get(key)
        if cached is not None:
            return cached
        image = base
        if image is None:
            image = self._load_image(name, size, self._scaled_size(size))
        if image is None:
            return None
        if mirror:
            image = image.transpose(self._Image.FLIP_LEFT_RIGHT)
        if flash:
            white = self._Image.new("RGBA", image.size, (255, 255, 255, 0))
            white.putalpha(image.getchannel("A").point(_FLASH_TABLE))
            image = self._Image.alpha_composite(image, white)
        photo = self._ImageTk.PhotoImage(image, master=master)
        self._photos[key] = photo
        return photo

    def frame_count(self, direction, state):
        return len(self.player_frames.get((direction, state), ()))

    def player_photo(self, direction, state, index, flash, master):
        frames = self.player_frames.get((direction, state))
        if not frames:
            return None
        name = frames[index % len(frames)]
        return self._variant_photo(name, PLAYER_SPRITE_SIZE, flash, False, master)

    def hurt_photo(self, flash, master, direction="e"):
        """受击贴图；只烘一张正面像，左向在运行时水平镜像。

        这样自定义受击图（import_player.py --hurt）也只需要提供一张，
        两侧受伤贴图自动都有；镜像结果和闪白变体都会被缓存。
        """
        mirror = direction == "w"
        return self._variant_photo(
            "player/player_hurt", PLAYER_SPRITE_SIZE, flash, mirror, master
        )

    def player_shadow_photo(self, master):
        return self.photo("ui/player_shadow", PLAYER_SHADOW_SIZE, master)

    def obstacle_photo(self, tier, radius, variant, frame, master):
        """障碍物帧动画贴图；frame 超出档位帧数时按循环取模。"""
        count = OBSTACLE_SPRITE_FRAMES.get(tier, 1)
        size = obstacle_sprite_size(radius)
        name = obstacle_sprite_name(tier, variant, int(frame) % max(1, count))
        return self.photo(name, (size, size), master, keep_image=False)

    def heart_photo(self, full, master):
        s = HEART_SPRITE_SIZE
        return self.photo("ui/heart_full" if full else "ui/heart_empty", (s, s), master)

    def pickup_photo(self, master):
        s = PICKUP_SPRITE_SIZE
        return self.photo("ui/pickup_heart", (s, s), master)

    def vignette_photo(self, master):
        return self.photo("bg/vignette", VIGNETTE_SIZE, master)

    def cloud_photo(self, index, size, master):
        """软云贴图按目标包围盒拉伸；size=(w,h)。"""
        return self.photo("bg/cloud_%d" % (index % 2), size, master)

    # ------------------------------------------------------------ 蜡笔 UI 贴图
    # 这些图由 tools/art/bake_crayon_ui.py 预生成，尺寸固定，不要再缩放：
    # 描边与颗粒都在像素级别调过，缩放会糊掉。
    def _load_ui_manifest(self):
        """烘焙清单：记录文字内容、格子尺寸与烘焙倍率。"""
        path = self.root / "sprites" / "ui" / "crayon" / "manifest.json"
        try:
            import json

            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def ui_image(self, name):
        """读取 assets/sprites/ui/crayon/<name>.png（原尺寸，不缩放）。"""
        if not self.ready:
            return None
        key = ("ui-image", name)
        cached = self._images.get(key)
        if cached is not None:
            return cached
        path = self.root / "sprites" / "ui" / "crayon" / (name + ".png")
        try:
            with self._Image.open(path) as source:
                image = source.convert("RGBA")
        except Exception as exc:
            self._warn_once("ui:%s" % name, "UI 贴图加载失败 %s：%s" % (path, exc))
            return None
        self._images[key] = image
        return image

    def ui_scale(self):
        """烘焙资源相对逻辑尺寸的倍率（manifest 里写 2 表示按 2 倍分辨率烘的）。"""
        try:
            return float(self._ui_manifest.get("scale", 1) or 1)
        except Exception:
            return 1.0

    def ui_size(self, name):
        """逻辑尺寸（1x）：HUD 布局按这个算，跟烘焙分辨率无关。"""
        image = self.ui_image(name)
        if image is None:
            return None
        scale = self.ui_scale()
        if scale == 1.0:
            return image.size
        return (int(round(image.width / scale)), int(round(image.height / scale)))

    def ui_pixels(self, name, target_scale):
        """按目标倍率取出位图（已从烘焙分辨率缩到 逻辑 × target_scale）。"""
        image = self.ui_image(name)
        if image is None:
            return None
        logical = self.ui_size(name)
        target = (max(1, int(round(logical[0] * target_scale))),
                  max(1, int(round(logical[1] * target_scale))))
        if image.size == target:
            return image
        try:
            from PIL import Image
        except Exception:
            return image
        return image.resize(target, Image.LANCZOS)

    def ui_photo(self, name, master):
        if not self.ready:
            return None
        key = ("ui-photo", name)
        cached = self._photos.get(key)
        if cached is not None:
            return cached
        image = self.ui_pixels(name, _UI_SCALE)
        if image is None:
            return None
        try:
            photo = self._ImageTk.PhotoImage(image, master=master)
        except Exception as exc:  # pragma: no cover
            self._warn_once("ui-photo:%s" % name, "UI PhotoImage 失败 %s：%s" % (name, exc))
            return None
        self._photos[key] = photo
        return photo

    def has_ui(self, names):
        """检查一组成套 UI 贴图是否齐全；缺任意一张就让渲染层回退到旧 HUD。"""
        if not self.ready:
            return False
        return all(
            (self.root / "sprites" / "ui" / "crayon" / (name + ".png")).is_file()
            for name in names
        )

    # ------------------------------------------------------------ 预加载
    def _decode_block(self, jobs):
        """线程池解码一块任务；jobs=[(name, size)]，返回同序 PIL 图（失败为 None）。

        只做磁盘 IO + Pillow 解码/缩放（Pillow 会释放 GIL），不碰 Tk。
        """
        if not jobs:
            return []

        def work(job):
            name, size = job
            return self._load_image(name, size, self._scaled_size(size))

        workers = min(_PRELOAD_WORKERS, len(jobs))
        if workers <= 1:
            return [work(job) for job in jobs]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(work, jobs))

    def _warm_photos(self, jobs, store):
        """分块“解码（线程池）→ 转 PhotoImage（主线程）”，控制峰值内存。"""
        for start in range(0, len(jobs), _PRELOAD_CHUNK):
            block = jobs[start:start + _PRELOAD_CHUNK]
            for (name, size), image in zip(block, self._decode_block(block)):
                if image is not None:
                    store(name, size, image)

    def _preload_player(self, master, defer_flash=False):
        """玩家帧：基础图 + 受击左右向；闪白变体可以推迟到空闲时分帧补。

        一张解码图能派生出普通/闪白/镜像几个 PhotoImage，所以按贴图名去重，
        只读一次盘。defer_flash=True 时闪白变体只登记任务，返回给调用方。
        """
        jobs = []
        seen = set()
        for direction in PLAYER_DIRECTIONS:
            for state in PLAYER_STATES:
                for name in self.player_frames.get((direction, state), ()):
                    if name in seen:
                        continue
                    seen.add(name)
                    jobs.append((name, PLAYER_SPRITE_SIZE))
        jobs.append(("player/player_hurt", PLAYER_SPRITE_SIZE))

        def store(name, size, image):
            self._variant_photo(name, size, False, False, master, base=image)
            if name == "player/player_hurt":
                # 左向受击是镜像；提前建好，别等受伤那一帧才做
                self._variant_photo(name, size, False, True, master, base=image)
            if defer_flash:
                return
            self._variant_photo(name, size, True, False, master, base=image)
            if name == "player/player_hurt":
                self._variant_photo(name, size, True, True, master, base=image)

        self._warm_photos(jobs, store)
        self.player_shadow_photo(master)
        self.heart_photo(True, master)
        self.heart_photo(False, master)
        self.pickup_photo(master)
        if not defer_flash:
            return []
        late = [("flash", name, size, False) for name, size in jobs]
        late.append(("flash", "player/player_hurt", PLAYER_SPRITE_SIZE, True))
        return late

    def _obstacle_jobs(self, obstacle_sizes):
        """展开障碍物角度帧任务：[(tier, name, (size, size))]。"""
        jobs = []
        for tier, radius in obstacle_sizes:
            size = obstacle_sprite_size(radius)
            for variant in range(OBSTACLE_VARIANTS):
                for frame in range(OBSTACLE_SPRITE_FRAMES.get(tier, 1)):
                    jobs.append(
                        (tier, obstacle_sprite_name(tier, variant, frame), (size, size)))
        return jobs

    def _store_obstacle_photos(self, master, jobs):
        """障碍物只留 PhotoImage，不留中间 PIL 图（几百张，省内存）。"""
        def store(name, size, image):
            self._photos[(name, size)] = self._ImageTk.PhotoImage(
                image, master=master)

        self._warm_photos(jobs, store)

    def _preload_obstacles(self, master, obstacle_sizes, defer_heavy=False):
        """障碍物角度帧：984 张，是启动预加载的大头，必须并行 + 省掉中间图。

        defer_heavy=True 时只同步加载“每档帧数很少”的档位（对局一开始就会出现），
        medium/large 这类几十上百帧的角度图登记成任务，交给调用方分帧补。
        """
        eager, late = [], []
        for tier, name, size in self._obstacle_jobs(obstacle_sizes):
            cheap = OBSTACLE_SPRITE_FRAMES.get(tier, 1) <= _PRELOAD_EAGER_FRAMES
            if defer_heavy and not cheap:
                late.append(("obstacle", name, size, False))
            else:
                eager.append((name, size))
        self._store_obstacle_photos(master, eager)
        return late

    def preload(self, master, cloud_sizes=(), obstacle_sizes=(), defer_heavy=True):
        """同步预加载“马上就可能用到”的贴图，返回剩余任务（交给调用方分帧补）。

        返回 [] 表示已全部加载；defer_heavy=False 时全部同步做完（旧行为）。
        流程：线程池解码/缩放到物理像素 → 回主线程建 PhotoImage；
        失败时只是少加载几张图，渲染层仍会按需重试/回退，不抛异常。
        """
        if not self.ready:
            return []
        late = self._preload_player(master, defer_flash=defer_heavy)
        for size in cloud_sizes:
            for index in range(2):
                self.cloud_photo(index, size, master)
        late.extend(
            self._preload_obstacles(master, obstacle_sizes, defer_heavy=defer_heavy))
        return late

    def load_more(self, jobs, master, budget=0.008):
        """分帧补预加载：在 budget 秒预算内尽量多做，返回剩余任务。

        任务形如 (kind, name, size, mirror)：obstacle 走线程池解码后建 PhotoImage，
        flash 是玩家闪白变体（合成 + PhotoImage）。
        按时间预算而不是张数切块 —— 每张图成本差很多（闪白合成约 6ms/张，
        障碍物约 0.5ms/张），按张数分帧会让某一帧莫名卡上几十毫秒。
        """
        if not self.ready or not jobs:
            return []
        deadline = time.perf_counter() + budget
        rest = list(jobs)
        while rest:
            if rest[0][0] == "obstacle":
                chunk = []
                while (rest and rest[0][0] == "obstacle"
                       and len(chunk) < _PRELOAD_CHUNK):
                    chunk.append((rest[0][1], rest[0][2]))
                    rest.pop(0)
                self._store_obstacle_photos(master, chunk)
            else:
                _kind, name, size, mirror = rest.pop(0)
                self._variant_photo(name, size, True, mirror, master)
            if time.perf_counter() >= deadline:
                break
        return rest

    # ------------------------------------------------------------ 诊断
    def _warn_once(self, key, message):
        if key in self._warned:
            return
        self._warned.add(key)
        if not self.error:
            self.error = message

    def status(self):
        if self.ready:
            idle = self.frame_count("e", "idle")
            walk = self.frame_count("e", "walk")
            return "贴图就绪：%s（idle %d 帧 / walk %d 帧）" % (self.root, idle, walk)
        return "贴图未启用：%s" % (self.error or "未知原因")
