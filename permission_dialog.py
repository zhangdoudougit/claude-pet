"""
permission_dialog.py — Claude Code PreToolUse hook

读 stdin 的 hook JSON, 根据规则决定:
1. 白名单工具 (纯读) → 直接放行 (退出 0)
2. permission_mode = bypassPermissions → 直接放行
3. acceptEdits 模式下的 Edit/Write 类 → 放行
4. 否则 → 弹 PyQt6 模态对话框, 用户点 [允许]/[拒绝]

退出码:
- 0: 允许工具继续
- 2: 阻止 (stdout 内容会回给 claude 作为反馈)

通过 .chat_state/hook_settings.json + claude --settings 加载, 不污染用户全局 ~/.claude/settings.json。
"""
import json
import sys
from pathlib import Path


def _try_route_via_socket(tool_name: str, tool_input: dict, cwd: str) -> str | None:
    """Try to route the permission request to the main foamo process via local socket.

    Returns 'allow' / 'deny' on success, or None if the route is unavailable
    (env var missing, socket not listening, timeout, parse error).
    """
    import os
    conv_key = os.environ.get("FOAMO_CONV_KEY", "")
    if not conv_key:
        return None
    try:
        from PyQt6.QtCore import QCoreApplication
        from PyQt6.QtNetwork import QLocalSocket
    except ImportError:
        return None

    # QLocalSocket needs a QCoreApplication instance for the event loop.
    # hook 进程在 main() 返回时整体退出, 不用主动清理 app.
    if QCoreApplication.instance() is None:
        QCoreApplication(sys.argv)

    socket = QLocalSocket()
    socket.connectToServer("foamo_perm_v1")
    if not socket.waitForConnected(500):
        _log(f"[route] connect failed: {socket.errorString()}")
        return None

    payload = {
        "conv_key": conv_key,
        "payload": {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "cwd": cwd,
        },
    }
    socket.write(json.dumps(payload).encode("utf-8"))
    socket.flush()
    # 60s 给用户决策
    if not socket.waitForReadyRead(60_000):
        _log(f"[route] timeout waiting decision")
        return None

    raw = bytes(socket.readAll()).decode("utf-8", errors="replace")
    try:
        decision = json.loads(raw).get("decision")
    except json.JSONDecodeError:
        return None
    socket.disconnectFromServer()
    _log(f"[route] decision={decision}")
    return decision

ROOT = Path(__file__).parent
STATE_DIR = ROOT / ".chat_state"
PERMISSION_MODE_FILE = STATE_DIR / "permission_mode"
LOG_FILE = STATE_DIR / "permission.log"

# 纯读 / 安全工具 — 兜底白名单 (matcher 已经过滤过, 这里再保险一次)
WHITELIST_TOOLS = {
    "Read", "Glob", "Grep", "LS", "NotebookRead",
    "TodoWrite", "TodoRead",
}


def _log(msg: str):
    try:
        import time
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def get_mode() -> str:
    if PERMISSION_MODE_FILE.exists():
        m = PERMISSION_MODE_FILE.read_text(encoding="utf-8").strip()
        if m in ("default", "acceptEdits", "bypassPermissions"):
            return m
    return "default"


def format_input(tool_name: str, tool_input: dict) -> str:
    """RichText, dialog 主体内容"""
    import html as _h
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        desc = tool_input.get("description", "")
        out = (f"<b>命令:</b><br>"
               f"<code style='color:#c7254e;'>{_h.escape(cmd)}</code>")
        if desc:
            out += f"<br><br><b>说明:</b> {_h.escape(desc)}"
        return out
    if tool_name in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or "?"
        out = f"<b>修改文件:</b><br><code>{_h.escape(str(path))}</code>"
        if tool_name == "Write":
            content = str(tool_input.get("content", ""))
            preview = content[:200] + ("…" if len(content) > 200 else "")
            out += f"<br><br><b>新内容预览:</b><br><code>{_h.escape(preview)}</code>"
        elif tool_name == "Edit":
            old = str(tool_input.get("old_string", ""))[:120]
            new = str(tool_input.get("new_string", ""))[:120]
            out += (f"<br><br><b>替换:</b><br>"
                    f"<code style='color:#c7254e;'>- {_h.escape(old)}</code><br>"
                    f"<code style='color:#22863a;'>+ {_h.escape(new)}</code>")
        return out
    if tool_name == "WebFetch":
        url = tool_input.get("url", "?")
        return f"<b>抓取 URL:</b><br><code>{_h.escape(str(url))}</code>"
    # 默认: dump 全部参数
    s = json.dumps(tool_input, ensure_ascii=False, indent=2)
    if len(s) > 600:
        s = s[:600] + "\n…[省略]"
    return f"<pre style='background:#f4f4f4;padding:6px;'>{_h.escape(s)}</pre>"


def show_dialog(tool_name: str, tool_input: dict, cwd: str) -> bool:
    """True = 允许, False = 拒绝"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QGuiApplication, QIcon
    from PyQt6.QtWidgets import (
        QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    )

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    # foamo 头像作 dialog icon
    icon_path = ROOT / "foamo.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    dlg = QDialog()
    dlg.setWindowTitle("Claude 权限请求")
    dlg.setMinimumWidth(520)
    dlg.setWindowFlags(
        Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
    )
    if icon_path.exists():
        dlg.setWindowIcon(QIcon(str(icon_path)))

    v = QVBoxLayout(dlg)
    v.setContentsMargins(20, 16, 20, 16)
    v.setSpacing(10)

    title = QLabel(
        f"<h3 style='margin:0;'>⚠ Claude 想使用 "
        f"<code style='color:#c7254e;'>{tool_name}</code></h3>"
    )
    title.setTextFormat(Qt.TextFormat.RichText)
    v.addWidget(title)

    if cwd:
        cwd_l = QLabel(f"<span style='color:#888;font-size:11px;'>"
                       f"项目目录: {cwd}</span>")
        cwd_l.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(cwd_l)

    body = QLabel(format_input(tool_name, tool_input))
    body.setTextFormat(Qt.TextFormat.RichText)
    body.setWordWrap(True)
    body.setStyleSheet(
        "background: #f6f8fa; padding: 12px; border-radius: 5px; "
        "font-family: Consolas, Menlo, monospace; font-size: 12px; "
        "color: #2c2c2c;"
    )
    body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    v.addWidget(body)

    hint = QLabel(
        "<span style='color:#888;font-size:10px;'>"
        "想全部跳过这种弹窗?把聊天框头部权限切到 "
        "<b>全放行</b> 即可</span>"
    )
    hint.setTextFormat(Qt.TextFormat.RichText)
    v.addWidget(hint)

    btns = QHBoxLayout()
    btns.addStretch(1)

    deny = QPushButton("拒绝")
    deny.setShortcut("Esc")
    deny.clicked.connect(lambda: dlg.done(0))
    deny.setStyleSheet(
        "padding: 6px 16px; border-radius: 4px; "
        "border: 1px solid #d9d9d9; background: white; font-size: 12px;"
    )
    btns.addWidget(deny)

    allow = QPushButton("允许")
    allow.setDefault(True)
    allow.clicked.connect(lambda: dlg.done(1))
    allow.setStyleSheet(
        "background: #07c160; color: white; padding: 6px 18px; "
        "border-radius: 4px; border: 0; font-size: 12px; font-weight: bold;"
    )
    btns.addWidget(allow)

    v.addLayout(btns)

    # 居中 + 置顶激活
    screen = QGuiApplication.primaryScreen().availableGeometry()
    dlg.adjustSize()
    dlg.move(
        screen.center().x() - dlg.width() // 2,
        screen.center().y() - dlg.height() // 2,
    )
    dlg.raise_()
    dlg.activateWindow()

    return dlg.exec() == 1


def main():
    try:
        data = json.load(sys.stdin)
    except Exception as e:
        _log(f"stdin parse failed: {e}")
        sys.exit(0)  # 解析失败直接放行 (避免卡死)

    tool_name = data.get("tool_name", "?")
    tool_input = data.get("tool_input", {}) or {}
    cwd = data.get("cwd", "")

    # 1) 兜底白名单
    if tool_name in WHITELIST_TOOLS:
        sys.exit(0)

    # 2) 权限模式
    mode = get_mode()
    if mode == "bypassPermissions":
        _log(f"[bypass] {tool_name}")
        sys.exit(0)
    if mode == "acceptEdits" and tool_name in (
        "Edit", "MultiEdit", "Write", "NotebookEdit"
    ):
        _log(f"[acceptEdits] {tool_name}")
        sys.exit(0)

    # 3) 先尝试 socket 路由到主进程
    decision = _try_route_via_socket(tool_name, tool_input, cwd)
    if decision == "allow":
        _log(f"  → allow (routed)")
        sys.exit(0)
    if decision == "deny":
        _log(f"  → deny (routed)")
        print(f"用户在聊天框拒绝了 {tool_name} 调用")
        sys.exit(2)

    # 4) 兜底: 弹独立对话框 (route 失败 / 无 env / 老版本主进程)
    _log(f"prompt {tool_name} (standalone)")
    if show_dialog(tool_name, tool_input, cwd):
        _log(f"  → allow (standalone)")
        sys.exit(0)
    _log(f"  → deny (standalone)")
    print(f"用户在聊天框拒绝了 {tool_name} 调用")
    sys.exit(2)


if __name__ == "__main__":
    main()
