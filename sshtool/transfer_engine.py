"""TransferEngine — QThread 后台传输引擎"""

import re
import subprocess

from PyQt5.QtCore import QThread, pyqtSignal

from .rsync_options import RsyncOptions
from .ssh_utils import check_tunnel, direction_text


class TransferEngine(QThread):
    """后台传输引擎 — 只负责验证隧道存活 + rsync，不管理隧道生命周期"""

    log_message = pyqtSignal(str)
    verify_status = pyqtSignal(str)
    transfer_progress = pyqtSignal(str, int)  # (行文本, 百分比)
    transfer_done = pyqtSignal(bool, str)     # (成功, 消息)
    tunnel_lost = pyqtSignal()                # 隧道断开，通知 MainWindow 重连

    def __init__(self, config: dict, rsync_opts: RsyncOptions,
                 sources: list, dest: str, direction: str,
                 tunnel_port: int, dry_run: bool = False):
        super().__init__()
        self.config = config
        self.rsync_opts = rsync_opts
        self.sources = sources
        self.dest = dest
        self.direction = direction
        self.tunnel_port = tunnel_port
        self.dry_run = dry_run
        self._cancelled = False
        self._rsync_proc = None

    def cancel(self):
        self._cancelled = True
        if self._rsync_proc:
            try:
                self._rsync_proc.terminate()
            except Exception:
                pass

    def run(self):
        try:
            self._emit_log("══════════════════════════════════════")
            self._emit_log(f"开始{direction_text(self.direction)}")
            self._emit_log(f"源: {', '.join(self.sources)}")
            self._emit_log(f"目标: {self.dest}")
            if self.dry_run:
                self._emit_log("模式: DRY-RUN (仅预览，不实际传输)")
            self._emit_log(f"Rsync 选项: {self.rsync_opts.summary()}")
            self._emit_log("──────────────────────────────────────")

            if self._cancelled:
                return

            # Step 1: 验证隧道是否存活（隧道由 MainWindow 维护）
            alive, msg = self._check_tunnel_alive()
            if not alive:
                self._emit_log(f"✗ 隧道不可用: {msg}")
                self._emit_log("  正在尝试通知主窗口重连隧道...")
                self.tunnel_lost.emit()
                self.transfer_done.emit(False, f"隧道不可用: {msg}")
                return
            self._emit_log("  ✓ 隧道可用")

            if self._cancelled:
                return

            # Step 2: rsync 传输
            success = self._do_rsync()
            if success:
                self._emit_log("✓ 传输完成")
            else:
                self._emit_log("✗ 传输失败")

        except Exception as e:
            self._emit_log(f"✗ 异常: {e}")
            self.transfer_done.emit(False, str(e))
        finally:
            self._emit_log("══════════════════════════════════════")

    def _check_tunnel_alive(self) -> tuple:
        """检查隧道是否存活"""
        self.verify_status.emit("检查隧道...")
        alive, msg = check_tunnel(self.config, timeout=10)
        if alive:
            self.verify_status.emit("隧道正常")
        return alive, msg

    def _do_rsync(self) -> bool:
        """通过已有隧道执行 rsync"""
        cfg = self.config

        self._emit_log(f"[rsync] {'预览模式' if self.dry_run else '开始同步'}...")

        ssh_cmd = (
            f"sshpass -p '{cfg['rk_pass']}' ssh -p {cfg['tunnel_port']} "
            f"-o StrictHostKeyChecking=no"
        )

        rsync_args = ["rsync"] + self.rsync_opts.to_args()
        if self.dry_run:
            rsync_args.append("--dry-run")
        rsync_args.extend(["-e", ssh_cmd])

        if self.direction == "push":
            for src in self.sources:
                rsync_args.append(src)
            rsync_args.append(f"{cfg['rk_user']}@127.0.0.1:{self.dest}")
        else:
            for src in self.sources:
                rsync_args.append(f"{cfg['rk_user']}@127.0.0.1:{src}")
            rsync_args.append(self.dest)

        # 打印安全命令（隐藏密码）
        safe = [
            "sshpass -p *** ..." if "sshpass" in str(a) else str(a)
            for a in rsync_args
        ]
        self._emit_log(f"  {' '.join(safe)}")

        try:
            self._rsync_proc = subprocess.Popen(
                rsync_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )

            for line in self._rsync_proc.stdout:
                if self._cancelled:
                    self._rsync_proc.terminate()
                    self._emit_log("⊗ 传输已取消")
                    self.transfer_done.emit(False, "用户取消传输")
                    return False

                line = line.strip()
                if line:
                    pct = self._parse_progress(line)
                    self.transfer_progress.emit(line, pct)
                    self._emit_log(f"  {line}")

            self._rsync_proc.wait()

            if self._rsync_proc.returncode == 0:
                self.transfer_progress.emit("完成", 100)
                self.transfer_done.emit(True, "传输完成")
                return True
            else:
                self.transfer_done.emit(False, f"rsync 返回码: {self._rsync_proc.returncode}")
                return False

        except Exception as e:
            self._emit_log(f"✗ rsync 异常: {e}")
            self.transfer_done.emit(False, str(e))
            return False

    def _parse_progress(self, line: str) -> int:
        """从 rsync --info=progress2 输出中解析百分比"""
        match = re.search(r'(\d+)%', line)
        if match:
            return int(match.group(1))
        if "xfr#" in line.lower() and "to-chk=" in line.lower():
            m = re.search(r'to-chk=(\d+)/(\d+)', line)
            if m:
                done = int(m.group(2)) - int(m.group(1))
                total = int(m.group(2))
                if total > 0:
                    return int(done / total * 100)
        return -1

    def _emit_log(self, msg: str):
        self.log_message.emit(msg)
