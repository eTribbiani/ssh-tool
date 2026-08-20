# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置 — 生成 Windows 11 单目录 GUI 可执行程序
#
# 构建 (需在 Windows + 已装 PyQt5/paramiko/pywinpty 的环境执行):
#   python -m PyInstaller --clean --noconfirm sshtool_windows.spec
# 产物:
#   dist/sshtool/sshtool.exe  (+ toolchain/ + Qt/DLL 依赖)
#
# 注意: 需要先将 MSYS2 工具链 (rsync/ssh/sshpass/bash 及依赖 DLL)
#       放入项目根目录的 toolchain/ 目录, 才会被打包进产物。

import os
import winpty as _winpty

# winpty (pywinpty 包) 的运行时文件: winpty.dll / conpty.dll /
# winpty-agent.exe / OpenConsole.exe / _winpty*.pyd。
# PyInstaller 自带的 hook 只收集 dll + pyd, 缺 .exe 与 conpty.dll,
# 这里显式把包目录下的所有二进制都打入 winpty/ 子目录。
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
        # 用不到的 Qt 子模块, 显著减小体积
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
    [],
    exclude_binaries=True,
    name='sshtool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # Windows 下 UPX 可能损坏 Qt/MSYS DLL, 关闭
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # GUI 程序, 无控制台窗口
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
    upx=False,
    upx_exclude=[],
    name='sshtool',
)
