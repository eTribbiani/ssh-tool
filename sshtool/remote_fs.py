"""RemoteFileSystemModel — 通过 SSH stat 懒加载的远程目录树模型"""

import datetime
from typing import Optional

from PyQt5.QtCore import QThread, Qt, pyqtSignal
from PyQt5.QtGui import QStandardItem, QStandardItemModel
from PyQt5.QtWidgets import QApplication, QStyle

from .config import file_type, format_mtime, format_size
from .ssh_utils import remote_listing


class _ListingFetcher(QThread):
    """后台获取目录列表(纯 ssh 调用, 不阻塞 GUI); 结果在 GUI 线程应用"""

    result_ready = pyqtSignal(str, list, object)  # (path, entries, parent_item)

    def __init__(self, config: dict, path: str, parent_item, parent=None):
        super().__init__(parent)
        self._config = config
        self._path = path
        self._parent_item = parent_item

    def run(self):
        entries = remote_listing(self._config, self._path)
        self.result_ready.emit(self._path, entries, self._parent_item)


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
        self._fetchers: set = set()

    def shutdown(self, timeout: int = 3000):
        """退出前等待所有后台列表获取结束"""
        for f in list(self._fetchers):
            f.wait(timeout)
        self._fetchers.clear()

    def set_root_path(self, path: str):
        """异步设置并加载远程根路径(不阻塞 GUI)"""
        self.model.removeRows(0, self.model.rowCount())
        self._cache.clear()
        self._request_listing(path, None)

    def apply_root_listing(self, path: str, entries: list):
        """用后台线程预取的列表同步填充根目录（必须在 GUI 线程调用）"""
        self.model.removeRows(0, self.model.rowCount())
        self._cache.clear()
        self._populate_from_entries(path, entries)

    def expand_directory(self, path: str, parent_item: QStandardItem):
        """展开目录时异步加载子内容(不阻塞 GUI)"""
        self._request_listing(path, parent_item)

    def _request_listing(self, path: str, parent_item: Optional[QStandardItem]):
        """发起异步目录获取; 结果到达后填充模型"""
        if path in self._loading_paths:
            return
        self._loading_paths.add(path)
        if parent_item is not None:
            parent_item.removeRows(0, parent_item.rowCount())
        fetcher = _ListingFetcher(self.config, path, parent_item)  # 由 _fetchers 持有引用
        fetcher.result_ready.connect(self._on_listing_fetched)
        self._fetchers.add(fetcher)
        fetcher.finished.connect(lambda f=fetcher: self._fetchers.discard(f))
        fetcher.start()

    def _on_listing_fetched(self, path: str, entries: list, parent_item):
        self._loading_paths.discard(path)
        if parent_item is not None:
            try:
                parent_item.row()  # 探测父节点是否已被树重建删除
            except RuntimeError:
                return  # 过期结果, 丢弃
        self._populate_from_entries(path, entries, parent_item)

    def _populate_from_entries(self, path: str, entries: list,
                               parent: Optional[QStandardItem] = None):
        """将已获取的列表填充到模型"""
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
        size_text = format_size(size) if size > 0 and not is_dir else ("—" if not is_dir else "")
        size_item = QStandardItem(size_text)
        size_item.setEditable(False)
        size_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # 修改时间列
        time_item = QStandardItem(self._format_mtime(mtime) if mtime > 0 else "")
        time_item.setEditable(False)

        parent.appendRow([name_item, size_item, time_item])

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

    def refresh_directory(self, path: str, parent_item: QStandardItem):
        """异步刷新单个目录缓存并重新加载子节点，不清除整棵树。

        与 expand_directory 不同，此方法始终失效缓存并重新获取远程列表，
        即使该路径已在 _cache 中。用于传输完成后的局部刷新和 F5 手动刷新。
        """
        if path in self._loading_paths:
            return
        # 级联失效所有子目录缓存，否则重建的子目录 item 带有
        # "加载中..." 占位符，但 has_children_cached 返回 True，
        # 懒加载守卫会阻止重新获取，导致子目录永远卡在占位符状态。
        self._cache.pop(path, None)
        prefix = path.rstrip("/") + "/"
        for k in list(self._cache.keys()):
            if k.startswith(prefix):
                del self._cache[k]
        self._request_listing(path, parent_item)

    def find_item_for_path(self, path: str) -> Optional[QStandardItem]:
        """在模型树中递归查找指定远程路径对应的 item。

        通过 UserRole 中存储的 full_path 匹配。用于定位需要局部刷新的目录节点。
        返回 None 表示该路径不在当前树中（可能是根路径本身，或还未加载）。
        """
        norm = path.rstrip("/") or "/"

        def search(parent: QStandardItem) -> Optional[QStandardItem]:
            for row in range(parent.rowCount()):
                child = parent.child(row)
                if not child:
                    continue
                child_path = (self.get_full_path(child) or "").rstrip("/")
                if child_path == norm:
                    return child
                if self.is_dir(child):
                    found = search(child)
                    if found is not None:
                        return found
            return None

        return search(self.model.invisibleRootItem())
