"""Terminal scrollback 回归测试 — 验证 pyte 历史行渲染与滚动行为。

pyte 是纯 Python, 测试不依赖 Qt 部件: 直接驱动 pyte.HistoryScreen,
通过 FakeDisplay 调用 TerminalDisplay._build_rows() 的真实实现
(该方法只访问 self._screen / 缓存字段, 不触碰任何 Qt 对象)。

运行: cd /home/book/Projects/MMTwork/sshtool && python3 -m pytest tests/ -q
"""

from collections import deque

import pyte

from sshtool.terminal_display import TerminalDisplay


class FakeDisplay:
    """模拟 TerminalDisplay 的渲染状态, 仅承载 _build_rows 所需字段。"""

    def __init__(self, screen):
        self._screen = screen
        self._history_text = deque()
        self._history_objs = deque()
        self._cache_cols = screen.columns
        self._history_dirty = False
        self._dropped_history = 0


def make_screen(cols=80, lines=24, history=5000):
    return pyte.HistoryScreen(cols, lines, history=history)


def feed(screen, lines):
    """逐行喂入并以 CRLF 换行, 使超出屏幕的行滚入 history.top。"""
    stream = pyte.Stream(screen)
    for line in lines:
        stream.feed(f"{line}\r\n")


def build_rows(screen):
    """无缓存的朴素重建 (镜像 _build_rows 的行格式), 用于对照断言。"""
    cols = screen.columns
    hist = ["".join(r[x].data for x in range(cols)) for r in screen.history.top]
    return hist + screen.display


def test_history_rows_render_oldest_first():
    screen = make_screen()
    feed(screen, [f"line-{i:04d}" for i in range(60)])

    fd = FakeDisplay(screen)
    rows, row_objs = TerminalDisplay._build_rows(fd)

    # 超出屏幕的行全部滚入历史 (历史 + 屏幕 = 全部 block)
    assert len(screen.history.top) > 0
    assert len(rows) == len(screen.history.top) + screen.lines
    assert len(rows) == len(row_objs)

    # 历史行最旧在前: 文档第 0 行是第一次喂入的行
    assert "line-0000" in rows[0]
    # 与朴素重建完全一致 (验证缓存正确性)
    assert rows == build_rows(screen)


def test_history_capped_at_5000():
    screen = make_screen()
    feed(screen, [f"line-{i:06d}" for i in range(5200)])

    # deque(maxlen=5000) 会挤出最旧行
    assert len(screen.history.top) == 5000

    fd = FakeDisplay(screen)
    rows, _ = TerminalDisplay._build_rows(fd)
    assert len(rows) == 5000 + screen.lines
    # 最早的输入已被挤出
    assert "line-000000" not in rows[0]


def test_cache_survives_deque_rotation():
    """deque 满 (maxlen=5000) 后挤出最旧行, 缓存不能因此卡死在最早的 5000 行。

    逐批喂入并每次调用 _build_rows 填充缓存, 直到越过 5000 上限触发旋转。
    """
    screen = make_screen()
    fd = FakeDisplay(screen)
    line_no = 0
    for _ in range(60):  # 60 × 100 = 6000 行 > 5000 上限
        feed(screen, [f"line-{line_no + i:06d}" for i in range(100)])
        line_no += 100
        rows, _ = TerminalDisplay._build_rows(fd)
        assert rows == build_rows(screen)
        assert len(rows) == len(screen.history.top) + screen.lines
    # 旋转后: 历史仍被 5000 上限截断, 最新输入可见
    assert len(screen.history.top) == 5000
    assert any("line-005999" in r for r in rows[-screen.lines:])


def test_history_clear_branch_when_cache_populated():
    """缓存已填充时 ESC[3J 清历史 → 走 not history 清空分支, 置 _history_dirty。"""
    screen = make_screen()
    fd = FakeDisplay(screen)
    feed(screen, [f"line-{i:04d}" for i in range(40)])
    TerminalDisplay._build_rows(fd)  # 先填充缓存
    assert len(fd._history_objs) > 0

    screen.erase_in_display(3)
    rows, _ = TerminalDisplay._build_rows(fd)
    assert len(fd._history_objs) == 0
    assert fd._history_dirty is True
    assert len(rows) == screen.lines
    assert rows == build_rows(screen)


def test_cache_stays_in_sync_across_growth():
    """缓存增量路径: 持续输出后, 缓存结果仍与朴素重建一致。"""
    screen = make_screen()
    fd = FakeDisplay(screen)
    feed(screen, [f"line-{i:04d}" for i in range(40)])

    rows1, _ = TerminalDisplay._build_rows(fd)
    assert rows1 == build_rows(screen)

    feed(screen, [f"more-{i:04d}" for i in range(20)])
    rows2, _ = TerminalDisplay._build_rows(fd)
    assert rows2 == build_rows(screen)
    assert len(rows2) == len(rows1) + 20


def test_column_shrink_truncates_history():
    screen = make_screen(cols=120)
    feed(screen, [f"x" * 110 for _ in range(30)])
    screen.resize(screen.lines, 40)  # 缩窄到 40 列

    fd = FakeDisplay(screen)
    rows, _ = TerminalDisplay._build_rows(fd)
    # 历史行按新列宽截断, 不抛 IndexError
    assert all(len(r) <= 40 for r in rows)
    assert rows == build_rows(screen)


def test_history_clear_via_erase_in_display_3():
    screen = make_screen()
    feed(screen, [f"line-{i:04d}" for i in range(40)])
    screen.erase_in_display(3)  # ESC[3J 清历史

    assert len(screen.history.top) == 0

    fd = FakeDisplay(screen)
    rows, _ = TerminalDisplay._build_rows(fd)
    assert len(rows) == screen.lines
    assert rows == build_rows(screen)


def test_append_path_preserves_ansi_colors():
    """增量追加的新 block 必须保留 ANSI 颜色。

    回归: 追加路径曾用 insertText() 直接写入文本, 丢失颜色。场景是一次喂入
    同时完成"换行滚动出新行"+"给该新行写绿色文本", 走的是 append 分支。
    Qt 部件测试, 使用 offscreen 平台, 无需真实显示器。
    """
    import os
    import sys

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtGui import QTextCursor
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    td = TerminalDisplay(80, 24)

    # 填满屏幕, 使后续换行滚动出历史
    for i in range(24):
        td.feed(f"fill-{i:03d}\r\n".encode())
    td._render()
    assert td.document().blockCount() == 1 + 24

    # 换行滚动 + 同批次给新底部行写绿色文本 → 新 block 走追加路径
    td.feed(b"\r\n\x1b[32mHI\x1b[0m")
    td._render()

    doc = td.document()
    assert doc.blockCount() == 2 + 24
    last = doc.findBlockByNumber(doc.blockCount() - 1)
    assert last.text().rstrip() == "HI"
    # 首个字符保留绿色 (pyte "green" → #9ece6a)
    cur = QTextCursor(last)
    cur.setPosition(last.position() + 1, QTextCursor.KeepAnchor)
    assert cur.charFormat().foreground().color().name() == "#9ece6a"


def test_autoscroll_preserves_scrolled_up_position():
    """用户上滚后新输出不得拉回底部; 回到底部后才恢复跟随。"""
    import os
    import sys

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    td = TerminalDisplay(80, 24)

    # 填满一屏以上, 产生可滚动内容
    for i in range(60):
        td.feed(f"line-{i:03d}\r\n".encode())
    td._render()

    vbar = td.verticalScrollBar()
    assert vbar.maximum() > 0             # 有可滚动空间
    assert vbar.value() == vbar.maximum() # 默认跟随底部

    # 用户上滚 → 新输出不得把视图拉回底部
    vbar.setValue(0)
    before = vbar.value()
    for i in range(5):
        td.feed(f"more-{i:03d}\r\n".encode())
    td._render()
    assert vbar.value() == before         # 视图保持在上滚位置

    # 回到底部 → 恢复跟随
    vbar.setValue(vbar.maximum())
    td.feed(b"again\r\n")
    td._render()
    assert vbar.value() == vbar.maximum()


def test_dropped_history_tracks_rotation():
    """历史满后每滚一行, _dropped_history 应记录被挤出的行数 (供删除文档前端)。"""
    screen = make_screen(cols=80, lines=24, history=50)
    fd = FakeDisplay(screen)
    feed(screen, [f"line-{i:04d}" for i in range(80)])  # 80-24=56 > 50 上限
    TerminalDisplay._build_rows(fd)
    assert len(fd._history_objs) == 50
    assert fd._dropped_history == 0  # 首次全量追加, 无旋转

    feed(screen, ["line-extra"])
    rows, _ = TerminalDisplay._build_rows(fd)
    assert fd._dropped_history == 1  # 满后滚一行, 挤出最旧 1 行
    assert rows == build_rows(screen)


def test_large_batch_no_missing_lines():
    """单批次滚动超过屏幕行数 / 已有历史长度时, 不得丢失行 (cat 大量文本回归)。"""
    import os
    import sys

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    td = TerminalDisplay(80, 24)

    # 第一批: 100 行
    for i in range(100):
        td.feed(f"line-{i:04d}\r\n".encode())
    td._render()

    # 第二批: 一次批次 100 行 (> 屏幕 24 行), 首尾连续
    for i in range(100, 200):
        td.feed(f"line-{i:04d}\r\n".encode())
    td._render()
    doc = td.document()
    assert "line-0000" in doc.findBlockByNumber(0).text()
    assert any("line-0199" in doc.findBlockByNumber(i).text()
               for i in range(doc.blockCount()))

    # 第三批: 一次批次 1300 行 (> 已有历史长度, 触发全量重建分支)
    for i in range(200, 1500):
        td.feed(f"line-{i:04d}\r\n".encode())
    td._render()
    doc = td.document()
    assert doc.blockCount() == 1024  # 1000 历史 + 24 屏幕, 封顶
    assert "line-0477" in doc.findBlockByNumber(0).text()  # 与 pyte history[0] 一致
    assert any("line-1499" in doc.findBlockByNumber(i).text()
               for i in range(doc.blockCount()))


def test_scroll_on_user_input():
    """敲命令 (用户输入) 应滚动到底部, 即使此前上滚查看日志。"""
    import os
    import sys

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    td = TerminalDisplay(80, 24)

    # 填满一屏以上, 产生可滚动内容
    for i in range(60):
        td.feed(f"line-{i:03d}\r\n".encode())
    td._render()

    vbar = td.verticalScrollBar()
    assert vbar.maximum() > 0
    vbar.setValue(0)  # 上滚查看日志

    # 用户输入命令 → 应回到底部显示回显
    td._emit_input(b"ls")
    td.feed(b"$ ls\r\n")
    td._render()
    assert vbar.value() == vbar.maximum()


def test_wide_char_rows_match_pyte_display():
    """宽字符行的朴素 join 与 pyte.display 逐字符一致。"""
    screen = make_screen()
    stream = pyte.Stream(screen)
    stream.feed("中文测试 123\r\n")
    stream.feed("ascii line\r\n")

    fd = FakeDisplay(screen)
    rows, _ = TerminalDisplay._build_rows(fd)
    assert rows == build_rows(screen)
    # 宽字符行内容保留, 且与屏幕上实际渲染一致
    assert rows[0] == screen.display[0]
    assert "中文测试" in rows[0]
