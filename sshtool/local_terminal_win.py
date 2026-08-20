"""Windows 本地终端后端 — 通过 ConPTY (pywinpty) 运行捆绑的 bash.exe。

仅 Windows 使用；Linux 使用 terminal_session.PosixLocalTerminal。
接口与 TerminalSession 基类一致: run / write / resize / stop / pid。
"""

import time

from .platform_tools import tool_path
from .terminal_session import TerminalSession


class WinConPtyTerminal(TerminalSession):
    """本地终端会话 (Windows): ConPTY 中运行 bash --login"""

    def __init__(self, columns: int = 80, rows: int = 24, parent=None):
        super().__init__(columns, rows, parent)
        self._proc = None

    def run(self):
        """创建 ConPTY, 启动 bash, 循环读取输出"""
        try:
            from winpty import PtyProcess

            bash = tool_path("bash")
            self._proc = PtyProcess.spawn([bash, "--login"])
            # ptyprocess 约定: setwinsize(rows, cols)
            self._proc.setwinsize(self._rows, self._columns)

            self._running = True
            while self._running:
                try:
                    data = self._proc.read(4096)
                    if data:
                        if isinstance(data, str):
                            data = data.encode("utf-8", errors="replace")
                        self.data_received.emit(data)
                    elif not self._proc.isalive():
                        break  # EOF
                except Exception:
                    if not self._running:
                        break
                    time.sleep(0.01)

        except Exception as e:
            self.data_received.emit(
                f"\r\n[终端错误: {e}]\r\n".encode("utf-8")
            )
        finally:
            self._running = False
            exit_code = -1
            if self._proc:
                try:
                    exit_code = self._proc.exitstatus
                    if exit_code is None:
                        exit_code = -1
                except Exception:
                    exit_code = -1
            self._exit_code = exit_code
            self.session_closed.emit(exit_code)

    def write(self, data: bytes):
        """向 ConPTY 写入输入 (pywinpty 的 write 期望 str)"""
        if self._proc and self._running:
            try:
                self._proc.write(data.decode("utf-8", errors="replace"))
            except Exception:
                pass

    def resize(self, columns: int, rows: int):
        """调整终端窗口尺寸"""
        super().resize(columns, rows)
        if self._proc:
            try:
                self._proc.setwinsize(rows, columns)
            except Exception:
                pass

    def stop(self):
        """终止 bash 进程"""
        self._running = False
        if self._proc:
            try:
                self._proc.terminate(force=True)
            except Exception:
                pass

    def pid(self) -> int | None:
        if self._proc:
            try:
                return self._proc.pid
            except Exception:
                return None
        return None
