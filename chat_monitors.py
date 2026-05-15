"""
chat_monitors.py - 聊天面板的监听系统

8 个事件 + 9 个反应通道 + 11 个 monitor.
设计文档: D:\\Notes\\ObsidianVault\\foamo_pet\\07-规格与计划\\2026-05-13-聊天面板监听系统.md

ChatPanel 在关键点调 bus.dispatch(kind, data), 各 monitor 决定是否反应.
反应通过 panel 暴露的 API (show_warning/show_toast/set_panel_tone/...) 完成.
"""

from __future__ import annotations

import datetime
import difflib
import json
import random
import re
import time
import traceback
from collections import deque
from typing import Any


# ============================================================
# 事件类型 (8 个)
# ============================================================
EVT_TEXT = "text"
EVT_TOOL_USE_START = "tool_use_start"
EVT_TOOL_USE_INPUT_READY = "tool_use_input_ready"
EVT_TOOL_RESULT = "tool_result"
EVT_ASSISTANT_DONE = "assistant_done"
EVT_USER_SEND = "user_send"
EVT_TICK = "tick"
EVT_MODE_SWITCH = "mode_switch"


# ============================================================
# 基类 + 总线
# ============================================================
class BaseMonitor:
    """所有 monitor 的基类. 子类实现 on_event"""

    name: str = "base"

    def __init__(self, panel):
        # ChatPanel 实例, monitor 通过它调反应 API
        self.panel = panel
        self.enabled: bool = True

    def on_event(self, kind: str, data: dict) -> bool | None:
        """
        处理事件.
        返回 True 表示已消费 (用于 G 暗号拦截 user_send 不发给 Claude).
        其他情况返回 None / False.
        """
        return None


class MonitorBus:
    """同步事件总线. dispatch 必须 microseconds 级, 不能拖慢面板渲染"""

    def __init__(self):
        self.monitors: list[BaseMonitor] = []
        self.shared: dict[str, Any] = {}

    def register(self, monitor: BaseMonitor) -> None:
        self.monitors.append(monitor)

    def dispatch(self, kind: str, data: dict | None = None) -> bool:
        """
        分发事件给所有启用的 monitor.
        返回 True 表示有 monitor 消费了事件 (调用方可据此跳过后续动作).

        语义: monitor 返回 True 后立即截断 — 后续 monitor 不再处理本事件.
        (例: G 暗号消费 "/晚安", I 项目首聊不应该再把它当普通消息欢迎一遍)
        """
        d = dict(data) if data else {}
        d.setdefault("_ts", time.time())
        for m in self.monitors:
            if not m.enabled:
                continue
            try:
                if m.on_event(kind, d):
                    return True
            except Exception as e:
                self._log_error(m, kind, e)
        return False

    def _log_error(self, m: BaseMonitor, kind: str, e: Exception) -> None:
        try:
            from chat_window import _log
            _log(f"[monitor:{m.name}] on {kind}: {e}\n{traceback.format_exc()}")
        except Exception:
            pass


# ============================================================
# 工具: meta.json 读写 (F / I 用)
# ============================================================
def _meta_path(panel, key: str | None = None):
    """conv/<key>/meta.json 路径"""
    from chat_window import CONV_DIR
    k = key or panel._key()
    return CONV_DIR / k / "meta.json"


def _meta_read(panel, key: str | None = None) -> dict:
    p = _meta_path(panel, key)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _meta_write_field(panel, field: str, value: Any, key: str | None = None) -> None:
    p = _meta_path(panel, key)
    d = _meta_read(panel, key)
    d[field] = value
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    except Exception:
        pass


# ============================================================
# G 隐藏暗号 (前置拦截 user_send)
# ============================================================
class HiddenCmdMonitor(BaseMonitor):
    name = "G_hidden_cmd"

    PRAISE_LINES = [
        "豆哥今天真厉害, 本泡沫这点小忙不算什么~",
        "嗯——确实最近代码写得不错, 继续保持哦",
        "...好啦好啦, 豆哥最棒了, 知道了吧",
        "...本泡沫不夸. 但确实, 不错.",
    ]

    def on_event(self, kind, data):
        if kind != EVT_USER_SEND:
            return None
        text = data.get("text", "").strip()
        if not text.startswith("/"):
            return None
        if text == "/晚安":
            self.panel.pet_request.emit("tender", "晚安豆哥")
            self.panel.flash_border("#b39ddb", 1500)
            self.panel.inject_system_msg("— /晚安 — 早点睡, 豆哥 —")
            return True
        if text == "/夸我":
            line = random.choice(self.PRAISE_LINES)
            self.panel._append_message("assistant", line)
            self.panel.pet_request.emit("proud", "...哼")
            return True
        if text == "/累了":
            self.panel.set_panel_tone("night")
            self.panel.pet_request.emit("tender", "歇会儿")
            self.panel.inject_system_msg("— /累了 — 切到温柔态, 30 分钟后回 —")
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(
                30 * 60 * 1000,
                lambda: self.panel.set_panel_tone("normal"),
            )
            return True
        return None


# ============================================================
# I 项目首聊
# ============================================================
class FirstChatMonitor(BaseMonitor):
    name = "I_first_chat"
    META_FIELD = "first_chat_ts"

    def __init__(self, panel):
        super().__init__(panel)

    def on_event(self, kind, data):
        if kind != EVT_USER_SEND:
            return None
        key = self.panel._key()
        # 闲聊不欢迎, 只欢迎项目模式
        if key == "chat":
            return None
        meta = _meta_read(self.panel, key)
        if meta.get(self.META_FIELD):
            return None
        _meta_write_field(self.panel, self.META_FIELD, int(time.time()), key)
        name = getattr(self.panel, "_mode_name", "这个地盘")
        self.panel.inject_system_msg(f"— 第一次在 {name} 聊呢 —")
        self.panel.pet_request.emit("happy", "新地盘!")
        return None


# ============================================================
# J 冷场监测
# ============================================================
class IdleMonitor(BaseMonitor):
    name = "J_idle"
    MARKS = [
        (5 * 60, "在的, 豆哥不急"),
        (10 * 60, "...还在想?"),
        (20 * 60, "豆哥不会跑了吧"),
    ]

    def __init__(self, panel):
        super().__init__(panel)
        self._last_activity: float = time.time()
        self._fired: set[int] = set()

    def on_event(self, kind, data):
        now = data.get("_ts", time.time())
        if kind in (EVT_USER_SEND, EVT_ASSISTANT_DONE):
            self._last_activity = now
            self._fired.clear()
            return None
        if kind != EVT_TICK:
            return None
        try:
            if not self.panel.isVisible():
                return None
        except Exception:
            return None
        elapsed = now - self._last_activity
        for sec, line in self.MARKS:
            if sec in self._fired:
                continue
            if elapsed >= sec:
                self._fired.add(sec)
                self.panel.inject_system_msg(line)
        return None


# ============================================================
# B 深夜温柔挡 (23:30 ~ 05:00)
# ============================================================
class NightMonitor(BaseMonitor):
    name = "B_night"

    def __init__(self, panel):
        super().__init__(panel)
        self._is_night: bool = False

    def on_event(self, kind, data):
        if kind != EVT_TICK:
            return None
        now = datetime.datetime.now()
        in_night = (
            (now.hour == 23 and now.minute >= 30)
            or now.hour < 5
        )
        if in_night and not self._is_night:
            self._is_night = True
            # 不覆盖 worried (优先级 worried > night > normal)
            if getattr(self.panel, "_current_tone", "normal") != "worried":
                self.panel.set_panel_tone("night")
        elif not in_night and self._is_night:
            self._is_night = False
            if getattr(self.panel, "_current_tone", "normal") == "night":
                self.panel.set_panel_tone("normal")
        return None


# ============================================================
# C 连续对话计时
# ============================================================
class ChatDurationMonitor(BaseMonitor):
    name = "C_duration"
    MARKS = [
        (30 * 60, "· 已聊 30min · 喝水"),
        (60 * 60, "· 已聊 1h · 站起来活动一下"),
        (90 * 60, "· 已聊 90min · 真的该歇了"),
    ]

    def __init__(self, panel):
        super().__init__(panel)
        self._session_start: float | None = None
        self._hit: set[int] = set()
        self._last_announce: float = 0.0

    def on_event(self, kind, data):
        now = data.get("_ts", time.time())
        if kind == EVT_USER_SEND:
            if self._session_start is None:
                self._session_start = now
            return None
        if kind != EVT_TICK:
            return None
        if self._session_start is None:
            return None
        elapsed = now - self._session_start
        for sec, line in self.MARKS:
            if sec in self._hit:
                continue
            if elapsed >= sec:
                self._hit.add(sec)
                self.panel.set_status(line)
                self._last_announce = now
        # 60s 后回 待机 (但 _set_busy 在 spawn 时也会改, 让它自己接管)
        if (self._last_announce
                and now - self._last_announce > 60
                and not getattr(self.panel, "_busy", False)):
            self.panel.set_status("· 待机")
            self._last_announce = 0.0
        return None


# ============================================================
# F 会话自动起名
# ============================================================
class AutoTitleMonitor(BaseMonitor):
    name = "F_auto_title"
    META_FIELD = "auto_title"

    _STRIP_PREFIX = re.compile(r"^[#\s*>\-`]+")
    _SENT_END = re.compile(r"[。\n?!.?!]")

    def __init__(self, panel):
        super().__init__(panel)
        self._named_sids: set[str] = set()

    def _extract_title(self, text: str) -> str:
        t = self._STRIP_PREFIX.sub("", text).strip()
        m = self._SENT_END.search(t)
        if m:
            t = t[:m.start()]
        return t[:12].strip()

    def on_event(self, kind, data):
        if kind != EVT_ASSISTANT_DONE:
            return None
        sid = data.get("session_id")
        if not sid or sid in self._named_sids:
            return None
        full = data.get("full_text", "") or ""
        if not full.strip():
            return None
        title = self._extract_title(full)
        if not title:
            return None
        self._named_sids.add(sid)
        self.panel.set_session_title(title)
        _meta_write_field(self.panel, self.META_FIELD, title)
        return None


# ============================================================
# K 上下文胖了 (per-session 累积)
# ============================================================
class ContextFatMonitor(BaseMonitor):
    name = "K_context_fat"
    TOOL_THRESHOLD = 30
    CHAR_THRESHOLD = 50_000

    def __init__(self, panel):
        super().__init__(panel)
        self._fired_sid: set[str] = set()
        self._cum_tools: dict[str, int] = {}
        self._cum_chars: dict[str, int] = {}

    def on_event(self, kind, data):
        if kind != EVT_ASSISTANT_DONE:
            return None
        sid = data.get("session_id") or "_"
        if sid in self._fired_sid:
            return None
        self._cum_tools[sid] = (
            self._cum_tools.get(sid, 0) + (data.get("tool_count") or 0)
        )
        self._cum_chars[sid] = (
            self._cum_chars.get(sid, 0) + (data.get("char_count") or 0)
        )
        if (self._cum_tools[sid] > self.TOOL_THRESHOLD
                or self._cum_chars[sid] > self.CHAR_THRESHOLD):
            self._fired_sid.add(sid)
            anchor = getattr(self.panel, "session_btn", None)
            self.panel.show_toast(
                "这轮挺长的, 要不要新开一轮?", anchor, 6000
            )
        return None


# ============================================================
# D 复读检测
# ============================================================
class RepeatMonitor(BaseMonitor):
    name = "D_repeat"
    THRESHOLD = 0.85
    MIN_LEN = 6  # 太短的不查

    def __init__(self, panel):
        super().__init__(panel)
        self._recent: deque[str] = deque(maxlen=20)

    def on_event(self, kind, data):
        if kind != EVT_USER_SEND:
            return None
        text = (data.get("text") or "").strip()
        if len(text) < self.MIN_LEN:
            self._recent.append(text)
            return None
        # 跟最近 20 条比 simhash 没必要, difflib 够用
        for prev in self._recent:
            if not prev or len(prev) < self.MIN_LEN:
                continue
            ratio = difflib.SequenceMatcher(None, text, prev).ratio()
            if ratio > self.THRESHOLD:
                anchor = getattr(self.panel, "input", None)
                self.panel.show_toast(
                    "...这个问过哦, 要不要先看历史?", anchor, 5000
                )
                break
        self._recent.append(text)
        return None


# ============================================================
# E 报错气压计 (5min 滑动窗口)
# ============================================================
class ErrorBarometerMonitor(BaseMonitor):
    name = "E_error"
    WINDOW_SEC = 5 * 60
    THRESHOLD = 3
    CLEAR_GOOD_ROUNDS = 3

    _BAD_PATTERN = re.compile(
        r"\berror\b|\bexception\b|traceback|stack ?trace",
        re.IGNORECASE,
    )

    def __init__(self, panel):
        super().__init__(panel)
        self._error_ts: deque[float] = deque()
        self._good_streak: int = 0
        self._in_storm: bool = False

    def on_event(self, kind, data):
        now = data.get("_ts", time.time())
        if kind == EVT_TOOL_RESULT:
            is_err = (
                bool(data.get("is_error"))
                or bool(self._BAD_PATTERN.search(data.get("content") or ""))
            )
            if is_err:
                self._error_ts.append(now)
                while self._error_ts and now - self._error_ts[0] > self.WINDOW_SEC:
                    self._error_ts.popleft()
                if not self._in_storm and len(self._error_ts) >= self.THRESHOLD:
                    self._in_storm = True
                    self.panel.set_panel_tone("worried")
                    self.panel.pet_request.emit(
                        "worried", "...这熟悉的味道"
                    )
                    self.panel.set_status("· 状况有点紧张")
            return None
        if kind == EVT_ASSISTANT_DONE and self._in_storm:
            # 本轮窗口内还有错就 streak 清零, 否则递增
            has_recent_err = bool(
                self._error_ts and now - self._error_ts[-1] <= 60
            )
            if has_recent_err:
                self._good_streak = 0
            else:
                self._good_streak += 1
                if self._good_streak >= self.CLEAR_GOOD_ROUNDS:
                    self._in_storm = False
                    self._good_streak = 0
                    self.panel.set_panel_tone("normal")
                    self.panel.set_status("· 待机")
        return None


# ============================================================
# H Claude 走神检测
# ============================================================
class StuckMonitor(BaseMonitor):
    name = "H_stuck"
    THINK_PATTERN = re.compile(
        r"let me (?:think|check|see)|wait,|等我看|让我看|我先看|稍等",
        re.IGNORECASE,
    )
    STUCK_AFTER_SEC = 10.0

    def __init__(self, panel):
        super().__init__(panel)
        self._think_ts: float | None = None
        self._last_event_ts: float = time.time()
        self._toasted: bool = False

    def on_event(self, kind, data):
        now = data.get("_ts", time.time())
        if kind == EVT_TEXT:
            self._last_event_ts = now
            self._toasted = False
            chunk = data.get("chunk", "") or ""
            if self.THINK_PATTERN.search(chunk):
                self._think_ts = now
            return None
        if kind in (EVT_TOOL_USE_START, EVT_TOOL_USE_INPUT_READY):
            self._last_event_ts = now
            self._think_ts = None
            self._toasted = False
            return None
        if kind == EVT_ASSISTANT_DONE:
            self._think_ts = None
            self._toasted = False
            return None
        if kind != EVT_TICK:
            return None
        if self._think_ts is None or self._toasted:
            return None
        if now - self._last_event_ts >= self.STUCK_AFTER_SEC:
            self._toasted = True
            anchor = getattr(self.panel, "_current_bubble", None)
            self.panel.show_toast("...卡住了?", anchor, 4000)
        return None


# ============================================================
# A 危险命令哨兵 (tool input 累积完成后才匹配)
# ============================================================
class DangerMonitor(BaseMonitor):
    name = "A_danger"
    PATTERNS = [
        re.compile(r"\brm\s+(?:-[a-z]+\s+)*-r?f", re.IGNORECASE),
        re.compile(r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b", re.IGNORECASE),
        re.compile(r"\bgit\s+push\s+(?:[^\n]*\s)?--force", re.IGNORECASE),
        re.compile(r"\bgit\s+reset\s+--hard", re.IGNORECASE),
        re.compile(r"\bTRUNCATE\s+(?:TABLE\s+)?\w+", re.IGNORECASE),
        re.compile(r"\bDELETE\s+FROM\s+\w+(?!\s+WHERE)", re.IGNORECASE),
        re.compile(r"\bshutdown\b|\breboot\b", re.IGNORECASE),
    ]

    def on_event(self, kind, data):
        if kind != EVT_TOOL_USE_INPUT_READY:
            return None
        inp = data.get("input") or {}
        # 把 input 全部展平成字符串过滤
        try:
            text = json.dumps(inp, ensure_ascii=False)
        except Exception:
            text = str(inp)
        for pat in self.PATTERNS:
            if pat.search(text):
                tid = data.get("tool_use_id")
                if tid:
                    self.panel.set_chip_emphasis(tid, "danger")
                self.panel.show_warning(
                    "⚠ 这条挺重的, 豆哥确定?", "#ff8800", 8000
                )
                self.panel.pet_request.emit("worried", "...等等等等")
                break
        return None


# ============================================================
# 默认总线构造
# ============================================================
def build_default_bus(panel) -> MonitorBus:
    """构造默认监听总线: 注册全部 11 个 monitor.

    顺序: G 暗号最先 (它会消费 user_send 拦截发送), 其它按 spec 顺序.
    """
    bus = MonitorBus()
    bus.register(HiddenCmdMonitor(panel))      # G
    bus.register(FirstChatMonitor(panel))      # I
    bus.register(IdleMonitor(panel))           # J
    bus.register(NightMonitor(panel))          # B
    bus.register(ChatDurationMonitor(panel))   # C
    bus.register(AutoTitleMonitor(panel))      # F
    bus.register(ContextFatMonitor(panel))     # K
    bus.register(RepeatMonitor(panel))         # D
    bus.register(ErrorBarometerMonitor(panel)) # E
    bus.register(StuckMonitor(panel))          # H
    bus.register(DangerMonitor(panel))         # A
    return bus
