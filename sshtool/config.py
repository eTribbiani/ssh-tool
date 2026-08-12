"""常量、默认配置、样式表与配置持久化"""

import datetime
import json
from pathlib import Path
from typing import Optional

APP_NAME = "SSHTool GUI"
CONFIG_DIR = Path.home() / ".config" / "sshtool"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "gw_host": "192.168.1.214",
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
    """根据基准字体大小 fs 动态生成全应用样式表"""
    s, t = fs - 2, fs - 1          # 次级/工具栏字体
    pad = max(4, fs // 4)          # 通用内边距
    pad2 = max(8, fs // 2)         # 按钮水平内边距
    tab_pad = max(10, fs // 2)     # 标签页水平内边距
    sp = max(3, fs // 6)           # 工具栏/下拉框内边距
    return f"""
QMainWindow {{ background-color: #2b2b2b; }}
QWidget {{ font-size: {fs}px; }}
QTreeView {{
    background-color: #1e1e1e;
    color: #d4d4d4;
    border: 1px solid #3c3c3c;
    font-size: {fs}px;
}}
QTreeView::item:selected {{
    background-color: #264f78;
    color: #ffffff;
}}
QTreeView::item:hover {{
    background-color: #2a2d2e;
}}
QHeaderView::section {{
    background-color: #333333;
    color: #cccccc;
    padding: {pad}px;
    border: 1px solid #3c3c3c;
    font-size: {fs}px;
}}
QLineEdit {{
    background-color: #1e1e1e;
    color: #d4d4d4;
    border: 1px solid #3c3c3c;
    padding: {pad}px;
    font-size: {fs}px;
}}
QToolBar {{
    background-color: #333333;
    border-bottom: 1px solid #3c3c3c;
    padding: {sp}px;
    spacing: 6px;
}}
QToolBar QPushButton {{
    background-color: #0e639c;
    color: white;
    border: none;
    padding: {pad}px {pad2}px;
    border-radius: 3px;
    font-size: {t}px;
}}
QToolBar QPushButton:hover {{ background-color: #1177bb; }}
QToolBar QPushButton:pressed {{ background-color: #094771; }}
QToolBar QPushButton:disabled {{ background-color: #555555; color: #888888; }}
QToolBar QCheckBox {{
    color: #cccccc;
    font-size: {t}px;
}}
QMenuBar {{
    background-color: #333333;
    color: #cccccc;
    border-bottom: 1px solid #3c3c3c;
    font-size: {fs}px;
}}
QMenuBar::item:selected {{ background-color: #0e639c; }}
QMenu {{
    background-color: #333333;
    color: #cccccc;
    font-size: {fs}px;
}}
QMenu::item:selected {{ background-color: #0e639c; }}
QProgressBar {{
    border: 1px solid #3c3c3c;
    border-radius: 3px;
    text-align: center;
    color: white;
    font-size: {s}px;
}}
QProgressBar::chunk {{
    background-color: #0e639c;
    border-radius: 2px;
}}
QTextEdit {{
    background-color: #1e1e1e;
    color: #a0a0a0;
    border: 1px solid #3c3c3c;
    font-family: "DejaVu Sans Mono", monospace;
    font-size: {s}px;
}}
QStatusBar {{
    background-color: #007acc;
    color: white;
    font-size: {fs}px;
}}
QDialog {{
    background-color: #2b2b2b;
    color: #d4d4d4;
}}
QTabWidget::pane {{ border: 1px solid #3c3c3c; }}
QTabBar::tab {{
    background-color: #333333;
    color: #cccccc;
    padding: {pad}px {tab_pad}px;
    border: 1px solid #3c3c3c;
    font-size: {fs}px;
}}
QTabBar::tab:selected {{
    background-color: #0e639c;
    color: white;
}}
QLabel {{ color: #cccccc; font-size: {fs}px; }}
QGroupBox {{
    color: #cccccc;
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 14px;
    font-size: {fs}px;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
QComboBox {{
    background-color: #1e1e1e;
    color: #d4d4d4;
    border: 1px solid #3c3c3c;
    padding: {sp}px;
    font-size: {fs}px;
}}
QComboBox::drop-down {{ border: none; }}
QCheckBox {{ font-size: {fs}px; }}
QMessageBox {{ font-size: {fs}px; }}
QPushButton {{ font-size: {fs}px; }}
"""


class ConfigManager:
    """连接配置的加载/保存/验证"""

    def __init__(self):
        self.config = dict(DEFAULT_CONFIG)
        self._ensure_config_dir()

    def _ensure_config_dir(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict:
        """从文件加载配置，文件不存在则用默认值创建"""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                # 合并，确保没有缺失字段
                for k, v in DEFAULT_CONFIG.items():
                    self.config[k] = data.get(k, v)
                # 保留类型
                self.config["gw_port"] = int(self.config["gw_port"])
                self.config["tunnel_port"] = int(self.config["tunnel_port"])
            except (json.JSONDecodeError, ValueError, KeyError):
                self.config = dict(DEFAULT_CONFIG)
                self.save()
        else:
            self.config = dict(DEFAULT_CONFIG)
            self.save()
        return dict(self.config)

    def save(self, config: Optional[dict] = None):
        """保存配置到文件"""
        if config is not None:
            self.config = dict(config)
        self._ensure_config_dir()
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.config, f, indent=2)

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
