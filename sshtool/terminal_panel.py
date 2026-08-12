"""TerminalManager — 多终端管理面板 (标签页 + 终端列表侧栏)"""

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QPushButton, QSplitter, QStackedWidget, QTabWidget,
    QVBoxLayout, QWidget,
)

from .icons import get_icon
from .terminal_display import TerminalDisplay
from .terminal_session import (
    LocalTerminalSession,
    make_gateway_session,
    make_remote_session,
)


# ─── 终端标签页常量 ────────────────────────────────────────────

TERM_LOCAL = "local"
TERM_REMOTE = "remote"
TERM_GATEWAY = "gateway"


# ─── TerminalTab ──────────────────────────────────────────────

class TerminalTab(QWidget):
    """单个终端标签页: 包含 TerminalDisplay + 会话管理"""

    title_changed = pyqtSignal(object, str)  # (self, new_title)

    def __init__(self, term_type: str, term_id: int,
                 config: dict | None = None, parent=None):
        super().__init__(parent)
        self._term_type = term_type
        self._term_id = term_id
        self._config = config
        self._session = None
        self._display: TerminalDisplay | None = None
        self._closed = False

        self._build_ui()
        self._start_session()

    @property
    def term_type(self) -> str:
        return self._term_type

    @property
    def term_id(self) -> int:
        return self._term_id

    @property
    def title(self) -> str:
        if self._term_type == TERM_LOCAL:
            return f"本地 #{self._term_id}"
        if self._term_type == TERM_GATEWAY:
            cfg = self._config or {}
            return (f"{cfg.get('gw_user', '?')}@"
                    f"{cfg.get('gw_host', '?')}")
        if self._config:
            return (f"{self._config.get('rk_user', '?')}@"
                    f"{self._config.get('rk_host', '?')}")
        return f"远程 #{self._term_id}"

    @property
    def display(self) -> TerminalDisplay | None:
        return self._display

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._display = TerminalDisplay(columns=80, rows=24, parent=self)
        self._display.input_data.connect(self._on_input)
        self._display.terminate_requested.connect(self.close_session)
        self._display.size_changed.connect(self._on_display_resized)
        layout.addWidget(self._display)

    def _start_session(self):
        """启动终端会话 (本地 / 网关 / 远程)"""
        cols, rows = self._display.term_size() if self._display else (80, 24)

        if self._term_type == TERM_LOCAL:
            self._session = LocalTerminalSession(cols, rows, parent=self)
        elif self._term_type == TERM_GATEWAY:
            if not self._config:
                self._display.feed(
                    "\r\n\x1b[31m[X] 缺少配置, 无法启动网关终端\x1b[0m\r\n".encode()
                )
                return
            self._session = make_gateway_session(
                self._config, cols, rows, parent=self,
            )
        else:
            if not self._config:
                self._display.feed(
                    "\r\n\x1b[31m[X] 缺少 SSH 配置, 无法启动远程终端\x1b[0m\r\n".encode()
                )
                return
            self._session = make_remote_session(
                self._config, cols, rows, parent=self,
            )

        self._session.data_received.connect(self._display.feed)
        self._session.session_closed.connect(self._on_session_closed)
        self._session.start()

    def _on_input(self, data: bytes):
        """用户输入 → 终端会话"""
        if self._session and not self._closed:
            self._session.write(data)

    def _on_session_closed(self, exit_code: int):
        """会话结束"""
        if self._display:
            self._display.session_closed(exit_code)
        self._session = None

    def resize_terminal(self, columns: int, rows: int):
        """调整终端尺寸"""
        if self._display:
            self._display.resize_terminal(columns, rows)
        if self._session:
            self._session.resize(columns, rows)

    def _on_display_resized(self):
        """控件尺寸变化 → 重算终端行列并通知会话"""
        display = self._display
        if not display:
            return
        vp = display.viewport()
        fm = display.fontMetrics()
        char_w = fm.horizontalAdvance("X")
        char_h = fm.height()
        if char_w > 0 and char_h > 0:
            cols = max(80, vp.width() // char_w)
            rows = max(24, vp.height() // char_h)
            self.resize_terminal(cols, rows)

    def close_session(self):
        """关闭终端会话"""
        self._closed = True
        if self._session:
            self._session.stop()
            self._session.wait(3000)
            self._session = None


# ─── TerminalManager ──────────────────────────────────────────

class TerminalManager(QWidget):
    """多终端管理面板: QTabWidget 标签页 + 右侧 QListWidget 列表

    提供:
      - 终端标签页 (QTabWidget), 可新增/关闭
      - 终端列表侧栏 (QListWidget), 点击切换
      - 工具栏按钮: 新建本地终端, 新建远程终端
    """

    terminal_count_changed = pyqtSignal(int)  # 终端数量变化

    def __init__(self, config_provider, tunnel_state_provider, parent=None):
        """
        Args:
            config_provider: callable → dict, 获取当前 SSH 配置
            tunnel_state_provider: callable → str ("connected"/"connecting"/"disconnected")
        """
        super().__init__(parent)
        self._config_provider = config_provider
        self._tunnel_state_provider = tunnel_state_provider
        self._terminals: list[TerminalTab] = []
        self._next_id = 1

        self._build_ui()

    @property
    def terminal_count(self) -> int:
        return len(self._terminals)

    def _build_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── 左侧: 终端区域 (工具栏 + 标签页) ──
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(4, 2, 4, 2)
        left_layout.setSpacing(2)

        # 工具栏
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)

        self._new_local_btn = QPushButton("")
        self._new_local_btn.setToolTip("新建本地终端 (bash)")
        self._new_local_btn.setFixedSize(32, 32)
        self._new_local_btn.setIcon(
            get_icon("monitor", size=18, color="#9ece6a"))
        self._new_local_btn.clicked.connect(lambda: self.add_terminal(TERM_LOCAL))
        toolbar.addWidget(self._new_local_btn)

        self._new_gw_btn = QPushButton("")
        self._new_gw_btn.setToolTip("新建网关终端 (直连 A733/bpx)")
        self._new_gw_btn.setFixedSize(32, 32)
        self._new_gw_btn.setIcon(
            get_icon("server", size=18, color="#e0af68"))
        self._new_gw_btn.clicked.connect(
            lambda: self.add_terminal(TERM_GATEWAY))
        toolbar.addWidget(self._new_gw_btn)

        self._new_remote_btn = QPushButton("")
        self._new_remote_btn.setToolTip("新建远程终端 (RK3588, 需隧道)")
        self._new_remote_btn.setFixedSize(32, 32)
        self._new_remote_btn.setIcon(
            get_icon("terminal", size=18, color="#7aa2f7"))
        self._new_remote_btn.clicked.connect(self._new_remote_with_check)
        toolbar.addWidget(self._new_remote_btn)

        toolbar.addSpacing(8)

        # 终端类型标签
        self._type_label = QLabel("终端")
        self._type_label.setStyleSheet("color: #a9b1d6; font-weight: bold;")
        toolbar.addWidget(self._type_label)

        toolbar.addStretch()

        # 关闭当前终端按钮
        self._close_btn = QPushButton("")
        self._close_btn.setToolTip("关闭当前终端")
        self._close_btn.setFixedSize(32, 32)
        self._close_btn.setIcon(
            get_icon("x", size=18, color="#f7768e"))
        self._close_btn.clicked.connect(self._close_current)
        toolbar.addWidget(self._close_btn)

        left_layout.addLayout(toolbar)

        # 标签页容器
        self._tab_widget = QTabWidget()
        self._tab_widget.setDocumentMode(True)
        self._tab_widget.setTabsClosable(True)
        self._tab_widget.tabCloseRequested.connect(self._on_tab_close_requested)
        self._tab_widget.currentChanged.connect(self._on_tab_changed)
        self._tab_widget.setMovable(True)
        left_layout.addWidget(self._tab_widget, 1)

        # ── 右侧: 终端列表侧栏 ──
        right_widget = QWidget()
        right_widget.setObjectName("terminalList")
        right_widget.setMinimumWidth(100)
        right_widget.setMaximumWidth(400)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 2, 2, 2)
        right_layout.setSpacing(2)

        list_header = QLabel(" 终端列表")
        list_header.setStyleSheet(
            "color: #a9b1d6; font-weight: bold; "
            "padding: 4px 8px; border-bottom: 1px solid #3b4261;"
        )
        right_layout.addWidget(list_header)

        self._terminal_list = QListWidget()
        self._terminal_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._terminal_list.customContextMenuRequested.connect(
            self._on_list_context_menu
        )
        self._terminal_list.currentRowChanged.connect(self._on_list_selection)
        right_layout.addWidget(self._terminal_list, 1)

        # ── 组合到水平分割器 ──
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([600, 180])
        main_layout.addWidget(splitter)

    # ─── 公开接口 ───────────────────────────────────────────────

    def add_terminal(self, term_type: str, config: dict | None = None) -> TerminalTab:
        """添加新终端标签页"""
        term_id = self._next_id
        self._next_id += 1

        if term_type == TERM_REMOTE and config is None:
            config = self._config_provider()

        tab = TerminalTab(term_type, term_id, config, parent=self)
        tab.title_changed.connect(self._on_title_changed)
        self._terminals.append(tab)

        # 添加到标签页
        idx = self._tab_widget.addTab(tab, tab.title)
        self._tab_widget.setCurrentIndex(idx)

        # 添加到列表
        item = QListWidgetItem(tab.title)
        item.setData(Qt.UserRole, id(tab))
        if term_type == TERM_LOCAL:
            item.setIcon(get_icon("monitor", size=16, color="#9ece6a"))
        elif term_type == TERM_GATEWAY:
            item.setIcon(get_icon("server", size=16, color="#e0af68"))
        else:
            item.setIcon(get_icon("globe", size=16, color="#7aa2f7"))
        self._terminal_list.addItem(item)
        self._terminal_list.setCurrentRow(self._terminal_list.count() - 1)

        self.terminal_count_changed.emit(len(self._terminals))
        self._update_button_states()

        # 自动聚焦新终端 (尺寸由 TerminalDisplay.showEvent/resizeEvent 自动处理)
        QTimer.singleShot(100, lambda: tab.display.setFocus()
                          if tab.display else None)

        return tab

    def active_display(self) -> TerminalDisplay | None:
        """返回当前活动终端的 TerminalDisplay"""
        tab = self._current_tab()
        return tab.display if tab else None

    def close_all(self):
        """关闭所有终端"""
        for tab in list(self._terminals):
            self._remove_terminal(tab)

    # ─── 内部: 终端生命周期 ────────────────────────────────────

    def _current_tab(self) -> TerminalTab | None:
        """返回当前选中的终端标签页"""
        widget = self._tab_widget.currentWidget()
        if isinstance(widget, TerminalTab):
            return widget
        return None

    def _find_tab_by_id(self, tab_id: int) -> TerminalTab | None:
        """通过内存 ID 查找终端"""
        for t in self._terminals:
            if id(t) == tab_id:
                return t
        return None

    def _remove_terminal(self, tab: TerminalTab):
        """移除并清理终端"""
        tab_id = id(tab)

        # 从标签页移除
        for i in range(self._tab_widget.count()):
            if self._tab_widget.widget(i) is tab:
                self._tab_widget.removeTab(i)
                break

        # 从列表移除
        for i in range(self._terminal_list.count()):
            item = self._terminal_list.item(i)
            if item.data(Qt.UserRole) == tab_id:
                self._terminal_list.takeItem(i)
                break

        # 从内部列表移除
        if tab in self._terminals:
            self._terminals.remove(tab)

        # 关闭会话
        tab.close_session()
        tab.deleteLater()

        self.terminal_count_changed.emit(len(self._terminals))
        self._update_button_states()

    # ─── 槽函数 ─────────────────────────────────────────────────

    def _new_remote_with_check(self):
        """创建远程终端前检查隧道状态"""
        state = self._tunnel_state_provider()
        if state != "connected":
            QMessageBox.warning(
                self, "提示",
                "远程终端需要先连接 SSH 隧道。\n请先点击「连接隧道」按钮建立连接。",
            )
            return
        self.add_terminal(TERM_REMOTE)

    def _on_tab_close_requested(self, index: int):
        """用户点击标签页关闭按钮"""
        tab = self._tab_widget.widget(index)
        if isinstance(tab, TerminalTab):
            self._remove_terminal(tab)

    def _on_tab_changed(self, index: int):
        """标签页切换 → 同步列表选中"""
        if index < 0:
            return
        tab = self._tab_widget.widget(index)
        if isinstance(tab, TerminalTab):
            tab_id = id(tab)
            for i in range(self._terminal_list.count()):
                item = self._terminal_list.item(i)
                if item.data(Qt.UserRole) == tab_id:
                    self._terminal_list.blockSignals(True)
                    self._terminal_list.setCurrentRow(i)
                    self._terminal_list.blockSignals(False)
                    break
            # 聚焦
            if tab.display:
                tab.display.setFocus()

    def _on_list_selection(self, row: int):
        """终端列表选择 → 切换标签页"""
        if row < 0:
            return
        item = self._terminal_list.item(row)
        if not item:
            return
        tab_id = item.data(Qt.UserRole)
        tab = self._find_tab_by_id(tab_id)
        if tab:
            for i in range(self._tab_widget.count()):
                if self._tab_widget.widget(i) is tab:
                    self._tab_widget.blockSignals(True)
                    self._tab_widget.setCurrentIndex(i)
                    self._tab_widget.blockSignals(False)
                    break
            if tab.display:
                tab.display.setFocus()

    def _on_title_changed(self, tab: TerminalTab, new_title: str):
        """终端标题变化 → 更新标签页和列表"""
        for i in range(self._tab_widget.count()):
            if self._tab_widget.widget(i) is tab:
                self._tab_widget.setTabText(i, new_title)
                break
        tab_id = id(tab)
        for i in range(self._terminal_list.count()):
            item = self._terminal_list.item(i)
            if item.data(Qt.UserRole) == tab_id:
                item.setText(new_title)
                break

    def _on_list_context_menu(self, pos):
        """终端列表右键菜单"""
        item = self._terminal_list.itemAt(pos)
        if not item:
            return
        tab_id = item.data(Qt.UserRole)
        tab = self._find_tab_by_id(tab_id)
        if not tab:
            return

        menu = QMenu(self)
        close_action = menu.addAction("关闭终端")
        close_action.triggered.connect(lambda: self._remove_terminal(tab))
        menu.addSeparator()
        rename_action = menu.addAction("重命名...")

        def _rename():
            from PyQt5.QtWidgets import QInputDialog, QLineEdit
            name, ok = QInputDialog.getText(
                self, "重命名终端", "名称:",
                QLineEdit.Normal, tab.title,
            )
            if ok and name.strip():
                tab.title_changed.emit(tab, name.strip())

        rename_action.triggered.connect(_rename)
        menu.exec_(self._terminal_list.mapToGlobal(pos))

    def _close_current(self):
        """关闭当前终端"""
        tab = self._current_tab()
        if tab:
            self._remove_terminal(tab)

    def _update_button_states(self):
        """更新按钮状态"""
        has_terminals = len(self._terminals) > 0
        self._close_btn.setEnabled(has_terminals)

        # 远程终端按钮状态取决于隧道
        state = self._tunnel_state_provider()
        self._new_remote_btn.setEnabled(state == "connected")
        self._new_remote_btn.setToolTip(
            "新建远程终端 (SSH)" if state == "connected"
            else "需要先连接 SSH 隧道"
        )

    # ─── resize 通知 ──────────────────────────────────────────

    def notify_resize(self):
        """通知所有终端重新计算尺寸 (在面板 resize 时调用)"""
        for tab in self._terminals:
            if tab.display:
                vp = tab.display.viewport()
                fm = tab.display.fontMetrics()
                char_w = fm.horizontalAdvance("X")
                char_h = fm.height()
                if char_w > 0 and char_h > 0:
                    cols = max(80, vp.width() // char_w)
                    rows = max(24, vp.height() // char_h)
                    tab.resize_terminal(cols, rows)

    def update_tunnel_state(self, state: str):
        """隧道状态变化时更新按钮"""
        self._update_button_states()
