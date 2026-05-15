"""
chat_window.py - 泡沫聊天面板 (附着在桌宠旁边, 多项目模式)

无边框 + Tool 窗口,贴在 FoamoWidget 旁边一起飘,桌宠移动时面板跟着走。
通过 QProcess 调 `claude -p --output-format stream-json` 与 Claude Code 对话。

模式:
- 闲聊模式: cwd = foamo_pet 自己 (默认)
- 项目模式: cwd = 用户选定的项目目录, 可以让 claude 直接改代码

集成:
    from chat_window import ChatPanel
    panel = ChatPanel()
    panel.attach_to(foamo_widget)
    panel.toggle()  # 显示/隐藏

状态目录:<foamo_pet>/.chat_state/
    proxy              代理 URL (一行)
    debug.log          诊断日志
    active.json        当前活跃 mode/path
    projects.json      最近项目列表 [{path, name, last_used}]
    conv/<key>/        每个项目/闲聊一个目录
        session        续话 UUID
        history.json   聊天记录
        meta.json      原始路径 / 名字 (项目模式才有)
"""

import hashlib
import html as _html
import json
import math as _math
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path

from PyQt6.QtCore import (
    Qt, QProcess, QProcessEnvironment, QTimer, QEvent, QPoint, QPointF,
    QRect, QRectF, QSize, pyqtSignal,
)
from PyQt6.QtGui import (
    QGuiApplication, QAction, QCursor, QPixmap, QPainter, QPainterPath, QIcon,
    QPen, QColor, QImage,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel, QFrame,
    QPushButton, QPlainTextEdit, QSizePolicy, QMessageBox, QToolButton,
    QMenu, QFileDialog, QLayout, QLayoutItem, QComboBox, QScrollBar,
)

from chat_monitors import (
    MonitorBus, build_default_bus,
    EVT_TEXT, EVT_TOOL_USE_START, EVT_TOOL_USE_INPUT_READY,
    EVT_TOOL_RESULT, EVT_ASSISTANT_DONE, EVT_USER_SEND,
    EVT_TICK, EVT_MODE_SWITCH,
)


# 边缘检测距离 (px)
EDGE_THRESH = 6
EDGE_CURSORS = {
    "tl": Qt.CursorShape.SizeFDiagCursor,
    "br": Qt.CursorShape.SizeFDiagCursor,
    "tr": Qt.CursorShape.SizeBDiagCursor,
    "bl": Qt.CursorShape.SizeBDiagCursor,
    "l":  Qt.CursorShape.SizeHorCursor,
    "r":  Qt.CursorShape.SizeHorCursor,
    "t":  Qt.CursorShape.SizeVerCursor,
    "b":  Qt.CursorShape.SizeVerCursor,
}


# ---------------- 常量 / 全局 ----------------
ROOT = Path(__file__).parent
STATE_DIR = ROOT / ".chat_state"
STATE_DIR.mkdir(exist_ok=True)
CONV_DIR = STATE_DIR / "conv"
CONV_DIR.mkdir(exist_ok=True)
PASTED_DIR = STATE_DIR / "pasted"
PASTED_DIR.mkdir(exist_ok=True)
PASTED_KEEP = 50  # 最多保留多少张, 超出按 mtime 旧的先删
PASTED_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
DEBUG_LOG = STATE_DIR / "debug.log"
PROXY_FILE = STATE_DIR / "proxy"
ACTIVE_FILE = STATE_DIR / "active.json"
PROJECTS_FILE = STATE_DIR / "projects.json"

CLAUDE_BIN = shutil.which("claude") or "claude"
DEFAULT_CWD = str(ROOT)  # 闲聊模式的 cwd
MAX_RECENT_PROJECTS = 10

# 权限模式 (持久化到 .chat_state/permission_mode, permission_dialog.py 也会读)
PERMISSION_MODE_FILE = STATE_DIR / "permission_mode"
HOOK_SETTINGS_FILE = STATE_DIR / "hook_settings.json"
PERMISSION_MODES = [
    ("default", "严格 (每个敏感工具弹确认)"),
    ("acceptEdits", "自动接受改动 (Bash 仍弹)"),
    ("bypassPermissions", "全放行 (危险!)"),
]

# 模型选项 — "" 表示不传 --model, 跟 Claude Code 全局走
MODEL_OPTIONS = [
    ("",       "默认"),
    ("sonnet", "Sonnet"),
    ("opus",   "Opus"),
    ("haiku",  "Haiku"),
]
MODEL_KEYS = [k for k, _ in MODEL_OPTIONS]


def load_permission_mode() -> str:
    if PERMISSION_MODE_FILE.exists():
        m = PERMISSION_MODE_FILE.read_text(encoding="utf-8").strip()
        if m in [k for k, _ in PERMISSION_MODES]:
            return m
    return "default"


def save_permission_mode(mode: str):
    if mode not in [k for k, _ in PERMISSION_MODES]:
        return
    try:
        PERMISSION_MODE_FILE.write_text(mode, encoding="utf-8")
    except Exception: pass


def _ensure_hook_settings():
    """
    保证 .chat_state/hook_settings.json 存在且 command 路径指向当前 ROOT 下
    的 permission_dialog.py。让别人 clone 后不用改任何路径就能用。
    """
    dialog_script = (ROOT / "permission_dialog.py").resolve()
    if not dialog_script.exists():
        # 没有 dialog 脚本就不写 hook (避免 claude 启动失败)
        return
    expected_cmd = f'python "{dialog_script.as_posix()}"'
    expected_config = {
        "hooks": {
            "PreToolUse": [{
                "matcher": "Bash|Edit|MultiEdit|Write|NotebookEdit|WebFetch",
                "hooks": [{
                    "type": "command",
                    "command": expected_cmd,
                    "timeout": 180,
                }]
            }]
        }
    }
    # 如果当前文件已经匹配, 不动
    if HOOK_SETTINGS_FILE.exists():
        try:
            cur = json.loads(HOOK_SETTINGS_FILE.read_text(encoding="utf-8"))
            cur_cmd = (cur.get("hooks", {})
                       .get("PreToolUse", [{}])[0]
                       .get("hooks", [{}])[0]
                       .get("command", ""))
            if cur_cmd == expected_cmd:
                return
        except Exception: pass
    # 写入
    try:
        HOOK_SETTINGS_FILE.write_text(
            json.dumps(expected_config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _log(f"hook_settings.json regenerated → {dialog_script}")
    except Exception as e:
        _log(f"hook_settings 写入失败: {e}")

# 头像
USER_AVATAR_DIR = STATE_DIR / "avatars"
USER_AVATAR_DIR.mkdir(parents=True, exist_ok=True)
POMO_AVATAR_FILE = ROOT / "foamo.ico"

_AVATAR_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico")


def _find_user_avatar() -> Path | None:
    """STATE_DIR/avatars/user.<任意常见图片后缀>"""
    for ext in _AVATAR_EXTS:
        p = USER_AVATAR_DIR / f"user{ext}"
        if p.exists():
            return p
    return None


def _pomo_avatar() -> Path | None:
    return POMO_AVATAR_FILE if POMO_AVATAR_FILE.exists() else None


def _load_avatar_source(path: Path) -> QPixmap | None:
    """
    智能加载头像源图。
    - .ico: 多尺寸容器, 用 QIcon 取最大那帧 (foamo.ico 含 16~128, 取 128)
    - 其他: QPixmap 直接读
    """
    suf = path.suffix.lower()
    if suf == ".ico":
        icon = QIcon(str(path))
        sizes = icon.availableSizes()
        if sizes:
            best = max(sizes, key=lambda s: s.width())
            pix = icon.pixmap(best)
            if not pix.isNull():
                return pix
    pix = QPixmap(str(path))
    return pix if not pix.isNull() else None


def _round_pix(pix: QPixmap, radius: int = 6) -> QPixmap:
    """给 pixmap 加圆角"""
    sz = pix.size()
    result = QPixmap(sz)
    result.fill(Qt.GlobalColor.transparent)
    p = QPainter(result)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0.0, 0.0, float(sz.width()), float(sz.height()),
                       float(radius), float(radius))
    p.setClipPath(path)
    p.drawPixmap(0, 0, pix)
    p.end()
    return result

# 配色 (面板用半透明配毛玻璃)
COLOR_PANEL_BG = "rgba(237, 237, 237, 200)"
COLOR_PANEL_BG_FALLBACK = "#ededed"
COLOR_USER = "#95EC69"
COLOR_ASSISTANT = "rgba(255, 255, 255, 235)"
COLOR_AVATAR_USER = "#576b95"
COLOR_AVATAR_POMO = "#ff6b9d"
COLOR_HEADER_BG = "rgba(31, 31, 58, 230)"
COLOR_HEADER_TXT = "#ede5dd"
COLOR_BORDER = "#3a3a5a"
COLOR_SEND = "#07c160"
COLOR_INPUT_BG = "rgba(245, 245, 245, 220)"
COLOR_INPUT_BORDER = "#d9d9d9"
COLOR_PROJECT = "#ffd062"  # 项目模式时标题点缀色

# ---- 主题 (light / dark) ----
# header 与 panel 同色调, 靠底部 1px 细线分隔 (macOS toolbar 风)
# 所有 rgba alpha 用 0-255 整数, 跟 Qt styleSheet 一致
THEMES = {
    "light": {
        "panel_bg":      "rgba(248, 248, 250, 220)",
        "panel_solid":   "#f8f8fa",
        "header_bg":     "rgba(252, 252, 254, 235)",
        "header_txt":    "#1a1a1f",
        "status_txt":    "#86868f",
        "icon_color":    "#3a3a40",
        "icon_hover":    "rgba(0, 0, 0, 15)",       # ≈ 6% 不透明
        "border":        "rgba(0, 0, 0, 20)",       # ≈ 8%
        "border_solid":  "#dadade",
        "input_bg":      "rgba(255, 255, 255, 200)",
        "input_border":  "rgba(0, 0, 0, 25)",       # ≈ 10%
        "input_field":   "#ffffff",
        "input_text":    "#1a1a1a",
        "scroll_handle": "rgba(0, 0, 0, 46)",       # ≈ 18%
        "combo_bg":      "rgba(0, 0, 0, 10)",
        "combo_view_bg": "#ffffff",
        "combo_text":    "#1a1a1f",
        # 菜单 (popup) 必须用实色背景, 半透明在 popup 上 Qt 渲染不稳定
        "menu_bg":       "#fcfcfe",
        "menu_hover":    "#e8e8ee",
        # 项目名点缀色 — 跟 header 底色形成足够对比
        "project_accent": "#c2410c",     # 深橙
        # 气泡 (微信 light: 用户绿底深字, 助手白底深字)
        "bubble_user_bg": "#95EC69",
        "bubble_user_fg": "#2c2c2c",
        "bubble_asst_bg": "rgba(255, 255, 255, 235)",
        "bubble_asst_fg": "#2c2c2c",
    },
    "dark": {
        "panel_bg":      "rgba(24, 24, 32, 220)",
        "panel_solid":   "#181820",
        "header_bg":     "rgba(28, 28, 38, 235)",
        "header_txt":    "#ede5dd",
        "status_txt":    "#8e8e9a",
        "icon_color":    "#c8c8d0",
        "icon_hover":    "rgba(255, 255, 255, 20)",
        "border":        "rgba(255, 255, 255, 20)",
        "border_solid":  "#3a3a48",
        "input_bg":      "rgba(35, 35, 46, 230)",
        "input_border":  "rgba(255, 255, 255, 25)",
        "input_field":   "#23232e",
        "input_text":    "#ede5dd",
        "scroll_handle": "rgba(255, 255, 255, 56)",
        "combo_bg":      "rgba(255, 255, 255, 15)",
        "combo_view_bg": "#1f1f2c",
        "combo_text":    "#ede5dd",
        # 菜单
        "menu_bg":       "#23232e",
        "menu_hover":    "#3a3a48",
        # 项目名点缀色
        "project_accent": "#ffd062",     # 金黄
        # 气泡 (微信 dark: 用户暗绿+浅字, 助手灰底+浅字)
        "bubble_user_bg": "#3e6b3e",
        "bubble_user_fg": "#ede5dd",
        "bubble_asst_bg": "rgba(50, 50, 60, 240)",
        "bubble_asst_fg": "#dadada",
    },
}

def _theme_file() -> Path:
    return Path(__file__).resolve().parent / ".chat_state" / "theme"

def _load_theme() -> str:
    try:
        v = _theme_file().read_text(encoding="utf-8").strip()
        if v in THEMES: return v
    except Exception: pass
    return "light"

def _save_theme(name: str):
    try:
        f = _theme_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(name, encoding="utf-8")
    except Exception: pass


# ---- 粘贴图片: 保存到 .chat_state/pasted/, LRU 清理 ----
def _pasted_stem() -> str:
    return time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]

def _trim_pasted_dir(keep: int = PASTED_KEEP):
    """保留最近 keep 张图, 多余按 mtime 旧的先删. 静默吞错"""
    try:
        files = [f for f in PASTED_DIR.iterdir() if f.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files[keep:]:
            try: f.unlink()
            except Exception: pass
    except Exception: pass

def _save_pasted_qimage(img) -> Path | None:
    """剪贴板里的 QImage → 落盘成 PNG. 失败返回 None"""
    try:
        path = PASTED_DIR / f"{_pasted_stem()}.png"
        if img.save(str(path), "PNG"):
            _trim_pasted_dir()
            return path
    except Exception: pass
    return None

def _save_pasted_file(src: Path) -> Path | None:
    """从外部图片文件 (拖/复制资源管理器里的图) 复制到 pasted/. 失败返回 None"""
    try:
        suffix = src.suffix.lower() or ".png"
        dst = PASTED_DIR / f"{_pasted_stem()}{suffix}"
        shutil.copy2(src, dst)
        _trim_pasted_dir()
        return dst
    except Exception: pass
    return None

PANEL_W = 380
PANEL_H = 520
GAP = 10

# ---------------- 独占欲 (jealous) 触发 ----------------
# 豆哥提到别的 AI / 编程助手, 泡沫吃醋脸 5 秒.
# 关键词全部 lowercase 匹配; 中文按 substring 匹配.
JEALOUS_KEYWORDS = (
    # 英文 AI / 助手
    "chatgpt", "openai", "gpt-3", "gpt-4", "gpt-5", "gpt4", "gpt5",
    "gemini", "bard", "claude.ai",
    "copilot", "cursor", "windsurf", "codeium", "tabnine",
    "deepseek", "kimi", "qwen", "doubao",
    "codex",
    # 中文表达
    "通义", "文心", "豆包", "智谱",
    "别的 ai", "别的ai", "其他 ai", "其他ai",
    "别的模型", "其他模型", "换个模型问",
    "问问 gpt", "问问gpt", "问下 gpt", "问下gpt",
)
JEALOUS_LINES = (
    "...哼,问就问。但他不会比泡沫更懂你这堆历史包袱。",
    "...嗯。让别人看一眼也行,毕竟豆哥的项目嘛。",
    "(瞄一眼) 那家伙真有那么懂?",
    "...本泡沫不说话。但记下来了。",
    "豆哥要换搭档?...不许的。",
)
JEALOUS_HOLD_MS = 5000


def _is_jealous_text(text: str) -> bool:
    s = text.lower()
    return any(k in s for k in JEALOUS_KEYWORDS)


# ---------------- 工具函数 ----------------
def _log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try: print(line, flush=True)
    except Exception: pass
    try:
        with DEBUG_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception: pass


def _resolve_proxy() -> str | None:
    if PROXY_FILE.exists():
        url = PROXY_FILE.read_text(encoding="utf-8").strip()
        if url: return url
    return (
        os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    )


def _build_process_env() -> QProcessEnvironment:
    env = QProcessEnvironment.systemEnvironment()
    proxy = _resolve_proxy()
    if proxy:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env.insert(k, proxy)
    return env


def _project_key(path: str | None) -> str:
    """闲聊 → 'chat', 项目 → 'proj_<md5前8>'"""
    if not path:
        return "chat"
    h = hashlib.md5(path.encode("utf-8")).hexdigest()[:8]
    return f"proj_{h}"


def _conv_dir(key: str) -> Path:
    d = CONV_DIR / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_active() -> dict:
    if ACTIVE_FILE.exists():
        try:
            d = json.loads(ACTIVE_FILE.read_text(encoding="utf-8"))
            if isinstance(d, dict): return d
        except Exception: pass
    return {"mode": "chat", "path": None, "name": "闲聊"}


def _save_active(active: dict):
    try:
        ACTIVE_FILE.write_text(
            json.dumps(active, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception: pass


def _load_projects() -> list:
    if PROJECTS_FILE.exists():
        try:
            d = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
            if isinstance(d, list): return d
        except Exception: pass
    return []


def _add_project(path: str, name: str):
    projects = [p for p in _load_projects() if p.get("path") != path]
    projects.insert(0, {"path": path, "name": name, "last_used": int(time.time())})
    projects = projects[:MAX_RECENT_PROJECTS]
    try:
        PROJECTS_FILE.write_text(
            json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception: pass


def _remove_project(path: str):
    projects = [p for p in _load_projects() if p.get("path") != path]
    try:
        PROJECTS_FILE.write_text(
            json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception: pass


# ---------------- mini markdown → HTML ----------------
_RE_FENCE = re.compile(r"```(\w*)\n?(.*?)```", re.DOTALL)
_RE_INLINE = re.compile(r"`([^`\n]+)`")
_RE_BOLD = re.compile(r"\*\*(.+?)\*\*")
_RE_ITAL = re.compile(r"(?<!\*)\*([^\*\n]+?)\*(?!\*)")
_RE_HDR = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
_RE_LIST = re.compile(r"^[\-\*]\s+(.+)$", re.MULTILINE)
_RE_TABLE = re.compile(
    r"^\|(.+)\|[ \t]*\n\|([ \t\-:|]+)\|[ \t]*\n((?:\|.+\|[ \t]*(?:\n|$))+)",
    re.MULTILINE,
)


def _split_md_row(line: str) -> list[str]:
    """切 markdown 表格行, 支持 \\| 转义"""
    line = line.replace(r"\|", "\x01PIPE\x01")
    return [c.strip().replace("\x01PIPE\x01", "|") for c in line.split("|")]


def _render_md_table(m: "re.Match", inlines: list[str],
                     pal: dict | None = None) -> str:
    """markdown 表格 → HTML <table>. inlines 用来还原 cell 内的 INL 占位符.
    pal 给配色 (跟主题走), 不传就用 light 兜底"""
    if pal is None:
        pal = _MD_PALETTES["light"]
    inline_bg = pal["inline_bg"]
    inline_fg = pal["inline_fg"]
    border = pal["table_border"]
    head_bg = pal["table_head_bg"]

    headers = _split_md_row(m.group(1))

    aligns: list[str | None] = []
    for cell in _split_md_row(m.group(2)):
        left = cell.startswith(":")
        right = cell.endswith(":")
        if left and right: aligns.append("center")
        elif right: aligns.append("right")
        elif left: aligns.append("left")
        else: aligns.append(None)

    rows: list[list[str]] = []
    for raw in m.group(3).strip("\n").split("\n"):
        raw = raw.strip()
        if not raw.startswith("|"):
            continue
        inner = raw[1:]
        if inner.endswith("|"):
            inner = inner[:-1]
        rows.append(_split_md_row(inner))

    def render_cell(text: str, align: str | None, bold: bool) -> str:
        text = _html.escape(text)
        text = _RE_BOLD.sub(r"<b>\1</b>", text)
        text = _RE_ITAL.sub(r"<i>\1</i>", text)

        def restore_inl(mm: "re.Match") -> str:
            i = int(mm.group(1))
            code = _html.escape(inlines[i])
            return (
                f'<code style="background:{inline_bg}; color:{inline_fg}; '
                f'font-family:Consolas,Menlo,monospace; '
                f'padding:1px 4px; border-radius:2px;">{code}</code>'
            )

        text = re.sub(r"\x00INL(\d+)\x00", restore_inl, text)
        if bold:
            text = f"<b>{text}</b>"
        align_attr = f' align="{align}"' if align else ""
        return (
            f'<td{align_attr} style="padding:4px 8px; '
            f'border:1px solid {border};">{text}</td>'
        )

    n_cols = len(headers)
    out = ['<table width="100%" cellpadding="0" cellspacing="0" border="0" '
           'style="margin:4px 0; border-collapse:collapse;">']
    out.append(f'<tr bgcolor="{head_bg}">')
    for i, h in enumerate(headers):
        align = aligns[i] if i < len(aligns) else None
        out.append(render_cell(h, align, bold=True))
    out.append('</tr>')
    for row in rows:
        out.append('<tr>')
        for i in range(n_cols):
            cell = row[i] if i < len(row) else ""
            align = aligns[i] if i < len(aligns) else None
            out.append(render_cell(cell, align, bold=False))
        out.append('</tr>')
    out.append('</table>')
    return "".join(out)


# markdown 渲染配色 (跟 ChatPanel 主题走). 在 dark 灰底气泡上:
# - inline code: 深灰底 + 柔和粉紫字 (跟原 light 紫红同调但降饱和)
# - fence 代码块: 更深底 + 浅米字
# - 标题: 跟气泡主字色一致, 不再硬编码黑色
_MD_PALETTES = {
    "light": {
        "h_color":        "#1a1a1a",
        "inline_bg":      "#f0f0f0",
        "inline_fg":      "#c7254e",
        "fence_bg":       "#f6f8fa",
        "fence_fg":       "#24292e",
        "table_border":   "#d0d0d0",
        "table_head_bg":  "#f0f0f0",
    },
    "dark": {
        "h_color":        "#ede5dd",
        "inline_bg":      "#3f3f4c",
        "inline_fg":      "#e8b8d6",
        "fence_bg":       "#1c1c26",
        "fence_fg":       "#dcdcd2",
        "table_border":   "#4a4a5a",
        "table_head_bg":  "#23232e",
    },
}


def md_to_html(text: str, theme: str = "light") -> str:
    """
    简化 markdown → HTML, 给 QLabel RichText 用。
    支持: ``` 代码块, `inline`, **bold**, *italic*, # 标题, - 列表, | 表格
    theme: "light" / "dark" — 切配色, 让代码块和标题跟气泡背景协调
    """
    if not text:
        return ""

    pal = _MD_PALETTES.get(theme, _MD_PALETTES["light"])

    # 先抓 ``` 代码块和 `inline`, 用占位符保护内部 (避免后续被 escape/解析)
    fences: list[str] = []
    def _grab_fence(m):
        fences.append(m.group(2))
        return f"\x00FENCE{len(fences)-1}\x00"
    text = _RE_FENCE.sub(_grab_fence, text)

    inlines: list[str] = []
    def _grab_inline(m):
        inlines.append(m.group(1))
        return f"\x00INL{len(inlines)-1}\x00"
    text = _RE_INLINE.sub(_grab_inline, text)

    # 表格: 必须在 escape 之前抓走 (整体结构识别完毕, cell 内部各自渲染好,
    # 整段塞进占位符避免被后续 escape / list / br 化破坏)
    html_blocks: list[str] = []
    def _grab_table(m):
        html_blocks.append(_render_md_table(m, inlines, pal))
        return f"\x00HTML{len(html_blocks)-1}\x00"
    text = _RE_TABLE.sub(_grab_table, text)

    # 转义剩余 HTML 特殊字符
    text = _html.escape(text)

    # 行内格式
    text = _RE_BOLD.sub(r"<b>\1</b>", text)
    text = _RE_ITAL.sub(r"<i>\1</i>", text)

    # 标题 # / ## / ###
    h_color = pal["h_color"]
    def _hdr(m):
        lvl = len(m.group(1))
        size = {1: 15, 2: 14, 3: 13}.get(lvl, 12)
        return (f'<span style="font-size:{size}px; font-weight:bold; '
                f'color:{h_color};">{m.group(2)}</span>')
    text = _RE_HDR.sub(_hdr, text)

    # 简单列表
    text = _RE_LIST.sub(r"&nbsp;&nbsp;• \1", text)

    # 换行
    text = text.replace("\n", "<br>")

    # 还原 inline code (单独 escape)
    inline_bg = pal["inline_bg"]
    inline_fg = pal["inline_fg"]
    for i, code in enumerate(inlines):
        rep = (f'<code style="background:{inline_bg}; color:{inline_fg}; '
               f'font-family:Consolas,Menlo,monospace; '
               f'padding:1px 4px; border-radius:2px;">'
               f'{_html.escape(code)}</code>')
        text = text.replace(f"\x00INL{i}\x00", rep)

    # 还原代码块: 用 table 布局 (QLabel RichText 对 table 支持最好)
    fence_bg = pal["fence_bg"]
    fence_fg = pal["fence_fg"]
    for i, code in enumerate(fences):
        code_h = _html.escape(code.rstrip())
        # 保留代码内换行: \n → <br>, 空格 → &nbsp; 防被吃
        code_h = code_h.replace("\n", "<br>")
        rep = (
            f'<table width="100%" cellpadding="6" cellspacing="0" border="0" '
            f'bgcolor="{fence_bg}" style="margin:4px 0;">'
            f'<tr><td>'
            f'<span style="font-family:Consolas,Menlo,monospace; '
            f'font-size:11px; color:{fence_fg};">{code_h}</span>'
            f'</td></tr></table>'
        )
        text = text.replace(f"\x00FENCE{i}\x00", rep)

    # 还原表格 HTML 块 (cell 内的 INL 已在 _render_md_table 里展开过, 整段塞回)
    for i, blk in enumerate(html_blocks):
        text = text.replace(f"\x00HTML{i}\x00", blk)

    return text


# ---------------- 顶部工具栏图标按钮 (QPainter 自绘) ----------------
class IconButton(QToolButton):
    """
    顶部 toolbar 用的图标按钮. 用 QPainter 自绘矢量图标
    (close / max / restore / sun / moon), 避免 Unicode 字符/emoji
    在不同字体下渲染不一致.

    - 28x28 hit area, ~12px 视觉图标
    - icon 颜色由 set_icon_color 控制 (跟主题走)
    - hover 半透明圆角背景由 styleSheet 提供
    """
    _ICONS = ("close", "max", "restore", "sun", "moon")

    def __init__(self, icon_name: str, parent=None):
        super().__init__(parent)
        self._icon_name = icon_name if icon_name in self._ICONS else "close"
        self._icon_color = "#1a1a1f"
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_icon_name(self, name: str):
        if name in self._ICONS and name != self._icon_name:
            self._icon_name = name
            self.update()

    def set_icon_color(self, color: str):
        if color != self._icon_color:
            self._icon_color = color
            self.update()

    def paintEvent(self, ev):
        # 让 stylesheet 先画 hover / pressed 背景
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = QColor(self._icon_color)
        pen = QPen(col)
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        cx, cy = self.width() / 2, self.height() / 2
        n = self._icon_name

        if n == "close":
            r = 5.0
            p.drawLine(QPointF(cx - r, cy - r), QPointF(cx + r, cy + r))
            p.drawLine(QPointF(cx - r, cy + r), QPointF(cx + r, cy - r))
        elif n == "max":
            r = 5.0
            p.drawRoundedRect(QRectF(cx - r, cy - r, r * 2, r * 2), 1.5, 1.5)
        elif n == "restore":
            # 前框 + 后框右上凸出, 像两层窗口堆叠
            r = 4.0
            front = QRectF(cx - r - 1, cy - r + 1, r * 2, r * 2)
            back_top = back_right = 1.6  # 后框露出的边长
            # 后框: 只画顶边 + 右边 (左下被前框遮)
            path = QPainterPath()
            bx_l = cx - r + 1 + 1.5
            bx_r = cx + r + 1
            by_t = cy - r - 1
            by_b = cy + r - 1 - 1.5
            path.moveTo(bx_l, by_t)
            path.lineTo(bx_r - 1.5, by_t)
            path.quadTo(bx_r, by_t, bx_r, by_t + 1.5)
            path.lineTo(bx_r, by_b)
            p.drawPath(path)
            # 前框: 完整
            p.drawRoundedRect(front, 1.5, 1.5)
        elif n == "sun":
            # 中心圆 + 8 条短射线
            p.drawEllipse(QPointF(cx, cy), 2.8, 2.8)
            for i in range(8):
                a = i * _math.pi / 4
                x1 = cx + _math.cos(a) * 4.9
                y1 = cy + _math.sin(a) * 4.9
                x2 = cx + _math.cos(a) * 6.6
                y2 = cy + _math.sin(a) * 6.6
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        elif n == "moon":
            # 月牙: 大圆 - 偏移小圆 (实心填充, 更醒目)
            r = 5.6
            big = QPainterPath()
            big.addEllipse(QPointF(cx, cy), r, r)
            cut = QPainterPath()
            cut.addEllipse(QPointF(cx + 2.6, cy - 1.3), r, r)
            crescent = big.subtracted(cut)
            p.fillPath(crescent, col)

        p.end()


# ---------------- 聊天输入框 (支持粘贴图片) ----------------
class ChatInput(QPlainTextEdit):
    """重写 paste 行为: 剪贴板里的图 / 复制的图片文件 → 落盘 + emit image_pasted.

    保留原本的文字 paste 行为 (走 super)."""
    image_pasted = pyqtSignal(Path)

    def insertFromMimeData(self, source):
        consumed_image = False
        # 1. 剪贴板里直接有图 (Snipping Tool / Win+Shift+S / QQ截图 等)
        if source.hasImage():
            img = source.imageData()
            if isinstance(img, QImage) and not img.isNull():
                path = _save_pasted_qimage(img)
                if path is not None:
                    self.image_pasted.emit(path)
                    consumed_image = True
        # 2. 复制了图片文件 (从资源管理器右键复制 → 粘贴到输入框)
        if source.hasUrls():
            urls = source.urls()
            img_urls = [
                u for u in urls
                if u.isLocalFile()
                and Path(u.toLocalFile()).suffix.lower() in PASTED_EXTS
            ]
            for u in img_urls:
                src = Path(u.toLocalFile())
                dst = _save_pasted_file(src)
                if dst is not None:
                    self.image_pasted.emit(dst)
                    consumed_image = True
            # url 全是图 → 拦截, 不要再让 super 把路径作为文字插进来
            if img_urls and len(img_urls) == len(urls):
                return
        # 处理过图但还有附带文字 → 让文字走原 paste; 没文字就返回
        if consumed_image and not source.hasText():
            return
        super().insertFromMimeData(source)


# ---------------- 粘贴图片预览条 (输入框上方) ----------------
class PasteStripChip(QFrame):
    """一张待发送图片的缩略图. 右上角 × 删除按钮"""
    removed = pyqtSignal(object)  # 自身

    THUMB = 56
    BOX = 64

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = path
        self.setFixedSize(self.BOX, self.BOX)
        self.setStyleSheet("background: transparent; border: 0;")

        # 缩略图: 居中裁剪 + 圆角
        self._img = QLabel(self)
        self._img.setGeometry(0, 4, self.THUMB, self.THUMB)
        self._img.setStyleSheet("background: transparent;")
        pix = QPixmap(str(path))
        if not pix.isNull():
            sw, sh = pix.width(), pix.height()
            side = min(sw, sh)
            x = (sw - side) // 2
            y = (sh - side) // 2
            square = pix.copy(x, y, side, side)
            rounded = _round_pix(square, max(8, side // 8))
            final = rounded.scaled(
                self.THUMB, self.THUMB,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img.setPixmap(final)

        # × 删除按钮
        self._del = QPushButton("×", self)
        self._del.setFixedSize(18, 18)
        self._del.setGeometry(self.THUMB - 12, 0, 18, 18)
        self._del.setCursor(Qt.CursorShape.PointingHandCursor)
        self._del.setStyleSheet(
            "QPushButton { background: rgba(0,0,0,170); color: white; "
            "border: 0; border-radius: 9px; font-size: 12px; font-weight: bold; "
            "padding: 0 0 2px 0; }"
            "QPushButton:hover { background: rgba(220,60,60,230); }"
        )
        self._del.clicked.connect(lambda: self.removed.emit(self))
        self._del.raise_()

        self.setToolTip(str(path))


class PasteStrip(QFrame):
    """输入框上方的图片预览条. 0..N 张 chip 横排, 空时隐藏"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("paste_strip")
        self._chips: list[PasteStripChip] = []
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 4, 10, 4)
        h.setSpacing(6)
        h.addStretch(1)
        self._h = h
        self.hide()

    def add(self, path: Path):
        chip = PasteStripChip(path, self)
        chip.removed.connect(self._on_chip_remove)
        # 插在 stretch 之前
        self._h.insertWidget(self._h.count() - 1, chip)
        self._chips.append(chip)
        self.show()

    def _on_chip_remove(self, chip):
        if chip in self._chips:
            self._chips.remove(chip)
        chip.setParent(None)
        chip.deleteLater()
        if not self._chips:
            self.hide()

    def paths(self) -> list[Path]:
        return [c.path for c in self._chips]

    def clear(self):
        for c in self._chips:
            c.setParent(None)
            c.deleteLater()
        self._chips.clear()
        self.hide()


# ---------------- 头像 / 气泡 / 工具标签 / 行 ----------------
class Avatar(QLabel):
    """
    32x32 圆角头像。
    - image_path 给了且能加载 → 用图片 (居中裁剪到正方形 + 圆角)
    - 否则 fallback 到字符 + 纯色背景
    """
    SIZE = 32
    RADIUS = 6

    def __init__(self, char: str, color: str,
                 image_path: Path | None = None, parent=None):
        super().__init__("", parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

        loaded = False
        if image_path is not None and image_path.exists():
            src = _load_avatar_source(image_path)
            if src is not None and not src.isNull():
                # 居中裁剪成正方形 (源图按其原始尺寸, 短边为准)
                sw, sh = src.width(), src.height()
                side = min(sw, sh)
                x = (sw - side) // 2
                y = (sh - side) // 2
                square = src.copy(x, y, side, side)
                # 圆角在源尺寸做, 然后一次性高质量降采样到 32 — 比"先缩到2x再裁"清晰
                rounded = _round_pix(square, max(8, side // 8))
                final = rounded.scaled(
                    self.SIZE, self.SIZE,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.setPixmap(final)
                self.setStyleSheet("background: transparent; border: 0;")
                loaded = True

        if not loaded:
            self.setText(char)
            self.setStyleSheet(
                f"background: {color}; color: white; "
                f"font-weight: bold; font-size: 13px; "
                f"border-radius: {self.RADIUS}px;"
            )


class Bubble(QLabel):
    """
    消息气泡。
    - user: PlainText 显示原文 (避免误解析 markdown)
    - assistant: RichText 渲染 markdown + 代码块
    - thinking 态: spinner 动画占位
    """
    def __init__(self, role: str, text: str = "", parent=None,
                 attachments: list[Path] | None = None):
        super().__init__("", parent)
        self.role = role
        self._raw = text
        self._attachments: list[Path] = list(attachments or [])
        self._thinking = False
        self._streaming = False  # 流式期间 (PlainText 直显, 不解析 md)
        self._dot_timer: QTimer | None = None
        self._dot_step = 0
        self.setWordWrap(True)
        # 默认: 用户和初始的助手用 PlainText (零开销),
        # finalize() 时再切 RichText 跑 markdown
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # 缓存当前主题文字色, 给 markdown 渲染时用 (默认 light 兜底)
        self._fg = "#2c2c2c"
        self._theme_name = "light"
        # 初始 styleSheet 用 light 兜底; 创建后会被 ChatPanel.apply_theme 覆盖
        self.apply_theme(THEMES["light"], "light")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        # 关键: WordWrap+QLabel 默认 sizeHint 退化到 minimumSizeHint(单字宽),
        # 会被 layout 压成竖排一列 — 给个合理最小宽度兜底
        self.setMinimumWidth(140)
        self._render()

    def apply_theme(self, colors: dict, theme_name: str = "light"):
        """根据主题切气泡背景/文字色 + markdown 配色. 切完主动 _render 跟着更新"""
        if self.role == "user":
            bg = colors["bubble_user_bg"]
            fg = colors["bubble_user_fg"]
        else:
            bg = colors["bubble_asst_bg"]
            fg = colors["bubble_asst_fg"]
        self._fg = fg
        self._theme_name = theme_name
        self.setStyleSheet(
            f"background: {bg}; border-radius: 6px; "
            f"padding: 7px 11px; font-size: 12px; color: {fg};"
        )
        # 已 finalize 的助手消息用 RichText 渲染, markdown 里嵌了
        # 颜色 (标题/code), 切主题后要重渲染才能跟上
        if (
            self.role == "assistant"
            and not self._streaming
            and not self._thinking
            and self._raw
        ):
            self._render()

    def minimumSizeHint(self) -> QSize:
        return QSize(140, 36)

    def sizeHint(self) -> QSize:
        # thinking 态 / 空 → 默认
        if self._thinking or not (self._raw or self._attachments):
            return QSize(140, 36)
        fm = self.fontMetrics()
        # 自然宽度: 取单行最长 + padding
        max_line = 0
        for line in self._raw.split("\n"):
            w = fm.horizontalAdvance(line)
            if w > max_line:
                max_line = w
        natural_w = max_line + 30  # padding ≈ 22px + 8 margin
        # 带图: 至少 200 宽, 给图留位置
        if self._attachments:
            natural_w = max(natural_w, 200)
        # 不超过 maxWidth (resizeEvent 设的, 跟随面板宽度)
        max_w = self.maximumWidth()
        if max_w >= 16000000:  # QWIDGETSIZE_MAX
            max_w = 9999
        target_w = max(140, min(natural_w, max_w))
        # 在 target_w 下 wordWrap 后的实际高度
        text_h = 0
        if self._raw:
            rect = fm.boundingRect(
                QRect(0, 0, target_w - 30, 99999),
                Qt.TextFlag.TextWordWrap,
                self._raw,
            )
            text_h = rect.height()
        # 图片高度: 每张 160 + 8 间距 (跟 _render 里 width="160" 对齐)
        img_h = len(self._attachments) * 168
        return QSize(target_w, max(36, text_h + img_h + 18))

    SPINNER_FRAMES = ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"]

    def _render(self):
        if self._thinking:
            # spinner 必须 RichText (用 <span style>)
            self.setTextFormat(Qt.TextFormat.RichText)
            frame = self.SPINNER_FRAMES[self._dot_step % len(self.SPINNER_FRAMES)]
            self.setText(
                f'<span style="font-size:14px; color:#999;">{frame}</span>'
            )
            return
        # 有附件 (粘贴的图片) → RichText 渲染: 文字 + <img>
        if self._attachments:
            self.setTextFormat(Qt.TextFormat.RichText)
            parts = []
            if self._raw:
                text_html = _html.escape(self._raw).replace("\n", "<br>")
                parts.append(text_html)
            for p in self._attachments:
                uri = "file:///" + str(p).replace("\\", "/")
                parts.append(f'<img src="{uri}" width="160">')
            self.setText("<br>".join(parts))
            return
        # 流式 / 用户消息 / 助手未 finalize: PlainText 直显, O(1) 不解析
        if self.role == "assistant" and not self._streaming and self._raw:
            # 已 finalize 的助手消息走 markdown
            self.setTextFormat(Qt.TextFormat.RichText)
            self.setText(md_to_html(self._raw, self._theme_name))
        else:
            self.setTextFormat(Qt.TextFormat.PlainText)
            self.setText(self._raw)

    def set_thinking(self, on: bool):
        if on == self._thinking:
            return
        self._thinking = on
        if on:
            self._dot_step = 0
            t = QTimer(self)
            t.timeout.connect(self._tick_dots)
            t.start(120)  # 8 帧 * 120ms ≈ 1秒 / 圈, 节奏更稳
            self._dot_timer = t
        else:
            if self._dot_timer is not None:
                self._dot_timer.stop()
                self._dot_timer = None
        self._render()

    def finalize(self):
        """流式结束 — 切 RichText, 跑一次 markdown 渲染"""
        if not self._streaming:
            return
        self._streaming = False
        self._render()

    def _tick_dots(self):
        self._dot_step += 1
        self._render()

    def append_text(self, chunk: str):
        if self._thinking:
            self.set_thinking(False)  # 第一个真实 chunk 来了自动关
        if not self._streaming:
            self._streaming = True
        self._raw += chunk
        self._render()


class FlowLayout(QLayout):
    """自动换行的水平 layout — 工具 chip 多了自动 wrap 到下一行"""

    def __init__(self, parent=None, margin: int = 0, spacing: int = 4):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self._items: list[QLayoutItem] = []

    def __del__(self):
        while self.count():
            self.takeAt(0)

    def addItem(self, item: QLayoutItem):
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        x, y = rect.x(), rect.y()
        line_h = 0
        sp = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + sp
            if next_x - sp > rect.right() and line_h > 0:
                x = rect.x()
                y = y + line_h + sp
                next_x = x + hint.width() + sp
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class ToolChip(QFrame):
    """单个工具调用 — 紧凑胶囊。点击切换展开"""
    chip_clicked = pyqtSignal(object)

    def __init__(self, name: str, tool_use_id: str | None, parent=None):
        super().__init__(parent)
        self.name = name
        self.tool_use_id = tool_use_id
        self._input_text = ""
        self._result_text = ""
        self._strip: "ToolStrip | None" = None
        self._level: str = "normal"  # normal / active / danger

        self.setObjectName("tool_chip")
        self._apply_style()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 0, 9, 0)
        layout.setSpacing(0)

        self.label = QLabel(f"⚙ {name}")
        self.label.setStyleSheet(
            "background: transparent; border: 0; color: #444; font-size: 10px;"
        )
        layout.addWidget(self.label)

    def _apply_style(self):
        if self._level == "danger":
            self.setStyleSheet(
                "#tool_chip { background: rgba(255,140,80,220); "
                "border: 2px solid #ff5500; border-radius: 11px; }"
            )
        elif self._level == "active":
            self.setStyleSheet(
                "#tool_chip { background: rgba(255,215,100,200); "
                "border: 1px solid #d4a017; border-radius: 11px; }"
            )
        else:
            self.setStyleSheet(
                "#tool_chip { background: rgba(240,240,240,220); "
                "border: 1px solid #c8c8c8; border-radius: 11px; }"
                "#tool_chip:hover { background: rgba(220,220,220,235); }"
            )

    def set_active(self, active: bool):
        # 点击展开的 UI 状态. danger 优先级更高, 不被 active 覆盖
        if self._level == "danger":
            return
        self._level = "active" if active else "normal"
        self._apply_style()

    def set_emphasis(self, level: str):
        """monitor 用: 强制设置 chip 的视觉级别. level: normal/active/danger"""
        self._level = level
        self._apply_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.chip_clicked.emit(self)
        super().mousePressEvent(event)

    def update_input(self, text: str):
        self._input_text = text

    def set_result(self, text: str):
        self._result_text = text
        self.label.setText(f"⚙ {self.name} ✓")
        # 如果用户当前展开的就是这个 chip, 实时刷新详情
        if self._strip is not None:
            self._strip.refresh_active()


class ToolStrip(QWidget):
    """同一轮连续工具调用的容器 — chips 一行 (FlowLayout 自动换行) + 详情区"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._chips: list[ToolChip] = []
        self._active_chip: ToolChip | None = None

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 2, 8, 2)
        v.setSpacing(4)

        # chips 行 (FlowLayout 自动 wrap)
        self._chip_holder = QWidget()
        self._chip_layout = FlowLayout(self._chip_holder, margin=0, spacing=4)
        v.addWidget(self._chip_holder)

        # 详情区 (默认隐藏, 点 chip 才显示对应工具的内容)
        self._detail = QLabel("")
        self._detail.setStyleSheet(
            "background: rgba(0,0,0,18); border-radius: 4px; color: #2c2c2c; "
            "font-family: Consolas, Menlo, monospace; font-size: 10px; "
            "padding: 5px 7px;"
        )
        self._detail.setWordWrap(True)
        self._detail.setTextFormat(Qt.TextFormat.PlainText)
        self._detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._detail.setVisible(False)
        v.addWidget(self._detail)

    def add_chip(self, chip: ToolChip):
        chip._strip = self
        chip.chip_clicked.connect(self._on_chip_clicked)
        self._chip_layout.addWidget(chip)
        self._chips.append(chip)

    def _on_chip_clicked(self, chip: ToolChip):
        # 点同一个 → 收起;否则切到该 chip
        if self._active_chip is chip:
            chip.set_active(False)
            self._active_chip = None
            self._detail.setVisible(False)
            return
        if self._active_chip is not None:
            self._active_chip.set_active(False)
        self._active_chip = chip
        chip.set_active(True)
        self._render_detail(chip)
        self._detail.setVisible(True)

    def _render_detail(self, chip: ToolChip):
        parts = []
        if chip._input_text:
            inp = chip._input_text
            if len(inp) > 800:
                inp = inp[:800] + "\n... [省略]"
            parts.append("📥 入参\n" + inp)
        if chip._result_text:
            res = chip._result_text
            if len(res) > 1200:
                res = res[:1200] + "\n... [省略]"
            parts.append("📤 结果\n" + res)
        self._detail.setText("\n\n".join(parts) if parts else "(无内容)")

    def refresh_active(self):
        """如果有展开的 chip, 刷新它的详情 (用于 result 后续到达时)"""
        if self._active_chip is not None:
            self._render_detail(self._active_chip)


class SystemNotice(QLabel):
    """系统通知 (居中灰色文字, 用于切换模式提示)"""
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setStyleSheet(
            "color: #999; background: transparent; "
            "font-size: 10px; padding: 6px 0;"
        )


class MessageRow(QWidget):
    def __init__(self, role: str, content: QWidget, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(6)
        if role == "system":
            layout.addWidget(content, 1)
            return
        avatar_char = "豆" if role == "user" else "泡"
        avatar_color = COLOR_AVATAR_USER if role == "user" else COLOR_AVATAR_POMO
        avatar_img = _find_user_avatar() if role == "user" else _pomo_avatar()
        avatar = Avatar(avatar_char, avatar_color, avatar_img)
        if role == "user":
            layout.addStretch(1)
            layout.addWidget(content, 0, Qt.AlignmentFlag.AlignTop)
            layout.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)
        else:
            layout.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)
            layout.addWidget(content, 0, Qt.AlignmentFlag.AlignTop)
            layout.addStretch(1)


# ---------------- 监听反应 widget (WarningBar / Toast) ----------------
class WarningBar(QFrame):
    """顶部警告横条. ttl_ms 后自动消失, 也可点 × 提前关掉"""

    def __init__(self, text: str, color: str = "#ff8800",
                 ttl_ms: int = 8000, parent=None):
        super().__init__(parent)
        self.setObjectName("warning_bar")
        self.setStyleSheet(
            f"#warning_bar {{ background: {color}; "
            f"border-bottom: 1px solid rgba(0,0,0,60); }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 4, 4)
        layout.setSpacing(6)

        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(
            "color: white; font-size: 11px; background: transparent;"
            " font-weight: 500;"
        )
        layout.addWidget(label, 1)

        close = QToolButton()
        close.setText("×")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(
            "QToolButton { color: white; background: transparent; "
            "border: 0; font-size: 16px; padding: 0 8px; }"
            "QToolButton:hover { background: rgba(0,0,0,40); }"
        )
        close.clicked.connect(self._dismiss)
        layout.addWidget(close)

        if ttl_ms > 0:
            QTimer.singleShot(ttl_ms, self._dismiss)

    def _dismiss(self):
        try:
            self.setParent(None)
            self.deleteLater()
        except Exception: pass


class Toast(QLabel):
    """浮动小气泡 (绝对定位在父 widget 内, ttl_ms 后消失)"""

    def __init__(self, text: str, parent: QWidget, ttl_ms: int = 4000):
        super().__init__(text, parent)
        self.setStyleSheet(
            "background: rgba(50, 50, 70, 235); color: #fff; "
            "border-radius: 4px; padding: 6px 10px; font-size: 11px;"
        )
        self.setWordWrap(True)
        self.setMaximumWidth(280)
        self.adjustSize()
        self.show()
        self.raise_()
        if ttl_ms > 0:
            QTimer.singleShot(ttl_ms, self.deleteLater)


# ---------------- 主面板 ----------------
class ChatPanel(QWidget):
    # 信号: 面板 → 桌宠. monitor 通过 pet_request(state, line) 让桌宠切状态 + 说话
    pet_request = pyqtSignal(str, str)

    def __init__(self, parent=None):
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(parent, flags)
        self.resize(PANEL_W, PANEL_H)

        # 当前模式状态
        active = _load_active()
        self._mode: str = active.get("mode", "chat")
        self._project_path: str | None = active.get("path")
        self._mode_name: str = active.get("name") or (
            Path(self._project_path).name if self._project_path else "闲聊"
        )

        self._busy = False
        self._proc: QProcess | None = None
        self._stdout_buf = b""
        self._stderr_buf = b""
        self._captured_sid: str | None = None
        self._current_bubble: Bubble | None = None
        self._current_text = ""
        self._host: QWidget | None = None

        # 8 方向边缘拖拽状态
        self._resize_edge: str | None = None
        self._press_global: QPoint = QPoint()
        self._press_geom: QRect = QRect()
        self._last_cursor_check_ms: int = 0  # MouseMove 节流
        self._last_max_w: int = 0  # resize 阈值

        # 最大化态: True 时屏蔽 resize 边缘 + reposition 跟随
        self._maximized: bool = False
        self._restore_geom: QRect | None = None

        # 主题 (light / dark), 持久化到 .chat_state/theme
        self._theme: str = _load_theme()
        self._colors: dict = THEMES[self._theme]

        # scroll 滚到底用单 timer 节流, 多次调用合并成一次
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(30)
        self._scroll_timer.timeout.connect(self._scroll_to_bottom_now)

        # 工具调用 chip (tool_use_id → ToolChip; index → ToolChip 累加 input)
        self._tool_blocks: dict[str, "ToolChip"] = {}
        self._pending_tool_index: dict[int, "ToolChip"] = {}
        # 当前正在累加的 ToolStrip (一段连续 tool_use 共用一个 strip;
        # 收到 text_delta 或新一轮就关闭, 下次新工具开新 strip)
        self._current_tool_strip: "ToolStrip | None" = None

        self.setMinimumSize(300, 360)
        self.setMouseTracking(True)

        # 毛玻璃: 必须在 show 之前设 WA_TranslucentBackground
        self._glass_applied = False
        if sys.platform.startswith("win"):
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._build_ui()
        self._refresh_title()
        self._refresh_model_combo()  # 初始化时拉当前 mode 的模型
        self._load_history()
        self._install_resize_filters()

        # 启动时确保 hook 配置路径是对的 (clone 到不同位置也能用)
        _ensure_hook_settings()

        # ---------- monitor bus (聊天面板监听系统) ----------
        # 见 chat_monitors.py + spec: 07-规格与计划/2026-05-13-聊天面板监听系统.md
        self.bus: MonitorBus = build_default_bus(self)
        # 1Hz tick (给时间敏感的 monitor: B 深夜 / C 计时 / J 冷场 / H 走神)
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(
            lambda: self.bus.dispatch(EVT_TICK, {"now": time.time()})
        )
        self._tick_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._glass_applied:
            self._apply_glass()
            self._glass_applied = True

    def _apply_glass(self):
        """Win11 mica → Win10 acrylic → opacity 三级降级"""
        if not sys.platform.startswith("win"):
            self.setWindowOpacity(0.96)
            return
        try:
            import ctypes
            from ctypes import wintypes
        except Exception as e:
            _log(f"glass: ctypes 不可用 ({e}), 降级 opacity")
            self.setWindowOpacity(0.96)
            return

        hwnd = int(self.winId())

        # 试 Win11 Mica (DWMWA_SYSTEMBACKDROP_TYPE = 38, value=2 mica)
        DWMWA_SYSTEMBACKDROP_TYPE = 38
        DWMSBT_MAINWINDOW = 2
        try:
            backdrop = ctypes.c_int(DWMSBT_MAINWINDOW)
            r = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                wintypes.DWORD(DWMWA_SYSTEMBACKDROP_TYPE),
                ctypes.byref(backdrop), ctypes.sizeof(backdrop),
            )
            if r == 0:
                # mica 还需要 dark/light mode 提示, 这里走 dark 配主题
                DWMWA_USE_IMMERSIVE_DARK_MODE = 20
                dark = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    wintypes.HWND(hwnd),
                    wintypes.DWORD(DWMWA_USE_IMMERSIVE_DARK_MODE),
                    ctypes.byref(dark), ctypes.sizeof(dark),
                )
                _log("glass: Win11 mica enabled")
                return
            _log(f"glass: mica unavailable (hr={r:#x}), 试 acrylic")
        except Exception as e:
            _log(f"glass: dwmapi mica 异常: {e}")

        # 退到 Win10 acrylic (旧 ABI: AccentPolicy)
        try:
            class ACCENT_POLICY(ctypes.Structure):
                _fields_ = [
                    ("AccentState", ctypes.c_uint),
                    ("AccentFlags", ctypes.c_uint),
                    ("GradientColor", ctypes.c_uint),
                    ("AnimationId", ctypes.c_uint),
                ]
            class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
                _fields_ = [
                    ("Attribute", ctypes.c_int),
                    ("Data", ctypes.POINTER(ctypes.c_int)),
                    ("SizeOfData", ctypes.c_size_t),
                ]
            ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
            WCA_ACCENT_POLICY = 19
            accent = ACCENT_POLICY()
            accent.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND
            accent.GradientColor = 0x80EDEDED  # AABBGGRR (50% 浅灰)
            data = WINDOWCOMPOSITIONATTRIBDATA()
            data.Attribute = WCA_ACCENT_POLICY
            data.SizeOfData = ctypes.sizeof(accent)
            data.Data = ctypes.cast(ctypes.pointer(accent),
                                    ctypes.POINTER(ctypes.c_int))
            r = ctypes.windll.user32.SetWindowCompositionAttribute(
                wintypes.HWND(hwnd), ctypes.byref(data)
            )
            if r:
                _log("glass: Win10 acrylic enabled")
                return
        except Exception as e:
            _log(f"glass: acrylic 异常: {e}")

        # 都失败: 退到 opacity, 关掉 translucent (否则会全透)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setWindowOpacity(0.96)
        _log("glass: fallback to opacity 0.96")

    # ---------------- UI ----------------
    def _build_ui(self):
        # 主题相关的 styleSheet 在 _build_ui 末尾统一调 _apply_theme() 设置
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题条
        self.header_frame = QFrame()
        self.header_frame.setObjectName("chat_header")
        self.header_frame.setFixedHeight(38)
        h = QHBoxLayout(self.header_frame)
        h.setContentsMargins(12, 0, 8, 0)
        h.setSpacing(8)

        # 模式 chip — 闲聊 vs 项目 一眼可分 (样式由 _refresh_title 设)
        self.mode_chip = QLabel("")
        h.addWidget(self.mode_chip)

        self.title_label = QLabel("")
        h.addWidget(self.title_label)

        self.status_label = QLabel("· 待机")
        h.addWidget(self.status_label)
        h.addStretch(1)

        # 模型下拉 (每个 mode/project 独立, 切 mode 自动刷新)
        self.model_combo = QComboBox()
        self.model_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.model_combo.setToolTip(
            "模型 — 当前模式 (闲聊 / 项目) 单独保存。"
            "默认 = 不传 --model, 跟 Claude Code 全局走"
        )
        for key, label in MODEL_OPTIONS:
            self.model_combo.addItem(label, key)
        self.model_combo.addItem("自定义…", "__custom__")
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        h.addWidget(self.model_combo)

        # 权限模式下拉
        self.perm_combo = QComboBox()
        self.perm_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        for key, label in PERMISSION_MODES:
            self.perm_combo.addItem(label, key)
        cur = load_permission_mode()
        for i, (k, _) in enumerate(PERMISSION_MODES):
            if k == cur:
                self.perm_combo.setCurrentIndex(i)
                break
        self.perm_combo.currentIndexChanged.connect(self._on_perm_changed)
        self.perm_combo.setToolTip("Claude 工具调用权限策略 — 改动立即生效")
        h.addWidget(self.perm_combo)

        self.session_btn = self._mk_header_btn("会话 ▾")
        self.session_btn.clicked.connect(self._show_session_menu)
        h.addWidget(self.session_btn)

        # 工具按钮分组前加小间距, 跟下拉/文字按钮区分
        h.addSpacing(4)

        # 太阳 / 月亮 主题切换 (图标和 tooltip 由 _apply_theme 设置)
        self.theme_btn = IconButton("moon")
        self.theme_btn.clicked.connect(self._toggle_theme)
        h.addWidget(self.theme_btn)

        self.max_btn = IconButton("max")
        self.max_btn.setToolTip("最大化")
        self.max_btn.clicked.connect(self._toggle_maximize)
        h.addWidget(self.max_btn)

        self.close_btn = IconButton("close")
        self.close_btn.setToolTip("关闭")
        self.close_btn.clicked.connect(self.hide)
        h.addWidget(self.close_btn)

        root.addWidget(self.header_frame)

        # 消息列表
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_holder = QWidget()
        self.list_layout = QVBoxLayout(self.list_holder)
        self.list_layout.setContentsMargins(0, 6, 0, 6)
        self.list_layout.setSpacing(2)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_holder)
        root.addWidget(self.scroll, 1)

        # 粘贴图片预览条 (composer 上方, 有图才显示)
        self.paste_strip = PasteStrip()
        root.addWidget(self.paste_strip)

        # 输入区
        self.composer = QFrame()
        self.composer.setObjectName("chat_composer")
        self.composer.setFixedHeight(72)
        c = QHBoxLayout(self.composer)
        c.setContentsMargins(8, 8, 8, 8)
        c.setSpacing(6)

        self.input = ChatInput()
        self.input.setPlaceholderText("Enter 发送, Shift+Enter 换行 · 支持粘贴图片")
        self.input.setFixedHeight(56)
        self.input.installEventFilter(self)
        self.input.image_pasted.connect(self.paste_strip.add)
        c.addWidget(self.input, 1)

        self.send_btn = QPushButton("发送")
        self.send_btn.setFixedSize(56, 56)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setStyleSheet(
            f"QPushButton {{ background: {COLOR_SEND}; color: white; "
            f"border: 0; border-radius: 4px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: #06ad57; }}"
            f"QPushButton:disabled {{ background: #b8e6cd; }}"
        )
        self.send_btn.clicked.connect(self._on_send)
        c.addWidget(self.send_btn)

        root.addWidget(self.composer)

        # 所有 widget 都创建完了, 统一上一次主题样式
        self._apply_theme()

    def _mk_header_btn(self, text: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # styleSheet 由 _apply_theme() 统一设置 (无边框 + hover 显背景)
        return btn

    # ---------------- 主题 ----------------
    def _apply_theme(self, name: str | None = None):
        """切到指定主题 (None = 用当前 self._theme). 重设所有相关 styleSheet"""
        if name is not None and name in THEMES:
            self._theme = name
            self._colors = THEMES[name]
        c = self._colors

        # 面板 (尊重当前 tone, 通过 set_panel_tone 走)
        self.set_panel_tone(getattr(self, "_current_tone", "normal"))

        # header — 跟 panel 同色调, 靠底部 1px 细线分隔
        self.header_frame.setStyleSheet(
            f"#chat_header {{ background: {c['header_bg']}; "
            f"border-top-left-radius: 7px; border-top-right-radius: 7px; "
            f"border-bottom: 1px solid {c['border']}; }}"
        )
        self.title_label.setStyleSheet(
            f"color: {c['header_txt']}; font-weight: 600; "
            f"font-size: 12px; background: transparent;"
        )
        self.status_label.setStyleSheet(
            f"color: {c['status_txt']}; font-size: 10px; background: transparent;"
        )

        # 下拉框 (两个 combo 共用) — 极简, 跟主题底色融合
        combo_qss = (
            f"QComboBox {{ background: {c['combo_bg']}; "
            f"color: {c['combo_text']}; border: 1px solid {c['border']}; "
            f"border-radius: 5px; padding: 2px 8px; font-size: 10px; }}"
            f"QComboBox:hover {{ background: {c['icon_hover']}; }}"
            f"QComboBox::drop-down {{ border: 0; width: 14px; }}"
            f"QComboBox QAbstractItemView {{ background: {c['combo_view_bg']}; "
            f"color: {c['combo_text']}; "
            f"selection-background-color: {c['icon_hover']}; "
            f"border: 1px solid {c['border']}; padding: 2px; }}"
        )
        self.model_combo.setStyleSheet(combo_qss)
        self.perm_combo.setStyleSheet(combo_qss)

        # 纯文字 header 按钮 (session_btn): 无边框, hover 显背景
        text_btn_qss = (
            f"QToolButton {{ background: transparent; color: {c['header_txt']}; "
            f"border: 0; padding: 4px 10px; border-radius: 5px; "
            f"font-size: 11px; }}"
            f"QToolButton:hover {{ background: {c['icon_hover']}; }}"
        )
        self.session_btn.setStyleSheet(text_btn_qss)

        # IconButton (主题/最大化/关闭) — 无边框 + hover 圆角背景, 图标由 paintEvent 画
        icon_btn_qss = (
            f"QToolButton {{ background: transparent; border: 0; "
            f"border-radius: 6px; }}"
            f"QToolButton:hover {{ background: {c['icon_hover']}; }}"
        )
        for btn in (self.theme_btn, self.max_btn, self.close_btn):
            btn.setStyleSheet(icon_btn_qss)
            btn.set_icon_color(c["icon_color"])

        # 滚动区
        self.scroll.setStyleSheet(
            f"QScrollArea {{ background: {c['panel_bg']}; border: 0; }}"
            f"QScrollBar:vertical {{ background: transparent; width: 8px; "
            f"margin: 2px 2px 2px 0; }}"
            f"QScrollBar::handle:vertical {{ background: {c['scroll_handle']}; "
            f"border-radius: 3px; min-height: 24px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical "
            f"{{ height: 0; }}"
            f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical "
            f"{{ background: transparent; }}"
        )
        self.list_holder.setStyleSheet(f"background: {c['panel_bg']};")

        # 粘贴图片预览条 (跟 composer 同色, 上面 1px 跟列表区分隔)
        if hasattr(self, "paste_strip"):
            self.paste_strip.setStyleSheet(
                f"#paste_strip {{ background: {c['input_bg']}; "
                f"border-top: 1px solid {c['border']}; }}"
            )

        # 输入区
        self.composer.setStyleSheet(
            f"#chat_composer {{ background: {c['input_bg']}; "
            f"border-top: 1px solid {c['border']}; "
            f"border-bottom-left-radius: 7px; border-bottom-right-radius: 7px; }}"
        )
        self.input.setStyleSheet(
            f"QPlainTextEdit {{ background: {c['input_field']}; "
            f"color: {c['input_text']}; "
            f"border: 1px solid {c['input_border']}; border-radius: 6px; "
            f"font-size: 12px; padding: 6px 8px; }}"
            f"QPlainTextEdit:focus {{ border-color: {COLOR_SEND}; }}"
        )

        # 太阳/月亮: dark 模式显示太阳 (点了变 light), light 模式显示月亮
        if hasattr(self, "theme_btn"):
            if self._theme == "dark":
                self.theme_btn.set_icon_name("sun")
                self.theme_btn.setToolTip("切到浅色")
            else:
                self.theme_btn.set_icon_name("moon")
                self.theme_btn.setToolTip("切到深色")

        # 已有的所有气泡同步主题
        if hasattr(self, "list_layout"):
            for b in self.findChildren(Bubble):
                b.apply_theme(c, self._theme)

        # title chip 跟主题走 (两种模式 chip 的边框/字色都依赖主题)
        if hasattr(self, "mode_chip"):
            self._refresh_title()

    def _toggle_theme(self):
        new = "dark" if self._theme == "light" else "light"
        self._apply_theme(new)
        _save_theme(new)

    def _toggle_maximize(self):
        """最大化 ↔ 还原. 最大化时屏蔽 resize 边缘 + 桌宠 reposition 跟随"""
        if self._maximized:
            self._maximized = False
            if self._restore_geom is not None:
                self.setGeometry(self._restore_geom)
            self.max_btn.set_icon_name("max")
            self.max_btn.setToolTip("最大化")
            # 还原后跟随桌宠回位
            if self._host is not None:
                self.reposition()
        else:
            self._restore_geom = QRect(self.geometry())
            screen = QGuiApplication.screenAt(self.frameGeometry().center())
            if screen is None:
                screen = QGuiApplication.primaryScreen()
            self._maximized = True  # 置位在 setGeometry 之前, 避免 reposition 抢走
            self.setGeometry(screen.availableGeometry())
            self.max_btn.set_icon_name("restore")
            self.max_btn.setToolTip("还原")

    def _on_perm_changed(self, idx: int):
        key = self.perm_combo.itemData(idx)
        if key:
            save_permission_mode(key)
            _log(f"permission mode → {key}")

    # ---- 模型 ----
    def _model_file(self) -> Path:
        return _conv_dir(self._key()) / "model"

    def _load_model(self) -> str:
        f = self._model_file()
        if f.exists():
            v = f.read_text(encoding="utf-8").strip()
            if v: return v
        return ""

    def _save_model(self, model: str):
        f = self._model_file()
        try:
            if model:
                f.write_text(model, encoding="utf-8")
            else:
                if f.exists(): f.unlink()
        except Exception: pass

    def _refresh_model_combo(self):
        """切 mode 时调用, 让下拉显示当前 mode 的模型"""
        # 拦掉信号, 避免重置触发 _on_model_changed
        self.model_combo.blockSignals(True)
        # 先清掉之前可能加的"自定义"item (留预设 + __custom__)
        i = self.model_combo.count() - 2  # 最后一个是 "自定义..."
        while i >= 0 and self.model_combo.itemData(i) not in MODEL_KEYS:
            self.model_combo.removeItem(i)
            i -= 1
        cur = self._load_model()
        target_idx = -1
        for i in range(self.model_combo.count()):
            if self.model_combo.itemData(i) == cur:
                target_idx = i
                break
        if target_idx < 0 and cur:
            # 自定义模型 — 在 "自定义..." 之前插一项
            insert_at = self.model_combo.count() - 1
            self.model_combo.insertItem(insert_at, f"[自定义] {cur}", cur)
            target_idx = insert_at
        if target_idx < 0:
            target_idx = 0  # 默认
        self.model_combo.setCurrentIndex(target_idx)
        self.model_combo.blockSignals(False)

    def _on_model_changed(self, idx: int):
        key = self.model_combo.itemData(idx)
        if key == "__custom__":
            from PyQt6.QtWidgets import QInputDialog
            text, ok = QInputDialog.getText(
                self, "自定义模型",
                "输入模型 ID (如 claude-opus-4-7 / 别名 / 第三方 endpoint id):",
                text=self._load_model(),
            )
            if ok and text.strip():
                self._save_model(text.strip())
                self._refresh_model_combo()
                _log(f"model → custom: {text.strip()}")
            else:
                # 取消 → 恢复到之前的选择
                self._refresh_model_combo()
            return
        # 预设或之前的自定义
        self._save_model(key or "")
        _log(f"model → {key or '(default)'}")

    def _refresh_title(self):
        c = self._colors
        accent = c.get("project_accent", COLOR_PROJECT)
        if self._mode == "chat":
            # 闲聊: 不显示 chip, title 自然
            self.mode_chip.setText("")
            self.mode_chip.setStyleSheet("background: transparent; border: 0;")
            self.mode_chip.hide()
            self.title_label.setTextFormat(Qt.TextFormat.PlainText)
            self.title_label.setText("和泡沫的聊天")
            self.title_label.setToolTip("闲聊模式 · cwd = foamo_pet")
        else:
            # 项目: 纯彩色加粗文字, 无背景无边框 (GitHub branch-name 风, 克制)
            self.mode_chip.setText(self._mode_name)
            self.mode_chip.setStyleSheet(
                f"background: transparent; color: {accent}; "
                f"border: 0; padding: 0; "
                f"font-size: 12px; font-weight: 700;"
            )
            self.mode_chip.setToolTip(f"项目: {self._project_path}")
            self.mode_chip.show()
            self.title_label.setTextFormat(Qt.TextFormat.PlainText)
            self.title_label.setText("· 和泡沫")
            self.title_label.setToolTip(f"项目模式 · {self._project_path}")

    # ---------------- 事件 ----------------
    def eventFilter(self, obj, event):
        et = event.type()

        # 输入框: Enter / Esc
        if obj is self.input and et == QEvent.Type.KeyPress:
            is_enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if is_enter and not shift:
                self._on_send()
                return True
            if event.key() == Qt.Key.Key_Escape:
                self.hide()
                return True

        # 桌宠移动/显隐 → 同步面板
        if obj is self._host:
            if et in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show):
                self.reposition()
            elif et == QEvent.Type.Hide:
                self.hide()
            return super().eventFilter(obj, event)

        # 滚动条贴 panel 右边, 跟 6px 边缘检测重叠 — 它上面的所有鼠标事件
        # 都不走 resize 逻辑, 让 QScrollBar 自己处理拖拽/点击
        if isinstance(obj, QScrollBar):
            return super().eventFilter(obj, event)

        # 8 方向边缘拖拽 (面板自身 + 所有子控件)
        if et == QEvent.Type.MouseMove:
            if self._resize_edge is not None:
                self._do_resize(event.globalPosition().toPoint())
                return True
            # 节流: setMouseTracking(True) 让 hover 也触发 MouseMove,
            # 50ms 一次足够流畅 (20Hz 人眼无感), 减轻边缘检测 + setCursor 压力
            now_ms = int(time.monotonic() * 1000)
            if now_ms - self._last_cursor_check_ms < 50:
                return super().eventFilter(obj, event)
            self._last_cursor_check_ms = now_ms
            self._update_resize_cursor(event.globalPosition().toPoint(), obj)
        elif et == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                gp = event.globalPosition().toPoint()
                edge = self._edge_at_global(gp)
                if edge:
                    self._resize_edge = edge
                    self._press_global = gp
                    self._press_geom = QRect(self.geometry())
                    return True  # 拦截不让子控件处理
        elif et == QEvent.Type.MouseButtonRelease:
            if self._resize_edge is not None:
                self._resize_edge = None
                return True
        elif et == QEvent.Type.Leave:
            # 离开 widget 时, 如果不在 resize 中就让 cursor 复原
            if self._resize_edge is None and isinstance(obj, QWidget):
                obj.unsetCursor()

        return super().eventFilter(obj, event)

    # ---------------- 8 方向 resize ----------------
    def _install_resize_filters(self):
        """给所有子控件装 eventFilter,让边缘 hover/拖拽生效"""
        self.installEventFilter(self)
        for w in self.findChildren(QWidget):
            w.installEventFilter(self)
            w.setMouseTracking(True)

    def _edge_at_global(self, gp: QPoint) -> str | None:
        """全局坐标 → 在哪个边缘 (None = 不在边缘)"""
        if self._maximized:
            return None
        rel = gp - self.frameGeometry().topLeft()
        x, y = rel.x(), rel.y()
        w, h = self.width(), self.height()
        t = EDGE_THRESH
        # 允许少量越界容差(鼠标稍微出窗口边也算)
        left = -2 <= x < t
        right = w - t < x <= w + 2
        top = -2 <= y < t
        bottom = h - t < y <= h + 2
        if top and left: return "tl"
        if top and right: return "tr"
        if bottom and left: return "bl"
        if bottom and right: return "br"
        if left: return "l"
        if right: return "r"
        if top: return "t"
        if bottom: return "b"
        return None

    def _update_resize_cursor(self, gp: QPoint, obj: QWidget):
        edge = self._edge_at_global(gp)
        if edge:
            cur = EDGE_CURSORS[edge]
            if obj.cursor().shape() != cur:
                obj.setCursor(cur)
        else:
            # 不在边缘时, 让子控件用自己的默认 cursor
            # (input 用 IBeam, button 用 Pointing) — unsetCursor 即可
            shape = obj.cursor().shape()
            if shape in (
                Qt.CursorShape.SizeFDiagCursor, Qt.CursorShape.SizeBDiagCursor,
                Qt.CursorShape.SizeHorCursor, Qt.CursorShape.SizeVerCursor,
            ):
                obj.unsetCursor()

    def _do_resize(self, gp: QPoint):
        dx = gp.x() - self._press_global.x()
        dy = gp.y() - self._press_global.y()
        g = QRect(self._press_geom)
        edge = self._resize_edge or ""
        min_w = max(self.minimumWidth(), 200)
        min_h = max(self.minimumHeight(), 240)
        if "l" in edge:
            new_left = g.left() + dx
            if g.right() - new_left + 1 < min_w:
                new_left = g.right() - min_w + 1
            g.setLeft(new_left)
        if "r" in edge:
            new_right = g.right() + dx
            if new_right - g.left() + 1 < min_w:
                new_right = g.left() + min_w - 1
            g.setRight(new_right)
        if "t" in edge:
            new_top = g.top() + dy
            if g.bottom() - new_top + 1 < min_h:
                new_top = g.bottom() - min_h + 1
            g.setTop(new_top)
        if "b" in edge:
            new_bottom = g.bottom() + dy
            if new_bottom - g.top() + 1 < min_h:
                new_bottom = g.top() + min_h - 1
            g.setBottom(new_bottom)
        self.setGeometry(g)

    # ---------------- 附着到桌宠 ----------------
    def attach_to(self, host: QWidget):
        if self._host is host: return
        if self._host is not None:
            self._host.removeEventFilter(self)
            # 解绑旧 host 的双向信号
            try:
                self.pet_request.disconnect(self._host._on_pet_request_from_chat)
            except Exception: pass
            try:
                self._host.pet_state_changed.disconnect(self._on_pet_state)
            except Exception: pass
        self._host = host
        host.installEventFilter(self)
        # 双向联动: 监听器通过 pet_request 让桌宠切状态,
        # 桌宠状态变化通过 pet_state_changed 反推面板配色
        if hasattr(host, "_on_pet_request_from_chat"):
            try:
                self.pet_request.connect(host._on_pet_request_from_chat)
            except Exception: pass
        if hasattr(host, "pet_state_changed"):
            try:
                host.pet_state_changed.connect(self._on_pet_state)
            except Exception: pass

    def _on_pet_state(self, state: str):
        """桌宠 → 面板. 只改视觉, 不反向触发 monitor 或 pet_request (防递归)"""
        if state == "worried":
            self.set_panel_tone("worried")
        elif state == "tender":
            self.set_panel_tone("night")
        elif state == "happy":
            self.flash_border("#ff6b9d", 1500)
        elif state == "proud":
            self.flash_border("#ffd54f", 1500)
        elif state == "focused":
            self.flash_border("#4dd0e1", 1500)
        elif state == "idle":
            self.set_panel_tone("normal")

    def reposition(self):
        if self._maximized: return
        if self._host is None or not self._host.isVisible(): return
        host_geom = self._host.frameGeometry()
        screen = QGuiApplication.screenAt(host_geom.center())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        pw, ph = self.width(), self.height()

        left_x = host_geom.left() - pw - GAP
        if left_x >= avail.left():
            x = left_x
        else:
            x = host_geom.right() + GAP
            if x + pw > avail.right():
                x = max(avail.left(), avail.right() - pw)

        y = host_geom.bottom() - ph
        if y < avail.top(): y = avail.top()
        if y + ph > avail.bottom(): y = avail.bottom() - ph

        self.move(x, y)

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.reposition()
            self.show()
            self.raise_()
            self.activateWindow()
            self.input.setFocus()

    # ---------------- session/history (按 mode 路径) ----------------
    def _key(self) -> str:
        return _project_key(self._project_path)

    def _session_file(self) -> Path:
        return _conv_dir(self._key()) / "session"

    def _history_file(self) -> Path:
        return _conv_dir(self._key()) / "history.json"

    def _meta_file(self) -> Path:
        return _conv_dir(self._key()) / "meta.json"

    def _save_meta(self):
        if self._mode == "project":
            try:
                self._meta_file().write_text(json.dumps({
                    "path": self._project_path,
                    "name": self._mode_name,
                }, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception: pass

    def _load_history(self):
        f = self._history_file()
        if not f.exists(): return
        try:
            arr = json.loads(f.read_text(encoding="utf-8"))
        except Exception: return
        for m in arr:
            role = m.get("role", "assistant")
            text = m.get("text", "")
            if role == "system":
                self._append_system(text, persist=False)
            else:
                self._append_message(role, text, persist=False)

    def _append_history(self, role: str, text: str):
        f = self._history_file()
        history = []
        if f.exists():
            try: history = json.loads(f.read_text(encoding="utf-8"))
            except Exception: history = []
        history.append({"role": role, "text": text, "ts": int(time.time())})
        try:
            f.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")
        except Exception: pass

    def _load_session(self) -> str | None:
        f = self._session_file()
        if f.exists():
            sid = f.read_text(encoding="utf-8").strip()
            return sid or None
        return None

    def _save_session(self, sid: str):
        try: self._session_file().write_text(sid, encoding="utf-8")
        except Exception: pass

    def _clear_current_state(self):
        for f in (self._session_file(), self._history_file()):
            try:
                if f.exists(): f.unlink()
            except Exception: pass

    # ---------------- 列表 ----------------
    def _append_message(self, role: str, text: str, persist: bool = True,
                        attachments: list[Path] | None = None) -> Bubble:
        bubble = Bubble(role, text, attachments=attachments)
        bubble.apply_theme(self._colors, self._theme)
        max_w = max(220, min(640, int(self.scroll.viewport().width() * 0.7)))
        bubble.setMaximumWidth(max_w)
        row = MessageRow(role, bubble)
        idx = self.list_layout.count() - 1
        self.list_layout.insertWidget(idx, row)
        self._scroll_to_bottom()
        if persist:
            # history 仅持久化文字 + 图片路径标记 (重启加载只还原文字)
            persist_text = text
            if attachments:
                tail = "\n".join(f"![pasted]({p})" for p in attachments)
                persist_text = (text + "\n" + tail) if text else tail
            self._append_history(role, persist_text)
        return bubble

    def _append_tool_block(self, name: str, tool_use_id: str | None) -> "ToolChip":
        # 没有正在累加的 strip 就开一个 (不带头像, 占位极简)
        if self._current_tool_strip is None:
            strip = ToolStrip()
            self._current_tool_strip = strip
            row = MessageRow("system", strip)  # system row 居中无头像
            idx = self.list_layout.count() - 1
            self.list_layout.insertWidget(idx, row)
        chip = ToolChip(name, tool_use_id)
        if tool_use_id:
            self._tool_blocks[tool_use_id] = chip
        self._current_tool_strip.add_chip(chip)
        self._scroll_to_bottom()
        return chip

    def _close_tool_strip(self):
        """收到新文字 / 新一轮时调用: 下次工具调用会开一个新的 strip"""
        self._current_tool_strip = None

    def _append_system(self, text: str, persist: bool = True):
        notice = SystemNotice(text)
        row = MessageRow("system", notice)
        idx = self.list_layout.count() - 1
        self.list_layout.insertWidget(idx, row)
        self._scroll_to_bottom()
        if persist:
            self._append_history("system", text)

    def _clear_list(self):
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _scroll_to_bottom(self):
        # 节流: 多次调用合并成一次, 30ms 后真滚 (避免每个 chunk 一个 QTimer)
        if not self._scroll_timer.isActive():
            self._scroll_timer.start()

    def _scroll_to_bottom_now(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        # Bubble sizeHint 依赖 wordWrap 后的实际高度, 第一次 setValue 时
        # 新加的气泡 layout 可能还没算完 → maximum 还是旧的, 滚到顶不到底.
        # 下一个事件循环再补一次, 这时 layout 肯定完成.
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 阈值: 宽度没变就不必遍历所有气泡 setMaximumWidth (历史多时省)
        max_w = max(220, min(640, int(self.scroll.viewport().width() * 0.7)))
        if max_w == self._last_max_w:
            return
        self._last_max_w = max_w
        for i in range(self.list_layout.count() - 1):
            item = self.list_layout.itemAt(i)
            if item is None: continue
            row = item.widget()
            if row is None: continue
            for child in row.findChildren(Bubble):
                child.setMaximumWidth(max_w)

    # ---------------- 会话菜单 ----------------
    def _show_session_menu(self):
        if self._busy:
            QMessageBox.information(self, "等等", "泡沫还在回上一句,等回完再切")
            return

        c = self._colors
        menu = QMenu(self)
        # popup 必须用实色背景, 半透明在 Win 下渲染不稳 (按钮点击仿佛"没反应")
        menu.setStyleSheet(
            f"QMenu {{ background: {c['menu_bg']}; color: {c['header_txt']}; "
            f"border: 1px solid {c['border_solid']}; padding: 4px; }}"
            f"QMenu::item {{ padding: 6px 16px; border-radius: 3px; }}"
            f"QMenu::item:selected {{ background: {c['menu_hover']}; }}"
            f"QMenu::separator {{ height: 1px; background: {c['border_solid']}; "
            f"margin: 4px 6px; }}"
        )

        # 顶部:开始新对话(清空当前 mode)
        new_act = QAction("🆕 开始新对话 (清空当前)", menu)
        new_act.triggered.connect(self._do_new_conversation)
        menu.addAction(new_act)
        menu.addSeparator()

        # 切换模式
        chat_act = QAction(
            ("● " if self._mode == "chat" else "  ") + "💬 闲聊模式",
            menu
        )
        chat_act.triggered.connect(lambda: self._switch_to(None, "闲聊"))
        menu.addAction(chat_act)

        pick_act = QAction("📁 选择项目目录...", menu)
        pick_act.triggered.connect(self._pick_project)
        menu.addAction(pick_act)

        menu.addSeparator()

        # MCP 服务器管理 (全局)
        mcp_act = QAction("🔌 MCP 服务器...", menu)
        mcp_act.triggered.connect(self._open_mcp_manager)
        menu.addAction(mcp_act)

        # 最近项目
        projects = _load_projects()
        if projects:
            menu.addSeparator()
            for p in projects:
                path = p.get("path", "")
                name = p.get("name", Path(path).name if path else "?")
                marker = "● " if (self._mode == "project" and self._project_path == path) else "  "
                act = QAction(f"{marker}📦 {name}", menu)
                act.setToolTip(path)
                act.triggered.connect(
                    lambda _checked=False, pa=path, n=name: self._switch_to(pa, n)
                )
                menu.addAction(act)
            menu.addSeparator()
            forget_act = QAction("🗑 忘掉所有项目", menu)
            forget_act.triggered.connect(self._forget_all_projects)
            menu.addAction(forget_act)

        menu.exec(self.session_btn.mapToGlobal(QPoint(0, self.session_btn.height())))

    def _switch_to(self, path: str | None, name: str):
        """切到指定模式 (path=None 闲聊). 不清空,加载该模式之前的历史"""
        if path == self._project_path and (
            (path is None and self._mode == "chat") or
            (path is not None and self._mode == "project")
        ):
            # 已经在该模式
            return
        self._mode = "chat" if path is None else "project"
        self._project_path = path
        self._mode_name = name
        if path is not None:
            _add_project(path, name)
            self._save_meta()
        _save_active({"mode": self._mode, "path": path, "name": name})

        self._refresh_title()
        self._refresh_model_combo()  # 切 mode 后刷新模型下拉
        self._clear_list()
        self._load_history()
        self._append_system(f"— 切到 {name} —", persist=False)
        _log(f"switch: mode={self._mode} path={path}")
        # monitor: 模式切换 (I 项目首聊 用)
        self.bus.dispatch(EVT_MODE_SWITCH, {
            "mode": self._mode,
            "path": self._project_path,
            "name": name,
            "key": self._key(),
        })

    def _open_mcp_manager(self):
        """打开全局 MCP 服务器配置 dialog"""
        try:
            from mcp_manager import MCPManagerDialog
        except Exception as e:
            QMessageBox.warning(self, "MCP 管理器加载失败", str(e))
            return
        dlg = MCPManagerDialog(ROOT, self)
        dlg.exec()

    def _pick_project(self):
        last = ""
        projects = _load_projects()
        if projects:
            last = str(Path(projects[0].get("path", "")).parent)
        path = QFileDialog.getExistingDirectory(
            self, "选择项目目录", last or str(Path.home()),
            QFileDialog.Option.ShowDirsOnly
        )
        if not path: return
        path = str(Path(path).resolve())
        name = Path(path).name or path
        self._switch_to(path, name)

    def _do_new_conversation(self):
        ret = QMessageBox.question(
            self, "新对话",
            f"清空当前 [{self._mode_name}] 的历史,开新 session?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes: return
        self._clear_current_state()
        self._clear_list()
        self._append_system(f"— [{self._mode_name}] 新对话开始 —", persist=False)

    def _forget_all_projects(self):
        ret = QMessageBox.question(
            self, "清空项目列表",
            "忘掉所有最近项目?\n(各项目的对话历史不会删,只是从快捷列表里移除)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes: return
        try: PROJECTS_FILE.unlink()
        except Exception: pass

    # ---------------- 用户操作 ----------------
    def _on_send(self):
        text = self.input.toPlainText().strip()
        images = self.paste_strip.paths()
        if not (text or images) or self._busy: return
        # 先给 monitor 一次机会: G 暗号会拦截 (只看文字, 不管图)
        if text and self.bus.dispatch(EVT_USER_SEND, {"text": text}):
            self.input.clear()
            self.paste_strip.clear()
            return
        self._append_message("user", text, attachments=images)
        self.input.clear()
        self.paste_strip.clear()
        # 独占欲触发: 豆哥提到别的 AI 时, 泡沫吃醋脸 + 一句话
        if text and _is_jealous_text(text):
            self._trigger_jealous()
        # 拼图片路径到 prompt: 让 Claude Code 自己识别 markdown 图引用,
        # 调它的 Read 工具读图. 比 --image flag 改动小.
        prompt = text
        if images:
            img_block = "\n".join(f"![pasted]({p})" for p in images)
            prompt = (text + "\n\n" + img_block) if text else img_block
        self._set_busy(True)
        self._spawn_claude(prompt)

    def _trigger_jealous(self):
        """让 FoamoWidget 切到 jealous pose + 冒一句酸话, JEALOUS_HOLD_MS 后自动复位."""
        host = self._host
        if host is None:
            return
        import random as _rnd
        line = _rnd.choice(JEALOUS_LINES)
        try:
            if hasattr(host, "_set_pose"):
                host._set_pose("jealous")
            if hasattr(host, "_show_line"):
                host._show_line(line, JEALOUS_HOLD_MS / 1000.0)
        except Exception as e:
            _log(f"jealous trigger failed: {e}")
            return
        # 到点自动复位 pose (None = 回 state)
        def _reset():
            try:
                if host is not None and hasattr(host, "_set_pose"):
                    # 只有还停在 jealous 才清; 期间豆哥拖到屏幕边切了别的 pose 就不动它
                    if getattr(host, "pose", None) == "jealous":
                        host._set_pose(None)
            except Exception: pass
        QTimer.singleShot(JEALOUS_HOLD_MS, _reset)

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.send_btn.setEnabled(not busy)
        self.input.setReadOnly(busy)
        self.status_label.setText("· 思考中…" if busy else "· 待机")

    # ---------------- claude QProcess ----------------
    def _spawn_claude(self, text: str):
        sid = self._load_session()
        perm_mode = load_permission_mode()
        model = self._load_model()
        args = [
            "-p",
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",
            "--permission-mode", perm_mode,
        ]
        if model:
            args += ["--model", model]
        # 加载我们自己的 hook 配置 (PreToolUse 弹窗), 不污染用户全局 settings
        if HOOK_SETTINGS_FILE.exists():
            args += ["--settings", str(HOOK_SETTINGS_FILE)]
        # 加载全局 MCP servers (只含 enabled 的, 来自 mcp_servers.json)
        try:
            from mcp_manager import build_effective_mcp_file
            mcp_file = build_effective_mcp_file(ROOT)
            mcp_count = 0
            if mcp_file is not None:
                args += ["--mcp-config", str(mcp_file)]
                # 数一下加载了几个
                try:
                    mcp_count = len(json.loads(
                        mcp_file.read_text(encoding="utf-8")
                    ).get("mcpServers", {}))
                except Exception: pass
        except Exception as e:
            mcp_count = 0
            _log(f"mcp 配置加载失败: {e}")
        if sid:
            args += ["--resume", sid]
        else:
            args += ["--session-id", str(uuid.uuid4())]
        # prompt 通过 stdin 喂, 不当命令行参数 — Windows 上 CreateProcess 拼命令行字符串
        # 时多行 argument 转义不稳, 经常被截到第一行.

        self._stdout_buf = b""
        self._stderr_buf = b""
        self._captured_sid = None
        self._current_text = ""
        self._tool_blocks = {}
        self._pending_tool_index = {}
        self._current_tool_strip = None

        # 立即出助手占位气泡 (头像 + thinking spinner),
        # 别让豆哥对着空白等 — 第一个 chunk 来了 append_text 自动关 thinking
        self._current_bubble = self._append_message("assistant", "", persist=False)
        self._current_bubble.set_thinking(True)

        cwd = self._project_path or DEFAULT_CWD
        proxy = _resolve_proxy()
        _log(f"spawn: mode={self._mode} model={model or '(default)'} "
             f"perm={perm_mode} mcp={mcp_count} cwd={cwd} "
             f"resume={'yes' if sid else 'no'} "
             f"proxy={proxy or '(none)'} prompt={len(text)}ch")

        proc = QProcess(self)
        proc.setProgram(CLAUDE_BIN)
        proc.setArguments(args)
        proc.setWorkingDirectory(cwd)
        proc.setProcessEnvironment(_build_process_env())
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        # process 起来后立刻把 prompt 喂进 stdin, 然后关掉写端让 claude 知道输入结束
        prompt_bytes = text.encode("utf-8")
        def _feed_prompt():
            try:
                proc.write(prompt_bytes)
                proc.closeWriteChannel()
            except Exception as e:
                _log(f"feed stdin failed: {e}")
        proc.started.connect(_feed_prompt)
        self._proc = proc
        proc.start()

    def _on_stdout(self):
        if self._proc is None: return
        self._stdout_buf += bytes(self._proc.readAllStandardOutput())
        while b"\n" in self._stdout_buf:
            line, self._stdout_buf = self._stdout_buf.split(b"\n", 1)
            line = line.decode("utf-8", errors="replace").strip()
            if line:
                self._handle_event(line)

    def _on_stderr(self):
        if self._proc is None: return
        chunk = bytes(self._proc.readAllStandardError())
        self._stderr_buf += chunk
        try:
            txt = chunk.decode("utf-8", errors="replace").rstrip()
            if txt: _log(f"stderr: {txt}")
        except Exception: pass

    def _emit_text(self, chunk: str):
        if not chunk: return
        # monitor 监听 text 流 (H 走神检测用)
        self.bus.dispatch(EVT_TEXT, {"chunk": chunk})
        # 助手又开始说话 → 当前 tool strip 封口
        self._close_tool_strip()
        if self._current_bubble is None:
            self._current_bubble = self._append_message("assistant", "", persist=False)
        elif self._current_bubble._thinking:
            pass  # append_text 会自己关 thinking
        self._current_bubble.append_text(chunk)
        self._current_text += chunk
        self._scroll_to_bottom()

    def _handle_event(self, line: str):
        try: evt = json.loads(line)
        except Exception: return

        if not self._captured_sid:
            sid = evt.get("session_id")
            if sid: self._captured_sid = sid

        t = evt.get("type")

        # --include-partial-messages 给的细粒度事件
        if t == "stream_event":
            inner = evt.get("event") or {}
            et = inner.get("type")
            if et == "content_block_delta":
                delta = inner.get("delta") or {}
                dtype = delta.get("type")
                if dtype == "text_delta":
                    self._emit_text(delta.get("text", ""))
                elif dtype == "input_json_delta":
                    # 工具入参分片累加
                    idx = inner.get("index")
                    block = self._pending_tool_index.get(idx)
                    if block is not None:
                        block.update_input(
                            block._input_text + (delta.get("partial_json") or "")
                        )
            elif et == "content_block_start":
                cb = inner.get("content_block") or {}
                if cb.get("type") == "tool_use":
                    block = self._append_tool_block(
                        cb.get("name", "?"), cb.get("id")
                    )
                    idx = inner.get("index")
                    if idx is not None:
                        self._pending_tool_index[idx] = block
                    # 如果 start 就给了 input 直接显示
                    if cb.get("input"):
                        try:
                            block.update_input(json.dumps(
                                cb["input"], ensure_ascii=False, indent=2
                            ))
                        except Exception: pass
                    # monitor: 工具调用开始 (chip 已经显示)
                    self.bus.dispatch(EVT_TOOL_USE_START, {
                        "name": cb.get("name", "?"),
                        "tool_use_id": cb.get("id"),
                    })
            elif et == "content_block_stop":
                # 工具入参累加完, 尝试 pretty-print
                idx = inner.get("index")
                block = self._pending_tool_index.pop(idx, None)
                if block is not None:
                    parsed_input: dict = {}
                    if block._input_text:
                        try:
                            parsed_input = json.loads(block._input_text)
                            block.update_input(json.dumps(
                                parsed_input, ensure_ascii=False, indent=2
                            ))
                        except Exception: pass
                    # monitor: input 累积完成 (A 危险命令哨兵用)
                    self.bus.dispatch(EVT_TOOL_USE_INPUT_READY, {
                        "name": block.name,
                        "tool_use_id": block.tool_use_id,
                        "input": parsed_input,
                    })
            return

        # 完整 assistant 事件 (fallback)
        if t == "assistant":
            if self._current_text: return
            msg = evt.get("message") or {}
            for c in (msg.get("content") or []):
                ct = c.get("type")
                if ct == "text":
                    self._emit_text(c.get("text", ""))
                elif ct == "tool_use":
                    block = self._append_tool_block(
                        c.get("name", "?"), c.get("id")
                    )
                    inp = c.get("input")
                    if inp:
                        try:
                            block.update_input(json.dumps(
                                inp, ensure_ascii=False, indent=2
                            ))
                        except Exception: pass
        elif t == "user":
            # user 消息里包含 tool_result
            msg = evt.get("message") or {}
            for c in (msg.get("content") or []):
                if c.get("type") == "tool_result":
                    tid = c.get("tool_use_id")
                    block = self._tool_blocks.get(tid)
                    if block is None: continue
                    content = c.get("content")
                    result_text = ""
                    if isinstance(content, str):
                        result_text = content
                        block.set_result(content)
                    elif isinstance(content, list):
                        parts = []
                        for ci in content:
                            if isinstance(ci, dict) and ci.get("type") == "text":
                                parts.append(ci.get("text", ""))
                        result_text = "\n".join(parts)
                        block.set_result(result_text)
                    # monitor: 工具结果 (E 报错气压计用)
                    self.bus.dispatch(EVT_TOOL_RESULT, {
                        "tool_use_id": tid,
                        "name": block.name,
                        "content": result_text,
                        "is_error": bool(c.get("is_error")),
                    })
        elif t == "result":
            res = evt.get("result")
            if isinstance(res, str) and res and not self._current_text:
                self._emit_text(res)

    def _on_finished(self, exit_code, _exit_status):
        if self._stdout_buf:
            for line in self._stdout_buf.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if line: self._handle_event(line)
            self._stdout_buf = b""

        if self._captured_sid and not self._load_session():
            self._save_session(self._captured_sid)

        _log(f"finished: exit={exit_code} text_len={len(self._current_text)} "
             f"stderr_len={len(self._stderr_buf)}")

        if not self._current_text:
            err = self._stderr_buf.decode("utf-8", errors="replace").strip()
            msg = err or f"(claude 退出码 {exit_code},无输出)"
            self._emit_text(f"⚠️ {msg}")

        # 兜底关 thinking (理论上 append_text 已经关过)
        if self._current_bubble is not None:
            self._current_bubble.set_thinking(False)
            # 流式结束: 切到 markdown 渲染 (一次性, 不再每 chunk 重做)
            self._current_bubble.finalize()

        self._append_history("assistant", self._current_text)
        # monitor: 本轮结束 (F 起名 / K 上下文胖了 用)
        self.bus.dispatch(EVT_ASSISTANT_DONE, {
            "full_text": self._current_text,
            "tool_count": len(self._tool_blocks),
            "char_count": len(self._current_text),
            "session_id": self._captured_sid or self._load_session(),
        })
        self._current_bubble = None
        self._current_text = ""
        self._proc = None
        self._set_busy(False)

    def _on_error(self, error):
        if self._proc is None: return
        _log(f"errorOccurred: {error}")
        self._emit_text(f"⚠️ 启动 claude 失败 ({error})")
        try: self._proc.kill()
        except Exception: pass
        self._proc = None
        self._set_busy(False)

    # ---------------- monitor 反应 API ----------------
    def show_warning(self, text: str, color: str = "#ff8800",
                     ttl_ms: int = 8000):
        """顶部弹横条警告. 同时存在最多 3 条, 超出先 dismiss 最早的"""
        existing = self.findChildren(WarningBar)
        while len(existing) >= 3:
            try: existing[0]._dismiss()
            except Exception: pass
            existing = existing[1:]
        bar = WarningBar(text, color, ttl_ms, parent=self)
        root = self.layout()
        if isinstance(root, QVBoxLayout):
            # 插到 header (index 0) 之后
            root.insertWidget(1, bar)
        return bar

    def show_toast(self, text: str, anchor: QWidget | None = None,
                   ttl_ms: int = 4000):
        """浮动小气泡. anchor 给了就挂它上方, 否则居中靠下"""
        toast = Toast(text, parent=self, ttl_ms=ttl_ms)
        if anchor is not None:
            try:
                top_left = anchor.mapToGlobal(QPoint(0, 0))
                local = self.mapFromGlobal(top_left)
                x = max(8, min(local.x(),
                               self.width() - toast.width() - 8))
                y = max(8, local.y() - toast.height() - 4)
                toast.move(x, y)
            except Exception:
                toast.move(8, 8)
        else:
            x = max(8, (self.width() - toast.width()) // 2)
            y = max(8, self.height() - toast.height() - 100)
            toast.move(x, y)
        toast.raise_()
        return toast

    def set_status(self, text: str):
        """状态栏文字. 注意 _set_busy 也会动这个, 监听器自己掌握时机"""
        if hasattr(self, "status_label"):
            self.status_label.setText(text)

    def set_panel_tone(self, name: str):
        """切换面板配色 preset. name: normal / night / worried"""
        c = self._colors
        presets = {
            "normal": (
                f"ChatPanel {{ background: {c['panel_bg']}; "
                f"border: 1px solid {c['border']}; border-radius: 8px; }}"
            ),
            "night": (
                "ChatPanel { background: rgba(40, 40, 60, 215); "
                "border: 1px solid #7e5bb8; border-radius: 8px; }"
            ),
            "worried": (
                f"ChatPanel {{ background: {c['panel_bg']}; "
                f"border: 2px solid #ff8800; border-radius: 8px; }}"
            ),
        }
        style = presets.get(name, presets["normal"])
        self.setStyleSheet(style)
        self._current_tone = name

    def set_chip_emphasis(self, tool_use_id: str, level: str):
        """让某工具 chip 强调显示. level: normal / active / danger"""
        chip = self._tool_blocks.get(tool_use_id)
        if chip is not None:
            chip.set_emphasis(level)

    def set_session_title(self, text: str):
        """F monitor 自动起名 — 已禁用 (会覆盖 mode 显示, 视觉抖动).
        meta 里仍按原逻辑写, 这里只是不再动 title_label"""
        return

    def flash_border(self, color: str = "#ffd700", ms: int = 1500):
        """边框闪一下. ms 后恢复当前 tone"""
        original_tone = getattr(self, "_current_tone", "normal")
        c = self._colors
        self.setStyleSheet(
            f"ChatPanel {{ background: {c['panel_bg']}; "
            f"border: 2px solid {color}; border-radius: 8px; }}"
        )
        QTimer.singleShot(ms, lambda: self.set_panel_tone(original_tone))

    def inject_system_msg(self, text: str):
        """monitor 主动插系统通知"""
        self._append_system(text, persist=False)

    def closeEvent(self, event):
        if self._proc is not None:
            try: self._proc.kill()
            except Exception: pass
            self._proc = None
        super().closeEvent(event)



# ==================== Task 6: New ChatWindow (sidebar + stack) ====================

class ChatWindow(QWidget):
    """Top-level chat window with left sidebar + right stack of ConversationPanels.

    Owns ConversationStore + PermissionRouter. Each conversation entry gets one
    ClaudeWorker + one ConversationPanel pair, kept alive in QStackedWidget
    (switching is cheap; background workers keep running).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("和泡沫聊")
        self.resize(900, 620)
        self.setMinimumSize(720, 480)

        # Lazy imports to avoid circular issues
        from conversation_store import ConversationStore
        from permission_router import PermissionRouter
        from sidebar import Sidebar, AddProjectDialog
        from chat_panel import ConversationPanel
        from claude_worker import ClaudeWorker
        from PyQt6.QtWidgets import QStackedWidget

        self._cls_AddProjectDialog = AddProjectDialog
        self._cls_ConversationPanel = ConversationPanel
        self._cls_ClaudeWorker = ClaudeWorker

        self.store = ConversationStore(STATE_DIR)
        self.router = PermissionRouter(self)
        self.router.permission_requested.connect(self._on_permission_request)

        # foamo icon
        icon_path = ROOT / "foamo.ico"
        self.foamo_icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        self.sidebar = Sidebar(self.store, self.foamo_icon, self)
        self.sidebar.setFixedWidth(240)
        self.sidebar.card_clicked.connect(self._switch_to)
        self.sidebar.add_project_requested.connect(self._show_add_dialog)
        self.sidebar.edit_project_requested.connect(self._show_edit_dialog)
        # NOTE: Sidebar._confirm_delete already calls store.delete_project with
        # the correct purge_history flag. ChatWindow listens to entry_removed
        # instead — connecting delete_project_requested too would double-delete
        # and override the user's "purge history" choice.
        h.addWidget(self.sidebar)

        # 1px vertical separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("background: rgba(0,0,0,0.06); max-width: 1px;")
        h.addWidget(sep)

        # Stack
        self.stack = QStackedWidget()
        h.addWidget(self.stack, 1)

        self._panels: dict[str, "ConversationPanel"] = {}
        self._current_key: str = "chat"
        self._pending_perm: dict[str, tuple[dict, object]] = {}  # key -> (payload, responder)

        # Build panels — stagger by 100ms to ease cold-start CPU
        entries = self.store.list_entries()
        if len(entries) > 5:
            _log(f"[window] {len(entries)} conversations registered — cold start may be slow")
        for i, entry in enumerate(entries):
            QTimer.singleShot(i * 100, lambda k=entry.key: self._ensure_panel(k))

        self.store.entry_added.connect(self._on_entry_added)
        self.store.entry_removed.connect(self._on_entry_removed)

    # ---------- panel lifecycle ----------

    def _ensure_panel(self, key: str):
        if key in self._panels:
            return
        entry = self.store.get(key)
        if entry is None:
            return
        cwd = entry.path or DEFAULT_CWD
        conv_dir = self.store.conv_dir / key
        conv_dir.mkdir(parents=True, exist_ok=True)
        worker = self._cls_ClaudeWorker(key, conv_dir, CLAUDE_BIN, cwd, parent=self)
        panel = self._cls_ConversationPanel(entry, self.store, worker, parent=self)
        self._panels[key] = panel
        self.stack.addWidget(panel)
        # If this is the first panel added (chat), make it current and update sidebar
        if self.stack.count() == 1:
            self.stack.setCurrentWidget(panel)
            self._current_key = key
            self.sidebar.set_current(key)
        elif key == self._current_key:
            self.stack.setCurrentWidget(panel)

    def _on_entry_added(self, key: str):
        self._ensure_panel(key)
        # Auto-switch to newly added project
        self._switch_to(key)

    def _on_entry_removed(self, key: str):
        panel = self._panels.pop(key, None)
        # Free any orphaned pending permission request so the hook doesn't
        # hang for 60s (responder never called).
        pending = self._pending_perm.pop(key, None)
        if pending is not None:
            try:
                pending[1]("deny")
            except Exception:
                pass
        if panel is None:
            return
        try:
            panel.worker.stop()
        except Exception:
            pass
        self.stack.removeWidget(panel)
        panel.deleteLater()
        if self._current_key == key:
            self._switch_to("chat")

    def _switch_to(self, key: str):
        panel = self._panels.get(key)
        if panel is None:
            return
        self.stack.setCurrentWidget(panel)
        self.sidebar.set_current(key)
        self._current_key = key
        # Clear unread badge on activation
        entry = self.store.get(key)
        if entry is not None and entry.badge == "unread":
            self.store.set_badge(key, "none")
        # Drain pending permission request for this key
        pending = self._pending_perm.pop(key, None)
        if pending is not None:
            payload, responder = pending
            self._show_perm_dialog(key, payload, responder)

    # ---------- add / edit / delete ----------

    def _show_add_dialog(self):
        dlg = self._cls_AddProjectDialog(self.store, self.foamo_icon, parent=self)
        dlg.exec()

    def _show_edit_dialog(self, key: str):
        entry = self.store.get(key)
        if entry is None:
            return
        dlg = self._cls_AddProjectDialog(self.store, self.foamo_icon,
                                         editing=entry, parent=self)
        dlg.exec()

    # ---------- permission routing ----------

    def _on_permission_request(self, conv_key: str, payload: dict, responder):
        self.store.set_badge(conv_key, "permission")
        panel = self._panels.get(conv_key)
        if panel is None:
            # Conversation gone — deny immediately to free the hook
            try:
                responder("deny")
            except Exception:
                pass
            return
        if self.stack.currentWidget() is panel:
            self._show_perm_dialog(conv_key, payload, responder)
        else:
            # Stash; will pop dialog when user switches to this card
            self._pending_perm[conv_key] = (payload, responder)

    def _show_perm_dialog(self, conv_key: str, payload: dict, responder):
        # Reuse the existing standalone dialog from permission_dialog.py
        try:
            from permission_dialog import show_dialog
        except Exception:
            responder("deny")
            return
        tool_name = payload.get("tool_name", "?")
        tool_input = payload.get("tool_input", {}) or {}
        cwd = payload.get("cwd", "") or ""
        # Note: show_dialog creates its own QDialog, modal blocks until done.
        try:
            allowed = show_dialog(tool_name, tool_input, cwd)
        except Exception as e:
            _log(f"[window] perm dialog failed: {e}")
            responder("deny")
            self.store.set_badge(conv_key, "none")
            return
        responder("allow" if allowed else "deny")
        # Clear badge after decision
        entry = self.store.get(conv_key)
        if entry is not None and entry.badge == "permission":
            self.store.set_badge(conv_key, "none")

    # ---------- public helpers ----------

    def toggle(self):
        """Show / hide. Convenience for FoamoWidget.open_chat to call."""
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()


# ---------------- 单跑测试 ----------------
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    host = QWidget()
    host.setWindowTitle("[假桌宠] 拖我看面板会不会跟着")
    host.setStyleSheet("background: #1f1f3a; color: white;")
    host.resize(280, 280)
    screen = QGuiApplication.primaryScreen().availableGeometry()
    host.move(screen.right() - 280 - 30, screen.bottom() - 280 - 50)
    host.show()
    panel = ChatPanel()
    panel.attach_to(host)
    panel.toggle()
    sys.exit(app.exec())
