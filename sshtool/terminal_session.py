"""TerminalSession — 终端会话管理 (本地 PTY 和远程 SSH)"""

import os
import signal
import struct
import subprocess
import termios
import time
import tty

import paramiko
from PyQt5.QtCore import QThread, pyqtSignal

# ─── TerminalSession 基类 ─────────────────────────────────────

class TerminalSession(QThread):
    """终端会话基类: 在后台线程运行, 读写 PTY/Socket 并桥接到 GUI 线程。

    Signals:
        data_received(bytes): 终端输出原始字节流
        session_closed(int):   会话结束, 携带退出码
    """

    data_received = pyqtSignal(bytes)
    session_closed = pyqtSignal(int)

    def __init__(self, columns: int = 80, rows: int = 24, parent=None):
        super().__init__(parent)
        self._columns = columns
        self._rows = rows
        self._running = False
        self._exit_code = -1

    def run(self):
        """子类必须实现: 读取终端输出并 emit data_received"""
        raise NotImplementedError

    def write(self, data: bytes):
        """子类必须实现: 向终端写入输入数据"""
        raise NotImplementedError

    def resize(self, columns: int, rows: int):
        """子类应实现: 调整终端尺寸"""
        self._columns = columns
        self._rows = rows

    def stop(self):
        """停止会话"""
        self._running = False

    def pid(self) -> int | None:
        """返回子进程 PID (如果有的话)"""
        return None


# ─── LocalTerminalSession ─────────────────────────────────────

class LocalTerminalSession(TerminalSession):
    """本地终端会话: 通过 PTY 运行 /bin/bash"""

    def __init__(self, columns: int = 80, rows: int = 24, parent=None):
        super().__init__(columns, rows, parent)
        self._master_fd: int | None = None
        self._slave_fd: int | None = None
        self._process: subprocess.Popen | None = None

    def run(self):
        """创建 PTY, 启动 bash, 读取输出"""
        try:
            self._master_fd, self._slave_fd = os.openpty()
            self._set_pty_size()

            # 设置 master 端为非阻塞
            import fcntl
            flags = fcntl.fcntl(self._master_fd, fcntl.F_GETFL)
            fcntl.fcntl(self._master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            # 启动 bash
            self._process = subprocess.Popen(
                ["/bin/bash", "--login"],
                stdin=self._slave_fd,
                stdout=self._slave_fd,
                stderr=self._slave_fd,
                preexec_fn=os.setsid,
                close_fds=True,
                env=self._make_env(),
            )

            # 关闭 slave 端 (子进程已继承)
            os.close(self._slave_fd)
            self._slave_fd = None

            self._running = True

            # 主循环: 读取 PTY 输出
            while self._running:
                try:
                    data = os.read(self._master_fd, 4096)
                    if data:
                        self.data_received.emit(data)
                    else:
                        break  # EOF
                except BlockingIOError:
                    time.sleep(0.005)  # 5ms 轮询
                except OSError:
                    break

        except Exception as e:
            self.data_received.emit(
                f"\r\n[终端错误: {e}]\r\n".encode()
            )
        finally:
            self._running = False
            # 获取退出码
            if self._process:
                try:
                    self._process.wait(timeout=2)
                    self._exit_code = self._process.returncode
                except Exception:
                    self._exit_code = -1
            # 清理
            if self._master_fd is not None:
                try:
                    os.close(self._master_fd)
                except OSError:
                    pass
                self._master_fd = None
            self.session_closed.emit(self._exit_code)

    def write(self, data: bytes):
        """向 PTY master 写入输入数据"""
        if self._master_fd is not None and self._running:
            try:
                os.write(self._master_fd, data)
            except OSError:
                pass

    def resize(self, columns: int, rows: int):
        """调整 PTY 窗口大小并发送 SIGWINCH"""
        super().resize(columns, rows)
        self._set_pty_size()
        # 发送 SIGWINCH 通知子进程
        if self._process and self._process.poll() is None:
            try:
                os.kill(self._process.pid, signal.SIGWINCH)
            except OSError:
                pass

    def _set_pty_size(self):
        """设置 PTY 窗口大小 (termios TIOCSWINSZ)"""
        if self._master_fd is not None:
            try:
                winsize = struct.pack("HHHH", self._rows, self._columns, 0, 0)
                fcntl = __import__("fcntl")
                fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
            except Exception:
                pass

    @staticmethod
    def _make_env() -> dict:
        """构造子进程环境变量 (继承当前环境 + TERM=xterm-256color)"""
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        return env

    def pid(self) -> int | None:
        if self._process:
            return self._process.pid
        return None

    def stop(self):
        """停止本地会话: 终止 bash 进程"""
        self._running = False
        if self._process and self._process.poll() is None:
            try:
                # 发送 SIGHUP 给进程组
                os.killpg(os.getpgid(self._process.pid), signal.SIGHUP)
                time.sleep(0.1)
                self._process.terminate()
                self._process.wait(timeout=2)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass


# ─── SSHTerminalSession (通用 SSH 终端) ──────────────────────

class SSHTerminalSession(TerminalSession):
    """通用 SSH 终端会话: 通过 paramiko 直连或隧道连接任意 SSH 主机。

    参数:
        hostname: 目标主机 IP/域名
        port:     SSH 端口
        username: 登录用户名
        password: 登录密码
        label:    连接提示标签 (如 "rk3588" / "a733")
    """

    def __init__(self, hostname: str, port: int, username: str,
                 password: str, label: str = "",
                 columns: int = 80, rows: int = 24, parent=None):
        super().__init__(columns, rows, parent)
        self._hostname = hostname
        self._port = port
        self._username = username
        self._password = password
        self._label = label or f"{username}@{hostname}"
        self._ssh: paramiko.SSHClient | None = None
        self._channel: paramiko.Channel | None = None

    @property
    def label(self) -> str:
        return self._label

    def run(self):
        """建立 SSH 通道并启动交互式 shell"""
        try:
            self._ssh = paramiko.SSHClient()
            self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            self.data_received.emit(
                f"\r\n⏳ 正在连接 {self._label} "
                f"({self._username}@{self._hostname}:{self._port})...\r\n".encode()
            )

            self._ssh.connect(
                hostname=self._hostname,
                port=self._port,
                username=self._username,
                password=self._password,
                look_for_keys=False,
                allow_agent=False,
                timeout=10,
            )

            # 请求交互式 shell
            self._channel = self._ssh.invoke_shell(
                term="xterm-256color",
                width=self._columns,
                height=self._rows,
            )
            self._channel.settimeout(0.1)

            self._running = True
            self.data_received.emit(
                "\r\n\x1b[32m[OK] 已连接\x1b[0m\r\n\r\n".encode("utf-8"))

            # 主循环: 读取 SSH channel 输出
            while self._running:
                try:
                    if self._channel.recv_ready():
                        data = self._channel.recv(4096)
                        if data:
                            self.data_received.emit(data)
                        else:
                            break  # channel closed
                    else:
                        time.sleep(0.01)  # 10ms 轮询
                except Exception:
                    time.sleep(0.01)

        except paramiko.AuthenticationException as e:
            self.data_received.emit(
                f"\r\n\x1b[31m[X] SSH 认证失败: {e}\x1b[0m\r\n".encode()
            )
        except paramiko.SSHException as e:
            self.data_received.emit(
                f"\r\n\x1b[31m[X] SSH 连接失败: {e}\x1b[0m\r\n".encode()
            )
        except OSError as e:
            self.data_received.emit(
                f"\r\n\x1b[31m[X] 网络错误: {e}\x1b[0m\r\n".encode()
            )
        except Exception as e:
            self.data_received.emit(
                f"\r\n\x1b[31m[X] SSH 错误: {e}\x1b[0m\r\n".encode()
            )
        finally:
            self._running = False
            self._exit_code = (self._channel.recv_exit_status()
                               if self._channel and self._channel.exit_status_ready()
                               else -1)
            if self._channel:
                try:
                    self._channel.close()
                except Exception:
                    pass
                self._channel = None
            if self._ssh:
                try:
                    self._ssh.close()
                except Exception:
                    pass
                self._ssh = None
            self.session_closed.emit(self._exit_code)

    def write(self, data: bytes):
        """向 SSH channel 写入输入数据"""
        if self._channel and not self._channel.closed:
            try:
                self._channel.send(data)
            except Exception:
                pass

    def resize(self, columns: int, rows: int):
        """调整终端窗口大小"""
        super().resize(columns, rows)
        if self._channel and not self._channel.closed:
            try:
                self._channel.resize_pty(width=columns, height=rows)
            except Exception:
                pass

    def stop(self):
        """停止会话: 关闭 SSH channel 和连接"""
        self._running = False
        if self._channel:
            try:
                self._channel.close()
            except Exception:
                pass
            self._channel = None
        if self._ssh:
            try:
                self._ssh.close()
            except Exception:
                pass
            self._ssh = None


# ─── 便捷工厂函数 ─────────────────────────────────────────────

def make_remote_session(config: dict, columns=80, rows=24, parent=None):
    """创建 RK3588 远程终端 (通过 SSH 隧道)"""
    return SSHTerminalSession(
        hostname="127.0.0.1",
        port=int(config.get("tunnel_port", 22)),
        username=config["rk_user"],
        password=config["rk_pass"],
        label=f"{config.get('rk_user', '?')}@{config.get('rk_host', '?')}",
        columns=columns, rows=rows, parent=parent,
    )


def make_gateway_session(config: dict, columns=80, rows=24, parent=None):
    """创建网关 A733 终端 (直连)"""
    return SSHTerminalSession(
        hostname=config["gw_host"],
        port=int(config.get("gw_port", 22)),
        username=config["gw_user"],
        password=config["gw_pass"],
        label=f"{config.get('gw_user', '?')}@{config.get('gw_host', '?')}",
        columns=columns, rows=rows, parent=parent,
    )


# ─── 向后兼容别名 ─────────────────────────────────────────────

RemoteTerminalSession = SSHTerminalSession  # 旧名称兼容
