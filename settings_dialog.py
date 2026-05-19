"""SettingsDialog — 新 ChatWebWindow 的"设置"按钮入口.

QTabWidget 分三个 tab:
- MCP 服务器  : 委托给老的 MCPManagerDialog (作为子 dialog 弹出)
- 钩子        : 显示 hook_settings.json 路径 + 重置/打开按钮
- 工作目录    : 显示 .chat_state / pasted / conv 目录, 提供"在资源管理器打开"

刻意做轻 — 老版的右键菜单功能可以慢慢补进来.
"""

from __future__ import annotations
import os
import sys
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QMessageBox, QPlainTextEdit, QFrame,
)

from chat_paths import (
    ROOT, STATE_DIR, CONV_DIR, PASTED_DIR,
    HOOK_SETTINGS_FILE, _ensure_hook_settings,
)


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
    """聊天框的设置入口 — Tab: MCP / 钩子 / 工作目录"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("泡沫 · 设置")
        self.setMinimumSize(560, 420)

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 12)
        v.setSpacing(10)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_mcp_tab(), "🔌 MCP 服务器")
        self.tabs.addTab(self._build_hooks_tab(), "🪝 钩子")
        self.tabs.addTab(self._build_dirs_tab(), "📁 工作目录")
        v.addWidget(self.tabs, 1)

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

    # -------- Tab 3: 工作目录 --------
    def _build_dirs_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 12, 8, 8)
        v.setSpacing(8)

        rows = [
            ("项目根目录", ROOT, "foamo_pet 安装位置"),
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
