"""SettingsDialog — 新 ChatWebWindow 的"设置"按钮入口.

QTabWidget:
- MCP 服务器  : 委托给老的 MCPManagerWidget
- 钩子        : 显示 hook_settings.json 路径 + 重置/打开按钮
- 模型 / API  : .chat_state/env.json — 第三方模型 (DeepSeek / Qwen / GLM 等) 接入
- 工作目录    : 显示 .chat_state / pasted / conv 目录, 提供"在资源管理器打开"
"""

from __future__ import annotations
import json
import os
import sys
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QMessageBox, QPlainTextEdit, QFrame, QMenu,
)

from chat_paths import (
    ROOT, STATE_DIR, CONV_DIR, PASTED_DIR,
    HOOK_SETTINGS_FILE, _ensure_hook_settings,
    ENV_FILE,
)


# env.json preset 模板 — 用户选了 preset 后, 把对应 JSON 灌进编辑器, 改 token 后保存
ENV_PRESETS: list[tuple[str, str]] = [
    ("空模板",
     '{\n  "ANTHROPIC_BASE_URL": "",\n  "ANTHROPIC_AUTH_TOKEN": "",\n  "ANTHROPIC_MODEL": ""\n}\n'),
    ("DeepSeek 官方 (Anthropic 兼容端点)",
     '{\n'
     '  "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",\n'
     '  "ANTHROPIC_AUTH_TOKEN": "sk-你的-deepseek-key",\n'
     '  "ANTHROPIC_MODEL": "deepseek-chat",\n'
     '  "HTTPS_PROXY": ""\n'
     '}\n'),
    ("智谱 GLM (Anthropic 兼容端点)",
     '{\n'
     '  "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",\n'
     '  "ANTHROPIC_AUTH_TOKEN": "你的-zhipu-key",\n'
     '  "ANTHROPIC_MODEL": "glm-4.5",\n'
     '  "HTTPS_PROXY": ""\n'
     '}\n'),
    ("Kimi (Anthropic 兼容端点)",
     '{\n'
     '  "ANTHROPIC_BASE_URL": "https://api.moonshot.cn/anthropic",\n'
     '  "ANTHROPIC_AUTH_TOKEN": "sk-你的-moonshot-key",\n'
     '  "ANTHROPIC_MODEL": "kimi-k2-turbo-preview",\n'
     '  "HTTPS_PROXY": ""\n'
     '}\n'),
    ("claude-code-router 本地代理",
     '{\n'
     '  "ANTHROPIC_BASE_URL": "http://127.0.0.1:3456",\n'
     '  "ANTHROPIC_AUTH_TOKEN": "any",\n'
     '  "HTTPS_PROXY": ""\n'
     '}\n'),
    ("LiteLLM 本地代理",
     '{\n'
     '  "ANTHROPIC_BASE_URL": "http://127.0.0.1:4000",\n'
     '  "ANTHROPIC_AUTH_TOKEN": "sk-litellm-master",\n'
     '  "ANTHROPIC_MODEL": "anthropic/claude-3-5-sonnet",\n'
     '  "HTTPS_PROXY": ""\n'
     '}\n'),
]


def _open_in_explorer(path: Path):
    """在文件管理器里打开目录 / 选中文件 (跨平台)."""
    try:
        p = str(path)
        if sys.platform.startswith("win"):
            if path.is_file():
                subprocess.Popen(["explorer", "/select,", p])
            else:
                os.startfile(p)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R" if path.is_file() else "", p])
        else:
            subprocess.Popen(["xdg-open", str(path.parent if path.is_file() else path)])
    except Exception as e:
        QMessageBox.warning(None, "打开失败", str(e))


class SettingsDialog(QDialog):
    """聊天框的设置入口 — Tab: MCP / 钩子 / 模型 / 工作目录.

    initial_tab: 默认 None (跳到第 0 个 MCP). 可传字符串别名 (mcp/hooks/env/dirs)
    或 int 索引, 用于"从首启引导直跳到模型/API"等场景.
    """

    TAB_ALIASES = {"mcp": 0, "hooks": 1, "env": 2, "dirs": 3}

    def __init__(self, parent=None, initial_tab=None):
        super().__init__(parent)
        self.setWindowTitle("泡沫 · 设置")
        self.setMinimumSize(560, 420)

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 12)
        v.setSpacing(10)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_mcp_tab(), "🔌 MCP 服务器")
        self.tabs.addTab(self._build_hooks_tab(), "🪝 钩子")
        self.tabs.addTab(self._build_env_tab(), "🌐 模型 / API")
        self.tabs.addTab(self._build_dirs_tab(), "📁 工作目录")
        v.addWidget(self.tabs, 1)

        if initial_tab is not None:
            if isinstance(initial_tab, str):
                idx = self.TAB_ALIASES.get(initial_tab, 0)
            else:
                idx = int(initial_tab)
            if 0 <= idx < self.tabs.count():
                self.tabs.setCurrentIndex(idx)

        # 底部关闭
        bar = QHBoxLayout()
        bar.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bar.addWidget(close_btn)
        v.addLayout(bar)

    # -------- Tab 1: MCP --------
    def _build_mcp_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 10, 8, 8)
        v.setSpacing(8)

        desc = QLabel(
            "全局 MCP 服务器 — 所有项目和闲聊共享。"
            "勾选 = 启用 (下次发消息时通过 <code>claude --mcp-config</code> 加载),"
            "取消勾选 = 禁用 (保留配置但不加载)。"
        )
        desc.setTextFormat(Qt.TextFormat.RichText)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #6b6457;")
        v.addWidget(desc)

        try:
            from mcp_manager import MCPManagerWidget
            v.addWidget(MCPManagerWidget(ROOT, w), 1)
        except Exception as e:
            err = QLabel(f"MCP 管理器加载失败:\n{e}")
            err.setStyleSheet("color: #c44; padding: 12px;")
            err.setWordWrap(True)
            v.addWidget(err)
            v.addStretch(1)
        return w

    # -------- Tab 2: 钩子 --------
    def _build_hooks_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 12, 8, 8)
        v.setSpacing(10)

        desc = QLabel(
            "Claude Code 的 PreToolUse 钩子 — 拦截 Bash/Edit/Write 等敏感工具,\n"
            "弹原生确认对话框 (permission_dialog.py)。"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #6b6457;")
        v.addWidget(desc)

        path_lbl = QLabel(f"配置文件: <code>{HOOK_SETTINGS_FILE}</code>")
        path_lbl.setTextFormat(Qt.TextFormat.RichText)
        path_lbl.setWordWrap(True)
        v.addWidget(path_lbl)

        self.hooks_text = QPlainTextEdit()
        self.hooks_text.setReadOnly(True)
        f = QFont("Consolas")
        f.setPointSize(9)
        self.hooks_text.setFont(f)
        self.hooks_text.setStyleSheet(
            "QPlainTextEdit { background: #f8f5ed; border: 1px solid #e8e3d6; "
            "border-radius: 6px; }"
        )
        self._refresh_hooks_text()
        v.addWidget(self.hooks_text, 1)

        bar = QHBoxLayout()
        reload_btn = QPushButton("重置为默认")
        reload_btn.setToolTip("按当前路径重新生成 hook_settings.json")
        reload_btn.clicked.connect(self._reset_hooks)
        bar.addWidget(reload_btn)

        open_btn = QPushButton("在资源管理器打开")
        open_btn.clicked.connect(lambda: _open_in_explorer(HOOK_SETTINGS_FILE))
        bar.addWidget(open_btn)
        bar.addStretch(1)
        v.addLayout(bar)
        return w

    def _refresh_hooks_text(self):
        try:
            if HOOK_SETTINGS_FILE.exists():
                self.hooks_text.setPlainText(
                    HOOK_SETTINGS_FILE.read_text(encoding="utf-8")
                )
            else:
                self.hooks_text.setPlainText("(还没生成 — 点'重置为默认'即可)")
        except Exception as e:
            self.hooks_text.setPlainText(f"读取失败: {e}")

    def _reset_hooks(self):
        try:
            if HOOK_SETTINGS_FILE.exists():
                HOOK_SETTINGS_FILE.unlink()
        except Exception:
            pass
        _ensure_hook_settings()
        self._refresh_hooks_text()
        QMessageBox.information(self, "已重置", "钩子配置已按当前 ROOT 重新生成。")

    # -------- Tab: 模型 / API (第三方模型接入) --------
    def _build_env_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 12, 8, 8)
        v.setSpacing(8)

        desc = QLabel(
            "<b>第三方模型接入</b> — 让 foamo 走 DeepSeek / GLM / Qwen / Kimi 等非官方模型。<br>"
            "原理: <code>claude</code> CLI 认 <code>ANTHROPIC_BASE_URL</code> 和 "
            "<code>ANTHROPIC_AUTH_TOKEN</code>, 指到任何 Anthropic 兼容的网关即可。<br>"
            "下面这份 JSON 会在每次发消息时<b>合并进子进程环境变量</b>, 只对 foamo 生效, "
            "不影响系统里其他 claude 用法。<br>"
            "<span style='color:#9a9387'>value 写空串 (\"\") 或 null 表示<b>删除该 key</b>, "
            "典型用途: <code>\"HTTPS_PROXY\": \"\"</code> 走国内直连网关时清掉代理。</span>"
        )
        desc.setTextFormat(Qt.TextFormat.RichText)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #524d40; font-size: 9pt; line-height: 1.55;")
        v.addWidget(desc)

        path_lbl = QLabel(f"配置文件: <code>{ENV_FILE}</code>")
        path_lbl.setTextFormat(Qt.TextFormat.RichText)
        path_lbl.setStyleSheet("color: #6b6457; font-size: 9pt;")
        v.addWidget(path_lbl)

        self.env_text = QPlainTextEdit()
        f = QFont("Consolas")
        f.setPointSize(10)
        self.env_text.setFont(f)
        self.env_text.setStyleSheet(
            "QPlainTextEdit { background: #f8f5ed; border: 1px solid #e8e3d6; "
            "border-radius: 6px; padding: 6px; }"
        )
        self._refresh_env_text()
        v.addWidget(self.env_text, 1)

        # 状态行 (校验提示)
        self.env_status = QLabel("")
        self.env_status.setStyleSheet("font-size: 9pt;")
        v.addWidget(self.env_status)

        bar = QHBoxLayout()
        preset_btn = QPushButton("插入示例 ▾")
        preset_btn.setToolTip("常见网关 preset — 选择后会覆盖编辑器内容, 改 token 后再保存")
        preset_btn.clicked.connect(lambda: self._show_preset_menu(preset_btn))
        bar.addWidget(preset_btn)

        save_btn = QPushButton("保存")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._save_env)
        bar.addWidget(save_btn)

        open_btn = QPushButton("在资源管理器打开")
        open_btn.clicked.connect(self._open_env_file)
        bar.addWidget(open_btn)

        clear_btn = QPushButton("清空 (走默认官方)")
        clear_btn.setToolTip("删除 env.json — 下次发消息回到默认 Anthropic 官方端点")
        clear_btn.clicked.connect(self._clear_env)
        bar.addWidget(clear_btn)

        bar.addStretch(1)
        v.addLayout(bar)
        return w

    def _refresh_env_text(self):
        try:
            if ENV_FILE.exists():
                self.env_text.setPlainText(ENV_FILE.read_text(encoding="utf-8"))
            else:
                self.env_text.setPlainText(
                    "// 还没配置. 点 [插入示例 ▾] 选一个网关 preset, 改 token 后保存.\n"
                    "// 这个注释不是合法 JSON, 保存时要先清掉.\n"
                )
        except Exception as e:
            self.env_text.setPlainText(f"// 读取失败: {e}\n")

    def _show_preset_menu(self, anchor: QPushButton):
        m = QMenu(self)
        for name, body in ENV_PRESETS:
            act = m.addAction(name)
            act.triggered.connect(lambda _=False, b=body: self._insert_preset(b))
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _insert_preset(self, body: str):
        self.env_text.setPlainText(body)
        self.env_status.setText("已填入示例 — 改完 token 后点 [保存]")
        self.env_status.setStyleSheet("color: #7a5a18; font-size: 9pt;")

    def _save_env(self):
        raw = self.env_text.toPlainText().strip()
        if not raw:
            # 空 = 清空配置
            self._clear_env(silent=True)
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            self.env_status.setText(f"JSON 解析失败: 行 {e.lineno} 列 {e.colno} — {e.msg}")
            self.env_status.setStyleSheet("color: #c44; font-size: 9pt;")
            return
        if not isinstance(data, dict):
            self.env_status.setText("根必须是 object {} 不能是 array / 字符串")
            self.env_status.setStyleSheet("color: #c44; font-size: 9pt;")
            return
        try:
            ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
            ENV_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return
        # 重读, 显示规范化结果
        self._refresh_env_text()
        # 列一下生效的 key
        keys = ", ".join(data.keys()) if data else "(空)"
        self.env_status.setText(f"已保存 → 下次发消息生效. 注入: {keys}")
        self.env_status.setStyleSheet("color: #2d5a3b; font-size: 9pt;")

    def _open_env_file(self):
        # 若文件不存在, 先创建一个空 {}, 让用户能用编辑器打开
        if not ENV_FILE.exists():
            try:
                ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
                ENV_FILE.write_text("{}\n", encoding="utf-8")
            except Exception as e:
                QMessageBox.warning(self, "创建失败", str(e))
                return
        _open_in_explorer(ENV_FILE)

    def _clear_env(self, silent: bool = False):
        if not silent:
            ans = QMessageBox.question(
                self, "确认清空",
                "删除 env.json — 之后发消息将回到默认 Anthropic 官方端点。继续?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        try:
            if ENV_FILE.exists():
                ENV_FILE.unlink()
        except Exception as e:
            QMessageBox.warning(self, "删除失败", str(e))
            return
        self._refresh_env_text()
        self.env_status.setText("已清空 — 已回到默认官方端点")
        self.env_status.setStyleSheet("color: #2d5a3b; font-size: 9pt;")

    # -------- Tab: 工作目录 --------
    def _build_dirs_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 12, 8, 8)
        v.setSpacing(8)

        rows = [
            ("项目根目录", ROOT, "Claude Pet 安装位置"),
            ("状态目录", STATE_DIR, "代理 / 历史 / 会话 都在这"),
            ("会话目录", CONV_DIR, "每个项目一个子文件夹"),
            ("粘贴图片", PASTED_DIR, "粘贴/拖入的截图落盘在这, 最近 50 张"),
        ]
        for name, path, hint in rows:
            v.addWidget(self._dir_row(name, path, hint))
        v.addStretch(1)
        return w

    def _dir_row(self, name: str, path: Path, hint: str) -> QWidget:
        row = QFrame()
        row.setObjectName("dir_row")
        row.setStyleSheet(
            "#dir_row { background: #faf7ef; border: 1px solid #efeadb; "
            "border-radius: 8px; }"
        )
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(10)

        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel(f"<b>{name}</b>")
        col.addWidget(title)
        path_lbl = QLabel(f"<code>{path}</code>")
        path_lbl.setTextFormat(Qt.TextFormat.RichText)
        path_lbl.setStyleSheet("color: #524d40; font-size: 9pt;")
        path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(path_lbl)
        hint_lbl = QLabel(hint)
        hint_lbl.setStyleSheet("color: #9a9387; font-size: 8pt;")
        col.addWidget(hint_lbl)
        h.addLayout(col, 1)

        btn = QPushButton("打开")
        btn.clicked.connect(lambda _, p=path: _open_in_explorer(p))
        h.addWidget(btn)
        return row
