"""常量、默认配置、样式表与配置持久化"""

import datetime
import json
from pathlib import Path
from typing import Optional

APP_NAME = "SSHTool GUI"
CONFIG_DIR = Path.home() / ".config" / "sshtool"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "gw_host": "192.168.1.215",
    "gw_port": 60000,
    "gw_user": "bpx",
    "gw_pass": "bpx1024",
    "rk_host": "10.21.20.200",
    "rk_user": "BAOBAO",
    "rk_pass": "BAOBAO2024",
    "rk_base_path": "/home/BAOBAO/MM/workspace/mm_nav_ws",
    "tunnel_port": 22358,
    "font_size": 29,
}

# (RsyncOptions 字段名, 排除模式) — 选项模型/对话框/摘要共用
EXCLUDE_PATTERNS = [
    ("exclude_git", ".git/"),
    ("exclude_build", "build/"),
    ("exclude_install", "install/"),
    ("exclude_cache", ".cache/"),
    ("exclude_pycache", "__pycache__/"),
    ("exclude_pyc", "*.pyc"),
    ("exclude_o", "*.o"),
    ("exclude_a", "*.a"),
    ("exclude_deb", "*.deb"),
    ("exclude_zip", "*.zip"),
    ("exclude_targz", "*.tar.gz"),
    ("exclude_compile_commands", "compile_commands.json"),
]


def clamp_font(v) -> int:
    """限制字体大小范围 12-48"""
    return max(12, min(48, int(v)))


def format_mtime(dt) -> str:
    """本地/远程统一的修改时间格式（当年仅显月日，跨年显年月日）"""
    if dt is None or not dt.year:
        return ""
    if dt.year == datetime.datetime.now().year:
        return dt.strftime("%m-%d %H:%M")
    return dt.strftime("%Y-%m-%d %H:%M")


def file_type(name: str) -> str:
    """提取文件类型（扩展名，小写），无扩展名返回空串"""
    return name.rsplit(".", 1)[1].lower() if "." in name else ""


def format_size(size: int) -> str:
    """本地/远程统一的大小格式（B/KB/MB/GB/TB，一位小数）"""
    if size <= 0:
        return ""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def make_stylesheet(fs: int) -> str:
    """根据基准字体大小 fs 动态生成全应用样式表

    Tokyo Night 暗色配色方案：
    - 最深底色 #1a1b26，表面 #1e2030，抬升 #24253b
    - 主色调：柔和天蓝 #7aa2f7
    - 语义色：绿 #9ece6a / 红 #f7768e / 黄 #e0af68 / 紫 #bb9af7
    - 文字：主 #c0caf5 / 次 #a9b1d6 / 弱 #565f89
    """
    s, t = fs - 2, fs - 1          # 次级/工具栏字体
    pad = max(4, fs // 4)          # 通用内边距
    pad2 = max(8, fs // 2)         # 按钮水平内边距
    tab_pad = max(10, fs // 2)     # 标签页水平内边距
    sp = max(3, fs // 6)           # 工具栏/下拉框内边距
    return f"""
/* ── 全局基础 ─────────────────────────────────────────── */
QMainWindow {{ background-color: #1a1b26; }}
QWidget {{ font-size: {fs}px; color: #c0caf5; }}

/* ── 树视图 ───────────────────────────────────────────── */
QTreeView {{
    background-color: #1e2030;
    color: #c0caf5;
    border: 1px solid #3b4261;
    font-size: {fs}px;
    alternate-background-color: #1c1e2d;
}}
QTreeView::item:selected {{
    background-color: #364a82;
    color: #e0e7ff;
}}
QTreeView::item:hover {{
    background-color: #292e42;
}}
QTreeView::branch {{
    background-color: #1e2030;
}}

/* ── 表头 ─────────────────────────────────────────────── */
QHeaderView::section {{
    background-color: #24253b;
    color: #a9b1d6;
    padding: {pad}px;
    border: none;
    border-right: 1px solid #3b4261;
    border-bottom: 2px solid #3b4261;
    font-size: {fs}px;
    font-weight: bold;
}}

/* ── 输入框 ───────────────────────────────────────────── */
QLineEdit {{
    background-color: #1e2030;
    color: #c0caf5;
    border: 1px solid #3b4261;
    border-radius: 4px;
    padding: {pad}px;
    font-size: {fs}px;
    selection-background-color: #364a82;
}}
QLineEdit:focus {{
    border-color: #7aa2f7;
}}

/* ── 工具栏面板 ───────────────────────────────────────── */
#toolbar {{
    background-color: #24253b;
    border-bottom: 2px solid #3b4261;
}}
#toolbar QPushButton {{
    background-color: #3b4261;
    color: #c0caf5;
    border: none;
    border-radius: 4px;
    padding: {pad}px {pad2}px;
    font-size: {t}px;
    font-weight: 500;
}}
#toolbar QPushButton:hover {{ background-color: #565f89; }}
#toolbar QPushButton:pressed {{ background-color: #7aa2f7; color: #1a1b26; }}
#toolbar QPushButton:disabled {{ background-color: #2a2e3f; color: #565f89; }}
#toolbar QLabel {{ color: #a9b1d6; font-size: {t}px; }}
#toolbar QComboBox {{
    background-color: #1e2030;
    color: #c0caf5;
    border: 1px solid #3b4261;
    border-radius: 4px;
    padding: {sp}px 10px;
    font-size: {t}px;
}}
#toolbar QComboBox:hover {{ border-color: #565f89; }}
#toolbar QComboBox::drop-down {{ border: none; width: 20px; }}
#toolbar QComboBox QAbstractItemView {{
    background-color: #1e2030;
    color: #c0caf5;
    selection-background-color: #364a82;
    border: 1px solid #3b4261;
}}

/* ── 语义按钮 ─────────────────────────────────────────── */
#connectBtn {{
    background-color: #2a4a3c;
    color: #9ece6a;
    font-weight: bold;
}}
#connectBtn:hover {{ background-color: #3a6a54; }}
#connectBtn:pressed {{ background-color: #4a8a6c; color: #1a1b26; }}
#connectBtn:disabled {{ background-color: #2a2e3f; color: #565f89; }}

#disconnectBtn {{
    background-color: #4a2a34;
    color: #f7768e;
    font-weight: bold;
}}
#disconnectBtn:hover {{ background-color: #6a3a4a; }}
#disconnectBtn:pressed {{ background-color: #8a4a5a; color: #1a1b26; }}
#disconnectBtn:disabled {{ background-color: #2a2e3f; color: #565f89; }}

#previewBtn {{
    background-color: #3a2a5a;
    color: #bb9af7;
    font-weight: bold;
}}
#previewBtn:hover {{ background-color: #5a3a7a; }}
#previewBtn:pressed {{ background-color: #7a5aaa; color: #1a1b26; }}

#stopBtn {{
    background-color: #4a2a34;
    color: #f7768e;
    font-weight: bold;
}}
#stopBtn:hover {{ background-color: #6a3a4a; }}
#stopBtn:pressed {{ background-color: #8a4a5a; color: #1a1b26; }}
#stopBtn:disabled {{ background-color: #2a2e3f; color: #565f89; }}

#addProfileBtn {{
    background-color: #3b4261;
    color: #c0caf5;
    border-radius: 4px;
    font-weight: bold;
}}
#addProfileBtn:hover {{ background-color: #565f89; }}

#dryRunCb {{
    color: #e0af68;
    font-size: {t}px;
    font-weight: 500;
}}

/* ── 菜单栏 ───────────────────────────────────────────── */
QMenuBar {{
    background-color: #1a1b26;
    color: #a9b1d6;
    border-bottom: 1px solid #3b4261;
    font-size: {fs}px;
    padding: 2px 0;
}}
QMenuBar::item:selected {{
    background-color: #364a82;
    color: #e0e7ff;
    border-radius: 3px;
}}
QMenu {{
    background-color: #24253b;
    color: #c0caf5;
    border: 1px solid #3b4261;
    border-radius: 6px;
    padding: 4px;
    font-size: {fs}px;
}}
QMenu::item {{
    padding: {pad}px 28px {pad}px 12px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: #364a82;
    color: #e0e7ff;
}}
QMenu::separator {{
    height: 1px;
    background-color: #3b4261;
    margin: 4px 8px;
}}

/* ── 进度条 ───────────────────────────────────────────── */
QProgressBar {{
    background-color: #1e2030;
    border: 1px solid #3b4261;
    border-radius: 4px;
    text-align: center;
    color: #c0caf5;
    font-size: {s}px;
    height: 18px;
}}
QProgressBar::chunk {{
    background-color: #7aa2f7;
    border-radius: 3px;
}}

/* ── 日志视图 ─────────────────────────────────────────── */
QTextEdit {{
    background-color: #161720;
    color: #a9b1d6;
    border: 1px solid #3b4261;
    border-radius: 4px;
    font-family: "DejaVu Sans Mono", "Cascadia Code", monospace;
    font-size: {s}px;
    selection-background-color: #364a82;
}}

/* ── 终端视图 ─────────────────────────────────────────── */
QPlainTextEdit {{
    background-color: #161720;
    color: #c0caf5;
    border: none;
    font-family: "DejaVu Sans Mono", "Cascadia Code", monospace;
    font-size: {s}px;
    selection-background-color: #364a82;
    selection-color: #e0e7ff;
}}

/* ── 终端列表面板 ────────────────────────────────────── */
#terminalList QListWidget {{
    background-color: #1a1b26;
    color: #c0caf5;
    border: none;
    border-left: 1px solid #3b4261;
    font-size: {s}px;
    outline: none;
}}
#terminalList QListWidget::item {{
    padding: 6px 8px;
    border-bottom: 1px solid #24253b;
}}
#terminalList QListWidget::item:selected {{
    background-color: #364a82;
    color: #e0e7ff;
}}
#terminalList QListWidget::item:hover {{
    background-color: #292e42;
}}
#terminalList QLabel {{
    color: #a9b1d6;
    font-size: {s}px;
}}

/* ── 状态栏 ───────────────────────────────────────────── */
QStatusBar {{
    background-color: #24253b;
    color: #a9b1d6;
    border-top: 1px solid #3b4261;
    font-size: {fs}px;
}}
QStatusBar::item {{ border: none; }}

/* ── 对话框 ───────────────────────────────────────────── */
QDialog {{
    background-color: #1e2030;
    color: #c0caf5;
}}

/* ── 选项卡 ───────────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid #3b4261;
    border-radius: 4px;
    background-color: #1e2030;
}}
QTabBar::tab {{
    background-color: #24253b;
    color: #a9b1d6;
    padding: {pad}px {tab_pad}px;
    border: 1px solid #3b4261;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
    font-size: {fs}px;
}}
QTabBar::tab:selected {{
    background-color: #1e2030;
    color: #7aa2f7;
    border-bottom: 2px solid #7aa2f7;
}}
QTabBar::tab:hover:!selected {{
    background-color: #2f3349;
    color: #c0caf5;
}}

/* ── 标签 & 分组 ──────────────────────────────────────── */
QLabel {{ color: #c0caf5; font-size: {fs}px; }}
QGroupBox {{
    color: #a9b1d6;
    border: 1px solid #3b4261;
    border-radius: 6px;
    margin-top: 12px;
    padding: 16px 8px 8px 8px;
    font-size: {fs}px;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #7aa2f7;
}}

/* ── 下拉框（全局） ───────────────────────────────────── */
QComboBox {{
    background-color: #1e2030;
    color: #c0caf5;
    border: 1px solid #3b4261;
    border-radius: 4px;
    padding: {sp}px 10px;
    font-size: {fs}px;
}}
QComboBox:hover {{ border-color: #565f89; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background-color: #1e2030;
    color: #c0caf5;
    selection-background-color: #364a82;
    border: 1px solid #3b4261;
    border-radius: 4px;
}}

/* ── 通用 ─────────────────────────────────────────────── */
QCheckBox {{ color: #c0caf5; font-size: {fs}px; }}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 2px solid #565f89;
    border-radius: 3px;
    background-color: #1e2030;
}}
QCheckBox::indicator:checked {{
    background-color: #7aa2f7;
    border-color: #7aa2f7;
}}
QMessageBox {{ font-size: {fs}px; background-color: #1e2030; }}
QPushButton {{
    font-size: {fs}px;
    background-color: #3b4261;
    color: #c0caf5;
    border: none;
    border-radius: 4px;
    padding: {pad}px {pad2}px;
}}
QPushButton:hover {{ background-color: #565f89; }}
QPushButton:pressed {{ background-color: #7aa2f7; color: #1a1b26; }}
QPushButton:disabled {{ background-color: #2a2e3f; color: #565f89; }}
QDialogButtonBox QPushButton {{
    background-color: #3b4261;
    color: #c0caf5;
    border: none;
    border-radius: 4px;
    padding: {pad}px {pad2}px;
    min-width: 80px;
}}
QDialogButtonBox QPushButton:hover {{ background-color: #565f89; }}
QDialogButtonBox QPushButton:pressed {{ background-color: #7aa2f7; color: #1a1b26; }}
QDialogButtonBox QPushButton:disabled {{ background-color: #2a2e3f; color: #565f89; }}
QListWidget {{
    background-color: #1e2030;
    color: #c0caf5;
    border: 1px solid #3b4261;
    border-radius: 4px;
}}
QScrollBar:vertical {{
    background-color: #1a1b26;
    width: 10px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical {{
    background-color: #3b4261;
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background-color: #565f89; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background-color: #1a1b26;
    height: 10px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal {{
    background-color: #3b4261;
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background-color: #565f89; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QToolTip {{
    background-color: #24253b;
    color: #c0caf5;
    border: 1px solid #565f89;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: {s}px;
}}
"""


class ConfigManager:
    """多环境连接配置管理: 加载/保存/切换/验证

    文件格式: {"current": "环境名", "profiles": {"环境名": {配置}}}
    旧版扁平配置自动迁移为默认环境「default」。
    """

    def __init__(self):
        self.config = dict(DEFAULT_CONFIG)
        self._profiles: dict = {}
        self._current_profile = "default"
        self._ensure_config_dir()

    def _ensure_config_dir(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def current_profile(self) -> str:
        return self._current_profile

    def profiles(self) -> list:
        return list(self._profiles.keys())

    def _load_raw(self) -> dict:
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, ValueError):
                return {}
        return {}

    def _write_raw(self, raw: dict):
        self._ensure_config_dir()
        with open(CONFIG_FILE, "w") as f:
            json.dump(raw, f, indent=2)

    @staticmethod
    def _merge_defaults(cfg: dict) -> dict:
        """合并默认值并保留字段类型"""
        merged = dict(DEFAULT_CONFIG)
        for k, v in cfg.items():
            if k in DEFAULT_CONFIG:
                merged[k] = v
        for k in ("gw_port", "tunnel_port", "font_size"):
            try:
                merged[k] = int(merged[k])
            except (ValueError, TypeError):
                merged[k] = DEFAULT_CONFIG[k]
        return merged

    def load(self) -> dict:
        """加载当前环境配置，旧版扁平格式自动迁移"""
        raw = self._load_raw()
        if "profiles" not in raw:
            # 旧格式迁移: 扁平配置 → 默认环境
            raw = {"current": "default",
                   "profiles": {"default": self._merge_defaults(raw)}}
            self._write_raw(raw)

        self._profiles = {
            name: self._merge_defaults(cfg)
            for name, cfg in raw.get("profiles", {}).items()
        }
        if not self._profiles:
            self._profiles["default"] = dict(DEFAULT_CONFIG)

        self._current_profile = raw.get("current", "default")
        if self._current_profile not in self._profiles:
            self._current_profile = next(iter(self._profiles))
        self.config = dict(self._profiles[self._current_profile])
        return dict(self.config)

    def save(self, config: Optional[dict] = None):
        """保存到当前环境"""
        if config is not None:
            self.config = dict(config)
        self.save_profile(self._current_profile, self.config)

    def save_profile(self, name: str, config: dict):
        """保存指定环境的配置"""
        self._profiles[name] = dict(config)
        self._write_raw({"current": self._current_profile,
                         "profiles": self._profiles})

    def switch_profile(self, name: str) -> dict:
        """切换到指定环境并持久化，返回其配置"""
        if name not in self._profiles:
            raise KeyError(f"环境不存在: {name}")
        self._current_profile = name
        self.config = dict(self._profiles[name])
        self._write_raw({"current": name, "profiles": self._profiles})
        return dict(self.config)

    def add_profile(self, name: str, config: Optional[dict] = None) -> dict:
        """新建环境（不切换），配置默认为当前环境的副本"""
        if name in self._profiles:
            raise ValueError(f"环境已存在: {name}")
        self._profiles[name] = dict(config) if config is not None \
            else dict(self.config)
        self._write_raw({"current": self._current_profile,
                         "profiles": self._profiles})
        return dict(self._profiles[name])

    def delete_profile(self, name: str) -> bool:
        """删除环境；删除当前环境时自动切到剩余第一个。至少保留一个。"""
        if len(self._profiles) <= 1 or name not in self._profiles:
            return False
        del self._profiles[name]
        if self._current_profile == name:
            self._current_profile = next(iter(self._profiles))
        self.config = dict(self._profiles[self._current_profile])
        self._write_raw({"current": self._current_profile,
                         "profiles": self._profiles})
        return True

    def validate(self, config: dict) -> list:
        """验证配置，返回错误列表"""
        errors = []
        required = [
            "gw_host", "gw_user", "gw_pass",
            "rk_host", "rk_user", "rk_pass", "rk_base_path",
        ]
        for field_name in required:
            if not config.get(field_name, "").strip():
                errors.append(f"「{field_name}」不能为空")
        for key, label in [("gw_port", "网关端口"), ("tunnel_port", "隧道端口")]:
            try:
                port = int(config.get(key, 0))
                if not 1 <= port <= 65535:
                    errors.append(f"{label}范围 1-65535")
            except (ValueError, TypeError):
                errors.append(f"{label}必须为数字")
        return errors
