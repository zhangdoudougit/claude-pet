# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Changed
- 聊天面板改造为微信式一体窗 (左侧会话列表 + 右侧独立 panel)
  - 闲聊永远置顶, 项目按最近活跃排序
  - 每张卡片独立 claude 子进程, 切走后台继续跑
  - 角标语言: 黄=思考中、红=等权限确认、蓝=未读
  - 新增 "+" 按钮添加项目, 自定义简码 (2-4 字符) + 8 色调色板
- 聊天窗不再贴桌宠跟随, 改为独立窗口 (标准 chrome, 默认 900×620)

### Added
- `conversation_store.py` — 元数据 + 持久化
- `claude_worker.py` — 单个 claude 子进程封装
- `permission_router.py` — 多 worker 权限请求路由
- `chat_panel.py` — `ConversationPanel` 单会话视图
- `sidebar.py` — 会话列表 + `AddProjectDialog`

### Migration
- 老 `.chat_state/conv/proj_xxx/meta.json` 启动时自动补全 short_code/color/timestamps 字段

## [1.0.0] - 2026-05-06

首个公开发布版本。

### 桌宠本体
- PyQt6 280×280 悬浮窗 + 系统托盘
- 6 状态自动切换(`idle / tender / focused / happy / worried / proud`)
- 监听 `~/.claude/projects/*.jsonl` 关键词驱动状态机
- 番茄钟 + 本周功劳簿
- 拖动抖动 + 扒边探头
- **多屏**:用 `screenAt(center)` 而非 `primaryScreen()`,扩展屏可拖
- **双击桌宠** 切换聊天面板

### 聊天面板(Native PyQt6)
- 无边框 Tool 窗口,自动贴桌宠跟随移动
- 通过 `claude -p --output-format stream-json --include-partial-messages` 双向对话
- **多模式 session**:闲聊 / 多个项目独立 history
- **8 方向边缘拖拽**调大小
- **Win11 Mica / Win10 Acrylic / Opacity** 三级毛玻璃降级
- **微信样式** + Markdown 渲染(代码块、bold、italic、列表、标题)
- **Spinner 思考动画**(8 帧 Braille 旋转)
- **工具调用 chip**:连续工具压成一行胶囊,FlowLayout 自动换行,点击展开 input/result
- **用户头像** + **泡沫头像**(foamo.ico 自动选最大帧 128×128 高清渲染)
- 用户消息一行短不会被压成竖排(`Bubble.sizeHint()` 重写)
- 滚动条不被边缘拖拽吞掉

### 权限管理
- 头部下拉:**严格 / 自动接受改动 / 全放行**(对应 `--permission-mode`)
- `PreToolUse` hook (`permission_dialog.py`) 弹 PyQt6 模态确认窗
- 白名单跳过 `Read / Glob / Grep / LS / NotebookRead / TodoWrite/Read`
- 通过 `claude --settings .chat_state/hook_settings.json` 加载,**不污染**用户全局 `~/.claude/settings.json`
- 路径每次启动自动校正,clone 到不同位置也能用

### 国内可用
- `.chat_state/proxy` 文件 + 环境变量双兜底
- `QProcessEnvironment` 注入 HTTP_PROXY / HTTPS_PROXY 给 claude 子进程

### 开发体验
- `.chat_state/debug.log` 持续记录 spawn / stderr / glass / finished
- `.chat_state/permission.log` 记录每次权限决策
- 单跑 `python chat_window.py`(假桌宠 + 面板)调试聊天面板,不必启完整 foamo_pet

[Unreleased]: https://github.com/zhangdoudougit/foamo-pet/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/zhangdoudougit/foamo-pet/releases/tag/v1.0.0
