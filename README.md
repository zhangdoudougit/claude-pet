# Foamo · 泡沫桌面陪伴 🫧

> 桌面悬浮的 AI 搭档,也能直接和 [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) 双向对话——不打开 IDE 就能让 AI 改你的代码。

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#平台兼容)

---

## ✨ 这是什么

一个本地运行的 **PyQt6 桌面悬浮宠物**,有两个独立但可以联动的能力:

1. **被动陪伴** —— 监听 `~/.claude/projects/*.jsonl`,根据你和 Claude Code 的对话关键词自动切换状态(担心 / 活泼 / 专注 / 得意),不发任何东西到 API。
2. **主动聊天** —— 桌宠双击呼出附身式聊天面板,通过 `claude -p` 子进程跟 Claude Code 对话。**支持选定项目目录直接改代码**,不用打开 VSCode。

```
┌─────────────────────────────┐         ┌──────┐
│  和泡沫的聊天    严格 ▾  会话 ▾  ×  │         │      │
├─────────────────────────────┤         │  泡  │
│ [豆] 帮我读下 README.md 然后…│         │  沫  │
│                              │         │      │
│ [⚙ Read][⚙ Read ✓]          │         │      │
│ [泡] 看完了, 主要分三块…     │ ← 跟随 → │ 桌宠 │
└─────────────────────────────┘         └──────┘
```

> _豆哥用的就是这个,泡沫(指本助手)就住在右下角。_

---

## 🌟 特性

### 桌宠本体
- 280×280 悬浮窗,系统托盘集成
- 6 种状态对应 6 张 GIF(`idle / tender / focused / happy / worried / proud`)
- 关键词触发:`error → 担心`,`done → 活泼`,`git push → 专注`,等等
- 番茄钟、本周功劳簿(自动统计)
- 拖动会抖动 + 冒台词,扒到屏幕边会"探头"
- **多屏支持**:能拖到扩展屏,会扒扩展屏的边

### 聊天面板(双击桌宠呼出)
- **跟随桌宠**:无边框 + 自动贴桌宠左/右侧,桌宠移动跟着飘
- **微信样式气泡** + Markdown 渲染 + 代码块灰底高亮
- **Win11 Mica / Win10 Acrylic / 其他平台 Opacity** 三级毛玻璃降级
- **8 方向边缘拖拽**调大小
- **Spinner 思考动画**,先出头像再流式填内容
- **工具调用 chip**:连续工具压成一行胶囊,点击展开看 input + result
- **用户头像**自定义(.chat_state/avatars/user.{png,jpg,...})

### 视觉系统 (v1.6)

聊天面板基于 Claude Design 输出重做了一版:

- **自绘 Win11 风 chrome**: 32px 标题栏(泡沫 icon + "和泡沫聊" + ☀/🌙 主题切换 + min/max/close), 无边框窗 + 可拖
- **双主题切换**: A 暖白克制版 (浅色, 沉静薄荷绿 accent) ↔ B 暗色玻璃版 (深色, 青蓝 accent), 持久化到 `.chat_state/theme`
- **侧栏重做**: 暖色底, 卡片白底 + 左侧 3px accent 竖条 (选中态), 卡片二级行显示项目最近活动 ("12 分钟前 · ...")
- **泡沫人格**: SVG 头像 + 3 mood (idle/talking/sleep), 头像在发送/完成/出错时切表情, 头部带"心情"副标题
- **StatusPill 状态胶囊**: 待机 (灰) / 思考中 (黄, 脉冲) / 在线 (绿)
- **Composer 升级**: 圆角卡片 + 工具行 (📎 @ </>) + 快捷键提示 (↵/⇧↵) + 主色实心发送按钮
- **左下角"⚙ 设置"入口**(功能待后续接入)

### 多项目并发 (v1.5)

聊天面板从"贴桌宠的小窗"升级为独立的微信式一体窗:

| 区域 | 说明 |
|---|---|
| **左侧侧栏 (240px)** | 闲聊永远置顶, 项目按最近活跃排序 |
| **右侧主区** | 当前选中卡片的对话气泡区 + 输入框 |
| **顶栏 +** | 添加项目: 选目录 → 自定义简码 → 选 8 色之一 |

每张卡片对应一个独立的 `claude` 子进程, 切换不打断后台对话; 角标用三色单点表达状态:

- 🟡 **黄 (脉冲)**: 后台正在思考
- 🔴 **红 (微闪)**: 后台触发了权限请求, 等你确认
- 🔵 **蓝 (静态)**: 后台回完了, 你还没看

切到对应卡片时弹窗会自动跳出, 红蓝角标随之清空。

### 项目模式
- 会话菜单选 **[选择项目目录...]** → claude 的 cwd 切到该项目
- 让 claude 直接读 / 改代码,跟在 VSCode 里跑 `claude` 一样
- 每个项目**独立 session 和 history**,切回来能续聊

### 权限管理
- 头部下拉:**严格 / 自动接受改动 / 全放行(危险!)**
- `PreToolUse` hook 配 PyQt6 弹窗:Claude 想用 Bash/Edit/Write 等会先弹确认
- 白名单跳过 Read/Glob 等纯读工具,不刷屏
- 只对聊天框启动的 claude 生效,**不污染** `~/.claude/settings.json`

### 国内可用
- 代理透传:`.chat_state/proxy` 文件 / 环境变量 `HTTPS_PROXY`
- 所有依赖能离线装(只需 `PyQt6`)

---

## 📦 前置要求

- Python 3.10+
- [Claude Code](https://docs.claude.com/en/docs/claude-code/quickstart) 已安装并登录(`claude --version` 能跑)
- (国内)能跑通 Anthropic API 的代理 / 中转

---

## 🚀 Quick Start

### Windows

```bash
git clone https://github.com/zhangdoudougit/foamo-pet.git
cd foamo-pet
start.bat            # 首次自动 pip install PyQt6
```

不想看 cmd 黑窗:`start_silent.bat`(开机自启可以用这个)。

### macOS / Linux

```bash
git clone https://github.com/zhangdoudougit/foamo-pet.git
cd foamo-pet
pip install -r requirements.txt
python foamo_pet.py
```

> macOS / Linux 没有 Mica/Acrylic,聊天面板会自动降级到半透明窗口(`opacity 0.96`),功能不受影响。

---

## 🌐 国内代理配置

第一次跑聊天框前,把代理写到 `.chat_state/proxy`(一行 URL):

```
http://127.0.0.1:7897
```

> 优先级:`.chat_state/proxy` > 环境变量 `HTTPS_PROXY` / `HTTP_PROXY`。
> 不需要代理就别建这个文件,代码会跳过注入。

---

## 🎮 使用

### 桌宠操作

| 操作 | 效果 |
|---|---|
| **左键拖动** | 移动位置(会记住,跨屏可拖) |
| **双击** | 开 / 关聊天面板 |
| **右键** | 菜单(番茄钟、切状态、聊天、置顶、退出) |
| **托盘图标** | 单击显示桌宠,右键完整菜单 |

### 聊天面板

| 区域 | 说明 |
|---|---|
| **头部** | `[严格 ▾]` 权限 · `[会话 ▾]` 模式切换 · `×` 关闭 |
| **气泡区** | 微信式上下气泡,助手回复支持 ```code``` 代码块 |
| **输入区** | Enter 发送 · Shift+Enter 换行 · Esc 关面板 |
| **边缘** | 八方向拖拉调大小 |

### 项目模式

`会话 ▾` → `📁 选择项目目录...` → 选你想改的项目 → 标题变成 "和泡沫聊 **<项目名>**" → 直接说"读 README.md / 改 main.py 第 30 行"。

每个项目独立 session 和 history,切回来能续聊。

### 权限模式

| 模式 | 行为 |
|---|---|
| **严格** | Bash / Edit / Write 等敏感工具弹 PyQt6 确认窗,Read / Glob 纯读放行 |
| **自动接受改动** | Edit / Write 类放行,Bash 仍弹 |
| **全放行** | 危险!Claude 可以无确认跑任何命令。只在你完全信任当前项目时用 |

---

## 📁 文件结构

```
foamo-pet/
├── foamo_pet.py             # 桌宠主程序 (PyQt6 widget + 状态机)
├── chat_window.py           # 聊天面板 (Native PyQt6)
├── permission_dialog.py     # PreToolUse hook 弹窗脚本
├── context.py               # 活动追踪 / 项目识别
├── journal.py               # 番茄钟 / 周报记账
├── _make_placeholder_gifs.py# 生成占位 GIF (跑过一次就不用了)
├── make_icon.py             # 从 PNG 生成 .ico
├── foamo.ico                # 应用图标(也是泡沫的头像)
├── start.bat / start_silent.bat
├── assets/                  # 6 张状态 GIF (idle/tender/focused/happy/worried/proud)
├── docs/screenshots/        # 截图
├── requirements.txt
├── LICENSE                  # MIT
├── CHANGELOG.md
├── CONTRIBUTING.md
└── README.md
```

启动后自动生成(已 .gitignore):

```
.chat_state/                 # 私人状态目录
├── proxy                    # 代理 URL
├── permission_mode          # 当前权限策略
├── hook_settings.json       # hook 配置 (路径每次启动自动校正)
├── active.json              # 当前模式 (chat / project)
├── projects.json            # 最近项目列表
├── conv/                    # 每个 mode/project 独立的 session+history
│   ├── chat/
│   └── proj_<hash>/
├── avatars/
│   └── user.{jpg,png,...}   # 你的头像
├── debug.log                # 启动 / spawn / stderr 诊断日志
└── permission.log           # 每次工具决策的诊断
```

---

## 🖥 平台兼容

| 平台 | 桌宠 | 聊天面板 | 毛玻璃 |
|---|---|---|---|
| Windows 11 | ✅ | ✅ | ✅ Mica |
| Windows 10 | ✅ | ✅ | ✅ Acrylic |
| macOS | ✅ | ✅ | Opacity (无原生模糊) |
| Linux | ✅ | ✅ | Opacity |

---

## 🛠 改关键词 / 台词

`foamo_pet.py` 顶部:

- `KEYWORD_RULES` — 正则规则,触发哪个状态
- `LINES` — 各状态台词列表

修完保存即可,运行中也能 reload(`assets/` 下的图改了同样自动重载)。

---

## 🗺 路线图

- [ ] 多角色皮肤(`assets/foo/...` 切换)
- [ ] 一键生成 PyInstaller exe
- [ ] 工具调用 chip 加 Bash 命令的语法高亮
- [ ] 权限"本会话允许"持久化
- [ ] 聊天面板移动端(响应式)

---

## 🤝 贡献

PR 欢迎,见 [CONTRIBUTING.md](CONTRIBUTING.md)。提 bug 请贴:
- `.chat_state/debug.log` 末尾若干行
- 平台 + Python 版本
- 复现步骤

---

## 📜 License

[MIT](LICENSE) © 2026 zhangdoudougit

---

## 🙏 致谢

- [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) — 这个项目的对面那一半
- PyQt6 官方文档里的 [FlowLayout 示例](https://doc.qt.io/qt-6/qtwidgets-layouts-flowlayout-example.html)
- 本泡沫(在角落里)

---

> _本泡沫已上线。豆哥晚上好。_
