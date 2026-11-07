# 蜡笔 UI 接入说明

状态：**HUD、暂停界面、结束界面均已接入 `game.py`**（crayon 贴图 + legacy 回退），开始界面仍用雅黑深色按钮。
资源已烘好在 `assets/sprites/ui/crayon/`（含 `manifest.json`），评审图在 `art_preview/`。

## 已完成：HUD

`HudRenderer` 现在有两套实现，接口一致，按资源是否存在自动选择：

- `_build_crayon()` —— 贴 `assets/sprites/ui/crayon/` 的预烘图（默认）
- `_build_legacy()` —— 没装 Pillow 或缺贴图时退回 Canvas 文字/图形

外部只调 `update()` / `set_pause_hover()` / `reset_view()` / `set_game_over_callbacks()`。
`AssetStore.has_ui()` 一次性检查 51 张图，缺任何一张就整体回退，不会出现半套样式。

截图脚本：`python tools/art/shot_hud.py [hud|over|hover]`，产物在 `art_preview/crayon_*.png`。

## 一、打包前必须做的一件事

Windows 高 DPI（125% / 150% 缩放）下，Tk 的逻辑坐标和 `ImageGrab` 的物理像素不是同一套坐标系，
暂停界面截图会整体偏移。**必须在创建窗口之前声明 DPI 感知**：

```python
import ctypes

def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # Win8.1+
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()     # 老系统兜底
        except Exception:
            pass
```

放在 `game.py` 的 `main()` 里、`tk.Tk()` 之前调用。已验证：不加会偏 70×30px。

## 二、资源清单

| 分组 | 文件 | 说明 |
| --- | --- | --- |
| 罗盘 | `compass_base.png` | 96×96 白盘 + 灰刻度 |
| 罗盘指针 | `compass_arrow_{n,ne,e,se,s,sw,w,nw}.png` | 96×96，与底盘同坐标系，直接叠放 |
| 生命 | `heart_full.png` / `heart_empty.png` | 34px |
| 其他符号 | `pause.png`、`star.png` | 41px / 20px |
| 计时 | `timer_label.png`、`digit_0..9`、`digit_dot`、`digit_s` | 数字按固定格子等宽，避免秒数跳动时抖动 |
| 风向 | `wind_{e,se,s,sw,w,nw,n,ne}.png` | 「西北风」等八向文字 |
| 提示 | `hint.png`、`god_badge.png`、`pause_hint.png` | |
| 暂停 | `pause_title.png` | 76px 黑描边白字 |
| 按钮 | `btn_{continue,restart,settings,quit,back}.png` | 白色文字，配镂空边框使用 |

镂空按钮边框**不在资源里**：暂停界面整屏合成为一张图，边框在合成时用
`rounded_frame()`（见 `tools/art/demo_pause_overlay.py`）画进去。

## 三、Canvas 的两个硬限制

1. **图片透明像素只透出画布底色，不透出下方 item。**
   所以暂停界面必须整屏合成一张图（模糊背景 + 边框 + 文字），不能分层贴。
2. **没有原生圆角 / 阴影 / 半透明。** 阴影一并烘进 PNG（木板）或合成时画（按钮）。

## 四、已接入的部分

- `HudRenderer`：贴图 HUD，缺图时整体回退 legacy。
- `CrayonPauseView`（定义在 `class PauseView` 之前）：抓图 → 模糊 → 整屏合成。
  - 应用层把 `<Motion>` / `<Button-1>` 转发给 `on_motion()` / `on_click()`。
  - 一次暂停只抓一次图，设置页与倒计时复用同一张底。
  - DPI 感知**默认关闭**：`run_game()` 只在环境变量 `DAFENGCHUI_DPI_AWARE=1`
    时才调用 `enable_dpi_awareness()`。声明感知后窗口按物理像素呈现，
    在 150% 缩放的屏幕上会明显变小（1200×840 物理像素 ≈ 原来的 2/3）。
  - 关闭感知时，`_capture()` 用 `display_scale()` 把 Tk 逻辑坐标换算成
    抓屏物理坐标（150% → ×1.5），否则模糊背景会整体偏移。
- 冒烟测试：`python tools/art/smoke_pause.py`（暂停→hover→设置→返回→倒计时→恢复）。

## 五、进度

- [x] **HUD**（`HudRenderer`）：暂停键 / 生命 / 计时 / 罗盘 / 风向名 / 上帝徽标 / 底部提示全部改成烘焙贴图。
- [x] **暂停界面**（`CrayonPauseView`）：抓图 → 高斯模糊 → 压暗 → 整屏合成，白描边镂空按钮 + hover。
- [x] **结束界面**（`CrayonGameOverView`）：同一套模糊背景，标题 / 死因 / 得分 / 两个镂空按钮 / 提示。
- [x] **开始界面**（`CrayonStartView`）：封面整屏 + 薄雾压暗，居中 96px 描边快乐体标题、
  三个 344×96 镂空按钮 + hover，提示文字同套烘焙。
  - 倍率与暂停/结束共用同一套 2× 烘焙管线，但按钮规格单独一组常量：`CRAYON_START_*`
    （尺寸 344×96、圆角 20、hover 上浮 2px + 暖色淡洗 `(255,246,224,82)`——亮底上纯白淡洗看不见）；
  - 封面在 `bake_start_cover.py` 里整体放大 1.06，把角色推到右侧、按钮居中也不压人；
  - 缺 `start_title` / `start_btn_*` 或封面时，`create_start_view()` 自动回退旧 `StartView`。

## 六、重新生成资源

```powershell
python tools/art/bake_crayon_ui.py     # 烘 48 张 PNG + manifest
python tools/art/preview_crayon_hud.py # 出评审图
```

字体在 `tools/art/fonts/`（站酷快乐体 / Patrick Hand，均 OFL，可随包分发）。
字号、质感强度等参数集中在 `bake_crayon_ui.py` 顶部，`crayon_kit.py` 里是质感管线。


## 界面放大（UI_SCALE）

- `ui_scale()`：默认取显示器缩放系数（150% → 1.5），可用 `DAFENGCHUI_UI_SCALE`
  覆盖（0.5–3.0）。
- `prepare_ui_scale()` 在创建 Tk 之前调用：系数 > 1 时声明 DPI 感知，让画布
  直接按物理像素渲染（否则 Tk 会再缩一次，等于双重放大）。
- `ScaledCanvas` 是 `tk.Canvas` 的代理，把所有 `create_*` 与 `coords()`
  的坐标乘以 UI_SCALE；模型/物理仍是 1200×840 逻辑坐标。
- 鼠标事件在 `_pointer()` 里除以 UI_SCALE 转回逻辑坐标。
- 贴图：`assets.set_ui_scale()` + `crisp()`，PhotoImage 生成前用 Pillow 放大，
  避免 Tk 拉伸；`load_ui_photo()` 与暂停遮罩的整屏合成同样按 UI_SCALE 输出。
- Tk widget 桌面（自定义界面）用 `px()` 换算 place 尺寸；字体由 tk scaling 自动放大。
