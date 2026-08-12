"""MainWindow — 主窗口与程序入口"""

import json
import os
import shlex
import shutil
import sys

from PyQt5.QtCore import QDir, QModelIndex, QSettings, Qt, pyqtSignal
from PyQt5.QtGui import QFont, QTextCursor
from PyQt5.QtWidgets import (
    QAction, QApplication, QCheckBox, QComboBox, QDialog, QFileSystemModel,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar,
    QPushButton, QShortcut, QSplitter, QStatusBar, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget,
)

from .config import APP_NAME, ConfigManager, clamp_font, make_stylesheet
from .dialogs import ConfigDialog, RsyncOptionsDialog, TransferPreviewDialog
from .icons import get_icon
from .remote_fs import RemoteFileSystemModel
from .rsync_options import RsyncOptions
from .ssh_utils import TunnelConnector, run_ssh, test_tunnel_flow
from .tasks import CANCELLED, DONE, FAILED, TransferQueue
from .transfer_engine import TransferEngine
from .tree_views import DragDropTreeView, DiffHighlightDelegate, DiffStatus, LocalFileProxyModel, RemoteFileFilterProxy


class TaskRowWidget(QWidget):
    """任务列表行: 标题/状态/进度/取消按钮"""

    cancel_requested = pyqtSignal(int)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(2)

        top = QHBoxLayout()
        self.title_label = QLabel(task.title)
        self.title_label.setStyleSheet("font-weight: bold;")
        top.addWidget(self.title_label, 1)
        self.status_label = QLabel("排队中")
        top.addWidget(self.status_label)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setFixedWidth(56)
        self.cancel_btn.clicked.connect(
            lambda: self.cancel_requested.emit(task.id))
        top.addWidget(self.cancel_btn)
        lay.addLayout(top)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        lay.addWidget(self.progress)

    def set_status(self, text: str, color: str = None):
        self.status_label.setText(text)
        if color:
            self.status_label.setStyleSheet(f"color: {color};")

    def set_progress(self, pct: int):
        if pct >= 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(pct)
        elif self.progress.value() == 0:
            self.progress.setRange(0, 0)  # 不确定模式


class MainWindow(QMainWindow):
    """SSHTool GUI 主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1200, 800)

        # 状态
        self._config_mgr = ConfigManager()
        self._config = self._config_mgr.load()
        self._default_rsync_opts = RsyncOptions.preset_full()
        self._tunnel_connected = False
        self._local_paths = set()    # 本地选中缓存
        self._remote_paths = set()   # 远程选中缓存
        self._tree_root_path: str = ""
        self._local_bookmarks: list[str] = []
        self._remote_bookmarks: list[str] = []

        # 后台传输队列
        self._task_rows: dict = {}
        self._queue = TransferQueue(
            config_provider=lambda: self._config,
            tunnel_provider=self._tunnel_state,
            tunnel_connect=lambda: self._connect_tunnel(silent=True),
            parent=self,
        )

        # 隧道连接状态(异步, 不阻塞界面)
        self._connecting = False
        self._connect_aborted = False
        self._connector = None

        # 字体大小
        self._font_size = clamp_font(self._config.get("font_size", 25))

        # 远程文件模型
        self._remote_fs = RemoteFileSystemModel(self._config)

        # 远程文件过滤代理（包装 QStandardItemModel，支持搜索过滤）
        self._remote_proxy = RemoteFileFilterProxy()
        self._remote_proxy.setSourceModel(self._remote_fs.model)

        self._build_ui()
        self._connect_signals()
        self._connect_queue()
        self._setup_shortcuts()

        # 恢复窗口几何
        settings = QSettings("sshtool", "gui")
        geo = settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        splitter_state = settings.value("splitter_state")
        if splitter_state:
            self._main_splitter.restoreState(splitter_state)

        # 加载书签
        self._load_bookmarks()

    # ─── UI 构建 ───────────────────────────────────────────────

    def _build_ui(self):
        # 菜单栏
        menubar = self.menuBar()

        config_menu = menubar.addMenu("连接配置")
        edit_config_action = QAction("编辑连接配置...", self)
        edit_config_action.triggered.connect(self._open_config_dialog)
        config_menu.addAction(edit_config_action)

        delete_profile_action = QAction("删除当前环境...", self)
        delete_profile_action.triggered.connect(self._delete_profile)
        config_menu.addAction(delete_profile_action)

        tunnel_menu = menubar.addMenu("隧道")
        connect_action = QAction("连接隧道", self)
        connect_action.triggered.connect(self._connect_tunnel)
        tunnel_menu.addAction(connect_action)

        disconnect_action = QAction("断开隧道", self)
        disconnect_action.triggered.connect(self._disconnect_tunnel)
        tunnel_menu.addAction(disconnect_action)

        tunnel_menu.addSeparator()
        test_action = QAction("测试连接", self)
        test_action.triggered.connect(self._test_connection)
        tunnel_menu.addAction(test_action)

        self._bookmarks_menu = menubar.addMenu("书签")
        # 菜单项在 _rebuild_bookmark_menu 中动态构建

        # ── 工具栏（QWidget + QHBoxLayout，底部对齐）──────────────
        toolbar_widget = QWidget()
        toolbar_widget.setObjectName("toolbar")
        toolbar_layout = QHBoxLayout(toolbar_widget)
        toolbar_layout.setContentsMargins(10, 4, 10, 6)
        toolbar_layout.setSpacing(8)
        toolbar_layout.setAlignment(Qt.AlignBottom)

        # 环境选择: 多组连接配置, 切换自动重连隧道
        env_label = QLabel("环境:")
        env_label.setObjectName("toolbarLabel")
        toolbar_layout.addWidget(env_label)

        self._loading_profiles = False
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(140)
        self.profile_combo.setObjectName("toolbarCombo")
        self._loading_profiles = True
        self.profile_combo.addItems(self._config_mgr.profiles())
        self.profile_combo.setCurrentText(self._config_mgr.current_profile)
        self._loading_profiles = False
        self.profile_combo.currentTextChanged.connect(self._on_profile_changed)
        toolbar_layout.addWidget(self.profile_combo)

        self.add_profile_btn = QPushButton("")
        self.add_profile_btn.setToolTip("新建环境")
        self.add_profile_btn.setFixedSize(32, 32)
        self.add_profile_btn.setObjectName("addProfileBtn")
        self.add_profile_btn.setIcon(get_icon("plus", size=18, color="#a9b1d6"))
        self.add_profile_btn.clicked.connect(self._add_profile)
        toolbar_layout.addWidget(self.add_profile_btn)

        toolbar_layout.addSpacing(12)

        self.connect_btn = QPushButton(" 连接隧道")
        self.connect_btn.setToolTip("建立 SSH 隧道并加载远程目录")
        self.connect_btn.setObjectName("connectBtn")
        self.connect_btn.setIcon(get_icon("plug", size=20, color="#9ece6a"))
        toolbar_layout.addWidget(self.connect_btn)

        self.disconnect_btn = QPushButton(" 断开")
        self.disconnect_btn.setToolTip("断开 SSH 隧道")
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.setObjectName("disconnectBtn")
        self.disconnect_btn.setIcon(get_icon("plug", size=20, color="#f7768e"))
        toolbar_layout.addWidget(self.disconnect_btn)

        toolbar_layout.addSpacing(12)

        self.push_btn = QPushButton(" 推送 (本地→远程)")
        self.push_btn.setToolTip("将选中文件推送到远程")
        self.push_btn.setIcon(get_icon("arrow-left", size=20, color="#a9b1d6"))
        toolbar_layout.addWidget(self.push_btn)

        self.pull_btn = QPushButton(" 拉取 (远程→本地)")
        self.pull_btn.setToolTip("将选中文件拉取到本地")
        self.pull_btn.setIcon(get_icon("arrow-right", size=20, color="#a9b1d6"))
        toolbar_layout.addWidget(self.pull_btn)

        toolbar_layout.addSpacing(12)

        self.dry_run_cb = QCheckBox("仅预览 (dry-run)")
        self.dry_run_cb.setToolTip("预览模式，不实际传输文件")
        self.dry_run_cb.setObjectName("dryRunCb")
        toolbar_layout.addWidget(self.dry_run_cb)

        self.sync_btn = QPushButton(" 开始传输")
        self.sync_btn.setToolTip("开始文件传输")
        self.sync_btn.setIcon(get_icon("play", size=20, color="#a9b1d6"))
        toolbar_layout.addWidget(self.sync_btn)

        self.preview_btn = QPushButton(" 预览传输")
        self.preview_btn.setToolTip("预览传输变更（dry-run），显示文件差异详情")
        self.preview_btn.setObjectName("previewBtn")
        self.preview_btn.setIcon(get_icon("search", size=20, color="#bb9af7"))
        toolbar_layout.addWidget(self.preview_btn)

        self.stop_btn = QPushButton(" 停止当前任务")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setToolTip("取消当前正在执行的任务")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setIcon(get_icon("square", size=20, color="#f7768e"))
        toolbar_layout.addWidget(self.stop_btn)

        toolbar_layout.addStretch()

        # 中央区域 — 双栏分割器
        splitter = QSplitter(Qt.Horizontal)

        # 左面板 — 本地
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(4, 4, 4, 4)

        left_header = QHBoxLayout()
        left_header.addWidget(QLabel("本地"))
        self.local_path_bar = QLineEdit(os.path.expanduser("~"))
        self.local_path_bar.setToolTip("本地当前路径，回车跳转")
        left_header.addWidget(self.local_path_bar, 1)
        left_layout.addLayout(left_header)

        # 本地导航按钮
        local_nav = QHBoxLayout()
        local_nav.setSpacing(2)
        self.local_up_btn = self._make_nav_btn("chevron-up", "上一级", self._local_go_up)
        self.local_home_btn = self._make_nav_btn("home", "主目录", self._local_go_home)
        self.local_refresh_btn = self._make_nav_btn("refresh-cw", "刷新", self._local_refresh)
        self.local_bookmark_btn = self._make_nav_btn("star", "收藏当前路径", self._toggle_local_bookmark)
        self.local_bookmark_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        self.local_bookmark_btn.customContextMenuRequested.connect(
            lambda pos: self._show_bookmark_menu(
                self.local_bookmark_btn, pos, self._local_bookmarks, self._set_local_root
            )
        )
        local_nav.addWidget(self.local_up_btn)
        local_nav.addWidget(self.local_home_btn)
        local_nav.addWidget(self.local_refresh_btn)
        local_nav.addWidget(self.local_bookmark_btn)
        local_nav.addStretch()
        left_layout.addLayout(local_nav)

        self.local_tree = DragDropTreeView()
        self.local_tree.set_role("local")
        self._local_model = QFileSystemModel()
        self._local_model.setRootPath(QDir.rootPath())
        self._local_model.setFilter(
            QDir.AllEntries | QDir.NoDotAndDotDot | QDir.Hidden
        )
        # 代理模型: 英文列名 → 中文，大小列右对齐
        self._local_proxy = LocalFileProxyModel()
        self._local_proxy.setSourceModel(self._local_model)
        self.local_tree.setModel(self._local_proxy)
        self._set_local_root(os.path.expanduser("~"))
        # 隐藏"类型"列，其余三列均匀分布
        self.local_tree.setColumnHidden(2, True)
        header = self.local_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)  # 名称
        header.setSectionResizeMode(1, QHeaderView.Stretch)  # 大小
        header.setSectionResizeMode(3, QHeaderView.Stretch)  # 修改时间
        header.setDefaultAlignment(Qt.AlignLeft)
        self.local_tree.setSortingEnabled(True)
        self.local_tree.sortByColumn(0, Qt.AscendingOrder)
        left_layout.addWidget(self.local_tree)

        # 本地搜索过滤框
        self.local_search = QLineEdit()
        self.local_search.setPlaceholderText("搜索/过滤文件名...")
        self.local_search.setClearButtonEnabled(True)
        self.local_search.setToolTip("输入文字即时过滤当前目录中的文件")
        left_layout.addWidget(self.local_search)

        self.local_selection_label = QLabel("已选: 0 项")
        left_layout.addWidget(self.local_selection_label)

        splitter.addWidget(left_panel)

        # 右面板 — 远程
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 4, 4, 4)

        right_header = QHBoxLayout()
        right_header.addWidget(QLabel("远程"))
        self.remote_path_bar = QLineEdit(self._config["rk_base_path"])
        self.remote_path_bar.setToolTip("远程路径，回车刷新")
        right_header.addWidget(self.remote_path_bar, 1)
        right_layout.addLayout(right_header)

        # 远程导航按钮
        remote_nav = QHBoxLayout()
        remote_nav.setSpacing(2)
        self.remote_up_btn = self._make_nav_btn("chevron-up", "上一级", self._remote_go_up)
        self.remote_root_btn = self._make_nav_btn("home", "根目录", self._remote_go_root)
        self.remote_refresh_btn = self._make_nav_btn("refresh-cw", "刷新当前目录 (F5)", self._remote_refresh_current)
        self.remote_bookmark_btn = self._make_nav_btn("star", "收藏当前路径", self._toggle_remote_bookmark)
        self.remote_bookmark_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        self.remote_bookmark_btn.customContextMenuRequested.connect(
            lambda pos: self._show_bookmark_menu(
                self.remote_bookmark_btn, pos, self._remote_bookmarks, self._load_remote_path
            )
        )
        remote_nav.addWidget(self.remote_up_btn)
        remote_nav.addWidget(self.remote_root_btn)
        remote_nav.addWidget(self.remote_refresh_btn)
        remote_nav.addWidget(self.remote_bookmark_btn)
        remote_nav.addStretch()
        right_layout.addLayout(remote_nav)

        self.remote_tree = DragDropTreeView()
        self.remote_tree.set_role("remote")
        self.remote_tree.setModel(self._remote_proxy)
        self.remote_tree.setColumnWidth(0, 420)
        # 三列均匀分布 + 左对齐
        rheader = self.remote_tree.header()
        rheader.setStretchLastSection(False)
        rheader.setSectionResizeMode(0, QHeaderView.Stretch)
        rheader.setSectionResizeMode(1, QHeaderView.Stretch)
        rheader.setSectionResizeMode(2, QHeaderView.Stretch)
        rheader.setDefaultAlignment(Qt.AlignLeft)
        right_layout.addWidget(self.remote_tree)

        # 远程搜索过滤框
        self.remote_search = QLineEdit()
        self.remote_search.setPlaceholderText("搜索/过滤文件名...")
        self.remote_search.setClearButtonEnabled(True)
        self.remote_search.setToolTip("输入文字即时过滤远程目录中的文件")
        right_layout.addWidget(self.remote_search)

        self.remote_selection_label = QLabel("已选: 0 项")
        right_layout.addWidget(self.remote_selection_label)

        splitter.addWidget(right_panel)

        splitter.setSizes([550, 550])

        # 底部面板 — 任务列表 + 日志
        self._bottom_tabs = QTabWidget()
        self._bottom_tabs.setDocumentMode(True)
        self._bottom_tabs.setMinimumHeight(48)  # 允许拉伸到很小

        self.task_list = QListWidget()
        self._bottom_tabs.addTab(self.task_list, "任务")

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("DejaVu Sans Mono", 13))
        self._bottom_tabs.addTab(self.log_view, "日志")

        # 主布局：工具栏 → 垂直分割器(双栏树 / 底部面板可上下拉伸)
        main_splitter = QSplitter(Qt.Vertical)
        main_splitter.addWidget(splitter)
        main_splitter.addWidget(self._bottom_tabs)
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 0)
        main_splitter.setCollapsible(0, False)  # 树面板不可完全折叠
        main_splitter.setSizes([620, 170])
        self._main_splitter = main_splitter

        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(toolbar_widget, 0)
        main_layout.addWidget(main_splitter, 1)
        self.setCentralWidget(main_widget)

        # 状态栏
        self.status_bar = QStatusBar()
        self.tunnel_status_label = QLabel("● 未连接")
        self.tunnel_status_label.setStyleSheet("color: #f7768e;")
        self.status_bar.addPermanentWidget(self.tunnel_status_label)
        self.setStatusBar(self.status_bar)

        # 差异高亮代理: 本地/远程各一个实例
        self._local_diff = DiffHighlightDelegate("local", self)
        self._remote_diff = DiffHighlightDelegate("remote", self)
        self.local_tree.setItemDelegate(self._local_diff)
        self.remote_tree.setItemDelegate(self._remote_diff)

    # ─── 信号连接 ─────────────────────────────────────────────

    def _connect_signals(self):
        # 工具栏按钮
        self.connect_btn.clicked.connect(self._connect_tunnel)
        self.disconnect_btn.clicked.connect(self._disconnect_tunnel)
        self.push_btn.clicked.connect(lambda: self._request_transfer("push"))
        self.pull_btn.clicked.connect(lambda: self._request_transfer("pull"))
        self.sync_btn.clicked.connect(lambda: self._request_transfer("push"))
        self.preview_btn.clicked.connect(self._request_preview)
        self.stop_btn.clicked.connect(self._stop_transfer)

        # 路径栏回车导航
        self.local_path_bar.returnPressed.connect(self._navigate_local)
        self.remote_path_bar.returnPressed.connect(self._remote_refresh)

        # 拖放传输
        self.local_tree.drop_requested.connect(self._on_drop_transfer)
        self.remote_tree.drop_requested.connect(self._on_drop_transfer)

        # 树视图双击导航
        self.local_tree.doubleClicked.connect(self._on_local_double_click)
        self.remote_tree.doubleClicked.connect(self._on_remote_double_click)

        # 选择变化
        self.local_tree.selectionModel().selectionChanged.connect(
            self._on_local_selection_changed
        )
        self.remote_tree.selectionModel().selectionChanged.connect(
            self._on_remote_selection_changed
        )

        # 远程树的展开信号
        self.remote_tree.expanded.connect(self._on_remote_expanded)

        # 搜索过滤
        self.local_search.textChanged.connect(self._on_local_search)
        self.remote_search.textChanged.connect(self._on_remote_search)

        # 右键上下文菜单 — 本地
        self.local_tree.context_new_folder.connect(self._local_context_new_folder)
        self.local_tree.context_rename.connect(self._local_context_rename)
        self.local_tree.context_delete.connect(self._local_context_delete)
        self.local_tree.context_refresh.connect(self._local_refresh)

        # 右键上下文菜单 — 远程
        self.remote_tree.context_new_folder.connect(self._remote_context_new_folder)
        self.remote_tree.context_rename.connect(self._remote_context_rename)
        self.remote_tree.context_delete.connect(self._remote_context_delete)
        self.remote_tree.context_refresh.connect(self._remote_refresh_current)

    # ─── 导航 ─────────────────────────────────────────────────

    def _make_nav_btn(self, icon_name: str, tooltip: str, slot) -> QPushButton:
        """创建紧凑导航按钮（Lucide 图标）"""
        btn = QPushButton("")
        btn.setToolTip(tooltip)
        btn.setFixedSize(44, 42)
        btn.setIcon(get_icon(icon_name, size=28, color="#a9b1d6"))
        btn.setStyleSheet(
            "QPushButton { background-color: #3b4261; color: #c0caf5; border: none; "
            "border-radius: 4px; padding: 0; }"
            "QPushButton:hover { background-color: #565f89; }"
        )
        btn.clicked.connect(slot)
        return btn

    def _set_local_root(self, path: str):
        """切换本地面板根目录"""
        self.local_tree.setRootIndex(
            self._local_proxy.mapFromSource(self._local_model.index(path))
        )
        self.local_path_bar.setText(path)
        self._update_bookmark_buttons()
        self._update_diff_highlighting()

    def _load_remote_path(self, path: str):
        """加载远程路径作为面板根目录（硬刷新）"""
        self._tree_root_path = path
        self.remote_path_bar.setText(path)
        self._remote_fs.set_root_path(path)
        self.remote_tree.expandAll()
        self._update_bookmark_buttons()
        self._update_diff_highlighting()

    # ─── 书签 ─────────────────────────────────────────────────

    def _load_bookmarks(self):
        settings = QSettings("sshtool", "bookmarks")
        try:
            local = settings.value("local_bookmarks", "[]")
            remote = settings.value("remote_bookmarks", "[]")
            self._local_bookmarks = json.loads(local) if local else []
            self._remote_bookmarks = json.loads(remote) if remote else []
        except (json.JSONDecodeError, TypeError):
            self._local_bookmarks = []
            self._remote_bookmarks = []

    def _save_bookmarks(self):
        settings = QSettings("sshtool", "bookmarks")
        settings.setValue("local_bookmarks", json.dumps(self._local_bookmarks))
        settings.setValue("remote_bookmarks", json.dumps(self._remote_bookmarks))

    def _toggle_local_bookmark(self):
        path = self.local_path_bar.text().strip()
        if not path:
            return
        if path in self._local_bookmarks:
            self._local_bookmarks.remove(path)
        else:
            self._local_bookmarks.append(path)
        self._save_bookmarks()
        self._update_bookmark_buttons()

    def _toggle_remote_bookmark(self):
        path = self.remote_path_bar.text().strip()
        if not path:
            return
        if path in self._remote_bookmarks:
            self._remote_bookmarks.remove(path)
        else:
            self._remote_bookmarks.append(path)
        self._save_bookmarks()
        self._update_bookmark_buttons()

    def _update_bookmark_buttons(self):
        if not hasattr(self, "remote_path_bar"):
            return  # _build_ui 早期调用, 远程面板尚未创建
        local = self.local_path_bar.text().strip()
        remote = self.remote_path_bar.text().strip()
        self.local_bookmark_btn.setIcon(
            get_icon("star-fill", size=28, color="#e0af68") if local in self._local_bookmarks
            else get_icon("star", size=28, color="#a9b1d6")
        )
        self.local_bookmark_btn.setToolTip(
            "取消收藏" if local in self._local_bookmarks else "收藏当前路径"
        )
        self.remote_bookmark_btn.setIcon(
            get_icon("star-fill", size=28, color="#e0af68") if remote in self._remote_bookmarks
            else get_icon("star", size=28, color="#a9b1d6")
        )
        self.remote_bookmark_btn.setToolTip(
            "取消收藏" if remote in self._remote_bookmarks else "收藏当前路径"
        )
        self._rebuild_bookmark_menu()

    def _show_bookmark_menu(self, btn, pos, bookmarks, navigate_cb):
        menu = QMenu()
        if bookmarks:
            for p in bookmarks:
                action = menu.addAction(p)
                action.triggered.connect(lambda checked, path=p: navigate_cb(path))
            menu.addSeparator()
        else:
            menu.addAction("（暂无书签）").setEnabled(False)
            menu.addSeparator()
        menu.addAction("清除全部书签", lambda: self._clear_bookmarks(bookmarks))
        menu.exec_(btn.mapToGlobal(pos))

    def _clear_bookmarks(self, bookmarks):
        bookmarks.clear()
        self._save_bookmarks()
        self._update_bookmark_buttons()

    def _rebuild_bookmark_menu(self):
        """重建菜单栏「书签」菜单，列出本地和远程书签"""
        menu = self._bookmarks_menu
        menu.clear()

        # 本地书签
        local_header = menu.addAction("📁 本地")
        local_header.setEnabled(False)
        if self._local_bookmarks:
            for p in self._local_bookmarks:
                action = menu.addAction(f"    {p}")
                action.triggered.connect(lambda checked, path=p: self._set_local_root(path))
        else:
            empty = menu.addAction("    （暂无）")
            empty.setEnabled(False)

        menu.addSeparator()

        # 远程书签
        remote_header = menu.addAction("🌐 远程")
        remote_header.setEnabled(False)
        if self._remote_bookmarks:
            for p in self._remote_bookmarks:
                action = menu.addAction(f"    {p}")
                action.triggered.connect(lambda checked, path=p: self._load_remote_path(path))
        else:
            empty = menu.addAction("    （暂无）")
            empty.setEnabled(False)

        menu.addSeparator()
        menu.addAction("清除全部书签", lambda: (
            self._local_bookmarks.clear(),
            self._remote_bookmarks.clear(),
            self._save_bookmarks(),
            self._update_bookmark_buttons(),
        ))

    def _navigate_local(self):
        path = self.local_path_bar.text().strip()
        if os.path.isdir(path):
            self._set_local_root(path)
        else:
            self._log(f"路径不存在: {path}")

    def _local_go_up(self):
        src_idx = self._local_proxy.mapToSource(self.local_tree.rootIndex())
        parent_idx = src_idx.parent()
        if parent_idx.isValid():
            self._set_local_root(self._local_model.filePath(parent_idx))

    def _local_go_home(self):
        self._set_local_root(os.path.expanduser("~"))

    def _local_refresh(self):
        path = self._local_model.filePath(
            self._local_proxy.mapToSource(self.local_tree.rootIndex())
        )
        self._local_model.setRootPath("")  # 触发刷新
        self._local_model.setRootPath(QDir.rootPath())
        self._set_local_root(path)

    def _remote_go_up(self):
        current = self.remote_path_bar.text().strip()
        parent = os.path.dirname(current) if current else "/"
        if parent and parent != current:
            self._load_remote_path(parent)

    def _remote_go_root(self):
        self._load_remote_path(self._config["rk_base_path"])

    def _remote_refresh(self):
        """完全重置远程树（硬刷新）—— 路径栏回车时使用"""
        self._load_remote_path(self.remote_path_bar.text().strip())

    def _remote_refresh_current(self):
        """刷新当前远程目录，保留整棵树的展开状态（软刷新）。

        仅失效当前路径栏目录的缓存并重新获取其子节点，
        不清除其他目录的缓存。由 F5 快捷键和刷新按钮触发。

        若当前路径不在模型树中（既不是根也不是已展开的子目录），
        回退到硬刷新 _load_remote_path。
        """
        path = self.remote_path_bar.text().strip()
        if not path or not self._tunnel_connected:
            return

        item = self._remote_fs.find_item_for_path(path)
        if item is not None and self._remote_fs.is_dir(item):
            # 路径是树中已展开的子目录 → 局部刷新
            self._remote_fs.refresh_directory(path, item)
            self._log(f"已刷新目录: {path}")
        elif self._is_tree_root_path(path):
            # 路径就是树的根 → 使用 invisibleRootItem 刷新
            self._remote_fs.refresh_directory(
                path, self._remote_fs.model.invisibleRootItem()
            )
            self._log(f"已刷新根目录: {path}")
        else:
            # 路径不在当前树中 → 回退到硬刷新
            self._load_remote_path(path)

        self._update_diff_highlighting()

    def _is_tree_root_path(self, path: str) -> bool:
        """判断给定路径是否为当前树的根路径"""
        root = self._tree_root_path.rstrip("/") or "/"
        target = path.rstrip("/") or "/"
        return root == target

    def _on_local_double_click(self, index: QModelIndex):
        src_index = self._local_proxy.mapToSource(index)
        path = self._local_model.filePath(src_index)
        if os.path.isdir(path):
            self._set_local_root(path)

    def _on_remote_double_click(self, index: QModelIndex):
        src_index = self._remote_proxy.mapToSource(index)
        item = self._remote_fs.model.itemFromIndex(src_index)
        if item is None or not self._remote_fs.is_dir(item):
            return
        path = self._remote_fs.get_full_path(item)
        self._load_remote_path(path)

    def _on_remote_expanded(self, index: QModelIndex):
        src_index = self._remote_proxy.mapToSource(index)
        item = self._remote_fs.model.itemFromIndex(src_index)
        if item is None:
            return
        path = self._remote_fs.get_full_path(item)
        if self._remote_fs.is_dir(item) and not self._remote_fs.has_children_cached(path):
            self._remote_fs.expand_directory(path, item)
        self._update_diff_highlighting()

    # ─── 选择 ─────────────────────────────────────────────────

    def _on_local_selection_changed(self):
        self._local_paths = set()
        for idx in self.local_tree.selectionModel().selectedRows():
            path = self._local_model.filePath(
                self._local_proxy.mapToSource(idx)
            )
            if path:
                self._local_paths.add(path)
        self.local_selection_label.setText(f"已选: {len(self._local_paths)} 项")

    def _on_remote_selection_changed(self):
        self._remote_paths = set()
        for idx in self.remote_tree.selectionModel().selectedRows():
            src_idx = self._remote_proxy.mapToSource(idx)
            item = self._remote_fs.model.itemFromIndex(src_idx)
            if item:
                path = self._remote_fs.get_full_path(item)
                if path:
                    self._remote_paths.add(path)
        self.remote_selection_label.setText(f"已选: {len(self._remote_paths)} 项")

    # ─── 搜索过滤 ─────────────────────────────────────────────

    def _on_local_search(self, text: str):
        """本地文件搜索过滤 —— 即时隐藏不匹配的行"""
        self._local_proxy.setFilterFixedString(text)

    def _on_remote_search(self, text: str):
        """远程文件搜索过滤 —— 即时隐藏不匹配的行"""
        self._remote_proxy.setFilterFixedString(text)

    # ─── 右键上下文菜单 ─────────────────────────────────────

    def _local_context_new_folder(self, parent_path: str):
        """在本地指定目录中新建文件夹"""
        name, ok = QInputDialog.getText(
            self, "新建文件夹", "文件夹名称:", QLineEdit.Normal, "")
        name = name.strip() if ok else ""
        if not name:
            return
        new_path = os.path.join(parent_path, name)
        try:
            os.makedirs(new_path, exist_ok=False)
            self._log(f"✓ 已创建本地目录: {new_path}")
            self._local_refresh()
        except FileExistsError:
            QMessageBox.warning(self, "提示", f"目录已存在: {name}")
        except OSError as e:
            QMessageBox.critical(self, "错误", f"创建目录失败:\n{e}")

    def _local_context_rename(self, old_path: str):
        """重命名本地文件或目录"""
        old_name = os.path.basename(old_path)
        name, ok = QInputDialog.getText(
            self, "重命名", "新名称:", QLineEdit.Normal, old_name)
        name = name.strip() if ok else ""
        if not name or name == old_name:
            return
        new_path = os.path.join(os.path.dirname(old_path), name)
        try:
            os.rename(old_path, new_path)
            self._log(f"✓ 已重命名: {old_name} → {name}")
            self._local_refresh()
        except OSError as e:
            QMessageBox.critical(self, "错误", f"重命名失败:\n{e}")

    def _local_context_delete(self, paths: list):
        """删除本地文件或目录（含确认对话框）"""
        if not paths:
            return
        names = "\n".join(f"  • {p}" for p in paths)
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除以下 {len(paths)} 项吗？\n\n{names}\n\n此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        for p in paths:
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
                self._log(f"✓ 已删除: {p}")
            except OSError as e:
                QMessageBox.critical(self, "错误", f"删除失败:\n{p}\n{e}")
        self._local_refresh()

    def _remote_context_new_folder(self, parent_path: str):
        """在远程指定目录中新建文件夹"""
        if not self._tunnel_connected:
            QMessageBox.warning(self, "提示", "请先连接远程隧道")
            return
        name, ok = QInputDialog.getText(
            self, "新建文件夹", "文件夹名称:", QLineEdit.Normal, "")
        name = name.strip() if ok else ""
        if not name:
            return
        remote_path = f"{parent_path.rstrip('/')}/{name}"
        cmd = f"mkdir -p {shlex.quote(remote_path)}"
        try:
            result = run_ssh(self._config, cmd)
            if result.returncode == 0:
                self._log(f"✓ 已创建远程目录: {remote_path}")
                self._remote_refresh_current()
            else:
                QMessageBox.critical(
                    self, "错误",
                    f"创建远程目录失败:\n{result.stderr.strip() or '未知错误'}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"SSH 命令执行失败:\n{e}")

    def _remote_context_rename(self, old_path: str):
        """重命名远程文件或目录"""
        if not self._tunnel_connected:
            QMessageBox.warning(self, "提示", "请先连接远程隧道")
            return
        old_name = os.path.basename(old_path)
        name, ok = QInputDialog.getText(
            self, "重命名", "新名称:", QLineEdit.Normal, old_name)
        name = name.strip() if ok else ""
        if not name or name == old_name:
            return
        new_path = os.path.join(os.path.dirname(old_path), name)
        cmd = f"mv {shlex.quote(old_path)} {shlex.quote(new_path)}"
        try:
            result = run_ssh(self._config, cmd)
            if result.returncode == 0:
                self._log(f"✓ 已重命名: {old_name} → {name}")
                self._remote_refresh_current()
            else:
                QMessageBox.critical(
                    self, "错误",
                    f"重命名失败:\n{result.stderr.strip() or '未知错误'}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"SSH 命令执行失败:\n{e}")

    def _remote_context_delete(self, paths: list):
        """删除远程文件或目录（含确认对话框）"""
        if not self._tunnel_connected:
            QMessageBox.warning(self, "提示", "请先连接远程隧道")
            return
        if not paths:
            return
        names = "\n".join(f"  • {p}" for p in paths)
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除以下 {len(paths)} 个远程项目吗？\n\n{names}\n\n此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        for p in paths:
            # 使用 rm -rf 以同时处理文件和目录
            cmd = f"rm -rf {shlex.quote(p)}"
            try:
                result = run_ssh(self._config, cmd)
                if result.returncode == 0:
                    self._log(f"✓ 已删除远程项目: {p}")
                else:
                    QMessageBox.critical(
                        self, "错误",
                        f"删除失败:\n{p}\n{result.stderr.strip() or '未知错误'}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"SSH 命令执行失败:\n{p}\n{e}")
        self._remote_refresh_current()

    # ─── 传输请求 ─────────────────────────────────────────────

    def _on_drop_transfer(self, sources: list, dest: str, direction: str):
        """从拖放触发的传输请求: 目标未指定时回退到对端当前目录"""
        if not sources:
            return
        if not dest:
            # 拖到空白区域 → 使用对端面板当前目录
            dest = (self.remote_path_bar.text().strip() if direction == "push"
                    else self.local_path_bar.text().strip())
            if not dest:
                QMessageBox.warning(self, "提示", "无法确定目标目录")
                return
        self._show_transfer_dialog(sources, dest, direction)

    def _request_transfer(self, direction: str):
        """用户请求传输（从按钮触发）"""
        if direction == "push":
            sources = list(self._local_paths)
            if not sources:
                QMessageBox.warning(self, "提示", "请先在左侧选择要传输的文件或文件夹")
                return
            dest = self.remote_path_bar.text().strip()
        else:
            sources = list(self._remote_paths)
            if not sources:
                QMessageBox.warning(self, "提示", "请先在右侧选择要传输的文件或文件夹")
                return
            dest = self.local_path_bar.text().strip()

        self._show_transfer_dialog(sources, dest, direction)

    def _request_preview(self):
        """预览传输变更: 先确认方向，再确认选项，然后执行 dry-run 并展示预览对话框"""
        # 根据选中内容自动判断方向
        if self._local_paths:
            direction = "push"
            sources = list(self._local_paths)
            dest = self.remote_path_bar.text().strip()
        elif self._remote_paths:
            direction = "pull"
            sources = list(self._remote_paths)
            dest = self.local_path_bar.text().strip()
        else:
            QMessageBox.warning(self, "提示", "请先在左侧或右侧选择要预览传输的文件或文件夹")
            return

        # 显示选项对话框（与正常传输相同）
        dlg = RsyncOptionsDialog(
            self._default_rsync_opts, sources, dest, direction, self
        )
        if dlg.exec_() != QDialog.Accepted:
            self._log("预览传输已取消")
            return

        rsync_opts = dlg.get_options()

        if dlg.save_as_default():
            self._default_rsync_opts = rsync_opts
            self._log("已保存为默认 Rsync 选项")

        # 以 dry_run 模式启动传输，等待预览结果
        self._queue.add_task(
            sources, dest, direction, rsync_opts, dry_run=True,
        )

    def _show_transfer_dialog(self, sources: list, dest: str, direction: str):
        """显示传输确认对话框"""
        dlg = RsyncOptionsDialog(
            self._default_rsync_opts, sources, dest, direction, self
        )
        if dlg.exec_() != QDialog.Accepted:
            self._log("传输已取消")
            return

        rsync_opts = dlg.get_options()

        if dlg.save_as_default():
            self._default_rsync_opts = rsync_opts
            self._log("已保存为默认 Rsync 选项")

        self._start_transfer(sources, dest, direction, rsync_opts)

    def _start_transfer(self, sources: list, dest: str,
                        direction: str, rsync_opts: RsyncOptions):
        """启动传输: 加入后台队列执行, 不阻塞浏览与继续添加任务"""
        self._queue.add_task(
            sources, dest, direction, rsync_opts,
            dry_run=self.dry_run_cb.isChecked(),
        )

    def _stop_transfer(self):
        self._queue.cancel_running()
        self._log("正在取消当前任务...")

    def _on_preview_result(self, task, preview_data: dict):
        """接收 dry-run 预览结果，弹出预览对话框"""
        total = sum(len(v) for v in preview_data.values())
        if total == 0:
            QMessageBox.information(self, "传输预览", "无变更 — 两端文件已完全同步。")
            return
        dlg = TransferPreviewDialog(preview_data, task.direction, self)
        dlg.exec_()

    def _on_tunnel_lost(self):
        """TransferEngine 检测到隧道断开: 异步自动重连, 结果由 _on_connect_done 记录"""
        self._log("⚠ 隧道已断开，正在自动重连...")
        self._tunnel_connected = False
        self._update_tunnel_buttons()
        if not self._connect_tunnel(silent=True):
            self._log("✗ 自动重连未启动，请手动检查连接配置")

    # ─── 任务队列 ─────────────────────────────────────────────

    def _connect_queue(self):
        q = self._queue
        q.task_added.connect(self._on_task_added)
        q.task_started.connect(self._on_task_started)
        q.task_progress.connect(self._on_task_progress)
        q.task_log.connect(self._on_task_log)
        q.task_finished.connect(self._on_task_finished)
        q.tunnel_lost.connect(self._on_tunnel_lost)
        q.preview_result.connect(self._on_preview_result)

    def _on_task_added(self, task):
        row = TaskRowWidget(task)
        row.cancel_requested.connect(self._queue.cancel)
        item = QListWidgetItem()
        item.setSizeHint(row.sizeHint())
        self.task_list.addItem(item)
        self.task_list.setItemWidget(item, row)
        self._task_rows[task.id] = row
        self._update_task_tab_label()

    def _on_task_started(self, task):
        row = self._task_rows.get(task.id)
        if row:
            row.set_status("传输中", "#7aa2f7")
        self._update_stop_button()

    def _on_task_progress(self, task, line: str, pct: int):
        row = self._task_rows.get(task.id)
        if row:
            row.set_progress(pct)
            if pct >= 0:
                row.set_status(f"传输中 {pct}%", "#7aa2f7")

    def _on_task_log(self, task, msg: str):
        self._log(f"[任务{task.id}] {msg}")

    def _on_task_finished(self, task):
        row = self._task_rows.get(task.id)
        if row:
            row.cancel_btn.setEnabled(False)
            if task.status == DONE:
                row.set_progress(100)
                row.set_status("✓ 完成", "#9ece6a")
            elif task.status == FAILED:
                row.set_status(f"✗ 失败: {task.message}", "#f7768e")
            else:
                row.set_status("已取消", "#565f89")

        if task.status == DONE:
            self.status_bar.showMessage("✓ 传输完成", 5000)
            # 局部刷新受影响的目录，保留树展开状态
            self._refresh_after_transfer(task.dest, task.direction)
        elif task.status == FAILED:
            self.status_bar.showMessage(f"✗ {task.message}", 8000)

        self._update_task_tab_label()
        self._update_stop_button()

    def _update_task_tab_label(self):
        count = self.task_list.count()
        self._bottom_tabs.setTabText(0, f"任务 ({count})")

    def _update_stop_button(self):
        self.stop_btn.setEnabled(self._queue.running)

    def _refresh_after_transfer(self, dest: str, direction: str):
        """传输完成后局部刷新受影响的目录，不清除整棵树。

        仅刷新传输目标目录（或当前路径栏目录作为回退），
        避免原本展开的树状结构瞬间闭合。
        """
        if direction == "push" and dest:
            # 推送 → 刷新远程目标目录
            item = self._remote_fs.find_item_for_path(dest)
            if item is not None and self._remote_fs.is_dir(item):
                self._remote_fs.refresh_directory(dest, item)
                self._log(f"已刷新远程目录: {dest}")
            elif self._is_tree_root_path(dest):
                # 目标路径是树的根
                self._remote_fs.refresh_directory(
                    dest, self._remote_fs.model.invisibleRootItem()
                )
                self._log(f"已刷新根目录: {dest}")
            else:
                # 目标目录不在当前树中，回退刷新当前路径栏目录
                self._remote_refresh_current()
        elif direction == "pull" and dest:
            # 拉取 → 刷新本地当前目录
            self._local_refresh()
        else:
            # 回退：刷新远程当前目录
            if self._tunnel_connected:
                self._remote_refresh_current()

        self._update_diff_highlighting()

    # ─── 隧道管理 ─────────────────────────────────────────────

    def _tunnel_state(self) -> str:
        """隧道状态: connected / connecting / disconnected"""
        if self._tunnel_connected:
            return "connected"
        if self._connecting:
            return "connecting"
        return "disconnected"

    def _connect_tunnel(self, silent: bool = False) -> bool:
        """异步建立 SSH 隧道并加载远程目录, 不阻塞界面。

        返回 True 表示已连接或已开始连接; 结果由 _on_connect_done 处理。
        """
        if self._tunnel_connected:
            if not silent:
                self._log("隧道已连接")
            return True
        if self._connecting:
            return False
        self._connecting = True
        self._connect_aborted = False
        if not silent:
            self._log("正在建立 SSH 隧道...")
        self.tunnel_status_label.setText("● 连接中...")
        self.tunnel_status_label.setStyleSheet("color: #e0af68;")
        self._update_tunnel_buttons()

        self._connector = TunnelConnector(self._config, self._config["rk_base_path"], self)
        self._connector.tunnel_opened.connect(self._on_tunnel_opened)
        self._connector.finished_connect.connect(
            lambda ok, msg, entries, silent=silent:
                self._on_connect_done(ok, msg, entries, silent))
        self._connector.start()
        return True

    def _on_tunnel_opened(self, proc):
        self._tunnel_proc = proc

    def _on_connect_done(self, ok: bool, msg: str, entries, silent: bool):
        self._connecting = False
        self._connector = None
        self._update_tunnel_buttons()

        if self._connect_aborted:
            self._log("隧道连接已取消")
            self._tunnel_proc = None
            self._queue.tunnel_result(False)
            return

        if not ok:
            self._log(f"✗ {msg}")
            self.tunnel_status_label.setText("● 连接失败")
            self.tunnel_status_label.setStyleSheet("color: #f7768e;")
            self._tunnel_proc = None
            if not silent:
                QMessageBox.critical(self, "连接失败", msg)
            self._queue.tunnel_result(False)
            return

        # 成功
        self._tunnel_connected = True
        self.tunnel_status_label.setText("● 已连接")
        self.tunnel_status_label.setStyleSheet("color: #9ece6a;")
        self._update_tunnel_buttons()
        self._log(f"✓ 隧道已连接 (PID={self._tunnel_proc.pid})")
        self.status_bar.showMessage("隧道已连接 — 正在加载远程目录...", 3000)

        # 加载远程目录(列表已在后台线程预取, 这里只填充模型)
        self._remote_fs.config = self._config
        self._remote_fs.apply_root_listing(self._config["rk_base_path"], entries or [])
        self.remote_tree.expandAll()

        # 检查是否加载成功
        if self._remote_proxy.sourceModel().rowCount() == 0:
            self._log("⚠ 远程目录为空或无法访问，请检查远程基础路径")
        else:
            self._log(f"✓ 远程目录已加载: {self._config['rk_base_path']}")

        self._queue.tunnel_result(True)

    def _disconnect_tunnel(self):
        """断开通路(连接中则取消连接)"""
        if self._connecting and self._connector:
            self._connect_aborted = True
            self._connector.cancel()
        if hasattr(self, '_tunnel_proc') and self._tunnel_proc:
            try:
                self._tunnel_proc.terminate()
                self._tunnel_proc.wait(timeout=3)
            except Exception:
                pass
            self._tunnel_proc = None
        self._tunnel_connected = False
        self.tunnel_status_label.setText("● 未连接")
        self.tunnel_status_label.setStyleSheet("color: #f7768e;")
        self._update_tunnel_buttons()
        self._log("隧道已断开")

    def _update_tunnel_buttons(self):
        """更新连接/断开按钮状态"""
        self.connect_btn.setEnabled(not self._tunnel_connected and not self._connecting)
        self.disconnect_btn.setEnabled(self._tunnel_connected or self._connecting)

    def _test_connection(self):
        """测试连接：成功后询问是否保持连接"""
        self._disconnect_tunnel()  # 先断开现有隧道，避免端口冲突

        QMessageBox.information(self, "测试连接",
                                "正在测试连接...\n请等待结果（约 5-10 秒）")

        ok, msg = test_tunnel_flow(self._config)
        if not ok:
            QMessageBox.critical(self, "连接失败", msg)
            return

        reply = QMessageBox.question(
            self, "连接隧道",
            "是否保持隧道连接并加载远程目录?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self._connect_tunnel()

    # ─── 配置 ─────────────────────────────────────────────────

    def _reload_profile_combo(self):
        self._loading_profiles = True
        self.profile_combo.clear()
        self.profile_combo.addItems(self._config_mgr.profiles())
        self.profile_combo.setCurrentText(self._config_mgr.current_profile)
        self._loading_profiles = False

    def _apply_config(self, cfg: dict, reconnect: bool):
        """应用新环境配置; 原隧道已连接时自动重连"""
        self._config = cfg
        self._remote_fs.config = cfg
        self.remote_path_bar.setText(cfg["rk_base_path"])

        fs = clamp_font(cfg.get("font_size", 25))
        if fs != self._font_size:
            self._font_size = fs
            self._apply_font_size()

        if reconnect:
            self._disconnect_tunnel()
            self._connect_tunnel()

    def _on_profile_changed(self, name: str):
        if self._loading_profiles or not name:
            return
        mgr = self._config_mgr
        if name == mgr.current_profile:
            return
        # 先保存当前环境的修改, 再切换
        mgr.save_profile(mgr.current_profile, self._config)
        cfg = mgr.switch_profile(name)
        self._apply_config(cfg, reconnect=self._tunnel_connected)
        self._log(f"已切换到环境「{name}」")

    def _add_profile(self):
        name, ok = QInputDialog.getText(self, "新建环境", "环境名称:", QLineEdit.Normal, "")
        name = name.strip() if ok else ""
        if not name:
            return
        mgr = self._config_mgr
        if name in mgr.profiles():
            QMessageBox.warning(self, "提示", f"环境「{name}」已存在")
            return
        mgr.add_profile(name, self._config)  # 以当前配置为副本
        mgr.switch_profile(name)
        self._reload_profile_combo()
        self._log(f"已创建并切换到环境「{name}」")

    def _delete_profile(self):
        mgr = self._config_mgr
        if len(mgr.profiles()) <= 1:
            QMessageBox.warning(self, "提示", "至少需要保留一个环境")
            return
        name = mgr.current_profile
        if QMessageBox.question(
            self, "删除环境", f"确定删除环境「{name}」?",
            QMessageBox.Yes | QMessageBox.No
        ) != QMessageBox.Yes:
            return
        mgr.delete_profile(name)
        self._apply_config(mgr.config, reconnect=self._tunnel_connected)
        self._reload_profile_combo()
        self._log(f"已删除环境「{name}」, 当前环境「{mgr.current_profile}」")

    def _open_config_dialog(self):
        dlg = ConfigDialog(self._config, self)
        if dlg.exec_() == QDialog.Accepted:
            new_config = dlg.get_config()
            self._config_mgr.save(new_config)
            self._config = new_config
            self.remote_path_bar.setText(self._config["rk_base_path"])
            self._remote_fs.config = self._config
            self._log("配置已保存")

            # 配置变更后自动重连隧道
            was_connected = self._tunnel_connected
            self._disconnect_tunnel()
            if was_connected or QMessageBox.question(
                self, "连接", "配置已保存。是否立即连接隧道并加载远程目录?",
                QMessageBox.Yes | QMessageBox.No
            ) == QMessageBox.Yes:
                self._connect_tunnel()

    # ─── 字体缩放 ─────────────────────────────────────────────

    def _setup_shortcuts(self):
        """注册 Ctrl+= 放大, Ctrl+- 缩小, F5 刷新远程当前目录"""
        zoom_in = QShortcut("Ctrl+=", self)
        zoom_in.activated.connect(self._zoom_in)
        zoom_out = QShortcut("Ctrl+-", self)
        zoom_out.activated.connect(self._zoom_out)
        f5 = QShortcut("F5", self)
        f5.activated.connect(self._remote_refresh_current)

    def _zoom_in(self):
        self._zoom(+2)

    def _zoom_out(self):
        self._zoom(-2)

    def _zoom(self, delta: int):
        new_size = clamp_font(self._font_size + delta)
        if new_size != self._font_size:
            self._font_size = new_size
            self._apply_font_size()
            self._config["font_size"] = new_size
            self._config_mgr.save(self._config)
            self._log(f"字体大小: {new_size}px")

    def _apply_font_size(self):
        """重新应用样式表"""
        QApplication.instance().setStyleSheet(
            make_stylesheet(self._font_size)
        )

    # ─── 差异高亮 ─────────────────────────────────────────────

    def _get_local_file_info(self) -> dict:
        """获取本地当前目录下的文件信息。

        返回 {filename: (size, mtime_unix)}，仅包含文件（不含目录）。
        """
        root_idx = self.local_tree.rootIndex()
        if not root_idx.isValid():
            return {}
        src_idx = self._local_proxy.mapToSource(root_idx)
        model = self._local_model
        files = {}
        for row in range(model.rowCount(src_idx)):
            child_idx = model.index(row, 0, src_idx)
            fi = model.fileInfo(child_idx)
            if not fi.isDir():
                try:
                    mtime = int(fi.lastModified().toSecsSinceEpoch())
                except (ValueError, OSError):
                    mtime = 0
                files[fi.fileName()] = (fi.size(), mtime)
        return files

    def _get_remote_file_info(self) -> dict:
        """获取远程当前目录下的文件信息。

        返回 {filename: (size, mtime_unix)}，仅包含文件，依赖 RemoteFileSystemModel 缓存。
        """
        path = self.remote_path_bar.text().strip()
        if not path or not self._tunnel_connected:
            return {}
        entries = self._remote_fs._cache.get(path)
        if entries is None:
            return {}
        files = {}
        for name, is_dir, size, mtime in entries:
            if not is_dir:
                files[name] = (size, mtime)
        return files

    def _update_diff_highlighting(self):
        """对比本地/远程当前目录中的文件，更新两个面板的差异高亮数据。

        比较规则（按优先级）：
          1. 仅本地有 → DiffStatus.ONLY_LOCAL
          2. 仅远程有 → DiffStatus.ONLY_REMOTE
          3. 大小不同 → DiffStatus.SIZE_DIFFERS
          4. 本地较新（mtime 差 > 2s）→ DiffStatus.LOCAL_NEWER
          5. 远程较新（mtime 差 > 2s）→ DiffStatus.REMOTE_NEWER
          6. 一致 → DiffStatus.SAME
        """
        # 委托尚未创建时跳过（初始化阶段 _set_local_root 在 delegate 创建前调用）
        if not hasattr(self, '_local_diff') or not hasattr(self, '_remote_diff'):
            return

        local_files = self._get_local_file_info()
        remote_files = self._get_remote_file_info()

        all_names = set(local_files.keys()) | set(remote_files.keys())
        diff = {}
        for name in all_names:
            in_local = name in local_files
            in_remote = name in remote_files

            if in_local and not in_remote:
                diff[name] = DiffStatus.ONLY_LOCAL
            elif not in_local and in_remote:
                diff[name] = DiffStatus.ONLY_REMOTE
            else:
                l_size, l_mtime = local_files[name]
                r_size, r_mtime = remote_files[name]
                if l_size != r_size:
                    diff[name] = DiffStatus.SIZE_DIFFERS
                elif l_mtime > r_mtime + 2:
                    diff[name] = DiffStatus.LOCAL_NEWER
                elif r_mtime > l_mtime + 2:
                    diff[name] = DiffStatus.REMOTE_NEWER
                else:
                    diff[name] = DiffStatus.SAME

        self._local_diff.set_diff_data(diff)
        self._remote_diff.set_diff_data(diff)
        # 触发重绘
        self.local_tree.viewport().update()
        self.remote_tree.viewport().update()

    # ─── 日志 ─────────────────────────────────────────────────

    def _log(self, msg: str):
        self.log_view.append(msg)
        # 滚动到底部
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_view.setTextCursor(cursor)

    # ─── 生命周期 ─────────────────────────────────────────────

    def closeEvent(self, event):
        # 保存窗口几何与分割器状态
        settings = QSettings("sshtool", "gui")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("splitter_state", self._main_splitter.saveState())

        # 取消进行中的隧道连接
        if self._connecting and self._connector:
            self._connect_aborted = True
            self._connector.cancel()
            self._connector.wait(3000)

        # 等待后台目录获取结束
        self._remote_fs.shutdown(3000)

        # 停止所有传输任务
        self._queue.shutdown(3000)

        # 断开隧道
        self._disconnect_tunnel()

        event.accept()


# ===================================================================
# 入口
# ===================================================================

def _check_sshpass():
    """检查 sshpass 是否已安装，缺失则弹出提示并退出"""
    if shutil.which("sshpass"):
        return
    QMessageBox.critical(
        None, "缺少依赖",
        "未找到 sshpass，SSH 连接功能无法使用。\n\n"
        "请安装后重试：\n"
        "  sudo apt install sshpass\n\n"
        "安装完成后重新启动本程序。",
    )
    sys.exit(1)


def main():
    app = QApplication(sys.argv)

    # ── 环境自检：sshpass ──────────────────────────────────────
    _check_sshpass()

    app.setStyle("Fusion")

    # 从配置加载字体大小
    mgr = ConfigManager()
    cfg = mgr.load()
    app.setStyleSheet(make_stylesheet(clamp_font(cfg.get("font_size", 25))))

    # CLI 模式
    if "--check-config" in sys.argv:
        errors = mgr.validate(cfg)
        if errors:
            print("配置存在问题:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)
        print("配置有效:")
        for k, v in cfg.items():
            if "pass" in k:
                print(f"  {k}: ***")
            else:
                print(f"  {k}: {v}")
        sys.exit(0)

    if "--test-transfer" in sys.argv:
        idx = sys.argv.index("--test-transfer")
        if idx + 2 < len(sys.argv):
            src, dst = sys.argv[idx + 1], sys.argv[idx + 2]
            print(f"测试传输: {src} → {dst}")
            engine = TransferEngine(
                cfg, RsyncOptions.preset_full(),
                [src], dst, "push", dry_run=True,
            )
            engine.log_message.connect(lambda m: print(m))
            engine.transfer_done.connect(lambda ok, msg: print(f"Done: {ok} - {msg}"))
            engine.run()
            engine.wait()
        else:
            print("用法: python3 sshtool_gui.py --test-transfer <src> <dst>")
        sys.exit(0)

    # GUI 模式
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
