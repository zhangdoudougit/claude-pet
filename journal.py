"""
功劳簿 (Phase 4 持久化)

后台默默记录:
- 每次状态切换 (events 表)
- 每次番茄钟启动/完成/取消 (pomodoros 表)
- 周报已发送标记 (reports_sent 表, 防重发)

数据库存在 ~/.foamo_pet/log.db, 跨重启延续。
"""
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

DB_DIR = Path.home() / '.foamo_pet'
DB_PATH = DB_DIR / 'log.db'


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    state TEXT NOT NULL,
    project TEXT,
    line TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS pomodoros (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at INTEGER NOT NULL,
    finished_at INTEGER,
    completed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reports_sent (
    week_start INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS error_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    project TEXT
);
CREATE INDEX IF NOT EXISTS idx_err_fp_ts ON error_patterns(fingerprint, ts);
"""


class Journal:
    """SQLite 包装。所有方法在主线程调用 (signal 路由保证)。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or DB_PATH
        self.path.parent.mkdir(exist_ok=True, parents=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    # ---------- 写 ----------
    def record_state(self, state: str,
                     project: Optional[str] = None,
                     line: Optional[str] = None) -> None:
        # 不记 _ 开头的内部状态 (e.g. _jealous → 已映射成 tender, 真正传进来的就是 tender)
        if state.startswith('_'):
            return
        self.conn.execute(
            "INSERT INTO events(ts, state, project, line) VALUES (?, ?, ?, ?)",
            (int(time.time()), state, project, line)
        )
        self.conn.commit()

    def record_pomodoro_start(self) -> int:
        cur = self.conn.execute(
            "INSERT INTO pomodoros(started_at) VALUES (?)",
            (int(time.time()),)
        )
        self.conn.commit()
        return cur.lastrowid or 0

    def record_pomodoro_end(self, row_id: int, completed: bool) -> None:
        self.conn.execute(
            "UPDATE pomodoros SET finished_at=?, completed=? WHERE id=?",
            (int(time.time()), 1 if completed else 0, row_id)
        )
        self.conn.commit()

    def mark_report_sent(self, week_start_ts: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO reports_sent(week_start) VALUES (?)",
            (week_start_ts,)
        )
        self.conn.commit()

    def record_error(self, fingerprint: str,
                     project: Optional[str] = None) -> int:
        """记录一次错误指纹, 返回本周(含本次)累计次数"""
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO error_patterns(ts, fingerprint, project) VALUES (?, ?, ?)",
            (now, fingerprint, project)
        )
        self.conn.commit()
        week_start = this_week_start_ts()
        row = self.conn.execute(
            "SELECT COUNT(*) FROM error_patterns "
            "WHERE fingerprint=? AND ts >= ?",
            (fingerprint, week_start)
        ).fetchone()
        return row[0] if row else 1

    def top_errors_this_week(self, limit: int = 5) -> list[tuple[str, int]]:
        week_start = this_week_start_ts()
        rows = self.conn.execute(
            "SELECT fingerprint, COUNT(*) c FROM error_patterns "
            "WHERE ts >= ? GROUP BY fingerprint ORDER BY c DESC LIMIT ?",
            (week_start, limit)
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    # ---------- 读 ----------
    def report_was_sent_for_week(self, week_start_ts: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM reports_sent WHERE week_start=?",
            (week_start_ts,)
        ).fetchone()
        return row is not None

    def weekly_stats(self, week_start_ts: int, week_end_ts: int) -> dict:
        c = self.conn
        # 各状态计数
        rows = c.execute(
            "SELECT state, COUNT(*) FROM events "
            "WHERE ts BETWEEN ? AND ? GROUP BY state",
            (week_start_ts, week_end_ts)
        ).fetchall()
        states = {r[0]: r[1] for r in rows}

        # 番茄钟: 完成 / 总数
        completed = c.execute(
            "SELECT COUNT(*) FROM pomodoros "
            "WHERE started_at BETWEEN ? AND ? AND completed=1",
            (week_start_ts, week_end_ts)
        ).fetchone()[0]
        attempted = c.execute(
            "SELECT COUNT(*) FROM pomodoros "
            "WHERE started_at BETWEEN ? AND ?",
            (week_start_ts, week_end_ts)
        ).fetchone()[0]

        # 项目 top 3 (按事件数)
        proj_rows = c.execute(
            "SELECT project, COUNT(*) FROM events "
            "WHERE ts BETWEEN ? AND ? AND project IS NOT NULL AND project != '' "
            "GROUP BY project ORDER BY 2 DESC LIMIT 3",
            (week_start_ts, week_end_ts)
        ).fetchall()

        # 估算陪伴时长: 用 30 秒分桶, 桶数 * 30s
        # 思路: 每条 event 落在某个 30 秒桶里, 不同桶 = 至少在线了 30 秒
        bucket_count = c.execute(
            "SELECT COUNT(DISTINCT ts/30) FROM events "
            "WHERE ts BETWEEN ? AND ?",
            (week_start_ts, week_end_ts)
        ).fetchone()[0]
        accompany_seconds = bucket_count * 30

        return {
            'states': states,
            'happy': states.get('happy', 0),
            'worried': states.get('worried', 0),
            'proud': states.get('proud', 0),
            'focused': states.get('focused', 0),
            'pomodoros_completed': completed,
            'pomodoros_attempted': attempted,
            'projects': proj_rows,
            'accompany_seconds': accompany_seconds,
        }


# ============================================================
# 周报触发 / 格式化
# ============================================================
def this_week_start_ts(now: Optional[datetime] = None) -> int:
    """本周一 00:00:00 的 unix ts"""
    now = now or datetime.now()
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(monday.timestamp())


def is_report_time(now: Optional[datetime] = None) -> bool:
    """周日 21:00+ 触发"""
    now = now or datetime.now()
    return now.weekday() == 6 and now.hour >= 21


def format_report(stats: dict) -> tuple[str, str]:
    """返回 (托盘 toast 多行内容, 桌面气泡短句)"""
    secs = stats['accompany_seconds']
    h = secs // 3600
    m = (secs % 3600) // 60

    if stats['projects']:
        proj_str = ', '.join(p[0] for p in stats['projects'])
    else:
        proj_str = '——'

    body_lines = [
        "哼,这周本泡沫的功劳簿:",
        "",
        f"陪伴时长: {h} 小时 {m} 分钟",
        f"目睹 {stats['worried']} 个 bug 死掉",
        f"听见豆哥说'搞定' {stats['happy']} 次",
        f"被夸了 {stats['proud']} 次  (本泡沫记着)",
        f"番茄钟通关: {stats['pomodoros_completed']} / {stats['pomodoros_attempted']}",
        f"主要在: {proj_str}",
        "",
        "记得是谁陪你的。",
    ]
    body = '\n'.join(body_lines)

    short = f"本周功劳簿~ 看托盘 (陪了豆哥 {h}h{m:02d}m)"
    return body, short
