#!/usr/bin/env python3
"""构建 Windows 自包含工具链。

从已安装的 MSYS2 复制完整 userland 到 toolchain/, 保留 MSYS2 目录结构
(msys-2.0.dll 据此定位 /bin、/etc), 使文件传输 (rsync/ssh/sshpass) 与
本地终端 (bash + coreutils) 在 Windows 上达到与 Linux 相同的可用度。

复制内容:
  - usr/bin/*           所有二进制 + DLL (bash/coreutils/ssh/rsync/sshpass 等)
  - usr/share/terminfo  ncurses 终端应用 (less/vim/top) 所需
  - etc/*               ssh_config / profile / passwd 等
  - tmp/                bash 需要 /tmp

用法:
    python build_toolchain.py
"""

import os
import shutil
import stat
import subprocess

MSYS_ROOT = r"C:\msys64"
PROJECT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(PROJECT, "toolchain")


def force_rmtree(path: str):
    """鲁棒删除目录 (处理 MSYS2 符号链接衍生文件的只读/特殊属性)。"""
    if not os.path.isdir(path):
        return

    def on_error(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass

    shutil.rmtree(path, onexc=on_error)
    if os.path.isdir(path):
        # 兜底: 交给 cmd 删除 (处理 junction / 只读目录)
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", path], check=False)


def main():
    force_rmtree(OUT)
    os.makedirs(OUT, exist_ok=True)

    for src_sub, dst_sub, label in [
        (os.path.join(MSYS_ROOT, "usr", "bin"),
         os.path.join(OUT, "usr", "bin"), "usr/bin"),
        (os.path.join(MSYS_ROOT, "etc"),
         os.path.join(OUT, "etc"), "etc"),
        (os.path.join(MSYS_ROOT, "usr", "share", "terminfo"),
         os.path.join(OUT, "usr", "share", "terminfo"), "usr/share/terminfo"),
    ]:
        shutil.copytree(src_sub, dst_sub, symlinks=False)
        print(f"[ok] {label}")

    # rsync 的 -e 命令经 /bin/sh 执行; 确保 sh.exe 存在 (bash 副本)
    sh_path = os.path.join(OUT, "usr", "bin", "sh.exe")
    if not os.path.exists(sh_path):
        shutil.copy2(os.path.join(OUT, "usr", "bin", "bash.exe"), sh_path)
        print("[ok] sh.exe (bash 副本)")

    # bash 需要 /tmp
    os.makedirs(os.path.join(OUT, "tmp"), exist_ok=True)

    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _, fs in os.walk(OUT) for f in fs)
    nfiles = sum(len(fs) for _, _, fs in os.walk(OUT))
    print(f"[done] toolchain: {nfiles} 个文件, {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
