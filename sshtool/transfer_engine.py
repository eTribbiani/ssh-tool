"""TransferEngine — QThread 后台传输引擎"""

import re
import subprocess
from typing import Iterable

from PyQt5.QtCore import QThread, pyqtSignal

from .rsync_options import RsyncOptions
from .ssh_utils import check_tunnel, direction_text


def parse_rsync_itemize(lines: list) -> dict:
    """解析 rsync --itemize-changes 输出，按类别分组。

    返回:
        {
            "new": [path, ...],        # 将新增的文件/目录
            "update": [path, ...],     # 将覆盖/更新的文件
            "delete": [path, ...],     # 将删除的文件 (--delete)
        }

    rsync -i 输出格式: YXcstpoguax path
      Y: >从本地发出, <从远程接收, c本地变更, *消息
      X: f文件, d目录, L符号链接
      cstpoguax: 属性位 (+新增, .未变, 字母=对应属性变化)
    """
    result = {"new": [], "update": [], "delete": []}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 删除行: *deleting   path
        if line.startswith("*deleting"):
            parts = line.split(None, 1)
            if len(parts) > 1:
                result["delete"].append(parts[1].strip())
            continue
        # itemize 行: 至少 12 字符 (11 字符 itemize + 空格 + 文件名)
        if len(line) < 12:
            continue
        itemize = line[:11]
        path = line[12:].strip()
        if not path:
            continue
        # 校验 itemize 格式: YXcstpoguax
        if itemize[0] not in "><ch.*" or itemize[1] not in "fdLD":
            continue
        attrs = itemize[2:11]  # cstpoguax
        if all(c == "+" for c in attrs):
            result["new"].append(path)
        elif all(c == "." for c in attrs):
            continue  # 未变化，不显示
        else:
            result["update"].append(path)
    return result


class TransferEngine(QThread):
    """后台传输引擎 — 只负责验证隧道存活 + rsync，不管理隧道生命周期"""

    log_message = pyqtSignal(str)
    verify_status = pyqtSignal(str)
    transfer_progress = pyqtSignal(str, int)  # (行文本, 百分比)
    transfer_done = pyqtSignal(bool, str)     # (成功, 消息)
    tunnel_lost = pyqtSignal()                # 隧道断开，通知 MainWindow 重连
    preview_result = pyqtSignal(dict)         # dry-run 预览结果: {"new":[],"update":[],"delete":[]}

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
        self._itemized_lines: list = []  # dry-run 模式下收集 itemize 行

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
                self.transfer_done.emit(False, "用户取消传输")
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
                self.transfer_done.emit(False, "用户取消传输")
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

        # --outbuf=L: rsync 行缓冲输出, 每条进度即时刷新, 避免 stdio 整块缓冲
        # 导致进度"长时间卡死, 然后瞬间 100%" (需 rsync >= 3.1.0)
        rsync_args = ["rsync", "--outbuf=L"] + self.rsync_opts.to_args()
        if self.dry_run:
            rsync_args.append("--dry-run")
            rsync_args.append("-i")  # --itemize-changes: 输出逐文件变更详情
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

            # 按 \r 或 \n 实时拆分输出(rsync 非 tty 模式下进度以 \r 刷新)
            self._itemized_lines = []
            for line in self._iter_output_lines(self._rsync_proc.stdout.buffer):
                if self._cancelled:
                    self._rsync_proc.terminate()
                    self._emit_log("⊗ 传输已取消")
                    self.transfer_done.emit(False, "用户取消传输")
                    return False
                self._process_output(line)

            self._rsync_proc.wait()

            # dry-run 完成 → 解析 itemize 行并发射预览数据
            if self.dry_run and self._itemized_lines:
                preview = parse_rsync_itemize(self._itemized_lines)
                self.preview_result.emit(preview)

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

    @staticmethod
    def _iter_output_lines(stream) -> Iterable[str]:
        """将 rsync 输出按 \n 或 \r 实时拆分为完整更新行。

        用 read1 读原始块(一次底层读, 有数据即返回, 不会等待填满缓冲),
        再按 \r/\n 双分隔符拆分: rsync 在非 tty 模式下进度更新以 \r 分隔,
        仅按行读取会一直等到换行或进程结束才能拿到数据, 造成进度
        "长时间卡死, 然后瞬间 100%"。
        """
        buf = ""
        for chunk in iter(lambda: stream.read1(4096), b""):
            buf += chunk.decode(errors="replace")
            parts = re.split(r"[\r\n]", buf)
            buf = parts.pop()
            for part in parts:
                yield part
        if buf.strip():
            yield buf

    def _process_output(self, line: str):
        """处理一条输出: 实时推送进度条, 进度刷新行不写入日志避免刷屏"""
        line = line.strip()
        if not line:
            return
        pct = self._parse_progress(line)
        self.transfer_progress.emit(line, pct)
        # 进度刷新行形如 " 5,308,416  25%    4.88MB/s    0:00:03", 仅用于进度显示
        if "%" not in line or ("/s" not in line and "xfr#" not in line):
            self._emit_log(f"  {line}")
        # dry-run 模式: 收集 itemize 行用于预览对话框
        if self.dry_run and self._is_itemize_line(line):
            self._itemized_lines.append(line)

    @staticmethod
    def _is_itemize_line(line: str) -> bool:
        """判断是否为 rsync -i 输出的变更行（YXcstpoguax 格式或 *deleting）"""
        if line.startswith("*deleting"):
            return True
        if len(line) < 12:
            return False
        # YXcstpoguax: Y 是 >, <, c, h, ., *; X 是 f, d, L, D, S
        if line[0] in "><ch.*" and line[1] in "fdLD":
            return True
        return False

    def _emit_log(self, msg: str):
        self.log_message.emit(msg)
