# SSHTool GUI

基于 SSH 隧道 + rsync 的双栏文件传输 GUI 工具。

```
本地 ↔ A733 网关 ↔ RK3588
```

## 功能

- **双栏文件浏览器**：左栏本地，右栏远程，均显示名称/大小/修改时间
- **按类型排序**：双栏均目录优先，文件按扩展名分组、同类型按名称排序
- **统一时间格式**：本地/远程修改时间格式一致（当年 `MM-DD HH:MM`，跨年 `YYYY-MM-DD HH:MM`）
- **拖拽传输**：从一边拖文件/文件夹到另一边即可传输；也支持从文件管理器直接拖入远程面板；拖到空白区域则传输到当前目录，传输前弹出确认对话框
- **Rsync 选项可选**：3 种预设模式（完整/增量/最简），排除项可独立勾选
- **持久 SSH 隧道**：连接一次，多次传输复用，无重复握手开销
- **预览模式**：dry-run 预览，不实际传输
- **传输确认**：每次传输前弹出确认对话框，显示源/目标路径
- **Ctrl+/- 缩放**：动态调整全局字体大小

## 环境依赖

```bash
sudo apt install python3-pyqt5 sshpass rsync
```

## 快速开始

```bash
# 编辑连接配置（首次运行自动生成默认配置）
python3 sshtool_gui.py        # 或 python3 -m sshtool

# 查看当前配置
python3 sshtool_gui.py --check-config
```

配置文件位于 `~/.config/sshtool/config.json`，首次运行自动生成。

## 连接配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| gw_host | 192.168.1.214 | 网关主机 |
| gw_port | 60000 | 网关 SSH 端口 |
| gw_user | bpx | 网关用户 |
| gw_pass | — | 网关密码 |
| rk_host | 10.21.20.200 | RK3588 主机 |
| rk_user | BAOBAO | RK3588 用户 |
| rk_pass | — | RK3588 密码 |
| rk_base_path | /home/BAOBAO/MM/workspace/mm_nav_ws | 远程基础路径 |
| tunnel_port | 22358 | 本地转发端口 |
| font_size | 29 | 界面字体大小 |

## 使用流程

1. 点击 **🔌 连接隧道** 建立 SSH 隧道
2. 远程目录自动加载，双击文件夹展开浏览
3. 选中文件/文件夹 → 拖到对面面板 → 确认对话框 → 传输
4. 或使用工具栏 **← 推送** / **→ 拉取** 按钮

## 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl` + `=` | 字体放大 |
| `Ctrl` + `-` | 字体缩小 |

## 传输选项

传输确认对话框中可选择 rsync 选项：

- **完整同步**：`-avz --delete` + 全部排除项（等同原脚本）
- **增量同步**：有排除项，无 `--delete`
- **最简同步**：仅 `-avz`，无排除
- **自定义**：手动勾选各项
- **覆盖策略**：对话框实时标注当前策略——默认覆盖/更新已存在文件（`--delete` 时同时删除多余文件）；可勾选 `--ignore-existing` 跳过已存在文件

## 项目结构

```
sshtool/
├── sync_to_rk3588.sh     # 原始 CLI 脚本
├── sshtool_gui.py        # 兼容启动器
├── sshtool/              # 主程序包 (python3 -m sshtool)
│   ├── __main__.py       # 包入口
│   ├── config.py         # 常量、默认配置、样式表、ConfigManager、通用格式工具
│   ├── ssh_utils.py      # SSH 隧道/远程命令工具
│   ├── rsync_options.py  # RsyncOptions 选项模型
│   ├── transfer_engine.py# QThread 传输引擎
│   ├── remote_fs.py      # 远程目录树模型
│   ├── dialogs.py        # 传输确认/连接配置对话框
│   ├── tree_views.py     # 树视图组件（类型排序、拖放、时间格式）
│   └── main_window.py    # 主窗口与入口
└── README.md
```
