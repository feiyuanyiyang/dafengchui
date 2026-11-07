# 大风吹

八向风力 + 惯性 + 尺寸分档障碍物的 tkinter 小游戏。V1.1 起采用 **B 方案美术（柔和体积 / 发光氛围）**，玩家为 **伪 3D 的 Q 版角色（左/右两朝向 × 待机/走路帧，脚底锚点 + 脚下阴影 + 深度排序）**，设计尺寸 125×135；贴图与音频从源码中分离，放在 `assets/`。

![游戏画面](art_preview/live_scene.png)

![占位角色方向帧（游戏使用左/右两张）](art_preview/style_b_sprites.png)

## 运行

```powershell
python -m pip install -r requirements.txt   # 可选；不装也能跑，只是没有贴图/BGM
python game.py
```

| 命令 | 用途 |
| --- | --- |
| `python game.py` | 开始游戏 |
| `python game.py --selftest` | 无窗口自测（22 组），退出码 0/1 |
| `python game.py --check-assets` | 检查贴图/音频文件、WAV 参数与音频后端 |
| `python game.py --soak 60` | 浸泡测试：无敌跑 N 秒，输出 FPS 与稳定性 |

启动后先到开始界面：`开始游戏` / `自定义` / `退出`；回车或空格直接开始。
操作：`WASD` / 方向键移动，`Esc` 暂停，`G` 上帝模式，`M` 静音，`R` 重新开始。
游戏结束界面可点击「退出游戏」回到开始界面，或点击「重新开始」（快捷键 `R` 仍然可用）。
背景默认是「明亮草原」，可在 开始界面 → 自定义 → 背景 切换为「明亮庭院」。
BGM：进入对局时开始播放；暂停（`Esc`）或对局结束时暂停；重新开始时接着播放；退出到开始界面或重启游戏后从头重新播放。

## 目录

```text
game.py                  入口与全部游戏逻辑（模型 / 渲染 / 应用壳 / 自测）
assets.py                贴图加载与缓存（Pillow；缺失时几何回退）
audio.py                 BGM + 音效（pygame → waveOut → winsound → 静默）
audio_profile.py         自定义音频：内置 / 玩家库 / active 清单
audio_import.py          玩家音频导入（解码 / 规范化 / 去重 / 写库）
background_profile.py    背景偏好：默认明亮草原 / 明亮庭院，写到 custom/background.json
assets/sprites/          玩家 左/右 × 多帧、障碍物 3 档 × 3 材质 × 2–4 帧、UI、地面背景（草原/庭院）
assets/audio/            sfx 与 bgm（内置只读）；bgm/bgm.json 记录曲名/作者；说明见 assets/audio/README.md
custom/                  玩家可写：audio/library/、audio/active/、profile.json、background.json
dafengchui.spec          PyInstaller 配置（onedir；瘦身：排除 av / numpy / yaml）
build_release.bat        双击打包 exe（Windows；调本机 Python 3.11 + PyInstaller）
backup/                  打包前移出的未用资源与配置备份，清单见 backup/assets_unused/MANIFEST.json
tools/art/bake_sprites.py   离线烘焙 PNG（B 方案绘制逻辑）
tools/art/bake_start_cover.py 开始界面封面 / 自定义界面底图：裁切 1200×840 + 暗角
tools/art/preview_cover_layout.py 离线画出版式评审图（封面 + 文字 + 按钮），不用起窗
tools/art/obstacle_art.py   障碍物材质绘制（树叶/纸片/羽毛、松果/碎石/纸团、树枝/木板/石块）
tools/art/obstacle_styles_preview.py  生成障碍物新旧对照评审图
tools/art/chibi.py          Q 版占位角色绘制（方向 × 待机/走路）
tools/art/import_player.py  把一张正面角色图转成游戏贴图（抠图/裁切/脚底对齐）
tools/art/rebuild_player.py 从原始视频/立绘重导高分辨率玩家帧（去绿幕/防绿边 + 自动选循环段）
tools/art/build_idle_video.py 从待机视频抽帧生成 idle
tools/art/bake_idle_sway.py 从一张待机图生成 12 帧轻微摇摆/呼吸（备用）
tools/art/extract_frames.py 从行走/待机视频抽帧（PyAV，无需 ffmpeg）
tools/art/preview_chibi.py  快速预览角色方向帧
tools/audio/bake_audio.py   离线合成 WAV（纯标准库）
tools/audio/make_hurt_voice.py  把短语音处理成受伤哼唧（裁静音 / 升调 / 气声）
tools/audio/audition_hurt.py    试听并安装受伤音效候选
tools/audio/transfer_intonation.py  把多段语音的语调统一到同一模板（YIN + TD-PSOLA，保音色）
tools/audio/plot_contours.py       把多段语音的语调画在一张图上对比
tools/audio/audition_intonation.py 试听“语调统一”后的模板组
tools/audio/import_hit_variants.py 把海螺 AI 等导出的短语音导入成受击 WAV 变体（裁静音/归一化）
tools/audio/import_library.py      把玩家音频导入自定义库，可选直接写 profile
tools/audio/install_builtin_bgm.py 把下载的音乐装成内置 BGM（转 44.1k 单声道 + 写 bgm.json）
tools/preview_live.py       开发用：起窗截图检查实际效果
tools/preview_start.py      开发用：截图开始界面、自定义音效页与背景音乐页
art_preview/            风格评审图，不参与游戏
```

## 重新生成资源

```powershell
python tools/art/bake_sprites.py    # 需要 Pillow
python tools/audio/bake_audio.py    # 只需标准库
```

两个脚本都是确定性的：重复运行得到相同字节的 PNG/WAV。`bake_sprites.py` 检测到 `player_e_walk2.png`（真实多帧玩家美术）时会跳过玩家帧**和受击帧**，不会覆盖 `import_player.py --hurt` 导入的自定义受击图；确实要重置成占位图时加 `--force-player`。受伤音效 `hit_1/2/3.wav` 由 `tools/audio/import_hit_variants.py` 从 `hit_voice/` 导入，`bake_audio.py` 不会覆盖它们；`hit.wav` 是旧版备选。说明见 `assets/audio/README.md`。

## 多段受伤音的语调统一

手上有几段满意的短语音、但语调不一致导致没法一起用时，用 `transfer_intonation.py`
把它们统一到同一套音高轮廓（只改语调，音色、咬字、气口不动）：

```powershell
python tools/audio/transfer_intonation.py -r 模板.wav -t 音频1.wav 音频2.wav `
    --outdir art_preview/intonation/template_x --strength 1.0 --trim --bare
python tools/audio/audition_intonation.py --set x --ab
```

原理、参数、以及 Praat / Melodyne 等手动替代方案见 [`docs/语调统一指南.md`](docs/语调统一指南.md)。
本次四段“哎呀/呃啊”的对比与试听在 `art_preview/intonation/`。

## 自定义音效

音频分三层找：内置 `assets/audio/` → 玩家导入 `custom/audio/library/` →
最终运行 `custom/audio/active/`。`custom/profile.json` 是选择清单，
`active/` 是运行时生成的真实文件；没有 `custom/` 时自动退回内置，游戏照常跑。
可写根默认是项目/exe 同级的 `custom/`，不可写时用 `%APPDATA%\Dafengchui\custom\`；
`DAFENG_RESOURCE_DIR` 可覆盖（测试用）。

![自定义音效页](art_preview/custom_screen.png)

开始界面进“自定义”后：左侧是板块栏（音效、背景可用，人物占位），右侧是当前板块内容。
音效页按槽位选择内置或“我的”条目；受击可多选（最多 8 条，随机播放），
背景音乐可多选（最多 12 首，随机播放），其余单选；
选中项有 `☑` 勾选框 + 整行高亮。每行可试听，库条目可删除（删文件 + index）；
底部可导入、刷新、恢复默认，点“应用”写入 `profile.json` 并重建 active。
waveOut 后端已改成 8 条固定流池，选满 8 条受击也只用固定几条流。

导入按槽位隔离：在“受击”页导入的只出现在受击页，其他页面看不到；风向/结束/拾取/BGM 同理。
内置受击有 8 条（唉 / 呃啊1 / 哎呀1 / 呃啊2 / 啊 / 呃啊3 / 哎呀2 / 哎呀3），默认启用前 3 条。

```powershell
python game.py --check-assets   # 打印内置音频、内置 BGM、自定义根、active 槽位

# 导入玩家音效（游戏内也能导：开始界面 → 自定义 → 音效 → 导入）
# 支持 WAV / MP3 / OGG / FLAC；M4A 需要 PyAV（打包版没有）
python tools/audio/import_library.py 我的音效.mp3 --slot hit --enable add
python tools/audio/import_library.py 我的音乐.mp3 --slot bgm --enable add
python tools/audio/import_library.py --list
```

导入只做解码、44.1k 单声道、裁静音、归一化、去重；不改音高。`--enable add`
把新条目追加进 `profile.json`，`only` 表示只保留这一条；之后启动游戏即生效。

### 背景音乐：内置 + 玩家导入随机播放

内置曲目放在 `assets/audio/bgm/*.wav`，启动时自动扫描；`bgm/bgm.json` 只记录
曲名、作者、来源等显示信息（没有也能用文件名）。下载好音乐后，用安装脚本
转成游戏统一格式并写入元数据：

```powershell
python -m pip install av     # 只给开发端脚本（install_builtin_bgm 等）用；游戏本体不依赖
python tools/audio/install_builtin_bgm.py 风起时.mp3 --title "风起时" --artist "某某" `
    --source "素材站" --license "CC0"
python tools/audio/install_builtin_bgm.py --list
```

- 安装脚本只做：解码 → 44.1 kHz / 单声道 / 16-bit → 峰值 0.35 → 写入
  `assets/audio/bgm/` + `bgm.json`；不裁前后、不加淡入淡出，保留原曲听感。
- 游戏内：自定义 → 音效 → 背景音乐，勾选想参与的曲目（内置和“我的”在同一个列表），
  点“应用”。游戏中按“一轮内不重复、轮与轮之间不连播同一首”的顺序随机播放；
  只勾一首时无缝循环。

![自定义背景音乐页](art_preview/custom_bgm_page.png)
- 旧版 `profile.json`（BGM 单选）会在读取时自动迁移为“全部内置 + 已选导入”，
  并打开自动收录：以后新增内置曲目会自动进池。只要玩家手动取消过任意一首内置
  曲目，自动收录就关闭，之后新增的曲子需要手动勾选（“恢复默认”可重新打开）。
- 峰值 0.35 是相对源文件；实际播放还会乘 `AUDIO_BGM_VOLUME = 0.35`，
  比受击音效低约 11 dB，适合当背景。觉得太轻可以调大这个常量。
- 删除内置曲目：删 `assets/audio/bgm/` 里的 WAV 和 `bgm.json` 对应条目，
  游戏启动时自动从 active 里清掉；已保存的 profile 校验时会丢弃失效条目。

## 自定义背景

默认使用 `assets/sprites/bg/ground_meadow.png`（明亮草原）。开始界面 →
自定义 → 背景 可切换：

- **明亮草原**（默认）：白天草地，草叶 + 野花 + 云影。
- **明亮庭院**：苔藓地 + 蜿蜒石板小径。

![自定义背景页](art_preview/custom_background_page.png)

点选背景卡片即会写入 `custom/background.json`（可写根规则与音效相同），
并立即更新游戏 Canvas，不需要再按“应用”；下次启动自动恢复。贴图缺失或自定义目录不可写时会
回退到旧版纯色 + 软云 + 暗角，不会影响启动。使用地面背景时会自动隐藏旧版
软云和暗角，避免叠在亮背景上。

当前是 **P5（已完成）**：三层目录、profile/active、导入管线、开始界面、
自定义音效页 / 自定义背景页、waveOut 固定流池、onedir exe 打包，
以及多首内置 BGM + 玩家导入 BGM 的随机播放池均已接入；
详见 [`docs/自定义音效方案.md`](docs/自定义音效方案.md)。

## 开始界面封面

开始界面与自定义界面的背景是两张烘焙好的 PNG：

- `assets/sprites/ui/start_cover.png`：开始界面整幅背景，2400×1680（2× 分辨率），底部带渐隐，
  并整体放大 1.06 把角色往右推，给中间的按钮让位；
- `assets/sprites/ui/custom_backdrop.png`：自定义界面底图（压暗 + 轻模糊，设置面板内嵌在中间）。

素材来自即梦 AI。重新烘焙：

```powershell
python tools/art/bake_start_cover.py
```

脚本输入按优先级取：

1. `art_preview/cover_source/cover_base.png`：已擦除水印、且压暗与底部渐隐都已烘好的封面底
   （2400×1680）。**平时走这条**，脚本只做 1.06 放大 + 裁切，不会再动亮度，也不重烘
   `custom_backdrop.png`；
2. `art_preview/cover_source/cover_123.jpg`：即梦导出的原图，右下角带“即梦AI”水印。只有缺
   上面那张时才回退到它，此时脚本会重做压暗与暗角——**水印也会一起烘进去**，所以脚本不做
   擦除，正式出图前请先导出无水印版本存成 `cover_base.png`。

两张源图都不打包进 exe。

开始界面由 `CrayonStartView`（蜡笔版）渲染；封面缺图、或 `assets/sprites/ui/crayon/`
里没有 `start_title` / `start_btn_*` 时，自动退回旧的 `StartView`（深色底 + 雅黑深藏青按钮），
不影响启动。

## 打包 exe

本机已验证 **onedir** 版本（Python 3.11.7 + pygame 2.6.1 + PyInstaller 6.22.3）：

```text
dist\dafengchui\dafengchui.exe      # 本地运行
dist\dafengchui\_internal\          # 依赖与资源（约 105 MB）
dist\dafengchui_share.zip           # 分享包（约 76 MB，不含 custom/）
```

构建（Windows，双击即可，产物在 `dist/`）：

```bat
build_release.bat
```

等价于手动执行：

```powershell
python -m pip install pygame pyinstaller
python -m PyInstaller --noconfirm --clean dafengchui.spec
```

**不要在命令行上再传 `game.py` 和一堆参数**：只要目录里存在 `dafengchui.spec`，
PyInstaller 会优先用它，命令行参数被静默忽略（踩过这个坑）。

`dafengchui.spec` 是瘦身配置，和默认打包的区别：

- 用 `collect_submodules("pygame", filter=...)` + `collect_data_files("pygame", excludes=[...])`
  取代 `collect_all("pygame")`，挡掉 `pygame/docs`、`pygame/tests`、`pygame/examples`
  （约 12 MB 纯文档/示例）。注意 `collect_data_files` 的 excludes 是**相对包根**的 glob：
  写 `docs/**` 才生效，写 `pygame/docs/**` 一个都排除不掉；`collect_submodules` 的
  filter 才用完整点分名（`pygame.tests.*`）。
- `excludes=['av', 'numpy', 'yaml']`，合计省 57 MB（185 MB → 128 MB）：
  - `numpy` 41 MB。本作是 tkinter Canvas 游戏，pygame 只用来放音频，源码里没有任何
    `numpy` / `surfarray` / `sndarray` 调用；pygame 对这几个模块的 import 包在
    `try/except (ImportError, OSError)` 里，缺失只是降级成 MissingModule，mixer 不受影响。
    构建警告 `excluded module named numpy - imported by pygame.mixer` 是良性的，
    `--check-assets` 会真的初始化 mixer 来验证。
  - `yaml` 由 `numpy.__config__` 带进来，跟着 numpy 一起走。
  - `av` 是 FFmpeg 绑定：`av` 包只有 3.5 MB，但 `av.libs` 有 83 MB，打进去体积翻倍，
    而它只影响 M4A。
- `--add-data "assets;assets"` 由 spec 的 `datas` 承担；新增内置 BGM 后必须重新打包
  才会进 exe。临时测试也可以直接把符合格式的 `44.1k / 单声道 / 16-bit` WAV 放进
  `dist/dafengchui/_internal/assets/audio/bgm/`，下次启动会自动扫描。
- DSH 沙箱里跑不了 PyInstaller（创建匿名管道被系统拒绝：`PermissionError: [WinError 5]`），
  `--onefile` 还会报 `Failed to create parent directory structure`，所以统一用
  onedir + 本机终端构建。
- 清理与备份记录见 [`docs/清理记录.md`](docs/清理记录.md)。

### 打包版的音频格式支持

| 格式 | 源码运行 | 打包版 | 解码器 |
|---|---|---|---|
| WAV | ✅ | ✅ | 标准库 `wave` |
| MP3 | ✅ | ✅ | SDL_mixer 内建 dr_mp3（`audio_import._decode_mixer`） |
| OGG | ✅ | ✅ | SDL_mixer 内建 stb_vorbis |
| FLAC | ✅ | ✅ | SDL_mixer 内建 dr_flac |
| M4A | ✅（开发机装了 PyAV） | ❌ | 只有 PyAV 能解，代价 +83 MB |

打包版不需要 PyAV；M4A 会明确提示玩家转成 WAV / MP3 / OGG / FLAC。测试时注意
「源码能导 M4A、打包版不能」这个差异，别在源码里验完就当打包版也行。

### 分发注意

exe 没有数字签名，玩家首次运行时 Windows SmartScreen 会拦：

> Windows 已保护你的电脑 / 发布者：发布者未知

这不是打包错误，是未签名程序的默认待遇。应对方式：

- 让玩家点「更多信息」→「仍要运行」；
- 或解压前右键 zip → 属性 → 勾选「解除锁定」，避免网络标记传给内部文件；
- 要彻底消除只能买代码签名证书（OV / EV），v1 不做。

分享包只放 `_internal/` + `dafengchui.exe`。**不要带 `custom/`、`log/`、`debug.log`**，
这些是游戏自己生成的玩家数据与日志。

## 不用画画：用一张图替换角色

游戏只要求一张**正面全身**角色图（左右方向都是正面像 + 镜像）。生成或找到图后：

```powershell
# 绿幕背景最稳（白色背景可能把白围裙一起抠掉）
python tools/art/import_player.py 角色.png --bg green
# 有单独的受击表情图就一起给
python tools/art/import_player.py 角色.png --hurt 受击.png --bg green
```

脚本会自动抠背景、裁切、缩放到 125×135、让脚底贴底边，生成 `player_e/w_idle/walk0/walk1`
和 `player_hurt`。如果要真实步态，先用视频工具生成行走循环，再抽帧：

```powershell
python tools/art/extract_frames.py walk.mp4 --count 16 --crop-bottom 160
```

把抽出的帧按 `player_e_walk0..15.png` 放好，游戏会自动按数字顺序读取，帧数不写死。详细规格见 `assets/sprites/player/README.md`。

玩家贴图规格：`assets/sprites/player/player_<dir>_<state>*.png`，`dir ∈ {e,w}`，`state ∈ {idle,walk}`；文件名末尾的数字决定帧序号，帧数完全由文件数量决定（当前 27 张 idle + 16 张 walk）。斜向移动按水平分量使用左/右贴图，纯上下移动保持当前贴图不变。另有 `player_hurt.png` 受击表情。设计尺寸 125×135，脚底基线在贴图底边。受击闪白由运行时从正常帧生成，**不需要单独提供闪白图**。

从原始视频/立绘高分辨率重导（含去绿幕、去地面阴影、防绿边、自动选循环段）：

```powershell
python tools/art/rebuild_player.py
```

## 自己调整大小

角色和障碍物的“显示尺寸 / 碰撞体积”是两套常量，必须一起改，否则判定会和贴图对不上。

角色：

| 常量 | 位置 | 当前值 |
| --- | --- | --- |
| `PLAYER_SPRITE_SIZE` | `assets.py` | `(125, 135)` |
| `PLAYER_RADIUS` | `game.py` A.2 | `23.4` |
| `PLAYER_HIT_RX` / `PLAYER_HIT_RY` | `game.py` A.2 | `27.3` / `52.0` |
| `PLAYER_HIT_CENTER_UP` | `game.py` A.2 | `57.2` |

贴图源文件是 250×270，显示尺寸不超过它就仍然清晰。**只想缩小的话，只改上面这五个数即可，不用重导**。

想按原设计（96×104）整体缩放 k 倍，用：

```text
贴图宽高 = round(96*k) × round(104*k)
PLAYER_RADIUS        = 18*k
PLAYER_HIT_RX        = 21*k
PLAYER_HIT_RY        = 40*k
PLAYER_HIT_CENTER_UP = 44*k
```

障碍物：

| 常量 | 位置 | 当前值 |
| --- | --- | --- |
| `OBSTACLE_*_RADIUS` | `game.py` A.4 | `11 / 20 / 31.5` |
| `TIERS` 里的半径 | `tools/art/bake_sprites.py` | 必须与上面一致 |

改完半径后运行 `python tools/art/bake_sprites.py` 重烘障碍物（会保留玩家帧和受击图）。大号当前直径 63px，约等于人物宽度（125px）的一半。障碍物大小和玩家缩放是独立的，按需要单独定。

例：想把玩家从当前 1.3 倍放大回 **1.5 倍**：

```text
PLAYER_SPRITE_SIZE  = (144, 156)
PLAYER_RADIUS       = 27.0
PLAYER_HIT_RX       = 31.5
PLAYER_HIT_RY       = 60.0
PLAYER_HIT_CENTER_UP = 66.0
```

障碍物若也要维持“大号≈半个人物”，1.5 倍时大号直径约 72px，半径约 36。窗口现在是 **1200×840**（原 1000×700 的 1.2 倍），HUD、字体、云层和生成边界都已按同一比例缩放。

角落阴影用 `assets.py` 的 `PLAYER_SHADOW_SIZE`（当前 `(83, 31)`），它只在重烘时生成；改得比较多时把 `bake_sprites.py` 里 `render_player_shadow(83, 31)` 一起改掉即可。

## 设计约束

- `GameModel` / `Player` / `Obstacle` 不引用 tkinter、Canvas、Pillow、pygame，所以 `--selftest` 永远可以在无窗口环境跑。
- 渲染层只读模型、只写 Canvas；贴图替换不改变任何游戏规则。
- 障碍物不再是彩色球：小/中/大三档分别是被风卷起的轻杂物（树叶/纸片/羽毛）、中等杂物（松果/碎石/纸团）、重杂物（树枝/木板/石块），靠**剪影与材质**区分；档位色只保留一层很淡的外发光，碰撞圆与伤害数值完全不变。
- 障碍物空中动作：每颗有独立的**摆动 + 起伏**（A1）、生成速度 ±8% 与慢速**阵风**（A2）、换风转向时朝弯道外侧**外抛/滞后**（A3）；小档另有 4 帧**翻面帧动画**，中/大档则预烘 144/180 个角度帧（2.5°/2°）、运行时**绕贴图中心慢速翻滚**（转速带正弦起伏，不是匀速马达）（B）。摆动与外抛叠加在轨迹上，绘制和碰撞都用同一个偏移，看到哪就撞哪。角度帧里的贴图细节（松果鳞片、树枝端面）必须跟主体按同一角度旋转，不能只转剪影，否则翻滚时花纹会“钉”在画面上（见 `backup/20261031-1745_pattern-fix/`）。
- Tk 的 PhotoImage 不能旋转、只能整数缩放：玩家按左/右 × 3 帧预烘，障碍物按档位预烘尺寸和多帧姿态；平滑缩放/模糊都在离线烘焙阶段完成。
- 角色用 `anchor="s"` 贴到碰撞圆底部，脚底阴影独立一层；角色与障碍按脚底 y 做深度排序（压在同层 HUD 之下）。
- 朝向只有左/右两张：斜向按水平分量取左/右，纯上下移动保留当前贴图，0.15 水平死区过滤漂移抖动。
- 奔跑动画 16 帧 @ 24 fps（帧数由文件数量决定）；静止时单帧 idle + 1px 正弦呼吸。
- 动画状态只看**玩家输入**：没有按键时，即使被风吹着移动，也播放 idle 而不是奔跑。
- 站立朝向 = **最后一次按键方向**；风吹不会改变。待机镜像按“尾巴在运动反方向”摆：右向 idle 尾巴在左，左向 idle 尾巴在右。
- 受击后 0.2 s 硬直：输入无效，沿风吹向被推开约 21.6 px，期间夹在窗口内；播放 `player_hurt` 并叠加闪白。
- 受击贴图分左右：按撞击瞬间障碍物在玩家的哪一侧选图（在右 → 朝右，在左 → 朝左；正上/正下沿用最后朝向）。左向是右向原图在运行时的水平镜像，不需要第二张 PNG。风从左往右吹时障碍物从左边撞来，于是显示朝左的受击图。
- 受击判定是**覆盖角色可见身体的椭圆**（半轴 27.3×52，中心在脚底上方 57.2px），头部/裙摆都有判定，不再只判脚底一个圆；椭圆随 125×135 贴图等比缩放。
- 左/右两张贴图**都是正面像**（左向由正面水平镜像烘焙），不会出现“背面像”。
- 资源路径基于 `__file__`，并兼容 PyInstaller 的 `sys._MEIPASS`；打包时需 `--add-data "assets;assets"`，详见 `大风吹_V1_制作参考说明.md` 附录 F。
