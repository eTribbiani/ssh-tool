"""SSH 工具函数 — 统一隧道/远程命令构造"""

import subprocess
import time


def ssh_base_args(cfg: dict, timeout: int = 5) -> list:
    """构造通过隧道连接 RK3588 的 ssh 基础参数"""
    return [
        "sshpass", "-p", cfg["rk_pass"],
        "ssh", "-p", str(cfg["tunnel_port"]),
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
        "sshpass", "-p", cfg["gw_pass"],
        "ssh", "-p", str(cfg["gw_port"]),
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
