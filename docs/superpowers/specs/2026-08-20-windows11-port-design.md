# SSHTool Windows 11 移植与打包设计

- 日期：2026-08-20
- 状态：已评审通过
- 目标：让当前 Linux 专用的 SSHTool GUI 在 Windows 11 上以**完整功能对等**运行，并产出自包含的 onedir release。

## 背景与约束

SSHTool 是「SSH 隧道 + rsync 的双栏文件传输 GUI」，当前代码只支持 Linux。跨平台障碍：

1. `terminal_session.py` 顶层 `import termios` / `import paramiko`：Windows 无 `termios`，且依赖未安装 → 启动即崩溃。
2. 本地终端用 POSIX PTY（`os.openpty` / `fcntl` / `/bin/bash` / `SIGWINCH`）→ Linux 专属。
3. 核心文件传输通过 `rsync` + `sshpass` 子进程调用 → Linux 二进制，Windows 没有。
4. 构建环境未装 PyQt5 / PyInstaller / paramiko / pywinpty。

已确认的需求决策：

- **功能范围**：完整对等 —— 文件传输 + 远程终端 + 本地终端全部在 Windows 可用。
- **传输工具链**：捆绑自包含 MSYS2 工具链（rsync + ssh + sshpass + bash），目标机器零安装。
- **架构**：单一代码库 + 平台抽象层（本地终端后端 / 工具链定位与路径转换），onedir 发布。

## 架构

单一代码库同时兼容 Linux 与 Windows，平台差异集中到两处抽象：

1. **本地终端后端**：POSIX PTY（Linux）vs ConPTY/pywinpty（Windows）。
2. **外部工具定位与路径转换**：裸命令名（Linux）vs 捆绑 `toolchain/` 下的 `.exe` + MSYS 路径转换（Windows）。

不采用：Windows 独立分叉（双份维护）、内联 `if os.name` 补丁（`import termios` 仍炸、边界模糊）、纯 Python SFTP 重写（丢失 rsync 语义，与「对等」冲突）。

## 组件

### 新增文件

**`sshtool/platform_tools.py`** — 工具链定位 + 路径转换

```python
import os, sys

IS_WINDOWS = sys.platform == "win32"

def _toolchain_dir() -> str:
    # 打包后: sys._MEIPASS/toolchain；源码开发: <项目>/toolchain
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(base, "toolchain")

def tool_path(name: str) -> str:
    """返回外部工具命令。Linux 裸名；Windows 返回 toolchain/<name>.exe"""
    if not IS_WINDOWS:
        return name
    return os.path.join(_toolchain_dir(), name + ".exe")

def to_msys_path(path: str) -> str:
    """Windows 本地路径 → MSYS 路径（/c/foo/bar）。Linux 原样返回。"""
    if not IS_WINDOWS:
        return path
    # C:\foo 或 C:/foo → /c/foo；UNC 路径暂不支持（源/目标均为本地盘）
    drive, _, rest = path.partition(":")
    if drive and len(drive) == 1:
        path = "/" + drive.lower() + rest.replace("\\", "/")
    return path
```

### 新增文件

**`sshtool/local_terminal_win.py`** — `WinConPtyTerminal(TerminalSession)`

- 用 `pywinpty.PTY.spawn(bash.exe, ["--login"])` 创建 ConPTY。
- 实现与 POSIX 版相同的接口：`run()`（后台线程读 PTY 输出并 `data_received.emit`）、`write(data)`、`resize(cols, rows)`、`stop()`、`pid()`。
- `bash.exe` 来自 `tool_path("bash")`。

### 修改文件

**`sshtool/terminal_session.py`**

- 移除顶层 `import termios`、`import fcntl`、`import tty`；这些下沉到 POSIX 后端子类。
- `import paramiko` 保持不变（跨平台，用于远程终端；仍保留在顶层，因为 `SSHTerminalSession` 依赖它）。
- 现有 `LocalTerminalSession` 拆为平台后端：
  - `PosixLocalTerminal`（即现有实现，仅 Linux 导入）。
  - `WinConPtyTerminal`（Windows，从 `local_terminal_win.py` 导入）。
- 模块级暴露 `LocalTerminalSession` 工厂/别名，按 `platform_tools.IS_WINDOWS` 选择后端，保持 `terminal_panel.py` 的 `LocalTerminalSession(cols, rows, parent=...)` 调用签名不变。

**`sshtool/ssh_utils.py`**

- `ssh_base_args()`、`open_tunnel()` 中 `sshpass`/`ssh` 改用 `platform_tools.tool_path("sshpass")` / `tool_path("ssh")`。

**`sshtool/transfer_engine.py`**

- `_do_rsync()` 中 `rsync` 改用 `tool_path("rsync")`；`ssh_cmd` 内嵌的 `sshpass`/`ssh` 改用 `tool_path()`。
- Windows 下，`push` 方向的本地 `sources`、`pull` 方向的本地 `dest` 经过 `to_msys_path()` 转换；远程路径（`user@127.0.0.1:path`）不变。

**`sshtool_windows.spec`**（新增，Linux 原 `sshtool.spec` 保留）

- 基于原 spec，针对 Windows 调整：
  - `datas=[("toolchain", "toolchain")]` 把工具链目录打进包。
  - `console=False`（GUI，无控制台窗口）。
  - `hiddenimports` 增加 `pywinpty` 相关；`excludes` 移除 `PyQt5.QtX11Extras` 等 Linux 专属、保留 Windows 平台插件，删去 Linux 无意义的排除项。
  - `name='sshtool'`，产出 `dist/sshtool/`（onedir）。

## 数据流

- **传输**：拖拽 → `TransferEngine` → 捆绑工具链验隧道（`sshpass`+`ssh`）→ `rsync -e "sshpass ... ssh ..."`；本地路径经 MSYS 转换，远程路径不变。
- **远程列表**：捆绑 `ssh` 执行 `stat -c "%F|%s|%Y|%n"`；远程命令在 RK3588 的 bash 上执行，逻辑不变。
- **本地终端**：ConPTY 起 `bash.exe --login`，读写/改尺寸桥接为 QThread 信号。
- **远程终端**：paramiko `invoke_shell`，跨平台，仅需安装依赖。

## 错误处理

- 工具链缺失（Windows 源码模式且无 `toolchain/`）：连接/传输前给出明确错误「未找到 rsync/sshpass，请检查 toolchain 目录」。
- ConPTY spawn 失败：与 POSIX 版一致，`data_received.emit("[终端错误: ...]")`。
- 含空格/中文的 Windows 路径：`to_msys_path` 正常转换，`rsync` 参数列表方式传入（不拼接 shell 字符串），避免引号注入；远程路径用 `shlex`/列表方式安全传递。

## 测试

- 回归：`tests/test_terminal_scrollback.py` 在 Linux 上仍需通过（确保抽象未破坏原行为）。
- 新增 `to_msys_path` 单元测试：盘符、正反斜杠、空格、相对路径。
- Windows 手工冒烟：启动 → 连隧道 → 双向传输 → 开本地终端（bash）→ 开远程终端。

## 构建与发布步骤

1. 安装依赖：`pip install pyqt5 pyinstaller paramiko`；Windows 另装 `pywinpty`。
2. 用 MSYS2 获取 `rsync` / `ssh` / `sshpass` / `bash` 及依赖 DLL，放入 `toolchain/` 暂存目录（`sshpass` 需在实施阶段确认 MSYS2 包名或预编译来源）。
3. 写 `sshtool_windows.spec` 并构建：`python -m PyInstaller --clean --noconfirm sshtool_windows.spec`。
4. 产物：`dist/sshtool/`（`sshtool.exe` + `toolchain/` + Qt/DLL），解压即用、零安装。

## 开放项（实施阶段确认）

- `sshpass` 的 Windows 二进制确切来源（MSYS2 包 vs 预编译）。
- `pywinpty` 与 PyQt5/Python 3.12 的版本兼容性。
