# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置 — 生成单文件 GUI 可执行程序 (Linux x86_64)
#
# 构建:
#   python3 -m PyInstaller --clean --noconfirm sshtool.spec
# 产物:
#   dist/sshtool
#
# 注意: 目标机器仍需系统安装 rsync (传输依赖) 与 bash (终端依赖)。

a = Analysis(
    ['sshtool_gui.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
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
        'PyQt5.QtDBus',
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
        'PyQt5.QtX11Extras',
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
    upx=True,          # 若系统有 upx 则压缩; 无则忽略
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,     # GUI 程序, 无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
