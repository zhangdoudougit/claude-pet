"""ConversationPanel — single conversation view used inside ChatWindow's stack.

UI-only: NO window chrome, NO subprocess. Subprocess is owned by the
injected ClaudeWorker. Loads history.json on construction, persists per
assistant turn.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QFrame,
)

from conversation_store import ConversationStore, ConversationEntry
from claude_worker import ClaudeWorker
from pet_avatar import PetAvatar
from status_pill import StatusPill

# Reuse helpers from existing chat_window — DO NOT redefine them here
from chat_window import (
    Bubble, ChatInput, ToolChip, ToolStrip, MessageRow,
    SystemNotice, load_permission_mode, HOOK_SETTINGS_FILE,
    _resolve_proxy,
)


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

        self.header_avatar = PetAvatar(size=32, mood="idle")
        hlay.addWidget(self.header_avatar)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.setContentsMargins(0, 0, 0, 0)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel(self.entry.name if self.entry else "泡沫")
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

        # Input row
        ibar = QHBoxLayout()
        ibar.setContentsMargins(8, 4, 8, 8)
        ibar.setSpacing(6)

        self.input = ChatInput()
        self.input.setFixedHeight(72)
        ibar.addWidget(self.input, 1)

        self.send_btn = QPushButton("发送")
        self.send_btn.clicked.connect(self._on_send_clicked)
        ibar.addWidget(self.send_btn)

        v.addLayout(ibar)

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
