"""SSH 工具函数 — 统一隧道/远程命令构造"""

import shlex
import subprocess
import time

from PyQt5.QtCore import QThread, pyqtSignal

from .platform_tools import tool_msys_path, tool_path


def ssh_base_args(cfg: dict, timeout: int = 5) -> list:
    """构造通过隧道连接 RK3588 的 ssh 基础参数"""
    return [
        tool_path("sshpass"), "-p", cfg["rk_pass"],
        tool_msys_path("ssh"), "-p", str(cfg["tunnel_port"]),
        "-o", "StrictHostKeyChecking=no",
        "-o", f"ConnectTimeout={timeout}",
        f"{cfg['rk_user']}@127.0.0.1",
    ]


def run_ssh(cfg: dict, remote_cmd: str, timeout: int = 15) -> subprocess.CompletedProcess:
    """通过隧道在远程执行命令"""
    return subprocess.run(
        ssh_base_args(cfg) + [remote_cmd],
        capture_output=True, text=True, timeout=timeout,
    )


def check_tunnel(cfg: dict, timeout: int = 15) -> tuple:
    """验证隧道是否存活，返回 (是否正常, 错误信息)"""
    try:
        result = run_ssh(cfg, "echo OK", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "连接超时"
    except Exception as e:
        return False, str(e)
    if "OK" in result.stdout:
        return True, ""
    return False, result.stderr.strip() or "无响应"


def open_tunnel(cfg: dict) -> subprocess.Popen:
    """建立本地 ↔ 网关 ↔ RK3588 的 SSH 隧道"""
    return subprocess.Popen([
        tool_path("sshpass"), "-p", cfg["gw_pass"],
        tool_msys_path("ssh"), "-p", str(cfg["gw_port"]),
        "-o", "StrictHostKeyChecking=no",
        "-N", "-L", f"{cfg['tunnel_port']}:{cfg['rk_host']}:22",
        f"{cfg['gw_user']}@{cfg['gw_host']}",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_tunnel_flow(cfg: dict, settle: float = 1.5) -> tuple:
    """打开隧道并验证连通性，返回 (是否成功, 消息)。隧道进程总是被清理。"""
    proc = open_tunnel(cfg)
    try:
        time.sleep(settle)
        if proc.poll() is not None:
            err = proc.stderr.read().decode(errors="replace")
            return False, f"SSH 隧道建立失败:\n{err}"
        ok, msg = check_tunnel(cfg)
        if not ok:
            return False, f"隧道验证失败:\n{msg}"
        return True, "连接成功"
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            pass


def direction_text(direction: str) -> str:
    return "推送 (本地→远程)" if direction == "push" else "拉取 (远程→本地)"


def parse_stat_output(output: str) -> list:
    """解析 stat -c '%F|%s|%Y|%n' 输出为 [(name, is_dir, size, mtime)]"""
    entries = []
    seen = set()
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue

        ftype, size_str, mtime_str, name = parts
        if name in (".", "..") or name in seen:
            continue
        seen.add(name)

        is_dir = (ftype == "directory")
        try:
            size = int(size_str)
        except (ValueError, TypeError):
            size = 0
        try:
            mtime = int(mtime_str)
        except (ValueError, TypeError):
            mtime = 0

        entries.append((name, is_dir, size, mtime))
    return entries


def remote_listing(config: dict, path: str) -> list:
    """通过 SSH stat 获取目录列表(线程安全, 失败返回 [])。

    输出格式: 文件类型|大小|时间戳|文件名 (每行一条)
    注意: 不使用 bash -c 避免引号嵌套问题
    """
    # 直接在远程 shell 执行: cd 到目标路径后遍历 *
    # 双引号避免与 SSH 命令字符串的单引号冲突
    remote_cmd = (
        "cd {} 2>/dev/null || {{ echo 'PATH_ERROR'; exit 1; }}; "
        "shopt -s nullglob; "
        'for f in *; do stat -c "%F|%s|%Y|%n" -- "$f" 2>/dev/null; done'
    ).format(shlex.quote(path))

    try:
        result = run_ssh(config, remote_cmd, timeout=15)
        if "PATH_ERROR" in result.stdout:
            return []
        if result.returncode != 0 and not result.stdout.strip():
            return []
        return parse_stat_output(result.stdout)
    except Exception:
        return []


class RemoteCommandWorker(QThread):
    """后台执行远程 SSH 命令，不阻塞 GUI"""

    finished = pyqtSignal(object)       # subprocess.CompletedProcess
    error = pyqtSignal(str)             # 错误消息

    def __init__(self, cfg: dict, remote_cmd: str, timeout: int = 15, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self._cmd = remote_cmd
        self._timeout = timeout

    def run(self):
        try:
            result = run_ssh(self._cfg, self._cmd, timeout=self._timeout)
            self.finished.emit(result)
        except subprocess.TimeoutExpired:
            self.error.emit("远程命令执行超时")
        except Exception as e:
            self.error.emit(str(e))


class TunnelConnector(QThread):
    """后台建立并验证 SSH 隧道(含根目录列表预取), 不阻塞 GUI"""

    tunnel_opened = pyqtSignal(object)                  # Popen 已创建
    finished_connect = pyqtSignal(bool, str, object)    # (成功, 消息, 根目录列表)

    def __init__(self, cfg: dict, root_path: str, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self._root_path = root_path
        self._cancelled = False
        self._proc = None

    def cancel(self):
        """取消进行中的连接(线程安全)"""
        self._cancelled = True
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def run(self):
        self._proc = open_tunnel(self._cfg)
        self.tunnel_opened.emit(self._proc)
        time.sleep(2.0)
        if self._cancelled:
            self.finished_connect.emit(False, "连接已取消", None)
            return
        if self._proc.poll() is not None:
            err = self._proc.stderr.read().decode(errors="replace")
            self.finished_connect.emit(False, f"SSH 隧道建立失败:\n{err}", None)
            return
        alive, msg = check_tunnel(self._cfg)
        if not alive:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                pass
            self.finished_connect.emit(False, f"隧道验证失败:\n{msg}", None)
            return
        # 预取根目录列表(纯 ssh 调用, 线程安全, 失败不阻塞连接)
        entries = remote_listing(self._cfg, self._root_path)
        self.finished_connect.emit(True, "", entries)
