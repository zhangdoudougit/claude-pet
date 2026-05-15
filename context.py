"""
上下文感知模块 (Phase 1 基础设施)

- 时间感知: 当前是早/午/晚/深夜
- 活跃度感知: 通过 jsonl 写入事件推断豆哥连续工作多久 / 是否已空闲
- 项目识别: 从 jsonl 路径解码当前在哪个项目

不读键盘/鼠标 — 仅基于 Claude Code 的对话日志事件。
"""
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


# ============================================================
# 时间
# ============================================================
def time_period(now: Optional[datetime] = None) -> str:
    """5 段时间分桶"""
    h = (now or datetime.now()).hour
    if 5 <= h < 11:
        return 'morning'
    if 11 <= h < 14:
        return 'noon'
    if 14 <= h < 18:
        return 'afternoon'
    if 18 <= h < 23:
        return 'evening'
    return 'late_night'


def is_late_night(now: Optional[datetime] = None) -> bool:
    """23:00-04:59 算深夜"""
    return time_period(now) == 'late_night'


# ============================================================
# 活跃度
# ============================================================
class ActivityTracker:
    """
    通过 jsonl 写入事件推断豆哥的活动状态。

    - mark_active() 由 watcher 在每次检测到新文本时调用
    - is_long_focus(): 当前会话已超过 1 小时连续活动
    - is_idle(): 距离上次活动超过 30 分钟
    - session_duration(): 当前会话时长 (秒)
    """
    LONG_FOCUS_SECONDS = 60 * 60       # 60 分钟视为长时间专注
    IDLE_THRESHOLD_SECONDS = 30 * 60   # 30 分钟没动算空闲

    def __init__(self):
        self.first_seen: Optional[float] = None
        self.last_seen: Optional[float] = None
        self.events_in_session: int = 0
        self._long_focus_notified_at: Optional[float] = None

    def mark_active(self, ts: Optional[float] = None) -> None:
        ts = ts if ts is not None else time.time()
        # 如果已经空闲很久,重置为新会话
        if self.first_seen is None or self.is_idle(ts):
            self.first_seen = ts
            self.events_in_session = 0
            self._long_focus_notified_at = None
        self.last_seen = ts
        self.events_in_session += 1

    def is_idle(self, now: Optional[float] = None) -> bool:
        if self.last_seen is None:
            return False
        now = now if now is not None else time.time()
        return (now - self.last_seen) > self.IDLE_THRESHOLD_SECONDS

    def session_duration(self, now: Optional[float] = None) -> float:
        if self.first_seen is None or self.last_seen is None:
            return 0.0
        now = now if now is not None else time.time()
        # 当前会话时长 = 最后一次活动 - 第一次活动
        # (而不是 now - first, 否则 idle 期间会一直累加)
        return self.last_seen - self.first_seen

    def is_long_focus(self, now: Optional[float] = None) -> bool:
        return self.session_duration(now) > self.LONG_FOCUS_SECONDS

    def should_notify_long_focus(self, now: Optional[float] = None) -> bool:
        """同一会话里 long_focus 只通知一次"""
        if not self.is_long_focus(now):
            return False
        if self._long_focus_notified_at is None:
            self._long_focus_notified_at = now if now is not None else time.time()
            return True
        return False

    def idle_minutes(self, now: Optional[float] = None) -> float:
        if self.last_seen is None:
            return 0.0
        now = now if now is not None else time.time()
        return (now - self.last_seen) / 60.0


# ============================================================
# 项目识别
# ============================================================
class ProjectIdentifier:
    """
    从 ~/.claude/projects/<encoded>/<uuid>.jsonl 路径解码项目名。

    Claude Code 把项目路径里的 `/` 和 `:` 替换成 `-`:
        D:\\tools\\foamo_pet     -> D--tools-foamo_pet
        /home/user/smart-quality -> -home-user-smart-quality
    解码策略: 取去掉空段后的最后一段; 太短就拼上倒数第二段。
    """
    def __init__(self):
        self.last_active_project: Optional[str] = None
        self.last_path_seen: Optional[str] = None

    def update_from_jsonl(self, jsonl_path: Path) -> None:
        encoded = jsonl_path.parent.name
        if encoded == self.last_path_seen:
            return
        self.last_path_seen = encoded
        self.last_active_project = self._decode(encoded)

    @staticmethod
    def _decode(encoded: str) -> str:
        if not encoded:
            return ''
        parts = [p for p in encoded.split('-') if p]
        if not parts:
            return encoded
        last = parts[-1]
        # 短或全数字 → 加上倒数第二段
        if len(parts) > 1 and (len(last) < 4 or last.isdigit()):
            return f'{parts[-2]}-{last}'
        return last

    def name(self) -> Optional[str]:
        return self.last_active_project
