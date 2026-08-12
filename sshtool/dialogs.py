"""对话框: RsyncOptionsDialog(传输前选项) + ConfigDialog(连接配置)"""

from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTabWidget,
    QVBoxLayout, QWidget,
)

from .config import ConfigManager, EXCLUDE_PATTERNS
from .rsync_options import RsyncOptions
from .ssh_utils import direction_text, test_tunnel_flow


class RsyncOptionsDialog(QDialog):
    """每次传输前选择 rsync 选项的对话框"""

    PRESETS = {
        "完整同步": RsyncOptions.preset_full,
        "增量同步（无 --delete）": RsyncOptions.preset_no_delete,
        "最简同步（仅 -avz）": RsyncOptions.preset_minimal,
        "自定义": None,
    }

    def __init__(self, default_opts: RsyncOptions, sources: list,
                 dest: str, direction: str, parent=None):
        super().__init__(parent)
        self._opts = default_opts
        self._sources = sources
        self._dest = dest
        self._direction = direction
        self._building = False  # 防止信号循环

        self.setWindowTitle("确认传输")
        self.setMinimumWidth(550)
        self._build_ui()
        self._apply_options(self._opts)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── 传输摘要 ──
        summary_group = QGroupBox("传输信息")
        s_layout = QFormLayout(summary_group)

        s_layout.addRow("方向:", QLabel(direction_text(self._direction)))

        src_label = QLabel("\n".join(self._sources))
        src_label.setWordWrap(True)
        src_label.setStyleSheet("color: #6cb6ff;")
        s_layout.addRow("源:", src_label)

        dest_label = QLabel(self._dest)
        dest_label.setWordWrap(True)
        dest_label.setStyleSheet("color: #6cb6ff;")
        s_layout.addRow("目标:", dest_label)

        # 覆盖策略: 随选项实时更新
        self.strategy_label = QLabel()
        self.strategy_label.setWordWrap(True)
        self.strategy_label.setStyleSheet("color: #6cb6ff;")
        s_layout.addRow("覆盖策略:", self.strategy_label)

        layout.addWidget(summary_group)

        # ── Rsync 选项 ──
        opts_group = QGroupBox("Rsync 选项")
        opts_layout = QVBoxLayout(opts_group)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("预设模式:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(self.PRESETS.keys()))
        self.preset_combo.currentTextChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self.preset_combo, 1)
        preset_row.addStretch()
        opts_layout.addLayout(preset_row)

        basic_group = QGroupBox("基本选项")
        basic_layout = QHBoxLayout(basic_group)
        self.cb_archive = QCheckBox("-a (归档)")
        self.cb_verbose = QCheckBox("-v (详细)")
        self.cb_compress = QCheckBox("-z (压缩)")
        self.cb_delete = QCheckBox("--delete (删除多余)")
        for cb in [self.cb_archive, self.cb_verbose, self.cb_compress, self.cb_delete]:
            cb.toggled.connect(self._on_option_toggled)
            basic_layout.addWidget(cb)
        opts_layout.addWidget(basic_group)

        # 安全选项: 跳过已存在的目标文件
        self.cb_ignore_existing = QCheckBox("--ignore-existing (跳过已存在的目标文件)")
        self.cb_ignore_existing.toggled.connect(self._on_option_toggled)
        opts_layout.addWidget(self.cb_ignore_existing)

        exclude_group = QGroupBox("排除项")
        exclude_layout = QHBoxLayout(exclude_group)
        exclude_layout.setSpacing(4)

        self._exclude_checks = {}
        for i, (attr, label) in enumerate(EXCLUDE_PATTERNS):
            cb = QCheckBox(label)
            cb.toggled.connect(self._on_option_toggled)
            self._exclude_checks[attr] = cb
            exclude_layout.addWidget(cb)
            if i == 5:  # 换行
                opts_layout.addWidget(exclude_group)
                row2 = QHBoxLayout()
                row2.setSpacing(4)
                exclude_layout = row2
        opts_layout.addLayout(exclude_layout)

        custom_row = QHBoxLayout()
        custom_row.addWidget(QLabel("自定义排除:"))
        self.custom_input = QLineEdit()
        self.custom_input.setPlaceholderText("逗号分隔，如: *.log, tmp/")
        custom_row.addWidget(self.custom_input, 1)
        opts_layout.addLayout(custom_row)

        self.save_default_cb = QCheckBox("保存为默认选项")
        opts_layout.addWidget(self.save_default_cb)

        layout.addWidget(opts_group)

        # ── 按钮 ──
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.button(QDialogButtonBox.Ok).setText("确认传输")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _match_preset(self, opts: RsyncOptions) -> str:
        """查找选项匹配的预设名称，未匹配返回「自定义」"""
        for name, factory in self.PRESETS.items():
            if factory is not None and opts.to_dict() == factory().to_dict():
                return name
        return "自定义"

    def _on_preset_changed(self, text: str):
        if self._building:
            return
        factory = self.PRESETS.get(text)
        if factory:
            self._apply_options(factory())

    def _update_strategy(self, opts: RsyncOptions):
        """更新覆盖策略说明文字"""
        if opts.ignore_existing:
            text = "跳过目标端已存在的文件，不覆盖 (--ignore-existing)"
        elif opts.delete:
            text = "覆盖/更新已存在的文件，并删除目标端多余文件 (--delete)"
        else:
            text = "覆盖/更新已存在的文件"
        self.strategy_label.setText(text)

    def _on_option_toggled(self):
        if self._building:
            return
        # 检查当前选项是否匹配某个预设
        opts = self._collect_options()
        self._update_strategy(opts)
        self._building = True
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentText(self._match_preset(opts))
        self.preset_combo.blockSignals(False)
        self._building = False

    def _apply_options(self, opts: RsyncOptions):
        self._building = True
        self.cb_archive.setChecked(opts.archive)
        self.cb_verbose.setChecked(opts.verbose)
        self.cb_compress.setChecked(opts.compress)
        self.cb_delete.setChecked(opts.delete)
        self.cb_ignore_existing.setChecked(opts.ignore_existing)

        for attr, cb in self._exclude_checks.items():
            cb.setChecked(getattr(opts, attr))

        self.custom_input.setText(", ".join(opts.custom_excludes))
        self._building = False

        self._update_strategy(opts)
        self.preset_combo.setCurrentText(self._match_preset(opts))

    def _collect_options(self) -> RsyncOptions:
        custom = [
            s.strip() for s in self.custom_input.text().split(",") if s.strip()
        ]
        return RsyncOptions(
            archive=self.cb_archive.isChecked(),
            verbose=self.cb_verbose.isChecked(),
            compress=self.cb_compress.isChecked(),
            delete=self.cb_delete.isChecked(),
            ignore_existing=self.cb_ignore_existing.isChecked(),
            **{attr: cb.isChecked() for attr, cb in self._exclude_checks.items()},
            custom_excludes=custom,
        )

    def get_options(self) -> RsyncOptions:
        return self._collect_options()

    def save_as_default(self) -> bool:
        return self.save_default_cb.isChecked()


class ConfigDialog(QDialog):
    """连接配置编辑对话框"""

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.config = dict(config)
        self.setWindowTitle("编辑连接配置")
        self.setMinimumWidth(500)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        tabs = QTabWidget()

        # ── 标签页 1: 网关 ──
        gw_tab = QWidget()
        gw_layout = QFormLayout(gw_tab)

        self.gw_host_edit = QLineEdit(self.config["gw_host"])
        self.gw_port_edit = QLineEdit(str(self.config["gw_port"]))
        self.gw_user_edit = QLineEdit(self.config["gw_user"])
        self.gw_pass_edit = QLineEdit(self.config["gw_pass"])
        self.gw_pass_edit.setEchoMode(QLineEdit.Password)
        self.tunnel_port_edit = QLineEdit(str(self.config["tunnel_port"]))

        gw_layout.addRow("网关主机:", self.gw_host_edit)
        gw_layout.addRow("网关端口:", self.gw_port_edit)
        gw_layout.addRow("网关用户:", self.gw_user_edit)
        gw_layout.addRow("网关密码:", self.gw_pass_edit)
        gw_layout.addRow("隧道本地端口:", self.tunnel_port_edit)

        tabs.addTab(gw_tab, "网关 (A733)")

        # ── 标签页 2: 远程 ──
        rk_tab = QWidget()
        rk_layout = QFormLayout(rk_tab)

        self.rk_host_edit = QLineEdit(self.config["rk_host"])
        self.rk_user_edit = QLineEdit(self.config["rk_user"])
        self.rk_pass_edit = QLineEdit(self.config["rk_pass"])
        self.rk_pass_edit.setEchoMode(QLineEdit.Password)
        self.rk_base_path_edit = QLineEdit(self.config["rk_base_path"])

        rk_layout.addRow("RK3588 主机:", self.rk_host_edit)
        rk_layout.addRow("RK3588 用户:", self.rk_user_edit)
        rk_layout.addRow("RK3588 密码:", self.rk_pass_edit)
        rk_layout.addRow("远程基础路径:", self.rk_base_path_edit)

        tabs.addTab(rk_tab, "远程 (RK3588)")

        layout.addWidget(tabs)

        # ── 按钮 ──
        btn_row = QHBoxLayout()

        test_btn = QPushButton("测试连接")
        test_btn.clicked.connect(self._test_connection)
        btn_row.addWidget(test_btn)

        btn_row.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply
        )
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.button(QDialogButtonBox.Apply).setText("应用")
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self._on_apply)
        btn_row.addWidget(buttons)

        layout.addLayout(btn_row)

    def _collect_config(self) -> dict:
        return {
            "gw_host": self.gw_host_edit.text().strip(),
            "gw_port": int(self.gw_port_edit.text().strip() or "0"),
            "gw_user": self.gw_user_edit.text().strip(),
            "gw_pass": self.gw_pass_edit.text(),
            "rk_host": self.rk_host_edit.text().strip(),
            "rk_user": self.rk_user_edit.text().strip(),
            "rk_pass": self.rk_pass_edit.text(),
            "rk_base_path": self.rk_base_path_edit.text().strip(),
            "tunnel_port": int(self.tunnel_port_edit.text().strip() or "0"),
        }

    def _on_apply(self):
        cfg = self._collect_config()
        mgr = ConfigManager()
        errors = mgr.validate(cfg)
        if errors:
            QMessageBox.warning(self, "验证失败", "\n".join(errors))
            return
        self.config = cfg
        mgr.save(cfg)

    def _on_ok(self):
        self._on_apply()
        self.accept()

    def _test_connection(self):
        """测试连接：打开隧道 + 验证"""
        cfg = self._collect_config()
        mgr = ConfigManager()
        errors = mgr.validate(cfg)
        if errors:
            QMessageBox.warning(self, "验证失败", "\n".join(errors))
            return

        QMessageBox.information(self, "测试连接",
                                "正在测试连接...\n请等待结果（约 5-10 秒）")

        ok, msg = test_tunnel_flow(cfg)
        if ok:
            QMessageBox.information(self, "连接成功", "✓ 连接测试成功!\nRK3588 可达")
        else:
            QMessageBox.critical(self, "连接失败", msg)

    def get_config(self) -> dict:
        return self.config
