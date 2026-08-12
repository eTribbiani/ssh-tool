"""MainWindow — 主窗口与程序入口"""

import os
import sys
import time
from typing import Optional

from PyQt5.QtCore import QDir, QModelIndex, QSettings, Qt
from PyQt5.QtGui import QFont, QTextCursor
from PyQt5.QtWidgets import (
    QAction, QApplication, QCheckBox, QDialog, QFileSystemModel, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QShortcut, QSplitter, QStatusBar, QTextEdit, QToolBar,
    QVBoxLayout, QWidget,
)

from .config import APP_NAME, ConfigManager, clamp_font, make_stylesheet
from .dialogs import ConfigDialog, RsyncOptionsDialog
from .remote_fs import RemoteFileSystemModel
from .rsync_options import RsyncOptions
from .ssh_utils import check_tunnel, open_tunnel, test_tunnel_flow
from .transfer_engine import TransferEngine
from .tree_views import DragDropTreeView, LocalFileProxyModel


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
        self._transfer_engine: Optional[TransferEngine] = None
        self._local_paths = set()    # 本地选中缓存
        self._remote_paths = set()   # 远程选中缓存

        # 字体大小
        self._font_size = clamp_font(self._config.get("font_size", 25))

        # 远程文件模型
        self._remote_fs = RemoteFileSystemModel(self._config)

        self._build_ui()
        self._connect_signals()
        self._setup_shortcuts()

        # 恢复窗口几何
        settings = QSettings("sshtool", "gui")
        geo = settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)

    # ─── UI 构建 ───────────────────────────────────────────────

    def _build_ui(self):
        # 菜单栏
        menubar = self.menuBar()

        config_menu = menubar.addMenu("连接配置")
        edit_config_action = QAction("编辑连接配置...", self)
        edit_config_action.triggered.connect(self._open_config_dialog)
        config_menu.addAction(edit_config_action)

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

        # 工具栏
        toolbar = QToolBar("传输")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.connect_btn = QPushButton("🔌 连接隧道")
        self.connect_btn.setToolTip("建立 SSH 隧道并加载远程目录")
        self.connect_btn.setStyleSheet(
            "QPushButton { background-color: #2a8c3c; }"
            "QPushButton:hover { background-color: #35a54a; }"
        )
        toolbar.addWidget(self.connect_btn)

        self.disconnect_btn = QPushButton("🔌 断开")
        self.disconnect_btn.setToolTip("断开 SSH 隧道")
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.setStyleSheet(
            "QPushButton { background-color: #8c3c2a; }"
            "QPushButton:hover { background-color: #a54a35; }"
        )
        toolbar.addWidget(self.disconnect_btn)

        toolbar.addSeparator()

        self.push_btn = QPushButton("← 推送 (本地→远程)")
        self.push_btn.setToolTip("将选中文件推送到远程")
        toolbar.addWidget(self.push_btn)

        self.pull_btn = QPushButton("→ 拉取 (远程→本地)")
        self.pull_btn.setToolTip("将选中文件拉取到本地")
        toolbar.addWidget(self.pull_btn)

        toolbar.addSeparator()

        self.dry_run_cb = QCheckBox("仅预览 (dry-run)")
        self.dry_run_cb.setToolTip("预览模式，不实际传输文件")
        toolbar.addWidget(self.dry_run_cb)

        self.sync_btn = QPushButton("▶ 开始传输")
        self.sync_btn.setToolTip("开始文件传输")
        toolbar.addWidget(self.sync_btn)

        self.stop_btn = QPushButton("■ 停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet(
            "QPushButton { background-color: #c44; }"
            "QPushButton:hover { background-color: #e55; }"
        )
        toolbar.addWidget(self.stop_btn)

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
        self.local_up_btn = self._make_nav_btn("⬆", "上一级", self._local_go_up)
        self.local_home_btn = self._make_nav_btn("⌂", "主目录", self._local_go_home)
        self.local_refresh_btn = self._make_nav_btn("↻", "刷新", self._local_refresh)
        local_nav.addWidget(self.local_up_btn)
        local_nav.addWidget(self.local_home_btn)
        local_nav.addWidget(self.local_refresh_btn)
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
        self.remote_up_btn = self._make_nav_btn("⬆", "上一级", self._remote_go_up)
        self.remote_root_btn = self._make_nav_btn("⌂", "根目录", self._remote_go_root)
        self.remote_refresh_btn = self._make_nav_btn("↻", "刷新", self._remote_refresh)
        remote_nav.addWidget(self.remote_up_btn)
        remote_nav.addWidget(self.remote_root_btn)
        remote_nav.addWidget(self.remote_refresh_btn)
        remote_nav.addStretch()
        right_layout.addLayout(remote_nav)

        self.remote_tree = DragDropTreeView()
        self.remote_tree.set_role("remote")
        self.remote_tree.setModel(self._remote_fs.model)
        self.remote_tree.setColumnWidth(0, 420)
        # 三列均匀分布 + 左对齐
        rheader = self.remote_tree.header()
        rheader.setStretchLastSection(False)
        rheader.setSectionResizeMode(0, QHeaderView.Stretch)
        rheader.setSectionResizeMode(1, QHeaderView.Stretch)
        rheader.setSectionResizeMode(2, QHeaderView.Stretch)
        rheader.setDefaultAlignment(Qt.AlignLeft)
        right_layout.addWidget(self.remote_tree)

        self.remote_selection_label = QLabel("已选: 0 项")
        right_layout.addWidget(self.remote_selection_label)

        splitter.addWidget(right_panel)

        splitter.setSizes([550, 550])

        # 底部面板 — 进度 + 日志
        bottom_panel = QWidget()
        bottom_layout = QVBoxLayout(bottom_panel)
        bottom_layout.setContentsMargins(4, 2, 4, 4)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        bottom_layout.addWidget(self.progress_bar)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(150)
        self.log_view.setFont(QFont("DejaVu Sans Mono", 13))
        bottom_layout.addWidget(self.log_view)

        # 放入主分割器下方
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(splitter, 1)
        main_layout.addWidget(bottom_panel, 0)
        self.setCentralWidget(main_widget)

        # 状态栏
        self.status_bar = QStatusBar()
        self.tunnel_status_label = QLabel("● 未连接")
        self.tunnel_status_label.setStyleSheet("color: #ff8888;")
        self.status_bar.addPermanentWidget(self.tunnel_status_label)
        self.setStatusBar(self.status_bar)

    # ─── 信号连接 ─────────────────────────────────────────────

    def _connect_signals(self):
        # 工具栏按钮
        self.connect_btn.clicked.connect(self._connect_tunnel)
        self.disconnect_btn.clicked.connect(self._disconnect_tunnel)
        self.push_btn.clicked.connect(lambda: self._request_transfer("push"))
        self.pull_btn.clicked.connect(lambda: self._request_transfer("pull"))
        self.sync_btn.clicked.connect(lambda: self._request_transfer("push"))
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

    # ─── 导航 ─────────────────────────────────────────────────

    def _make_nav_btn(self, text: str, tooltip: str, slot) -> QPushButton:
        """创建紧凑导航按钮"""
        btn = QPushButton(text)
        btn.setToolTip(tooltip)
        btn.setFixedSize(44, 42)
        btn.setStyleSheet(
            "QPushButton { background-color: #444; color: #ccc; border: none; "
            "border-radius: 3px; font-size: 30px; padding: 0; }"
            "QPushButton:hover { background-color: #555; }"
        )
        btn.clicked.connect(slot)
        return btn

    def _set_local_root(self, path: str):
        """切换本地面板根目录"""
        self.local_tree.setRootIndex(
            self._local_proxy.mapFromSource(self._local_model.index(path))
        )
        self.local_path_bar.setText(path)

    def _load_remote_path(self, path: str):
        """加载远程路径作为面板根目录"""
        self.remote_path_bar.setText(path)
        self._remote_fs.set_root_path(path)
        self.remote_tree.expandAll()

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
        self._load_remote_path(self.remote_path_bar.text().strip())

    def _on_local_double_click(self, index: QModelIndex):
        src_index = self._local_proxy.mapToSource(index)
        path = self._local_model.filePath(src_index)
        if os.path.isdir(path):
            self._set_local_root(path)

    def _on_remote_double_click(self, index: QModelIndex):
        item = self._remote_fs.model.itemFromIndex(index)
        if item is None or not self._remote_fs.is_dir(item):
            return
        path = self._remote_fs.get_full_path(item)
        self.remote_path_bar.setText(path)
        if not self._remote_fs.has_children_cached(path):
            self._remote_fs.expand_directory(path, item)
        self.remote_tree.expand(index)

    def _on_remote_expanded(self, index: QModelIndex):
        item = self._remote_fs.model.itemFromIndex(index)
        if item is None:
            return
        path = self._remote_fs.get_full_path(item)
        if self._remote_fs.is_dir(item) and not self._remote_fs.has_children_cached(path):
            self._remote_fs.expand_directory(path, item)

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
            item = self._remote_fs.model.itemFromIndex(idx)
            if item:
                path = self._remote_fs.get_full_path(item)
                if path:
                    self._remote_paths.add(path)
        self.remote_selection_label.setText(f"已选: {len(self._remote_paths)} 项")

    # ─── 传输请求 ─────────────────────────────────────────────

    def _on_drop_transfer(self, sources: list, dest: str, direction: str):
        """从拖放触发的传输请求"""
        if sources:
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
        """启动传输。隧道由 MainWindow 维护，TransferEngine 只做 rsync"""
        if self._transfer_engine and self._transfer_engine.isRunning():
            QMessageBox.warning(self, "提示", "已有传输在进行中")
            return

        # 确保隧道已连接
        if not self._tunnel_connected:
            self._log("隧道未连接，正在自动连接...")
            if not self._connect_tunnel(silent=True):
                QMessageBox.critical(self, "连接失败",
                                     "无法建立 SSH 隧道，请检查连接配置")
                return

        self._transfer_engine = TransferEngine(
            self._config, rsync_opts, sources, dest, direction,
            tunnel_port=self._config["tunnel_port"],
            dry_run=self.dry_run_cb.isChecked(),
        )
        self._transfer_engine.log_message.connect(self._log)
        self._transfer_engine.transfer_progress.connect(self._on_progress)
        self._transfer_engine.transfer_done.connect(self._on_transfer_done)
        self._transfer_engine.tunnel_lost.connect(self._on_tunnel_lost)

        self._set_transfer_ui(True)
        self._transfer_engine.start()

    def _stop_transfer(self):
        if self._transfer_engine and self._transfer_engine.isRunning():
            self._transfer_engine.cancel()
            self._log("正在取消传输...")

    def _on_tunnel_lost(self):
        """TransferEngine 检测到隧道断开"""
        self._log("⚠ 隧道已断开，正在自动重连...")
        self._tunnel_connected = False
        self._update_tunnel_buttons()
        if self._connect_tunnel(silent=True):
            self._log("✓ 隧道已自动重连，请重试传输")
        else:
            self._log("✗ 自动重连失败，请手动检查连接配置")

    def _on_progress(self, line: str, percent: int):
        self.progress_bar.setVisible(True)
        if percent >= 0:
            self.progress_bar.setValue(percent)
        elif self.progress_bar.value() == 0:
            self.progress_bar.setRange(0, 0)  # 不确定模式

    def _on_transfer_done(self, success: bool, message: str):
        self._set_transfer_ui(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        if success:
            self.progress_bar.setValue(100)
            self.status_bar.showMessage("✓ 传输完成", 5000)
            # 刷新远程目录
            self._remote_refresh()
        else:
            self.progress_bar.setValue(0)
            self.status_bar.showMessage(f"✗ {message}", 8000)

    def _set_transfer_ui(self, active: bool):
        self.sync_btn.setEnabled(not active)
        self.push_btn.setEnabled(not active)
        self.pull_btn.setEnabled(not active)
        self.stop_btn.setEnabled(active)
        if not active:
            self.progress_bar.setVisible(False)
            self.progress_bar.reset()

    # ─── 隧道管理 ─────────────────────────────────────────────

    def _connect_tunnel(self, silent: bool = False):
        """建立 SSH 隧道并加载远程目录。silent=True 时不弹错误框"""
        if self._tunnel_connected:
            if not silent:
                self._log("隧道已连接")
            return True
        if not silent:
            self._log("正在建立 SSH 隧道...")
        self.tunnel_status_label.setText("● 连接中...")
        self.tunnel_status_label.setStyleSheet("color: #ffaa44;")
        self._update_tunnel_buttons()

        cfg = self._config
        try:
            self._tunnel_proc = open_tunnel(cfg)
            time.sleep(2.0)
            if self._tunnel_proc.poll() is not None:
                stderr = self._tunnel_proc.stderr.read().decode(errors="replace")
                self._log(f"✗ 隧道建立失败: {stderr}")
                self.tunnel_status_label.setText("● 连接失败")
                self.tunnel_status_label.setStyleSheet("color: #ff4444;")
                self._tunnel_proc = None
                self._update_tunnel_buttons()
                if not silent:
                    QMessageBox.critical(self, "连接失败", f"SSH 隧道建立失败:\n{stderr}")
                return False

            # 验证隧道
            alive, msg = check_tunnel(cfg)
            if not alive:
                self._log(f"✗ 隧道验证失败: {msg}")
                self.tunnel_status_label.setText("● 验证失败")
                self.tunnel_status_label.setStyleSheet("color: #ff4444;")
                self._disconnect_tunnel()
                if not silent:
                    QMessageBox.critical(self, "连接失败", f"隧道验证失败:\n{msg}")
                return False

            # 成功
            self._tunnel_connected = True
            self.tunnel_status_label.setText("● 已连接")
            self.tunnel_status_label.setStyleSheet("color: #88ff88;")
            self._update_tunnel_buttons()
            self._log(f"✓ 隧道已连接 (PID={self._tunnel_proc.pid})")
            self.status_bar.showMessage("隧道已连接 — 正在加载远程目录...", 3000)

            # 加载远程目录
            self._remote_fs.config = cfg
            self._remote_fs.set_root_path(cfg["rk_base_path"])
            self.remote_tree.expandAll()

            # 检查是否加载成功
            if self._remote_fs.model.rowCount() == 0:
                self._log("⚠ 远程目录为空或无法访问，请检查远程基础路径")
            else:
                self._log(f"✓ 远程目录已加载: {cfg['rk_base_path']}")

            return True

        except Exception as e:
            self._log(f"✗ 隧道连接异常: {e}")
            self.tunnel_status_label.setText("● 连接失败")
            self.tunnel_status_label.setStyleSheet("color: #ff4444;")
            self._disconnect_tunnel()
            if not silent:
                QMessageBox.critical(self, "连接失败", str(e))
            return False

    def _disconnect_tunnel(self):
        """断开通路"""
        if hasattr(self, '_tunnel_proc') and self._tunnel_proc:
            try:
                self._tunnel_proc.terminate()
                self._tunnel_proc.wait(timeout=3)
            except Exception:
                pass
            self._tunnel_proc = None
        self._tunnel_connected = False
        self.tunnel_status_label.setText("● 未连接")
        self.tunnel_status_label.setStyleSheet("color: #ff8888;")
        self._update_tunnel_buttons()
        self._log("隧道已断开")

    def _update_tunnel_buttons(self):
        """更新连接/断开按钮状态"""
        self.connect_btn.setEnabled(not self._tunnel_connected)
        self.disconnect_btn.setEnabled(self._tunnel_connected)

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
        """注册 Ctrl+= 放大, Ctrl+- 缩小"""
        zoom_in = QShortcut("Ctrl+=", self)
        zoom_in.activated.connect(self._zoom_in)
        zoom_out = QShortcut("Ctrl+-", self)
        zoom_out.activated.connect(self._zoom_out)

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

    # ─── 日志 ─────────────────────────────────────────────────

    def _log(self, msg: str):
        self.log_view.append(msg)
        # 滚动到底部
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_view.setTextCursor(cursor)

    # ─── 生命周期 ─────────────────────────────────────────────

    def closeEvent(self, event):
        # 保存窗口几何
        settings = QSettings("sshtool", "gui")
        settings.setValue("geometry", self.saveGeometry())

        # 断开隧道
        self._disconnect_tunnel()

        # 停止传输
        if self._transfer_engine and self._transfer_engine.isRunning():
            self._transfer_engine.cancel()
            self._transfer_engine.wait(3000)

        event.accept()


# ===================================================================
# 入口
# ===================================================================

def main():
    app = QApplication(sys.argv)
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
