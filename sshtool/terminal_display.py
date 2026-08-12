"""TerminalDisplay — PyQt5 终端渲染控件 (基于 pyte 0.8.x 终端仿真)"""

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QBrush, QColor, QFont, QKeyEvent, QPalette, QTextCharFormat,
    QTextCursor,
)
from PyQt5.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

import pyte


# ─── Tokyo Night 终端配色 ──────────────────────────────────────

# pyte 颜色名称 → Tokyo Night 十六进制色值
PYTE_COLOR_TO_HEX = {
    "black":         "#1a1b26",
    "red":           "#f7768e",
    "green":         "#9ece6a",
    "brown":         "#e0af68",
    "yellow":        "#e0af68",
    "blue":          "#7aa2f7",
    "magenta":       "#bb9af7",
    "cyan":          "#7dcfff",
    "white":         "#c0caf5",
    "brightblack":   "#565f89",
    "brightred":     "#ff9e9e",
    "brightgreen":   "#b9f27c",
    "brightyellow":  "#ffc777",
    "brightbrown":   "#ffc777",
    "brightblue":    "#a9b1d6",
    "brightmagenta": "#c4a7ff",
    "brightcyan":    "#b4f9f8",
    "brightwhite":   "#e0e7ff",
    "bfightmagenta": "#c4a7ff",   # pyte 0.8.2 拼写错误兼容
    "default":       "#c0caf5",
}

TERMINAL_BG = "#161720"   # 最深底色
TERMINAL_FG = "#c0caf5"   # 主文字色
CURSOR_COLOR = "#7aa2f7"  # 光标色


# ─── TerminalDisplay ──────────────────────────────────────────

class TerminalDisplay(QPlainTextEdit):
    """基于 pyte 的终端渲染控件。

    在 GUI 线程运行 pyte.Screen + pyte.Stream，
    接收来自 TerminalSession 的原始输出字节并渲染到 QPlainTextEdit。

    Signals:
        input_data(bytes): 用户键盘输入, 转发给 TerminalSession
    """

    input_data = pyqtSignal(bytes)
    terminate_requested = pyqtSignal()
    size_changed = pyqtSignal()  # 控件尺寸变化 → 终端需重算行列

    def __init__(self, columns: int = 80, rows: int = 24, parent: QWidget = None):
        super().__init__(parent)
        self._columns = columns
        self._rows = rows

        # ── pyte 终端仿真器 (GUI 线程, pyte 0.8.x 使用字符串) ──
        self._screen = pyte.HistoryScreen(columns, rows, history=5000)
        self._screen.cursor.hidden = False
        self._stream = pyte.Stream(self._screen)

        # ── 渲染状态 ──
        self._render_scheduled = False
        self._prev_display: list[str] = []

        # ── 外观 ──
        self.setFont(QFont("DejaVu Sans Mono", 13))
        self.setCursorWidth(0)  # 隐藏默认光标, 我们自己绘制
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setUndoRedoEnabled(False)
        self.setContextMenuPolicy(Qt.DefaultContextMenu)

        # 终端配色
        p = self.palette()
        p.setColor(QPalette.Base, QColor(TERMINAL_BG))
        p.setColor(QPalette.Text, QColor(TERMINAL_FG))
        self.setPalette(p)

        # ── 光标闪烁计时器 ──
        self._cursor_visible = True
        self._cursor_timer = QTimer(self)
        self._cursor_timer.timeout.connect(self._toggle_cursor)
        self._cursor_timer.start(530)
        self._cursor_block = None  # (row, col) of current cursor

        # ── resize 去抖 ──
        self._resize_debounce: QTimer | None = None
        self._initial_sized = False

    # ─── Qt 事件 ─────────────────────────────────────────────────

    def showEvent(self, event):
        """首次可见时触发尺寸计算"""
        super().showEvent(event)
        if not self._initial_sized:
            self._initial_sized = True
            QTimer.singleShot(50, self._debounced_resize)

    def resizeEvent(self, event):
        """控件尺寸变化 → 去抖后重算终端行列"""
        super().resizeEvent(event)
        if not self._resize_debounce:
            self._resize_debounce = QTimer(self)
            self._resize_debounce.setSingleShot(True)
            self._resize_debounce.timeout.connect(self._debounced_resize)
        self._resize_debounce.start(80)

    def _debounced_resize(self):
        """通知外部重新计算终端尺寸"""
        self.size_changed.emit()

    # ─── 公开接口 ───────────────────────────────────────────────

    def term_size(self) -> tuple:
        """返回 (columns, rows)"""
        return (self._columns, self._rows)

    def feed(self, data: bytes):
        """喂入原始终端输出字节流 (由 TerminalSession 信号触发)。

        pyte 0.8.x 的 Stream.feed() 要求字符串输入, 这里先解码。
        """
        text = data.decode("utf-8", errors="replace")
        self._stream.feed(text)
        self._schedule_render()

    def session_closed(self, exit_code: int):
        """会话结束, 在终端底部显示退出信息"""
        msg = f"\r\n\n[进程结束, 退出码: {exit_code}]\r\n"
        self._stream.feed(msg)
        self._schedule_render()
        # 停止光标闪烁
        self._cursor_timer.stop()
        self._cursor_visible = False

    def resize_terminal(self, columns: int, rows: int):
        """调整终端尺寸 (响应窗口 resize)"""
        if columns == self._columns and rows == self._rows:
            return
        self._columns = max(20, columns)
        self._rows = max(4, rows)
        # 注意: pyte Screen.resize(lines, columns) — 行在前, 列在后!
        self._screen.resize(self._rows, self._columns)
        self._schedule_render()

    # ─── 渲染 ───────────────────────────────────────────────────

    def _schedule_render(self):
        """计划渲染 (同一事件循环迭代内批处理)"""
        if not self._render_scheduled:
            self._render_scheduled = True
            QTimer.singleShot(0, self._render)

    def _render(self):
        """将 pyte.Screen 渲染到 QPlainTextEdit (增量 + 批处理)"""
        self._render_scheduled = False

        screen = self._screen
        display = screen.display
        cursor_x, cursor_y = screen.cursor.x, screen.cursor.y

        # 快速检查: 内容未变化时跳过
        if display == self._prev_display:
            self._update_cursor_block(cursor_y, cursor_x)
            return
        self._prev_display = list(display)

        doc = self.document()
        current_blocks = doc.blockCount()
        target_blocks = len(display)

        # ── 块数不匹配 (resize / 初次渲染) → 全量重建 ──
        if current_blocks != target_blocks:
            self._full_rebuild(doc, display, screen)
        else:
            self._incremental_update(doc, display, screen)

        # ── 滚动到光标所在行 ──
        self._scroll_to_cursor(cursor_y)
        self._update_cursor_block(cursor_y, cursor_x)

    def _full_rebuild(self, doc, display, screen):
        """全量重建 (仅 resize 或首次渲染时使用)"""
        doc.clear()
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        for row, line_text in enumerate(display):
            self._write_line(cursor, row, line_text, screen, row)
        cursor.endEditBlock()

    def _incremental_update(self, doc, display, screen):
        """增量更新: 只重写变化的行, 所有插入合并为一次 layout"""
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()

        for row, line_text in enumerate(display):
            block = doc.findBlockByNumber(row)
            if not block.isValid():
                # 不应发生, 但防御: 追加新 block
                cursor.movePosition(QTextCursor.End)
                cursor.insertBlock()
                cursor.insertText(line_text)
                continue

            old_text = block.text()
            if old_text == line_text:
                continue  # 该行未变, 跳过

            # 就地替换该行内容
            cursor.setPosition(block.position())
            cursor.movePosition(QTextCursor.StartOfBlock)
            cursor.movePosition(QTextCursor.EndOfBlock,
                               QTextCursor.KeepAnchor)

            # 逐字符写入 (保留 ANSI 颜色属性)
            self._write_line_at_cursor(cursor, line_text, screen, row)

        cursor.endEditBlock()

    def _write_line_at_cursor(self, cursor, line_text, screen, screen_row):
        """在当前位置写入一行 (替换原有内容), 不插入新 block"""
        default_fmt = QTextCharFormat()
        default_fmt.setForeground(QColor(TERMINAL_FG))

        # 先清除现有行内容 (select + remove)
        cursor.movePosition(QTextCursor.StartOfBlock)
        cursor.movePosition(QTextCursor.EndOfBlock,
                           QTextCursor.KeepAnchor)
        cursor.removeSelectedText()

        for col, char in enumerate(line_text):
            try:
                sc = screen.buffer[screen_row][col]
            except (KeyError, IndexError):
                sc = None

            if sc is not None:
                if (sc.fg == "default" and sc.bg == "default"
                        and not sc.bold and not sc.italics
                        and not sc.underscore and not sc.reverse):
                    char_fmt = default_fmt
                else:
                    char_fmt = self._make_format(sc)
            else:
                char_fmt = default_fmt

            cursor.insertText(char, char_fmt)

    def _write_line(self, cursor: QTextCursor, display_row: int,
                    line_text: str, screen: pyte.Screen, screen_row: int):
        """写入一行文本, 应用 ANSI 颜色/属性"""
        if display_row > 0:
            cursor.insertBlock()

        default_fmt = QTextCharFormat()
        default_fmt.setForeground(QColor(TERMINAL_FG))

        for col, char in enumerate(line_text):
            # 查询 pyte 缓冲区中的字符属性
            try:
                sc = screen.buffer[screen_row][col]
            except (KeyError, IndexError):
                sc = None

            if sc is not None:
                # 检查是否是默认格式 (无特殊属性)
                if (sc.fg == "default" and sc.bg == "default"
                        and not sc.bold and not sc.italics
                        and not sc.underscore and not sc.reverse):
                    char_fmt = default_fmt
                else:
                    char_fmt = self._make_format(sc)
            else:
                char_fmt = default_fmt

            cursor.insertText(char, char_fmt)

    def _make_format(self, sc) -> QTextCharFormat:
        """将 pyte.Char 转换为 QTextCharFormat。

        pyte 0.8.x 中 Char.fg / Char.bg 是颜色名字符串
        (如 'green', 'red', 'brightblue'), 而非 ANSI 数字码。
        """
        fmt = QTextCharFormat()

        # 前景色
        fg_name = sc.fg if sc.fg != "default" else "default"
        fg_hex = PYTE_COLOR_TO_HEX.get(fg_name, TERMINAL_FG)
        fmt.setForeground(QColor(fg_hex))

        # 背景色
        bg_name = sc.bg if sc.bg != "default" else "default"
        if bg_name != "default":
            bg_hex = PYTE_COLOR_TO_HEX.get(bg_name, TERMINAL_BG)
            fmt.setBackground(QColor(bg_hex))

        # 粗体
        if sc.bold:
            fmt.setFontWeight(QFont.Bold)

        # 斜体
        if sc.italics:
            fmt.setFontItalic(True)

        # 下划线
        if sc.underscore:
            fmt.setFontUnderline(True)

        # 反显
        if sc.reverse:
            cur_fg = fmt.foreground().color()
            cur_bg = fmt.background().color()
            fmt.setForeground(QBrush(cur_bg))
            fmt.setBackground(QBrush(cur_fg))

        return fmt

    def _scroll_to_cursor(self, cursor_row: int):
        """滚动确保光标所在行可见"""
        # cursor_row 是 screen 坐标 (不含 scrollback history offset)
        # 在 display 中的行 = display 总行数 - screen 行数 + cursor_row
        display = self._screen.display
        history_rows = len(display) - self._screen.lines
        target = history_rows + cursor_row

        # 获取光标在文档中的 block
        block = self.document().findBlockByNumber(max(0, target))
        if block.isValid():
            cursor = QTextCursor(block)
            self.setTextCursor(cursor)
            self.ensureCursorVisible()

    def _update_cursor_block(self, row: int, col: int):
        """更新光标指示块"""
        self._cursor_block = (row, col)
        self._draw_cursor()

    def _draw_cursor(self):
        """绘制光标高亮 (通过 ExtraSelections)"""
        extra_selections: list = []

        if self._cursor_block and self._cursor_visible:
            row, col = self._cursor_block
            display = self._screen.display
            history_rows = len(display) - self._screen.lines
            target = history_rows + row

            block = self.document().findBlockByNumber(max(0, target))
            if block.isValid() and 0 <= col < len(block.text()):
                sel = QTextEdit.ExtraSelection()
                sel.format.setBackground(QColor(CURSOR_COLOR))
                sel.format.setForeground(QColor(TERMINAL_BG))
                sel.cursor = QTextCursor(block)
                sel.cursor.setPosition(block.position() + col)
                sel.cursor.setPosition(block.position() + col + 1,
                                       QTextCursor.KeepAnchor)
                extra_selections.append(sel)

        self.setExtraSelections(extra_selections)

    def _toggle_cursor(self):
        """切换光标可见性"""
        self._cursor_visible = not self._cursor_visible
        if self._cursor_block:
            self._draw_cursor()

    # ─── 键盘输入处理 ─────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        """拦截所有键盘输入并转换为终端转义序列"""
        text = event.text()
        key = event.key()
        modifiers = event.modifiers()

        # ── Ctrl 组合键 ──
        if modifiers == Qt.ControlModifier:
            data = self._ctrl_key(key)
            if data is not None:
                self.input_data.emit(data)
                return

        # ── Shift+Ctrl 组合 ──
        if modifiers == (Qt.ControlModifier | Qt.ShiftModifier):
            if key == Qt.Key_C:
                self.copy()
                return
            if key == Qt.Key_V:
                self._paste_from_clipboard()
                return

        # ── 特殊键 ──
        if key in _KEY_TO_SEQUENCE:
            self.input_data.emit(_KEY_TO_SEQUENCE[key])
            return

        # ── 普通文本 ──
        if text and not (modifiers & (Qt.ControlModifier | Qt.AltModifier
                                      | Qt.MetaModifier)):
            self.input_data.emit(text.encode("utf-8"))
            return

    def _ctrl_key(self, key: int) -> bytes | None:
        """Ctrl+Key → ASCII 控制字符映射"""
        mapping = {
            Qt.Key_A: b"\x01", Qt.Key_B: b"\x02", Qt.Key_C: b"\x03",
            Qt.Key_D: b"\x04", Qt.Key_E: b"\x05", Qt.Key_F: b"\x06",
            Qt.Key_G: b"\x07", Qt.Key_H: b"\x08", Qt.Key_I: b"\x09",
            Qt.Key_J: b"\x0a", Qt.Key_K: b"\x0b", Qt.Key_L: b"\x0c",
            Qt.Key_M: b"\x0d", Qt.Key_N: b"\x0e", Qt.Key_O: b"\x0f",
            Qt.Key_P: b"\x10", Qt.Key_Q: b"\x11", Qt.Key_R: b"\x12",
            Qt.Key_S: b"\x13", Qt.Key_T: b"\x14", Qt.Key_U: b"\x15",
            Qt.Key_V: b"\x16", Qt.Key_W: b"\x17", Qt.Key_X: b"\x18",
            Qt.Key_Y: b"\x19", Qt.Key_Z: b"\x1a",
            Qt.Key_BracketLeft: b"\x1b",  # Ctrl+[ = ESC
            Qt.Key_Slash: b"\x1f",
            Qt.Key_BraceLeft: b"\x1b",
        }
        return mapping.get(key)

    def _paste_from_clipboard(self):
        """从剪贴板粘贴文本到终端"""
        from PyQt5.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        if text:
            self.input_data.emit(text.encode("utf-8"))

    def contextMenuEvent(self, event):
        """右键菜单: 复制 / 粘贴 / 全选 / 清屏 / 终止"""
        from PyQt5.QtWidgets import QMenu, QAction

        has_selection = self.textCursor().hasSelection()

        menu = QMenu(self)

        copy_action = menu.addAction("复制")
        copy_action.setEnabled(has_selection)
        copy_action.triggered.connect(self.copy)

        paste_action = menu.addAction("粘贴")
        paste_action.triggered.connect(self._paste_from_clipboard)

        menu.addSeparator()

        select_all = menu.addAction("全选")
        select_all.triggered.connect(self.selectAll)

        clear_action = menu.addAction("清屏")
        clear_action.triggered.connect(self._clear_screen)

        menu.addSeparator()

        # 终止终端 (红色警告样式)
        kill_action = QAction("终止终端", menu)
        kill_action.triggered.connect(self._request_terminate)
        menu.addAction(kill_action)

        menu.exec_(event.globalPos())

    def _clear_screen(self):
        """发送 Ctrl+L 清屏"""
        self.input_data.emit(b"\x0c")

    def _request_terminate(self):
        """请求终止当前终端会话"""
        self.terminate_requested.emit()


# ─── 键映射表 ─────────────────────────────────────────────────

_KEY_TO_SEQUENCE: dict[int, bytes] = {
    Qt.Key_Enter:     b"\r",
    Qt.Key_Return:    b"\r",
    Qt.Key_Tab:       b"\t",
    Qt.Key_Backspace: b"\x7f",
    Qt.Key_Escape:    b"\x1b",
    Qt.Key_Up:        b"\x1b[A",
    Qt.Key_Down:      b"\x1b[B",
    Qt.Key_Right:     b"\x1b[C",
    Qt.Key_Left:      b"\x1b[D",
    Qt.Key_Home:      b"\x1b[H",
    Qt.Key_End:       b"\x1b[F",
    Qt.Key_PageUp:    b"\x1b[5~",
    Qt.Key_PageDown:  b"\x1b[6~",
    Qt.Key_Insert:    b"\x1b[2~",
    Qt.Key_Delete:    b"\x1b[3~",
    Qt.Key_F1:        b"\x1bOP",
    Qt.Key_F2:        b"\x1bOQ",
    Qt.Key_F3:        b"\x1bOR",
    Qt.Key_F4:        b"\x1bOS",
    Qt.Key_F5:        b"\x1b[15~",
    Qt.Key_F6:        b"\x1b[17~",
    Qt.Key_F7:        b"\x1b[18~",
    Qt.Key_F8:        b"\x1b[19~",
    Qt.Key_F9:        b"\x1b[20~",
    Qt.Key_F10:       b"\x1b[21~",
    Qt.Key_F11:       b"\x1b[23~",
    Qt.Key_F12:       b"\x1b[24~",
}
