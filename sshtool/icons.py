"""Lucide 图标助手 — 内嵌 SVG，无需外部文件依赖。

用法:
    from .icons import get_icon, get_pixmap

    # 按钮图标
    btn.setIcon(get_icon("plug"))
    btn.setIcon(get_icon("arrow-left", color="#9ece6a", size=20))

    # 标签用 Pixmap
    label.setPixmap(get_pixmap("circle", "#f7768e", 12))
"""

from PyQt5.QtCore import QByteArray, QSize, Qt
from PyQt5.QtGui import QIcon, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer

# ── Lucide SVG 路径 (24×24 viewBox, stroke-width 2) ──────────────
# 每个图标都是一个完整的 SVG 字符串，stroke="currentColor" 在渲染时替换为目标颜色。

_ICONS: dict[str, str] = {
    # 连接 / 断开
    "plug": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 22v-5"/>'
        '<path d="M9 7V2h6v5"/>'
        '<path d="M15 7H9"/>'
        '<path d="M15 7v2a3 3 0 1 1-6 0V7"/>'
        "</svg>"
    ),

    # 方向箭头
    "arrow-left": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="m12 19-7-7 7-7"/>'
        '<path d="M19 12H5"/>'
        "</svg>"
    ),
    "arrow-right": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="m12 5 7 7-7 7"/>'
        '<path d="M5 12h14"/>'
        "</svg>"
    ),

    # 传输
    "play": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<polygon points="5 3 19 12 5 21 5 3"/>'
        "</svg>"
    ),
    "search": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="11" cy="11" r="8"/>'
        '<path d="m21 21-4.3-4.3"/>'
        "</svg>"
    ),
    "square": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<rect width="18" height="18" x="3" y="3" rx="2"/>'
        "</svg>"
    ),

    # 导航
    "chevron-up": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="m18 15-6-6-6 6"/>'
        "</svg>"
    ),
    "home": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
        '<polyline points="9 22 9 12 15 12 15 22"/>'
        "</svg>"
    ),
    "refresh-cw": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>'
        '<path d="M21 3v5h-5"/>'
        '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/>'
        '<path d="M3 21v-5h5"/>'
        "</svg>"
    ),

    # 书签
    "star": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02'
        ' 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>'
        "</svg>"
    ),

    # 状态
    "check": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="20 6 9 17 4 12"/>'
        "</svg>"
    ),
    "x": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M18 6 6 18"/>'
        '<path d="m6 6 12 12"/>'
        "</svg>"
    ),
    "triangle-alert": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/>'
        '<line x1="12" x2="12" y1="9" y2="13"/>'
        '<line x1="12" x2="12.01" y1="17" y2="17"/>'
        "</svg>"
    ),
    "circle": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="10"/>'
        "</svg>"
    ),
    "circle-fill": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="currentColor" stroke="none">'
        '<circle cx="12" cy="12" r="10"/>'
        "</svg>"
    ),

    # 书签（实心）
    "star-fill": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="currentColor" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02'
        ' 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>'
        "</svg>"
    ),

    # 加号
    "plus": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M5 12h14"/>'
        '<path d="M12 5v14"/>'
        "</svg>"
    ),

    # 文件夹（本地面板）
    "folder": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/>'
        "</svg>"
    ),

    # 地球/服务器（远程面板）
    "globe": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
        ' viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="10"/>'
        '<path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/>'
        '<path d="M2 12h20"/>'
        "</svg>"
    ),
}


# ── 公共 API ──────────────────────────────────────────────────────

def _render_svg(svg_data: str, color: str | None, size: int) -> QPixmap:
    """将 SVG 字符串渲染为 QPixmap。"""
    if color:
        svg_data = svg_data.replace('currentColor', color)
    data = QByteArray(svg_data.encode("utf-8"))
    renderer = QSvgRenderer(data)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap


def get_icon(name: str, color: str | None = None, size: int = 24) -> QIcon:
    """返回 Lucide 图标的 QIcon。

    参数:
        name: 图标名称 (见 _ICONS 键)
        color: 描边颜色 (#rrggbb 格式)，None 则使用 currentColor
        size: 像素尺寸
    """
    svg = _ICONS.get(name)
    if svg is None:
        # 回退：返回透明图标
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        return QIcon(pixmap)
    return QIcon(_render_svg(svg, color, size))


def get_pixmap(name: str, color: str | None = None, size: int = 24) -> QPixmap:
    """返回 Lucide 图标的 QPixmap，适用于 QLabel.setPixmap()。

    参数:
        name: 图标名称
        color: 描边/填充颜色
        size: 像素尺寸
    """
    svg = _ICONS.get(name)
    if svg is None:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        return pixmap
    return _render_svg(svg, color, size)


def available_icons() -> list[str]:
    """列出所有可用图标名称。"""
    return sorted(_ICONS.keys())
