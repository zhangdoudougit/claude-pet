"""ConversationPanel — single conversation view used inside ChatWindow's stack.

UI-only: NO window chrome, NO subprocess. Subprocess is owned by the
injected ClaudeWorker. Loads history.json on construction, persists per
assistant turn.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QSize
from PyQt6.QtGui import QFont, QPainter, QPainterPath, QIcon, QPen, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QFrame,
)

from conversation_store import ConversationStore, ConversationEntry
from claude_worker import ClaudeWorker
from status_pill import StatusPill


# ---------------------------------------------------------------------------
# ChatAvatar — 圆形头像 widget
# 闲聊 (chat) 用 foamo.ico (小丸子); 项目用 ProjectBadge.
# 用户 feedback: 闲聊永远是小丸子, 不用 PetAvatar 替代.
# 保留 set_mood / set_dark 这两个 API 是为了不破坏现有 caller, 实际 no-op.
# ---------------------------------------------------------------------------

class ChatAvatar(QWidget):
    """单图头像 widget. 闲聊渲染圆形 foamo.ico; 项目渲染圆角方块 + 简码."""

    def __init__(self, entry: ConversationEntry, foamo_icon: QIcon,
                 size: int = 32, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.foamo_icon = foamo_icon
        self.setFixedSize(size, size)
        self._size = size

    # API compat with old PetAvatar — silent no-op
    def set_mood(self, _mood: str):
        return

    def set_dark(self, _dark: bool):
        return

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self._size
        rect = QRectF(0, 0, s, s)
        if self.entry is not None and self.entry.kind == "project":
            from PyQt6.QtGui import QColor
            p.setBrush(QColor(self.entry.color or "#7C8290"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(rect, 10, 10)
            f = QFont()
            f.setPointSize(max(9, int(s * 0.35)))
            f.setWeight(QFont.Weight.DemiBold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.3)
            p.setFont(f)
            p.setPen(QColor(255, 255, 255, 242))
            code = (self.entry.short_code or "")[:4]
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, code)
            return
        # chat: 圆形 foamo.ico
        path = QPainterPath()
        path.addEllipse(rect)
        p.setClipPath(path)
        if self.foamo_icon and not self.foamo_icon.isNull():
            pm = self.foamo_icon.pixmap(QSize(s, s))
            p.drawPixmap(rect.toRect(), pm)
        else:
            from PyQt6.QtGui import QColor
            p.fillRect(rect, QColor("#E07A5F"))

# Reuse helpers from existing chat_window — DO NOT redefine them here
from chat_window import (
    Bubble, ChatInput, ToolChip, ToolStrip, MessageRow,
    SystemNotice, load_permission_mode, HOOK_SETTINGS_FILE,
    _resolve_proxy,
)


# ---------------------------------------------------------------------------
# Composer icon helpers (paperclip / @ / </> / send-arrow)
# 自绘 SVG 风 icon 作 QPushButton 的 QIcon, 替代 emoji
# ---------------------------------------------------------------------------

def _make_paperclip_icon(color: str = "#6b6457", size: int = 15) -> QIcon:
    from PyQt6.QtGui import QPixmap
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # 简化 paperclip: 斜向椭圆 + 内圈
    path = QPainterPath()
    path.moveTo(11.5, 3.5)
    path.lineTo(5.0, 10.0)
    path.cubicTo(3.0, 12.0, 3.0, 13.5, 4.5, 13.5)
    path.cubicTo(6.0, 13.5, 6.0, 12.5, 7.5, 11.0)
    path.lineTo(12.0, 6.5)
    p.drawPath(path)
    p.end()
    return QIcon(pm)


def _make_at_icon(color: str = "#6b6457", size: int = 15) -> QIcon:
    from PyQt6.QtGui import QPixmap
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    cx, cy = size / 2.0, size / 2.0
    # 内圆 + 外弧
    p.drawEllipse(QRectF(cx - 2.5, cy - 2.5, 5.0, 5.0))
    p.drawArc(QRectF(1.5, 1.5, size - 3.0, size - 3.0), 0, 270 * 16)
    p.end()
    return QIcon(pm)


def _make_code_icon(color: str = "#6b6457", size: int = 15) -> QIcon:
    from PyQt6.QtGui import QPixmap
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # < 和 >
    path = QPainterPath()
    path.moveTo(5.5, 4.5)
    path.lineTo(2.0, 7.5)
    path.lineTo(5.5, 10.5)
    p.drawPath(path)
    path2 = QPainterPath()
    path2.moveTo(9.5, 4.5)
    path2.lineTo(13.0, 7.5)
    path2.lineTo(9.5, 10.5)
    p.drawPath(path2)
    p.end()
    return QIcon(pm)


def _make_send_arrow_icon(color: str = "#ffffff", size: int = 14) -> QIcon:
    """向上箭头, design line 425: M12 19V5 M5 12l7-7 7 7"""
    from PyQt6.QtGui import QPixmap
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    cx = size / 2.0
    # 垂直竖线
    p.drawLine(int(cx), int(size * 0.20), int(cx), int(size * 0.82))
    # 箭头 V
    p.drawLine(int(size * 0.22), int(size * 0.50), int(cx), int(size * 0.20))
    p.drawLine(int(size * 0.78), int(size * 0.50), int(cx), int(size * 0.20))
    p.end()
    return QIcon(pm)


class ConversationPanel(QWidget):
    user_sent = pyqtSignal(str)
    streaming_started = pyqtSignal()
    streaming_finished = pyqtSignal()

    def __init__(
        self,
        entry: ConversationEntry,
        store: ConversationStore,
        worker: ClaudeWorker,
        parent=None,
    ):
        super().__init__(parent)
        self.entry = entry
        self.store = store
        self.worker = worker

        self._loaded_history_count: int = 0
        self._current_bubble: Optional[Bubble] = None
        self._current_text: str = ""
        self._tool_strip: Optional[ToolStrip] = None
        self._tool_chips: dict[str, ToolChip] = {}
        self._tool_index_to_chip: dict[int, ToolChip] = {}

        # foamo.ico 头像 (chat 类型用)
        from pathlib import Path as _Path
        _icon_path = _Path(__file__).parent / "foamo.ico"
        self._foamo_icon = QIcon(str(_icon_path)) if _icon_path.exists() else QIcon()

        self._build_ui()
        self._wire_worker()
        self._load_history()

    # ------------------------------------------------------------------ UI --

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Header — 64px design with avatar + mood + status pill
        hbar = QFrame()
        hbar.setFixedHeight(64)
        hbar.setObjectName("chat_header")
        hlay = QHBoxLayout(hbar)
        hlay.setContentsMargins(22, 14, 22, 14)
        hlay.setSpacing(12)

        self.header_avatar = ChatAvatar(self.entry, self._foamo_icon, size=32)
        hlay.addWidget(self.header_avatar)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.setContentsMargins(0, 0, 0, 0)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.setContentsMargins(0, 0, 0, 0)
        # design: chat 模式标题固定 "泡沫", project 模式用项目名
        if self.entry and self.entry.kind == "chat":
            _title_txt = "泡沫"
        else:
            _title_txt = self.entry.name if self.entry else "泡沫"
        self.title_label = QLabel(_title_txt)
        tf = self.title_label.font()
        tf.setPointSize(11)
        tf.setWeight(QFont.Weight.DemiBold)
        self.title_label.setFont(tf)
        title_row.addWidget(self.title_label)

        self.subtitle_label = QLabel("· 桌面伙伴")
        sf = self.subtitle_label.font()
        sf.setPointSize(8)
        self.subtitle_label.setFont(sf)
        self.subtitle_label.setStyleSheet("color: #9a9387;")
        title_row.addWidget(self.subtitle_label)
        title_row.addStretch(1)
        title_col.addLayout(title_row)

        self.mood_line = QLabel("正在和你聊点设计 · 心情 松弛")
        mf = self.mood_line.font()
        mf.setPointSize(8)
        self.mood_line.setFont(mf)
        self.mood_line.setStyleSheet("color: #6b6457;")
        title_col.addWidget(self.mood_line)

        hlay.addLayout(title_col, 1)

        self.status_pill = StatusPill(state="idle")
        hlay.addWidget(self.status_pill)

        # legacy status_label kept invisible for backwards-compat
        self.status_label = QLabel("")
        self.status_label.setVisible(False)
        hlay.addWidget(self.status_label)

        v.addWidget(hbar)

        # Bubble scroll area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.bubble_container = QWidget()
        self.bubble_layout = QVBoxLayout(self.bubble_container)
        self.bubble_layout.setContentsMargins(8, 8, 8, 8)
        self.bubble_layout.setSpacing(8)
        # stretch pushes bubble rows to the bottom as they accumulate
        self.bubble_layout.addStretch(1)

        self.scroll.setWidget(self.bubble_container)
        v.addWidget(self.scroll, 1)

        # Composer (Task VR7): rounded card + toolbar
        composer_card = QFrame()
        composer_card.setObjectName("composer_card")
        composer_card.setStyleSheet(
            "QFrame#composer_card { background: #fff; "
            "border: 1px solid #e8e3d6; border-radius: 12px; }"
        )
        cv = QVBoxLayout(composer_card)
        cv.setContentsMargins(10, 8, 10, 6)
        cv.setSpacing(6)

        self.input = ChatInput()
        self.input.setFixedHeight(44)
        self.input.setPlaceholderText("和泡沫说点什么…")
        self.input.setStyleSheet(
            "QPlainTextEdit { background: transparent; border: none; "
            "font-size: 11pt; color: #1d1b16; padding: 0; }"
        )
        cv.addWidget(self.input)

        # Toolbar separator (dashed line)
        sep_line = QFrame()
        sep_line.setFixedHeight(1)
        sep_line.setStyleSheet(
            "QFrame { background: transparent; border-top: 1px dashed #f0ebdd; }"
        )
        cv.addWidget(sep_line)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        toolbar.setContentsMargins(0, 4, 0, 0)

        def _tool_btn(icon: QIcon, tooltip: str) -> QPushButton:
            b = QPushButton()
            b.setIcon(icon)
            b.setIconSize(QSize(15, 15))
            b.setFixedSize(28, 24)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tooltip)
            b.setStyleSheet(
                "QPushButton { background: transparent; border: none; padding: 0; }"
                "QPushButton:hover { background: rgba(0,0,0,0.04); border-radius: 4px; }"
            )
            return b

        self.tool_attach_btn = _tool_btn(_make_paperclip_icon(), "附件 (尚未接入)")
        self.tool_mention_btn = _tool_btn(_make_at_icon(), "@ 提及 (尚未接入)")
        self.tool_code_btn = _tool_btn(_make_code_icon(), "代码块 (尚未接入)")
        toolbar.addWidget(self.tool_attach_btn)
        toolbar.addWidget(self.tool_mention_btn)
        toolbar.addWidget(self.tool_code_btn)
        toolbar.addStretch(1)

        # 快捷键 hint: ↵ / ⇧↵ 嵌米色 Code 块 (design line 419-420)
        self.kbd_hint = QLabel(
            "<span style='color:#9a9387;font-size:9pt;'>"
            "<span style='background:#efeadd;color:#5b4632;padding:1px 5px;"
            " border-radius:3px;font-family:Consolas,monospace;font-size:8pt;'>"
            "↵</span> 发送 · "
            "<span style='background:#efeadd;color:#5b4632;padding:1px 5px;"
            " border-radius:3px;font-family:Consolas,monospace;font-size:8pt;'>"
            "⇧↵</span> 换行</span>"
        )
        self.kbd_hint.setTextFormat(Qt.TextFormat.RichText)
        self.kbd_hint.setStyleSheet("margin-right: 8px; background: transparent;")
        toolbar.addWidget(self.kbd_hint)

        # 主色发送按钮: accent fill + 上箭头 icon + "发送"
        self.send_btn = QPushButton(" 发送")
        self.send_btn.setIcon(_make_send_arrow_icon("#ffffff", 13))
        self.send_btn.setIconSize(QSize(13, 13))
        self.send_btn.setFixedHeight(28)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setStyleSheet(
            "QPushButton { background: #7fb993; color: #fff; border: none; "
            "border-radius: 8px; padding: 0 12px 0 10px; font-size: 9.5pt; "
            "font-weight: 600; }"
            "QPushButton:hover { background: #6fa882; }"
            "QPushButton:disabled { background: #c9c2b2; }"
        )
        self.send_btn.clicked.connect(self._on_send_clicked)
        toolbar.addWidget(self.send_btn)

        cv.addLayout(toolbar)

        # Outer: padding around composer card
        outer_composer = QHBoxLayout()
        outer_composer.setContentsMargins(22, 12, 22, 16)
        outer_composer.addWidget(composer_card)
        v.addLayout(outer_composer)

    # --------------------------------------------------------- worker wiring --

    def _wire_worker(self):
        self.worker.text_chunk.connect(self._on_text_chunk)
        self.worker.tool_event.connect(self._on_tool_event)
        self.worker.error.connect(self._on_error)
        self.worker.finished.connect(self._on_finished)

    # ------------------------------------------------------------ history I/O --

    def _history_file(self) -> Path:
        return self.store.conv_dir / self.entry.key / "history.json"

    def _load_history(self):
        hf = self._history_file()
        if not hf.exists():
            return
        try:
            rows = json.loads(hf.read_text(encoding="utf-8"))
        except Exception:
            return
        for row in rows:
            role = row.get("role")
            text = row.get("text", "")
            if role in ("user", "assistant") and text:
                self._append_row(role, text, persist=False)
                self._loaded_history_count += 1

    def loaded_history_count(self) -> int:
        """Number of message rows rendered from history at construction time."""
        return self._loaded_history_count

    def _append_row(self, role: str, text: str, persist: bool = True) -> Bubble:
        """Create a Bubble + MessageRow and insert above the bottom stretch."""
        bubble = Bubble(role=role, text=text)
        row = MessageRow(role=role, content=bubble)
        # insert before the trailing stretch (always the last item)
        self.bubble_layout.insertWidget(self.bubble_layout.count() - 1, row)
        if persist:
            self._append_to_history(role, text)
        return bubble

    def _append_to_history(self, role: str, text: str):
        hf = self._history_file()
        hf.parent.mkdir(parents=True, exist_ok=True)
        rows: list = []
        if hf.exists():
            try:
                rows = json.loads(hf.read_text(encoding="utf-8"))
            except Exception:
                rows = []
        rows.append({"role": role, "text": text})
        hf.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------- send flow --

    # --------------------------------------------------------- theme application --

    def apply_theme(self, name: str):
        """Reapply palette-based stylesheets for current theme.

        name: "warm" or "glass".
        """
        if name == "glass":
            self._apply_glass_theme()
        else:
            self._apply_warm_theme()

    def _apply_warm_theme(self):
        # Window bg + body text color
        self.setStyleSheet(
            "ConversationPanel { background: #fafaf6; }"
        )
        # Subtitle + mood line
        if hasattr(self, "subtitle_label"):
            self.subtitle_label.setStyleSheet("color: #9a9387;")
        if hasattr(self, "mood_line"):
            self.mood_line.setStyleSheet("color: #6b6457;")
        # Send button (accent mint)
        if hasattr(self, "send_btn"):
            self.send_btn.setStyleSheet(
                "QPushButton { background: #7fb993; color: #fff; border: none; "
                "border-radius: 8px; padding: 0 14px; font-size: 9.5pt; "
                "font-weight: 600; }"
                "QPushButton:hover { background: #6fa882; }"
                "QPushButton:disabled { background: #c9c2b2; }"
            )
        # Composer card
        composer = self.findChild(QFrame, "composer_card")
        if composer is not None:
            composer.setStyleSheet(
                "QFrame#composer_card { background: #fff; "
                "border: 1px solid #e8e3d6; border-radius: 12px; }"
            )
        # Input
        if hasattr(self, "input"):
            self.input.setStyleSheet(
                "QPlainTextEdit { background: transparent; border: none; "
                "font-size: 11pt; color: #1d1b16; padding: 0; }"
            )
        # Tool buttons + kbd hint
        if hasattr(self, "kbd_hint"):
            self.kbd_hint.setStyleSheet("color: #9a9387; margin-right: 8px;")
        # Avatar dark mode off
        if hasattr(self, "header_avatar"):
            self.header_avatar.set_dark(False)
        # Title label
        if hasattr(self, "title_label"):
            self.title_label.setStyleSheet("color: #1d1b16;")
        # 气泡 (传 legacy THEMES["light"] 进每个 Bubble 重渲染)
        self._reapply_bubble_theme("light")

    def _reapply_bubble_theme(self, legacy_name: str):
        """让所有 Bubble 切 legacy theme name (light / dark) 来跟随 warm / glass."""
        from chat_window import THEMES
        colors = THEMES.get(legacy_name, THEMES["light"])
        for bubble in self.findChildren(Bubble):
            try:
                bubble.apply_theme(colors, legacy_name)
            except Exception:
                pass

    def _apply_glass_theme(self):
        self.setStyleSheet(
            "ConversationPanel { background: #1a1c20; }"
        )
        if hasattr(self, "subtitle_label"):
            self.subtitle_label.setStyleSheet("color: #6e6b62;")
        if hasattr(self, "mood_line"):
            self.mood_line.setStyleSheet("color: #a8a59b;")
        if hasattr(self, "send_btn"):
            self.send_btn.setStyleSheet(
                "QPushButton { background: #5ea8c9; color: #fff; border: none; "
                "border-radius: 8px; padding: 0 14px; font-size: 9.5pt; "
                "font-weight: 600; }"
                "QPushButton:hover { background: #4a8ea8; }"
                "QPushButton:disabled { background: rgba(255,255,255,0.18); }"
            )
        composer = self.findChild(QFrame, "composer_card")
        if composer is not None:
            composer.setStyleSheet(
                "QFrame#composer_card { background: rgba(255,255,255,0.04); "
                "border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; }"
            )
        if hasattr(self, "input"):
            self.input.setStyleSheet(
                "QPlainTextEdit { background: transparent; border: none; "
                "font-size: 11pt; color: #ecebe7; padding: 0; }"
            )
        if hasattr(self, "kbd_hint"):
            self.kbd_hint.setStyleSheet("color: #6e6b62; margin-right: 8px;")
        if hasattr(self, "header_avatar"):
            self.header_avatar.set_dark(True)
        if hasattr(self, "title_label"):
            self.title_label.setStyleSheet("color: #ecebe7;")
        # 气泡跟到 dark
        self._reapply_bubble_theme("dark")

    # ------------------------------------------------------------- send flow --

    def _on_send_clicked(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self.send_message(text)

    def send_message(self, text: str):
        # 1. User bubble
        self._append_row("user", text, persist=True)

        # 2. Reset per-turn tracking
        self._tool_strip = None
        self._tool_chips.clear()
        self._tool_index_to_chip.clear()
        self._current_text = ""

        # 3. Placeholder assistant bubble (thinking spinner)
        self._current_bubble = Bubble(role="assistant", text="")
        self._current_bubble.set_thinking(True)
        row = MessageRow(role="assistant", content=self._current_bubble)
        self.bubble_layout.insertWidget(self.bubble_layout.count() - 1, row)

        # 4. Store state
        self.store.touch(self.entry.key)
        self.store.set_badge(self.entry.key, "thinking")
        self.status_label.setText("· 思考中")
        self.status_pill.set_state("thinking")
        self.header_avatar.set_mood("talking")
        self.mood_line.setText("正在帮你想 · 心情 专注")

        # 5. Launch worker
        try:
            hook_settings = HOOK_SETTINGS_FILE if HOOK_SETTINGS_FILE.exists() else None
        except Exception:
            hook_settings = None

        # 注入代理 (国内连 Anthropic API 必须走代理, 不然 403).
        # 从 .chat_state/proxy 文件或 HTTPS_PROXY 环境变量读.
        env_extra = {}
        proxy = _resolve_proxy()
        if proxy:
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                env_extra[k] = proxy

        self.worker.send(
            prompt=text,
            perm_mode=load_permission_mode(),
            model=None,
            hook_settings=hook_settings,
            mcp_file=None,
            env_extra=env_extra,
        )

        self.user_sent.emit(text)
        self.streaming_started.emit()

    # ------------------------------------------------------- worker callbacks --

    def _on_text_chunk(self, chunk: str):
        if not chunk:
            return
        if self._current_bubble is None:
            # spontaneous text chunk (no preceding send_message call in test)
            self._current_bubble = Bubble(role="assistant", text="")
            row = MessageRow(role="assistant", content=self._current_bubble)
            self.bubble_layout.insertWidget(self.bubble_layout.count() - 1, row)

        self._current_bubble.append_text(chunk)
        self._current_text += chunk

    def _on_tool_event(self, evt: dict):
        kind = evt.get("kind")

        if kind == "use_start":
            name = evt.get("name", "?")
            tid = evt.get("tool_use_id", "")
            idx = evt.get("index", -1)
            chip = ToolChip(name=name, tool_use_id=tid)
            self._tool_chips[tid] = chip
            if idx >= 0:
                self._tool_index_to_chip[idx] = chip
            if self._tool_strip is None:
                self._tool_strip = ToolStrip()
                self.bubble_layout.insertWidget(
                    self.bubble_layout.count() - 1, self._tool_strip
                )
            self._tool_strip.add_chip(chip)

        elif kind == "input_chunk":
            idx = evt.get("index")
            chip = self._tool_index_to_chip.get(idx)
            if chip is not None:
                current = chip._input_text or ""
                chip.update_input(current + evt.get("partial_json", ""))

        elif kind == "input_ready":
            tid = evt.get("tool_use_id", "")
            chip = self._tool_chips.get(tid)
            if chip is not None:
                try:
                    pretty = json.dumps(
                        evt.get("input", {}), ensure_ascii=False, indent=2
                    )
                    chip.update_input(pretty)
                except Exception:
                    pass

        elif kind == "result":
            tid = evt.get("tool_use_id", "")
            chip = self._tool_chips.get(tid)
            if chip is not None:
                chip.set_result(evt.get("content", ""))

        elif kind == "assistant_done":
            self._finalize_turn()

    def _finalize_turn(self):
        # Persist assistant text
        if self._current_text:
            self._append_to_history("assistant", self._current_text)

        # Switch bubble from streaming PlainText to RichText markdown
        if self._current_bubble is not None:
            if hasattr(self._current_bubble, "finalize"):
                try:
                    self._current_bubble.finalize()
                except Exception:
                    pass

        # Badge: active window → clear; background → bump unread
        if self._is_active_in_window():
            self.store.set_badge(self.entry.key, "none")
        else:
            self.store.bump_unread(self.entry.key)

        self.store.touch(self.entry.key)
        self.status_label.setText("· 待机")
        self.status_pill.set_state("idle")
        self.header_avatar.set_mood("idle")
        self.mood_line.setText("正在和你聊点设计 · 心情 松弛")

        # Reset per-turn state (chip dicts too, so a crash-path call to
        # _finalize_turn doesn't carry stale tool state into the next turn)
        self._tool_strip = None
        self._tool_chips.clear()
        self._tool_index_to_chip.clear()
        self._current_bubble = None
        self._current_text = ""

        self.streaming_finished.emit()

    def _is_active_in_window(self) -> bool:
        win = self.window()
        if win is None:
            return False
        return self.isVisible() and win.isActiveWindow()

    def _on_error(self, msg: str):
        notice = SystemNotice(text=f"⚠️ {msg}")
        self.bubble_layout.insertWidget(self.bubble_layout.count() - 1, notice)
        self.store.set_badge(self.entry.key, "none")
        self.status_label.setText("· 错误")
        self.status_pill.set_state("idle")
        self.header_avatar.set_mood("idle")
        self.mood_line.setText("刚刚踩到点意外 · 心情 心虚")
        # Stop the thinking spinner before dropping the reference (the Bubble
        # owns a QTimer that keeps ticking if we just null the ref).
        if self._current_bubble is not None and hasattr(self._current_bubble, "set_thinking"):
            try:
                self._current_bubble.set_thinking(False)
            except Exception:
                pass
        # Clear per-turn state so a late stale chunk doesn't latch onto an
        # orphaned bubble.
        self._tool_strip = None
        self._tool_chips.clear()
        self._tool_index_to_chip.clear()
        self._current_bubble = None
        self._current_text = ""

    def _on_finished(self, exit_code: int):
        # assistant_done event already called _finalize_turn; only act if it
        # didn't (e.g. process crashed before emitting assistant_done).
        if self._current_bubble is not None:
            self._finalize_turn()
