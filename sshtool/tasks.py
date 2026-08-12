"""TransferQueue — 后台传输队列(串行执行, 非阻塞)"""

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from .rsync_options import RsyncOptions
from .transfer_engine import TransferEngine

# 任务状态
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"


@dataclass
class TransferTask:
    """一次传输任务"""
    id: int
    sources: list
    dest: str
    direction: str            # push / pull
    options: RsyncOptions
    dry_run: bool = False
    status: str = PENDING
    progress: int = 0
    message: str = ""
    cancel_requested: bool = False
    created_at: float = field(default_factory=time.time)

    @property
    def title(self) -> str:
        """任务标题: 方向 + 源名称 → 目标"""
        names = ", ".join(os.path.basename(p) for p in self.sources[:3])
        if len(self.sources) > 3:
            names += f" 等{len(self.sources)}项"
        arrow = "→" if self.direction == "push" else "←"
        return f"{arrow} {names} → {self.dest}"


class TransferQueue(QObject):
    """后台传输队列: 任务串行执行, 可随时添加, 不阻塞浏览与添加新任务"""

    task_added = pyqtSignal(object)               # TransferTask
    task_started = pyqtSignal(object)
    task_progress = pyqtSignal(object, str, int)  # (task, 行文本, 百分比)
    task_log = pyqtSignal(object, str)            # (task, 日志行)
    task_finished = pyqtSignal(object)            # 终态: done/failed/cancelled
    tunnel_lost = pyqtSignal()                    # 转发引擎的隧道断开信号
    preview_result = pyqtSignal(object, dict)     # (task, preview_data) dry-run 预览结果

    def __init__(self, config_provider: Callable[[], dict],
                 tunnel_provider: Callable[[], str],
                 tunnel_connect: Callable[[], None] = None, parent=None):
        """tunnel_provider 返回隧道状态: "connected" / "connecting" / "disconnected";
        tunnel_connect 用于在未连接时发起异步连接"""
        super().__init__(parent)
        self._config_provider = config_provider
        self._tunnel_provider = tunnel_provider
        self._tunnel_connect = tunnel_connect or (lambda: None)
        self._tasks: dict = {}
        self._pending: list = []
        self._current: Optional[TransferTask] = None
        self._waiting_task: Optional[TransferTask] = None  # 等待异步隧道连接
        self._engine: Optional[TransferEngine] = None
        self._next_id = 1

    @property
    def running(self) -> bool:
        return self._current is not None

    def add_task(self, sources: list, dest: str, direction: str,
                 options: RsyncOptions, dry_run: bool = False) -> TransferTask:
        """添加任务到队列, 空闲时立即开始执行"""
        task = TransferTask(self._next_id, list(sources), dest, direction,
                            options, dry_run=dry_run)
        self._next_id += 1
        self._tasks[task.id] = task
        self._pending.append(task)
        self.task_added.emit(task)
        self._start_next()
        return task

    def cancel(self, task_id: int):
        """取消任务: 运行中终止 rsync, 排队/等待中直接移除"""
        task = self._tasks.get(task_id)
        if task is None or task.status not in (PENDING, RUNNING):
            return
        if task is self._waiting_task:
            self._waiting_task = None
            task.status = CANCELLED
            self.task_finished.emit(task)
            self._start_next()
        elif task is self._current:
            task.cancel_requested = True  # 由引擎结束回调统一收尾
            if self._engine:
                self._engine.cancel()
        else:
            self._pending.remove(task)
            task.status = CANCELLED
            self.task_finished.emit(task)

    def cancel_running(self):
        """取消当前正在执行的任务"""
        if self._current:
            self.cancel(self._current.id)

    def cancel_all(self):
        for t in list(self._pending):
            self.cancel(t.id)
        self.cancel_running()

    def shutdown(self, timeout: int = 3000):
        """退出前停止全部任务并等待引擎结束"""
        self.cancel_all()
        if self._engine:
            self._engine.wait(timeout)
            self._engine = None

    # ─── 内部: 任务调度 ───────────────────────────────────────

    def _start_next(self):
        if self._current is not None or not self._pending:
            return
        task = self._pending.pop(0)
        state = self._tunnel_provider()
        if state == "connected":
            self._launch_task(task)
        elif state == "connecting":
            self._waiting_task = task  # 隧道异步连接中, 等待 tunnel_result
        else:
            # 未连接: 发起异步连接并等待结果
            self._tunnel_connect()
            self._waiting_task = task

    def tunnel_result(self, ok: bool):
        """异步隧道连接结果回调(GUI 线程调用)"""
        task = self._waiting_task
        self._waiting_task = None
        if task is None:
            return
        if ok:
            self._launch_task(task)
        else:
            task.status = FAILED
            task.message = "无法建立 SSH 隧道，请检查连接配置"
            self.task_log.emit(task, f"✗ {task.message}")
            self.task_finished.emit(task)
        self._start_next()

    def _launch_task(self, task: TransferTask):
        self._current = task
        task.status = RUNNING
        task.cancel_requested = False
        self.task_started.emit(task)

        cfg = self._config_provider()
        self._engine = TransferEngine(
            cfg, task.options, task.sources, task.dest, task.direction,
            tunnel_port=cfg["tunnel_port"], dry_run=task.dry_run,
        )
        self._engine.log_message.connect(
            lambda m, t=task: self.task_log.emit(t, m))
        self._engine.transfer_progress.connect(
            lambda l, p, t=task: self._on_progress(t, l, p))
        self._engine.transfer_done.connect(
            lambda ok, msg, t=task: self._on_done(t, ok, msg))
        self._engine.tunnel_lost.connect(self.tunnel_lost)
        self._engine.preview_result.connect(
            lambda data, t=task: self.preview_result.emit(t, data))
        self._engine.start()

    def _on_progress(self, task: TransferTask, line: str, pct: int):
        if pct >= 0:
            task.progress = pct
        self.task_progress.emit(task, line, pct)

    def _on_done(self, task: TransferTask, ok: bool, msg: str):
        if task.cancel_requested:
            task.status = CANCELLED
        elif ok:
            task.status = DONE
        else:
            task.status = FAILED
        task.message = msg
        self._finish_current()

    def _finish_current(self):
        task = self._current
        self._current = None
        if self._engine:
            self._engine.wait(3000)
            self._engine = None
        self.task_finished.emit(task)
        self._start_next()
