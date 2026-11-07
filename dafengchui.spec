# -*- mode: python ; coding: utf-8 -*-
# 精简版 spec
# ---------------------------------------------------------------
# 相比 collect_all('pygame') 的三处改动：
#   1. 不打包 pygame/docs（8.3MB）、pygame/tests（2.6MB）、
#      pygame/examples（1.1MB）—— 这些是 pygame 附带的文档/测试/示例，
#      游戏运行时完全用不到。
#   2. 不复制 pygame 的 .py 源码（include_py_files=False，运行时走 PYZ 里的 .pyc）。
#   3. excludes 里加上 numpy / yaml：它们是 collect_all 顺着
#      pygame.surfarray、pygame.sndarray 带进来的隐性依赖，本项目源码
#      （game.py / assets.py / audio.py / crayon_text.py 等）从不 import numpy。
#      pygame/__init__.py 对 surfarray/sndarray 的 import 包在
#      try/except ImportError 里，缺失时降级为 MissingModule，不影响启动。
# ---------------------------------------------------------------
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

# pygame 里跟游戏无关的三个子包
_PYGAME_SKIP = ("pygame.docs", "pygame.tests", "pygame.examples")

datas = [("assets", "assets")]
binaries = []
hiddenimports = ["PIL._tkinter_finder"]

hiddenimports += collect_submodules(
    "pygame", filter=lambda name: not name.startswith(_PYGAME_SKIP)
)
datas += collect_data_files(
    "pygame",
    excludes=[
        "docs/**",
        "tests/**",
        "examples/**",
    ],
)
binaries += collect_dynamic_libs("pygame")


a = Analysis(
    ['game.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['av', 'numpy', 'yaml'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='dafengchui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='dafengchui',
)
