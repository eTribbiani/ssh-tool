"""平台工具链定位与路径转换。

Linux 下外部命令直接使用裸命令名 (rsync/ssh/sshpass/bash)。
Windows 下这些命令来自随应用捆绑的 MSYS2 工具链目录 `toolchain/`，
本地路径还需转换为 MSYS 风格 (/c/foo/bar) 供 rsync 等使用。
"""

import os
import sys

IS_WINDOWS = sys.platform == "win32"


def _toolchain_dir() -> str:
    """返回捆绑工具链目录。

    打包后: <PyInstaller 解包目录>/toolchain
    源码开发: <项目根目录>/toolchain
    """
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "toolchain")


def tool_path(name: str) -> str:
    """返回外部工具的调用命令。

    Linux: 裸命令名 (依赖 PATH)。
    Windows: toolchain/usr/bin/<name>.exe 的绝对路径 (供 subprocess/pywinpty 启动)。
    """
    if not IS_WINDOWS:
        return name
    return os.path.join(_toolchain_dir(), "usr", "bin", name + ".exe")


def tool_msys_path(name: str) -> str:
    """返回工具的 MSYS 风格路径 (/d/.../usr/bin/<name>.exe)。

    用于把命令作为参数传给另一个 MSYS2 工具 (如 sshpass 要 exec 的 ssh、
    rsync -e 要执行的 sshpass/ssh): MSYS2 的 execvp 能识别 POSIX 路径。
    """
    return to_msys_path(tool_path(name))


def to_msys_path(path: str) -> str:
    """把 Windows 本地路径转为 MSYS 风格；Linux 原样返回。

    C:\\foo\\bar 或 C:/foo/bar → /c/foo/bar
    仅处理本地盘符路径；远程路径 (user@host:path) 不含盘符，不受影响。
    """
    if not IS_WINDOWS:
        return path
    drive, sep, rest = path.partition(":")
    if sep and len(drive) == 1 and drive.isalpha():
        return "/" + drive.lower() + rest.replace("\\", "/")
    return path


def sh_quote(s: str) -> str:
    """单引号包裹，用于嵌入 sh 命令字符串（转义内嵌单引号）。"""
    return "'" + s.replace("'", "'\\''") + "'"
