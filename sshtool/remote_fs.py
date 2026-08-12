"""RemoteFileSystemModel — 通过 SSH stat 懒加载的远程目录树模型"""

import datetime
import shlex
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QStandardItem, QStandardItemModel
from PyQt5.QtWidgets import QApplication, QStyle

from .config import file_type, format_mtime
from .ssh_utils import run_ssh


class RemoteFileSystemModel:
    """通过 SSH stat 懒加载远程目录树，获取完整文件信息"""

    COLUMNS = ["名称", "大小", "修改时间"]

    # 入口数据结构: (name, is_dir, size, mtime)
    # mtime 为 Unix 时间戳(int)，0 表示未知

    def __init__(self, config: dict):
        self.config = config
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(self.COLUMNS)
        self._style = QApplication.style()
        self._cache: dict[str, list] = {}
        self._loading_paths: set = set()

    def set_root_path(self, path: str):
        """设置并加载远程根路径"""
        self.model.removeRows(0, self.model.rowCount())
        self._cache.clear()
        self._populate_directory(path, None)

    def expand_directory(self, path: str, parent_item: QStandardItem):
        """展开目录时调用，加载子内容"""
        if path in self._loading_paths:
            return
        self._loading_paths.add(path)
        try:
            parent_item.removeRows(0, parent_item.rowCount())
            self._populate_directory(path, parent_item)
        finally:
            self._loading_paths.discard(path)

    def _populate_directory(self, path: str, parent: Optional[QStandardItem]):
        """通过 SSH stat 获取目录内容并填充模型（目录优先，文件按类型排序）"""
        entries = self._fetch_remote_listing(path)
        self._cache[path] = entries

        target = parent if parent else self.model.invisibleRootItem()
        dirs = sorted((e for e in entries if e[1]), key=lambda e: e[0].lower())
        files = sorted((e for e in entries if not e[1]),
                       key=lambda e: (file_type(e[0]), e[0].lower()))
        for entry in dirs + files:
            self._add_item(target, *entry, parent_path=path)

    def _add_item(self, parent: QStandardItem, name: str, is_dir: bool,
                  size: int, mtime: int, parent_path: str):
        """添加单个项目"""
        full_path = f"{parent_path.rstrip('/')}/{name}"
        name_item = QStandardItem(name)
        name_item.setData(full_path, Qt.UserRole)
        name_item.setData(is_dir, Qt.UserRole + 1)

        # Qt 原生图标
        if is_dir:
            name_item.setIcon(self._style.standardIcon(QStyle.SP_DirIcon))
            name_item.setData("dir", Qt.UserRole + 3)
            name_item.appendRow(QStandardItem("加载中..."))
        else:
            name_item.setIcon(self._style.standardIcon(QStyle.SP_FileIcon))
            name_item.setData("file", Qt.UserRole + 3)

        name_item.setEditable(False)

        # 大小列
        size_text = self._format_size(size) if size > 0 and not is_dir else ("—" if not is_dir else "")
        size_item = QStandardItem(size_text)
        size_item.setEditable(False)
        size_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # 修改时间列
        time_item = QStandardItem(self._format_mtime(mtime) if mtime > 0 else "")
        time_item.setEditable(False)

        parent.appendRow([name_item, size_item, time_item])

    def _fetch_remote_listing(self, path: str) -> list:
        """通过 SSH 执行 stat 获取目录列表。
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
            result = run_ssh(self.config, remote_cmd, timeout=15)
            if "PATH_ERROR" in result.stdout:
                print(f"[RemoteFileSystem] 远程路径不存在: {path}")
                return []
            if result.returncode != 0 and not result.stdout.strip():
                print(f"[RemoteFileSystem] SSH 错误 (rc={result.returncode}): {result.stderr}")
                return []
            return self._parse_stat_output(result.stdout)
        except Exception as e:
            print(f"[RemoteFileSystem] 异常: {e}")
            return []

    def _parse_stat_output(self, output: str) -> list:
        """解析 stat -c '%F|%s|%Y|%n' 输出"""
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

    @staticmethod
    def _format_size(size: int) -> str:
        if size <= 0:
            return ""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    @staticmethod
    def _format_mtime(mtime: int) -> str:
        """将 Unix 时间戳转为统一可读格式"""
        if mtime <= 0:
            return ""
        return format_mtime(datetime.datetime.fromtimestamp(mtime))

    def get_full_path(self, item: QStandardItem) -> str:
        return item.data(Qt.UserRole) or ""

    def is_dir(self, item: QStandardItem) -> bool:
        return bool(item.data(Qt.UserRole + 1))

    def has_children_cached(self, path: str) -> bool:
        return path in self._cache
