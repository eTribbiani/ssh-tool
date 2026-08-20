# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置 — 生成 Windows 11 单文件 (onefile) GUI 可执行程序
#
# 构建 (需在 Windows + 已装 PyQt5/paramiko/pywinpty/pyte 的环境执行):
#   python -m PyInstaller --clean --noconfirm sshtool_windows_onefile.spec
# 产物:
#   dist/sshtool.exe
#
# 注意: 单文件版把 toolchain (约 140MB) 一并压缩进 exe, 每次启动会解压到
#       %TEMP%\_MEIxxxx 临时目录, 首次启动较慢; 大体积 exe 易被杀软误报。

import os
import winpty as _winpty

# winpty (pywinpty 包) 运行时文件: winpty.dll / conpty.dll /
# winpty-agent.exe / OpenConsole.exe / _winpty*.pyd。
_winpty_dir = os.path.dirname(_winpty.__file__)
winpty_binaries = [
    (os.path.join(_winpty_dir, f), 'winpty')
    for f in os.listdir(_winpty_dir)
    if f.endswith(('.dll', '.exe', '.pyd'))
]

a = Analysis(
    ['sshtool_gui.py'],
    pathex=[],
    binaries=winpty_binaries,
    datas=[
        # 捆绑 MSYS2 工具链, 供 ssh_utils/transfer_engine 在 Windows 调用
        ('toolchain', 'toolchain'),
    ],
    hiddenimports=[
        'winpty',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt5.QtWebEngineWidgets',
        'PyQt5.QtWebEngine',
        'PyQt5.QtWebEngineCore',
        'PyQt5.QtQml',
        'PyQt5.QtQuick',
        'PyQt5.QtQuickWidgets',
        'PyQt5.QtMultimedia',
        'PyQt5.QtMultimediaWidgets',
        'PyQt5.QtNetwork',
        'PyQt5.QtSql',
        'PyQt5.QtTest',
        'PyQt5.QtXml',
        'PyQt5.QtBluetooth',
        'PyQt5.QtDesigner',
        'PyQt5.QtHelp',
        'PyQt5.QtLocation',
        'PyQt5.QtNfc',
        'PyQt5.QtOpenGL',
        'PyQt5.QtPositioning',
        'PyQt5.QtSensors',
        'PyQt5.QtSerialPort',
        'PyQt5.QtWebChannel',
        'PyQt5.QtWebSockets',
        'PyQt5.QtXmlPatterns',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='sshtool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
