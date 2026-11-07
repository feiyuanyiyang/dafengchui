# 生成受伤哼唧的原始语音素材（Windows 自带中文女声 Huihui）。
# 用途：tools/audio/source/hurt_wua_raw.wav 的来源记录；之后交给
# tools/audio/make_hurt_voice.py 做升调/气声/裁静音处理，最终生成
# assets/audio/sfx/hit.wav。
#
# 运行：
#   powershell -ExecutionPolicy Bypass -File tools/audio/tts_hurt_source.ps1
#
# 说明：这段原始素材只是“中间产物”，游戏不会直接播放它。
# Windows 语音的输出仅建议个人项目使用；若要公开发布，请换成自己录制的
# 哼唧声或明确可商用/CC0 的语音素材，再用 make_hurt_voice.py 处理。

$ErrorActionPreference = "Stop"

$outDir = Join-Path $PSScriptRoot "source"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$path = Join-Path $outDir "hurt_wua_raw.wav"

$voice = New-Object -ComObject SAPI.SpVoice
$voices = @($voice.GetVoices())
# 选择中文女声 Huihui；不同系统索引可能变化，按描述匹配更稳。
$chosen = $null
for ($i = 0; $i -lt $voices.Count; $i++) {
    if ($voices.Item($i).GetDescription() -like "*Huihui*") {
        $chosen = $voices.Item($i)
        break
    }
}
if ($null -eq $chosen) {
    throw "没有找到中文女声 Huihui，可改用其他 voice 或自己录制源音频。"
}
$voice.Voice = $chosen

$stream = New-Object -ComObject SAPI.SpFileStream
$null = $stream.Open($path, 3, $false)   # SSFMCreateForWrite
$voice.AudioOutputStream = $stream
# “呜啊” = U+545C U+554A
$text = [string]([char]0x545C) + [string]([char]0x554A)
$null = $voice.Speak($text, 0)           # 同步播放到文件流
$stream.Close()

Write-Output ("written: " + $path)
