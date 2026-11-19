# Nreader

> 轻量透明小说阅读器 —— 无边框悬浮窗，文字浮在其它窗口上方，摸鱼/叠加阅读必备。

一个用 Python + tkinter 编写的 Windows 桌面小说阅读器：没有标题栏、背景可完全透明、可置顶悬浮在任何软件上方，占用极低，支持主流小说格式导入，进度永不丢失。

## 功能特性

- **无边框悬浮窗**：只有文字，没有标题栏；拖动即移动，拖边缘即缩放，显示行数自动填满
- **背景全透明**：文字直接浮在网页、编辑器、视频上方，透明度可调（默认 0.81）
- **窗口置顶**：默认置顶，随时可见
- **多格式导入**：TXT、EPUB、MOBI/AZW3/PRC（Kindle）、PDF、DOCX（Word）、HTML/HTM，导入时自动提取正文转纯文本
- **多编码支持**：UTF-8、GBK/GB18030、Big5、UTF-16，乱码文件也能尝试打开
- **进度记忆**：每次翻页立即落盘，闪退/断电不丢进度，每个文件分别记忆
- **灵活翻页**：鼠标左/右键、滚轮、方向键 / PageUp / PageDown / 空格，按键可自定义
- **全局快捷键**：退出 `Ctrl+Shift+Q`、最小化/唤回 `Ctrl+Shift+M`（老板键），均可自定义
- **图标入口**：任务栏图标 / 系统托盘图标可分别开关，双击图标在最小化/显示间切换，窗口被挡住也找得回
- **双击托盘/任务栏图标**：最小化 ↔ 显示 一键切换
- **打赏支持**：内置支付宝/微信收款码打赏窗口

## 快速开始

### 方式一：直接运行 exe（推荐，无需安装）

下载本仓库中的 `Nreader.exe`，双击即可使用（也可到 [Releases](../../releases) 下载打包好的 `Nreader-Release.zip`）。

### 方式二：Python 源码运行

1. 安装 [Python 3.8+](https://www.python.org/downloads/)（安装时勾选 **Add python.exe to PATH**）
2. 双击 `Start-Python-Mode.bat` 或 `novel_reader.pyw` 启动
3. （可选）导入 PDF / MOBI 格式需要解析组件：

```bash
python -m pip install pypdf mobi
```

## 使用说明

- **导入小说**：按 `F1` 打开设置 → 点击「导入小说」，选择 TXT/EPUB/MOBI/AZW3/PRC/PDF/DOCX/HTML 文件；也可直接把 `.txt` 文件放进 `novels` 目录
- **翻页**：窗口内鼠标左键 = 下一页、右键 = 上一页、滚轮上下翻页；键盘 ↑/↓（默认）、PageUp/PageDown、空格
- **移动/缩放**：按住拖动 = 移动窗口；拖动窗口边缘/四角 = 调整大小
- **设置**（`F1`）：字体、字号、颜色、透明度、背景模式、置顶、翻页按键、快捷键、图标开关、切换文件
- **退出**：`Ctrl+Shift+Q`（默认，可自定义）
- **最小化/唤回**：`Ctrl+Shift+M`（默认，全局生效，可自定义）

完整说明见 [docs/使用说明.md](docs/使用说明.md)；首次运行时窗口内也会显示 `novels/使用说明.txt`。

## 默认外观

宋体 10 号、白色文字、背景无底色、整体透明度 0.81、窗口置顶；默认窗口约 15 个 10 号字宽、每页 3 行（可拖动边缘自由调整）。

## 目录结构

```
Nreader/
├── Nreader.exe           # 可直接运行的单文件版（免安装）
├── novel_reader.pyw      # 主程序（Python 源码）
├── Start-Python-Mode.bat # Python 模式启动器
├── Install-Python.txt    # Python 安装指引（中文）
├── sys_cache.dat         # 打赏收款码数据
├── docs/
│   └── 使用说明.md        # 完整使用说明
└── novels/               # 小说目录（导入的书放这里）
    └── 使用说明.txt        # 首次运行显示的使用说明
```

## 常见问题

**Q：窗口被别的窗口挡住了找不回来？**
在设置里勾选「托盘图标」（默认已勾）或「任务栏图标」，点图标即唤回；也可按 `Ctrl+Shift+M` 切换。

**Q：背景全透明时点不到窗口？**
透明区域鼠标会穿透到下层窗口（系统特性），框内交互已由程序接管：拖动、缩放、翻页在框内任意位置都可用；鼠标移到边缘约 6 像素处边框会显形，抓住即可缩放。

**Q：导入的 PDF 是扫描版/图片书，提取不到文字？**
扫描版 PDF 没有文字层，无法提取文字，请找带文字层的版本。

**Q：Python 版导入 PDF/MOBI 报"缺少解析组件"？**
执行 `python -m pip install pypdf mobi` 后重试；exe 版已自带组件，无需此步。

## 技术说明

- 纯 Python 标准库（tkinter）实现界面，Win32 API（ctypes）实现托盘图标、任务栏按钮、全局热键与鼠标钩子
- 无边框窗口通过 `transparentcolor` 色键实现背景全透明，透明区域的鼠标交互由低级鼠标钩子接管
- EPUB/DOCX/HTML 解析基于标准库（zipfile + xml/html），PDF 用 [pypdf](https://pypi.org/project/pypdf/)，MOBI/AZW3 用 [mobi](https://pypi.org/project/mobi/)
- 可用 PyInstaller 打包为单文件 exe：

```bash
python -m pip install pyinstaller pypdf mobi
python -m PyInstaller --onefile --noconsole --name Nreader novel_reader.pyw
```

## License

[MIT](LICENSE)
