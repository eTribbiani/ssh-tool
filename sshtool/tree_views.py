"""支持拖放的树视图组件"""

import os

from PyQt5.QtCore import (
    QAbstractProxyModel, QMimeData, QModelIndex, QSortFilterProxyModel, Qt,
    pyqtSignal,
)
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (
    QAbstractItemView, QFileSystemModel, QStyledItemDelegate, QStyleOptionViewItem,
    QTreeView,
)

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


class RemoteFileFilterProxy(QSortFilterProxyModel):
    """远程文件模型过滤代理: 支持递归文件名过滤。

    包装 RemoteFileSystemModel 的 QStandardItemModel，
    按名称列(第0列)过滤，同时保持匹配文件的父目录可见。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDynamicSortFilter(True)
        self.setRecursiveFilteringEnabled(True)
        self.setFilterKeyColumn(0)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        """递归过滤: 目录下任一后代匹配则保留该目录。"""
        model = self.sourceModel()
        if model is None:
            return True

        idx = model.index(source_row, 0, source_parent)
        name = model.data(idx, Qt.DisplayRole)
        if name is None:
            return True

        # 始终显示"加载中..."占位符，避免目录被误隐藏
        if name == "加载中...":
            return True

        # 文件名匹配 → 接受
        if self.filterRegExp().isEmpty():
            return True
        if name and self.filterRegExp().indexIn(name) >= 0:
            return True

        # 检查子节点是否有匹配项（配合 RecursiveFilteringEnabled）
        if model.hasChildren(idx):
            return super().filterAcceptsRow(source_row, source_parent)

        return False


class DragDropTreeView(QTreeView):
    """自定义 TreeView，支持文件拖放传输与右键上下文菜单"""

    drop_requested = pyqtSignal(list, str, str)  # (源路径列表, 目标路径, 方向 push/pull)

    # 右键上下文菜单信号
    context_new_folder = pyqtSignal(str)   # parent_path — 将在其中创建新目录
    context_rename = pyqtSignal(str)       # path — 要重命名的项目路径
    context_delete = pyqtSignal(list)      # paths — 要删除的项目路径列表
    context_refresh = pyqtSignal()         # 刷新当前目录

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)
        self._role = "local"  # "local" 或 "remote"
        self._drop_target = QModelIndex()  # 拖放悬停的目标目录(用于高亮)

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

    def _is_dir_item(self, idx: QModelIndex) -> bool:
        """判断索引对应的是否为目录"""
        model = self.model()
        if isinstance(model, QAbstractProxyModel):
            src_model = model.sourceModel()
            src_idx = model.mapToSource(idx)
        else:
            src_model, src_idx = model, idx
        if isinstance(src_model, QFileSystemModel):
            return src_model.isDir(src_idx)
        item = src_model.itemFromIndex(src_idx)
        return bool(item and item.data(Qt.UserRole + 1))

    def _drop_target_index(self, pos) -> QModelIndex:
        """拖放目标: 目录本身高亮; 落在文件上时高亮其父目录(需为可见子目录)"""
        idx = self.indexAt(pos)
        if not idx.isValid():
            return QModelIndex()
        if self._is_dir_item(idx):
            return idx
        parent = idx.parent()
        if parent.isValid() and parent != self.rootIndex():
            return parent
        return QModelIndex()

    def _set_drop_target(self, idx: QModelIndex):
        """更新拖放目标并触发重绘"""
        if idx != self._drop_target:
            self._drop_target = idx
            self.viewport().update()

    def dragEnterEvent(self, event):
        if event.source() is self:
            self._set_drop_target(QModelIndex())
            event.ignore()
            return
        if event.mimeData().hasFormat("application/x-sshtool-paths") \
                or event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            self._set_drop_target(QModelIndex())
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-sshtool-paths") \
                or event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_drop_target(self._drop_target_index(event.pos()))
        else:
            self._set_drop_target(QModelIndex())
            event.ignore()

    def dragLeaveEvent(self, event):
        self._set_drop_target(QModelIndex())
        super().dragLeaveEvent(event)

    def paintEvent(self, event):
        """绘制拖放目标高亮框"""
        super().paintEvent(event)
        if not self._drop_target.isValid():
            return
        rect = self.visualRect(self._drop_target)
        if not rect.isValid():
            return
        painter = QPainter(self.viewport())
        painter.fillRect(rect, QColor(38, 79, 120, 110))
        painter.setPen(QColor(14, 99, 156))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.end()

    def dropEvent(self, event):
        self._set_drop_target(QModelIndex())
        md = event.mimeData()

        # ── 面板间拖放 (自定义 MIME) ──
        if md.hasFormat("application/x-sshtool-paths"):
            data = bytes(md.data("application/x-sshtool-paths")).decode()
            source_role = data.split("|", 1)[0]
            paths_str = data.split("|", 1)[1] if "|" in data else ""
            source_paths = [p for p in paths_str.split("\n") if p]

            if not source_paths:
                event.ignore()
                return

            # 目标路径可能为空(落在空白区域) → 由主窗口回退到面板当前目录
            target_path = self._get_target_path(event.pos())

            # 判断方向: 从其他面板拖过来
            if source_role != self._role:
                direction = "push" if source_role == "local" else "pull"
                event.acceptProposedAction()
                self.drop_requested.emit(source_paths, target_path, direction)
            else:
                event.ignore()
            return

        # ── 外部应用拖入 (text/uri-list, 如文件管理器) ──
        if md.hasUrls():
            source_paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            source_paths = [p for p in source_paths if p]
            if not source_paths:
                event.ignore()
                return
            # 外部拖入的都是本地文件: 仅远程面板接受(推送到远程);
            # 拖到本地面板属于本地复制, 不支持
            if self._role != "remote":
                event.ignore()
                return
            # 目标路径可能为空(落在空白区域) → 由主窗口回退到面板当前目录
            target_path = self._get_target_path(event.pos())
            event.acceptProposedAction()
            self.drop_requested.emit(source_paths, target_path, "push")
            return

        event.ignore()

    def _get_target_path(self, pos) -> str:
        """获取当前面板的目标路径（落在文件上时取其父目录）"""
        idx = self.indexAt(pos)
        if not idx.isValid():
            return ""
        path = self._path_for_index(idx)
        if not path:
            return ""
        if not self._is_dir_item(idx):
            path = os.path.dirname(path)
        return path

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
        return ["application/x-sshtool-paths", "text/uri-list", "text/plain"]

    def contextMenuEvent(self, event):
        """右键上下文菜单: 新建文件夹、重命名、删除、刷新"""
        from PyQt5.QtWidgets import QMenu

        menu = QMenu(self)

        # ── 确定点击目标 ──
        idx = self.indexAt(event.pos())
        selected_indexes = self.selectionModel().selectedRows()

        # 收集当前面板的根路径（用于"新建文件夹"的默认父目录）
        root_path = self._path_for_index(self.rootIndex()) if self.rootIndex().isValid() else ""

        if idx.isValid():
            clicked_path = self._path_for_index(idx)
            is_dir = self._is_dir_item(idx)

            # ── 多选时仅支持删除 ──
            if len(selected_indexes) > 1 and idx in selected_indexes:
                selected_paths = [self._path_for_index(i) for i in selected_indexes]
                selected_paths = [p for p in selected_paths if p]
                if selected_paths:
                    del_action = menu.addAction("删除选中项")
                    del_action.triggered.connect(
                        lambda: self.context_delete.emit(selected_paths))
                menu.addSeparator()
                menu.addAction("刷新", lambda: self.context_refresh.emit())
                menu.exec_(event.globalPos())
                return

            # ── 单项操作 ──
            if is_dir:
                # 点击目录: 新建文件夹在目录内创建
                parent_for_new = clicked_path
                menu.addAction("新建文件夹", lambda: self.context_new_folder.emit(parent_for_new))
                menu.addSeparator()
                menu.addAction("重命名", lambda: self.context_rename.emit(clicked_path))
                menu.addAction("删除", lambda: self.context_delete.emit([clicked_path]))
            else:
                # 点击文件: 新建文件夹在父目录中创建
                parent_dir = os.path.dirname(clicked_path) if clicked_path else root_path
                menu.addAction("新建文件夹", lambda: self.context_new_folder.emit(parent_dir))
                menu.addSeparator()
                menu.addAction("重命名", lambda: self.context_rename.emit(clicked_path))
                menu.addAction("删除", lambda: self.context_delete.emit([clicked_path]))
        else:
            # 点击空白区域: 新建文件夹在当前根目录
            if root_path:
                menu.addAction("新建文件夹", lambda: self.context_new_folder.emit(root_path))

        menu.addSeparator()
        menu.addAction("刷新", lambda: self.context_refresh.emit())
        menu.exec_(event.globalPos())


# ─── Diff Status ───────────────────────────────────────────────


class DiffStatus:
    """文件差异状态"""
    SAME = 0
    ONLY_LOCAL = 1
    ONLY_REMOTE = 2
    LOCAL_NEWER = 3
    REMOTE_NEWER = 4
    SIZE_DIFFERS = 5


# 差异状态对应的背景色 (RGBA, 暗色主题下带透明度的柔和底色)
_DIFF_COLORS = {
    DiffStatus.ONLY_LOCAL:   QColor(80, 180, 80, 55),   # 绿色 — 仅本地存在
    DiffStatus.ONLY_REMOTE:  QColor(200, 90, 90, 55),   # 红色 — 仅远程存在
    DiffStatus.LOCAL_NEWER:  QColor(90, 150, 220, 55),  # 蓝色 — 本地较新
    DiffStatus.REMOTE_NEWER: QColor(220, 160, 70, 55),  # 橙色 — 远程较新
    DiffStatus.SIZE_DIFFERS: QColor(210, 190, 60, 55),  # 黄色 — 大小不同
}


class DiffHighlightDelegate(QStyledItemDelegate):
    """根据本地/远程差异状态为树视图行着色。

    在列 0（文件名列）上绘制差异背景色，使差异一目了然。
    左右面板各自设置一个 delegate 实例，由 MainWindow 在目录切换后
    调用 set_diff_data() 更新差异数据。
    """

    def __init__(self, side: str = "local", parent=None):
        """
        side: "local" 或 "remote" — 用于区分该 delegate 所属面板
              远程面板不显示 ONLY_LOCAL 状态，本地面板不显示 ONLY_REMOTE
        """
        super().__init__(parent)
        self._side = side
        self._diff: dict = {}  # filename -> DiffStatus

    def set_diff_data(self, diff: dict):
        """更新差异数据并触发重绘。diff: {filename: DiffStatus}"""
        self._diff = diff

    def paint(self, painter, option, index):
        """在列 0 上绘制差异背景色"""
        if index.column() == 0 and self._diff:
            filename = index.data(Qt.DisplayRole)
            status = self._diff.get(filename)
            if status is not None and status != DiffStatus.SAME:
                # 远程面板不显示 ONLY_LOCAL，本地面板不显示 ONLY_REMOTE
                if self._side == "remote" and status == DiffStatus.ONLY_LOCAL:
                    pass
                elif self._side == "local" and status == DiffStatus.ONLY_REMOTE:
                    pass
                else:
                    bg = _DIFF_COLORS.get(status)
                    if bg:
                        painter.save()
                        painter.fillRect(option.rect, bg)
                        painter.restore()
        super().paint(painter, option, index)
