"""支持拖放的树视图组件"""

from PyQt5.QtCore import (
    QAbstractProxyModel, QMimeData, QModelIndex, QSortFilterProxyModel, Qt,
    pyqtSignal,
)
from PyQt5.QtWidgets import QAbstractItemView, QFileSystemModel, QTreeView

from .config import file_type, format_mtime, format_size


class LocalFileProxyModel(QSortFilterProxyModel):
    """QFileSystemModel 代理: 中文列名、统一时间格式、按文件类型排序"""

    _HEADERS = {0: "名称", 1: "大小", 2: "类型", 3: "修改时间"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDynamicSortFilter(True)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self._HEADERS.get(section, "")
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.DisplayRole):
        # 所有列左对齐
        if role == Qt.TextAlignmentRole:
            return Qt.AlignLeft | Qt.AlignVCenter
        # 大小列与远程面板使用相同格式 (B/KB/MB/GB/TB)
        if role == Qt.DisplayRole and index.column() == 1:
            fi = self.sourceModel().fileInfo(self.mapToSource(index))
            if fi.isDir():
                return ""
            return format_size(fi.size()) if fi.size() > 0 else "—"
        # 修改时间列与远程面板使用相同格式
        if role == Qt.DisplayRole and index.column() == 3:
            fi = self.sourceModel().fileInfo(self.mapToSource(index))
            if fi.lastModified().isValid():
                return format_mtime(fi.lastModified().toPyDateTime())
            return ""
        return super().data(index, role)

    def lessThan(self, left, right):
        """排序: 目录优先，文件按类型(扩展名)再按名称"""
        src = self.sourceModel()
        if left.model() is not src:  # 兼容传入代理索引
            left, right = self.mapToSource(left), self.mapToSource(right)
        l_dir, r_dir = src.isDir(left), src.isDir(right)
        if l_dir != r_dir:
            return l_dir
        l_name, r_name = src.fileName(left).lower(), src.fileName(right).lower()
        if not l_dir:
            l_type, r_type = file_type(l_name), file_type(r_name)
            if l_type != r_type:
                return l_type < r_type
        return l_name < r_name


class DragDropTreeView(QTreeView):
    """自定义 TreeView，支持文件拖放传输"""

    drop_requested = pyqtSignal(list, str, str)  # (源路径列表, 目标路径, 方向 push/pull)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)
        self._role = "local"  # "local" 或 "remote"

    def set_role(self, role: str):
        self._role = role

    def _path_for_index(self, idx: QModelIndex) -> str:
        """将视图索引映射为文件系统路径（兼容代理模型与标准模型）"""
        model = self.model()
        if isinstance(model, QAbstractProxyModel):
            src_model = model.sourceModel()
            src_idx = model.mapToSource(idx)
        else:
            src_model, src_idx = model, idx
        if isinstance(src_model, QFileSystemModel):
            return src_model.filePath(src_idx)
        item = src_model.itemFromIndex(src_idx)
        return item.data(Qt.UserRole) if item else ""

    def dragEnterEvent(self, event):
        if event.source() is self:
            event.ignore()
            return
        if event.mimeData().hasFormat("application/x-sshtool-paths"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-sshtool-paths"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasFormat("application/x-sshtool-paths"):
            event.ignore()
            return

        data = bytes(event.mimeData().data("application/x-sshtool-paths")).decode()
        source_role = data.split("|", 1)[0]
        paths_str = data.split("|", 1)[1] if "|" in data else ""
        source_paths = [p for p in paths_str.split("\n") if p]

        if not source_paths:
            event.ignore()
            return

        # 获取目标路径
        target_path = self._get_target_path()
        if not target_path:
            event.ignore()
            return

        # 判断方向: 从其他面板拖过来
        if source_role != self._role:
            direction = "push" if source_role == "local" else "pull"
            event.acceptProposedAction()
            self.drop_requested.emit(source_paths, target_path, direction)
        else:
            event.ignore()

    def _get_target_path(self) -> str:
        """获取当前面板的目标路径"""
        idx = self.indexAt(self.viewport().rect().center())
        if idx.isValid():
            return self._path_for_index(idx)
        return ""

    def mimeData(self, indexes):
        """编码选中的路径为自定义 MIME 数据"""
        paths = [p for p in (self._path_for_index(i) for i in indexes) if p]

        if paths:
            mime_data = QMimeData()
            payload = f"{self._role}|" + "\n".join(paths)
            mime_data.setData("application/x-sshtool-paths",
                              payload.encode("utf-8"))
            # 同时设置文本格式以支持标准拖放
            mime_data.setText("\n".join(paths))
            return mime_data
        return super().mimeData(indexes)

    def mimeTypes(self):
        return ["application/x-sshtool-paths", "text/plain"]
