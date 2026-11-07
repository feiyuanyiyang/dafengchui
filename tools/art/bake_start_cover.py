# -*- coding: utf-8 -*-
"""把封面源图烘成开始界面 / 自定义界面用的背景。

    python tools/art/bake_start_cover.py

输入
    art_preview/cover_source/cover_base.png  已擦除水印的封面底（2400×1680，优先使用）
    art_preview/cover_source/cover_123.jpg   即梦导出的 2496×1664 原图（带平台水印，兜底）

    源图右下角带“即梦AI”水印，脚本不做擦除：仓库里保存一份已擦洗干净的封面底，
    有它时优先用它，只做放大 / 裁切，避免把水印重新烘进资源。

输出
    assets/sprites/ui/start_cover.png        2400×1680（2 倍），开始界面背景
    assets/sprites/ui/custom_backdrop.png    1200×840，自定义界面背景（压暗 + 轻模糊）

处理
    1) 等比缩放、居中裁成 1200×840 的构图（按 2 倍输出）；
    2) 整体放大 ZOOM：左边缘 + 底边对齐裁切，人物随之外移；
    3) 开始界面：整体轻压暗 + 底部暗角（托住按钮/提示）；
    4) 自定义界面：整体压暗 + 高斯模糊，作为设置面板后面的底图。

为什么放大：封面里人物贴着右边缘，居中排版时会被按钮盖住。放大 1.06 后
人物右移约 54px，按钮基本让开了；代价是右侧裁掉一条（约一个袖口）。
封面 2400×1680 是 2 倍分辨率，所以这里的裁切与缩放都在 2 倍下做，不额外损失。

确定性
    只做 resize / crop / 渐变 / 模糊，无随机数；重复运行得到相同字节。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "art_preview" / "cover_source" / "cover_123.jpg"
# 已擦除水印、且已含压暗与底部渐隐的封面底：有它时不再重做压暗
BASE_CLEAN = ROOT / "art_preview" / "cover_source" / "cover_base.png"
OUT_DIR = ROOT / "assets" / "sprites" / "ui"

W, H = 1200, 840

# 2 倍分辨率输出：开始界面在高分屏上缩小显示，不会被放大糊掉
OUT_SCALE = 2

# 画面整体放大倍数（评审定稿 1.06）：1.0 = 不放大
ZOOM = 1.06

# 开始界面暗角参数
# 标题区实测是很亮的白云/天空，深藏青文字在亮底上可读，比压暗整幅更好看，
# 所以顶部不做暗角；只在底部做渐隐，托住按钮列和“回车/空格”提示。
GLOBAL_DARKEN = 0.94       # 整幅亮度系数（1.0 = 不动）
TOP_ALPHA = 0.0            # 顶部不压暗（保留明亮天空）
BOTTOM_FADE_START = 560    # 560 以下开始变暗（1x 逻辑坐标）
BOTTOM_ALPHA = 0.86        # 840 处的暗度

# 自定义界面底图参数
CUSTOM_DARKEN = 0.42
CUSTOM_BLUR = 3.0

# 用来做可读性自检的区域（设计坐标 1200×840）
REGIONS = {
    "标题": (480, 243, 720, 332),
    "按钮列": (420, 416, 780, 680),
    "底部提示": (420, 772, 780, 808),
    "水印区": (980, 706, 1140, 812),
}


def build_base(source: Path) -> Image.Image:
    """等比缩放到 2 倍高度、水平居中裁成 2400×1680。"""
    with Image.open(source) as image:
        image = image.convert("RGB")
        width, height = image.size
        scale = (H * OUT_SCALE) / height
        resized = image.resize(
            (round(width * scale), round(H * OUT_SCALE)), Image.LANCZOS)
    left = (resized.width - W * OUT_SCALE) // 2
    return resized.crop((left, 0, left + W * OUT_SCALE, H * OUT_SCALE))


def apply_zoom(image: Image.Image, zoom: float = ZOOM) -> Image.Image:
    """整体放大：左边缘 + 底边对齐裁切，再把画面缩回原尺寸。

    左对齐让内容整体右移（人物离开按钮区），底对齐保证她的脚不被切掉。
    """
    if abs(zoom - 1.0) < 1e-4:
        return image
    width, height = image.size
    box = (
        0,
        int(round(height - height / zoom)),
        int(round(width / zoom)),
        height,
    )
    return image.crop(box).resize((width, height), Image.LANCZOS)


def scrim_alpha(y: float) -> float:
    """y 是 1x 逻辑坐标。"""
    alpha = TOP_ALPHA
    if y > BOTTOM_FADE_START:
        t = (y - BOTTOM_FADE_START) / float(H - BOTTOM_FADE_START)
        alpha = max(alpha, BOTTOM_ALPHA * t ** 1.15)
    return alpha


def apply_scrim(image: Image.Image) -> Image.Image:
    width, height = image.size
    factor = height / float(H)
    dark = Image.new("RGB", (width, height), (7, 11, 17))
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    for y in range(height):
        draw.line([(0, y), (width, y)],
                  fill=int(round(255 * scrim_alpha(y / factor))))
    return Image.composite(dark, image, mask)


def stats(image: Image.Image, box) -> str:
    crop = image.crop(box).convert("L")
    values = sorted(crop.getdata())
    total = len(values)

    def pct(p):
        return values[min(total - 1, int(total * p))] / 255.0

    return "p50=%.3f p90=%.3f p99=%.3f" % (pct(0.5), pct(0.9), pct(0.99))


def contrast_vs_white(lum: float) -> float:
    return 1.05 / (lum + 0.05)


def main() -> None:
    clean = BASE_CLEAN.is_file()
    backdrop = None
    if clean:
        # 已擦水印的封面底：压暗与底部渐隐都烘在里面了，这里只做放大
        base = Image.open(BASE_CLEAN).convert("RGB")
        if base.size != (W * OUT_SCALE, H * OUT_SCALE):
            base = base.resize((W * OUT_SCALE, H * OUT_SCALE), Image.LANCZOS)
        zoomed = base
        cover = apply_zoom(base)
    else:
        if not SOURCE.is_file():
            raise SystemExit("找不到源图：%s" % SOURCE)
        base = build_base(SOURCE)
        zoomed = apply_zoom(base)
        darkened = ImageEnhance.Brightness(zoomed).enhance(GLOBAL_DARKEN)
        cover = apply_scrim(darkened)
        backdrop = base.resize((W, H), Image.LANCZOS)
        backdrop = backdrop.filter(ImageFilter.GaussianBlur(CUSTOM_BLUR))
        backdrop = ImageEnhance.Brightness(backdrop).enhance(CUSTOM_DARKEN)
        backdrop.save(OUT_DIR / "custom_backdrop.png", optimize=True)
    cover.save(OUT_DIR / "start_cover.png", optimize=True)

    print("底图：%s" % ("cover_base.png（已擦水印，只做放大）" if clean
                       else "cover_123.jpg（源图，重做压暗）"))
    print("输出：")
    for name in (("start_cover.png",) if clean else
                 ("start_cover.png", "custom_backdrop.png")):
        path = OUT_DIR / name
        print("  %s  %s  %d bytes" % (path.name, Image.open(path).size,
                                      path.stat().st_size))
    print("\n可读性自检（开始界面，白字对比度按区域 p90 估算）：")
    preview = cover.resize((W, H), Image.LANCZOS)
    for label, box in REGIONS.items():
        before = stats(zoomed.resize((W, H), Image.LANCZOS), box)
        after = stats(preview, box)
        lum90 = float(after.split("p90=")[1].split(" ")[0])
        print("  %-6s %s | %s | 白字对比度≈%.2f:1"
              % (label, before, after, contrast_vs_white(lum90)))
    if backdrop is not None:
        print("custom_backdrop 亮度：%s" % stats(backdrop, (0, 0, W, H)))


if __name__ == "__main__":
    main()
