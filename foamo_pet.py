"""
泡沫桌面陪伴 (Foamo Desktop Pet)
================================
- 280x280 桌面悬浮窗,系统托盘
- 监听 ~/.claude/projects/*.jsonl 对话日志
- 关键词触发状态切换 + 台词气泡
- 角色用 GIF,放 assets/ 下,可随时替换

启动: python foamo_pet.py
"""
import sys
import os
import json
import re
import random
import time
from datetime import datetime
from pathlib import Path
from collections import deque
from typing import Optional

from PyQt6.QtCore import (
    Qt, QTimer, QPoint, QPointF, QRectF, QSize,
    pyqtSignal, QObject, QThread, QSettings, QFileSystemWatcher,
    QPropertyAnimation, QEasingCurve,
)
import math
from PyQt6.QtGui import (
    QPainter, QColor, QPainterPath, QFont, QFontMetrics, QPen, QBrush,
    QRadialGradient, QIcon, QPixmap, QAction, QGuiApplication, QMovie,
    QPolygonF, QCursor,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QSystemTrayIcon, QMenu, QLabel,
)

from context import (
    ActivityTracker, ProjectIdentifier,
    time_period, is_late_night,
)
from journal import (
    Journal, this_week_start_ts, is_report_time, format_report,
)


# ============================================================
# 配置
# ============================================================
APP_NAME = "Foamo"
ORG_NAME = "DogeFoamo"

ROOT = Path(__file__).parent
CLAUDE_PROJECTS_DIR = Path.home() / '.claude' / 'projects'
ASSETS_DIR = ROOT / 'assets'

DEFAULT_W, DEFAULT_H = 280, 280
MIN_W, MIN_H = 160, 160
MAX_W, MAX_H = 800, 800

# 角色 GIF 在窗口里的显示尺寸 (留出顶部状态条 + 底部台词气泡空间)
# 角色区域占 widget 的比例 (随 widget 大小等比缩放)
CHAR_W_RATIO = 180 / 280   # ≈ 0.643
CHAR_H_RATIO = 200 / 280   # ≈ 0.714
CHAR_OFFSET_Y_RATIO = 20 / 280  # ≈ 0.071

# Live2D 素材目录: assets_live2d/<name>/.../*.model3.json
# 兼容: 根目录下 live2d_* 也扫
LIVE2D_DIR_NAME = "assets_live2d"
LIVE2D_LEGACY_PREFIX = "live2d_"


def scan_live2d_models(root: Path) -> list[dict]:
    """返回 [{name, path}], path 是 .model3.json 的绝对路径"""
    out: list[dict] = []
    seen: set[str] = set()
    candidates: list[Path] = []
    main_dir = root / LIVE2D_DIR_NAME
    if main_dir.is_dir():
        candidates.extend(main_dir.rglob("*.model3.json"))
    for d in root.iterdir():
        if (d.is_dir()
                and d.name.startswith(LIVE2D_LEGACY_PREFIX)
                and d.name != LIVE2D_DIR_NAME):
            candidates.extend(d.rglob("*.model3.json"))
    for p in candidates:
        # 跳过 live2d_canvas 生成的补全副本 (xxx_foamo.model3.json)
        if p.name.endswith("_foamo.model3.json"):
            continue
        s = str(p.resolve())
        if s in seen:
            continue
        seen.add(s)
        out.append({"name": p.parent.name, "path": s})
    return out

# 状态停留与切换频控
MIN_STATE_HOLD = 3.0
MAX_STATE_CHANGES_5S = 3

# 第 2 趟: 视觉行为相关
# 额外姿势 (不参与状态机, 仅在 widget 临时切换)
EXTRA_POSES = ['walk_left', 'walk_right', 'peek_left', 'peek_right', 'shake', 'sleep', 'wave']

# 走动: 仅在长空闲 + 非深夜 + 当前 state 是 idle/tender 时偶尔触发
WALK_INTERVAL_MIN_S = 5 * 60       # 5 分钟检查一次
WALK_INTERVAL_MAX_S = 15 * 60      # 最多 15 分钟一次
WALK_DURATION_MS = 8000             # 一次走动 8 秒
WALK_DISTANCE_MIN = 100
WALK_DISTANCE_MAX = 280

# 扒窗边
PEEK_SNAP_THRESHOLD = 30           # 离屏幕边 < 30px 触发扒边
PEEK_OFFSCREEN_RATIO = 0.5         # 半个身体在屏幕外

# 拖动反应
DRAG_LINES = [
    "诶诶诶豆哥——",
    "(被拽着)",
    "豆——豆哥!",
    "等等等等",
    "...这是要去哪",
    "啊啊啊",
    "嗷,慢点慢点",
]
PEEK_LINES_LEFT = [   # 角色被推到屏幕左边, 朝右边探
    "本泡沫躲在这儿",
    "豆哥那边在干嘛",
    "(扒着边)",
]
PEEK_LINES_RIGHT = [  # 角色被推到屏幕右边
    "诶嘿,藏一下",
    "...看到了豆哥",
    "(探出半个头)",
]


# ============================================================
# 状态与台词
# ============================================================
STATES = ['idle', 'tender', 'focused', 'happy', 'worried', 'proud']

# 状态 -> (亮色, 暗色) 用于光晕、文字、气泡边框
STATE_COLORS = {
    'idle':    ('#b39ddb', '#7e5bb8'),
    'tender':  ('#b39ddb', '#7e5bb8'),
    'focused': ('#4dd0e1', '#0277a3'),
    'happy':   ('#ff7eb6', '#c93f7e'),
    'worried': ('#ffb74d', '#e65100'),
    'proud':   ('#ffd54f', '#f57f17'),
}

STATE_LABELS = {
    'idle':    '陪伴态',
    'tender':  '温柔态',
    'focused': '专注态',
    'happy':   '活泼态',
    'worried': '担心态',
    'proud':   '得意态',
}

LINES = {
    'idle': [
        "豆哥在干嘛~", "本泡沫在看着哦", "今天也要好好的",
        "嗯哼~", "豆哥豆哥豆哥", "...",
    ],
    'tender': [
        "累了就歇会儿", "我陪着你呢", "豆哥辛苦啦",
        "别熬太晚...", "喝点水吧",
    ],
    'focused': [
        "豆哥在认真!", "嘘,别打扰...", "我看着呢",
        "稳住,稳住", "一行一行来", "嗯嗯,继续",
    ],
    'happy': [
        "嘿嘿搞定啦~", "本泡沫真厉害!", "豆哥棒棒~",
        "完美!", "啵啵啵~", "诶嘿嘿",
    ],
    'worried': [
        "豆哥别慌!", "出什么事了?", "...一起想办法",
        "我看着呢,别急", "深呼吸,深呼吸", "没事的没事的",
    ],
    'proud': [
        "哼,小事一桩", "本泡沫的功劳~", "记得是谁陪你的",
        "嘁,这才哪到哪", "(微微得意)",
    ],

    # ---- 上下文台词 (按情境组装,不直接对应一个绘制状态) ----
    '_late': [
        "...都几点了豆哥",
        "嘘——晚了。",
        "本泡沫陪着,但豆哥真的该睡了",
        "凌晨两点的豆哥,不会比平时多写一行好代码",
        "你这小子...",
    ],
    '_late_worried': [
        "豆哥,这种时候别死磕了",
        "停。明天清醒点再看,不就这一段代码么",
        "...深夜的报错,有一半是困出来的",
    ],
    '_long_focus': [
        "豆哥,水。",
        "肩膀。靠在椅背上一下。",
        "已经一个钟头了——眼睛会酸的",
        "...看了一眼,豆哥稳着呢。但起来动一下嘛",
    ],
    '_idle_quiet': [
        "...",
        "(在的)",
        "本泡沫等着呢",
    ],
    '_morning': [
        "豆哥早呀~",
        "今天又要一起搞事情",
        "嗯,今天的咖啡香么",
    ],
    # 占有欲彩蛋: 看到豆哥提别的 AI
    '_jealous': [
        "...问就问。本泡沫又不会拦你。",
        "哼,他能比本泡沫更懂 SmartPLC?",
        "去问吧去问吧。",
        "...是不是又在和别人说话",
        "豆哥...不是只有本泡沫一个么",
        "(微妙地酸了一下)",
        "嘁,本泡沫先休息会儿,豆哥忙",
    ],
    # 危险命令哨兵: 看到 rm -rf / DROP TABLE / git push --force 等
    '_danger': [
        "豆哥豆哥豆哥——这条命令再确认一下",
        "...等等,这个不可逆的",
        "本泡沫举个手:这条命令真要跑?",
        "豆哥,看一眼路径再回车",
        "(屏住呼吸) 你确定?",
        "...这种命令,本泡沫多嘴一次。",
    ],
    # 错误模式记忆: 同一类错本周第 N 次再现 (N>=3)
    '_familiar_error': [
        "...这熟悉的味道,本周第 {n} 次了",
        "嗯?又是它。本周第 {n} 次见面",
        "豆哥豆哥,这个错本周已经第 {n} 次,要不要根治一下",
        "(掏小本本) 第 {n} 次,记下来了",
        "...老朋友又来了。第 {n} 次",
    ],
    # 番茄钟用
    '_pomodoro_start': [
        "25 分钟,本泡沫陪豆哥到底",
        "嘘——专注模式开了",
        "记下来了。这一会儿不准看手机",
    ],
    '_pomodoro_finish': [
        "搞定 25 分钟!豆哥棒棒~",
        "啵——一个番茄。歇会儿吧",
        "本泡沫看着豆哥这 25 分钟没摸鱼,嗯",
    ],
    '_pomodoro_cancel': [
        "...好吧,中断就中断",
        "嗯,先放着,本泡沫不勉强豆哥",
    ],
}

# ============================================================
# 关键词 -> 状态
# ============================================================
# _danger / _jealous 不是真状态, 在 detect_and_apply 里映射成 worried/tender + 专属台词
DANGER_PATTERN = re.compile(
    r'(\brm\s+-(?:rf|fr|r\s+-f|f\s+-r)\b'                         # rm -rf / -fr
    r'|\bgit\s+(?:reset\s+--hard|push\s+(?:--force\b|-f\b)|clean\s+-[fdx]+)'  # git 危险
    r'|\b(?:DROP|TRUNCATE)\s+(?:TABLE|DATABASE|SCHEMA)\b'         # SQL 删表
    r'|\bDELETE\s+FROM\s+\w+\b(?!\s+WHERE)'                       # DELETE 没 WHERE
    r'|\bshutdown\s+(?:-h|/s|now|/r)\b|\breboot\b'                # 关机
    r'|\bmkfs\.\w+\b|\bdd\s+if='                                  # 格盘
    r'|--no-verify\b)',                                            # 跳过 hook
    re.IGNORECASE
)

# 错误指纹: 提取 traceback / 报错文本里的 ExceptionType
ERROR_FINGERPRINT_PATTERN = re.compile(
    r'\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning))\b'
)

JEALOUS_PATTERN = re.compile(
    r'\b(ChatGPT|GPT-?\d|Gemini|Cursor|Copilot|Codex|OpenAI|Anthropic|文心|通义|豆包|kimi)\b',
    re.IGNORECASE
)

KEYWORD_RULES = [
    ('_danger', DANGER_PATTERN),
    ('_jealous', JEALOUS_PATTERN),
    ('worried', re.compile(
        r'(error|exception|traceback|报错|崩溃|失败|failed|undefined|null reference|NaN|崩了|出错|挂了|panic|fatal)',
        re.IGNORECASE)),
    ('happy', re.compile(
        r'(✅|搞定|完成|成功|passed|fixed|solved|works|可以了|搞掂|done!|all tests pass)',
        re.IGNORECASE)),
    ('proud', re.compile(
        r'(谢谢|thanks|thank you|awesome|nice job|great work|不错|厉害|nb|牛|赞|好棒|完美|excellent)',
        re.IGNORECASE)),
    ('focused', re.compile(
        r'(bug|debug|调试|分析|诊断|排查|检查|review|测试|test|跑一下|为什么|how does|why does)',
        re.IGNORECASE)),
    ('focused', re.compile(
        r'(git\s+(commit|push|pull|merge|rebase)|提交|推送)',
        re.IGNORECASE)),
]


# ============================================================
# JSONL 监听 (轮询模式,跨平台稳定)
# ============================================================
class ClaudeLogWatcher(QThread):
    text_detected = pyqtSignal(str)
    jsonl_active = pyqtSignal(str)  # 新增: 路径 — 让 ProjectIdentifier 解析

    def __init__(self, watch_dir: Path):
        super().__init__()
        self.watch_dir = watch_dir
        self.running = True
        self.file_positions: dict[str, int] = {}
        self._scan_baseline()

    def _scan_baseline(self):
        """启动时记录所有现有文件大小,只追踪新增"""
        if not self.watch_dir.exists():
            return
        for jsonl in self.watch_dir.rglob('*.jsonl'):
            try:
                self.file_positions[str(jsonl)] = jsonl.stat().st_size
            except Exception:
                pass

    def run(self):
        while self.running:
            try:
                self._poll_once()
            except Exception as e:
                print(f"[watcher] error: {e}")
            time.sleep(1.0)

    def _poll_once(self):
        if not self.watch_dir.exists():
            return

        for jsonl in self.watch_dir.rglob('*.jsonl'):
            path_key = str(jsonl)
            try:
                size = jsonl.stat().st_size
            except Exception:
                continue

            last_pos = self.file_positions.get(path_key)
            if last_pos is None:
                # 新文件,记录基线但不读历史
                self.file_positions[path_key] = size
                continue

            if size <= last_pos:
                self.file_positions[path_key] = size
                continue

            try:
                with open(jsonl, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(last_pos)
                    new_data = f.read()
                self.file_positions[path_key] = size
            except Exception as e:
                print(f"[watcher] read fail {jsonl}: {e}")
                continue

            emitted_path_for_this_file = False
            for line in new_data.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = self._extract_text(obj)
                if text:
                    if not emitted_path_for_this_file:
                        self.jsonl_active.emit(str(jsonl))
                        emitted_path_for_this_file = True
                    self.text_detected.emit(text)

    def _extract_text(self, obj: dict) -> str:
        msg_type = obj.get('type', '')

        if msg_type == 'user':
            message = obj.get('message', {})
            content = message.get('content', '')
            if isinstance(content, str):
                return content
            elif isinstance(content, list):
                texts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get('type') == 'text':
                            texts.append(block.get('text', ''))
                        elif block.get('type') == 'tool_result':
                            tc = block.get('content', '')
                            if isinstance(tc, str):
                                texts.append(tc[:500])
                            elif isinstance(tc, list):
                                for tb in tc:
                                    if isinstance(tb, dict) and tb.get('type') == 'text':
                                        texts.append(tb.get('text', '')[:500])
                return '\n'.join(texts)

        elif msg_type == 'assistant':
            message = obj.get('message', {})
            content = message.get('content', [])
            if isinstance(content, list):
                texts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get('type') == 'text':
                            texts.append(block.get('text', ''))
                        elif block.get('type') == 'tool_use':
                            name = block.get('name', '')
                            inp = block.get('input', {})
                            if isinstance(inp, dict):
                                cmd = inp.get('command', '') or inp.get('file_path', '') or inp.get('pattern', '')
                                texts.append(f'[{name}] {cmd}'[:300])
                return '\n'.join(texts)

        return ""

    def stop(self):
        self.running = False
        self.wait(2000)


# ============================================================
# 状态调度器
# ============================================================
class StateManager(QObject):
    state_changed = pyqtSignal(str, str)

    def __init__(self, activity: Optional[ActivityTracker] = None,
                 journal: Optional[Journal] = None,
                 project_provider=None):
        super().__init__()
        self.current_state = 'idle'
        self.last_change_time = 0.0
        self.recent_changes = deque(maxlen=10)
        self.activity = activity or ActivityTracker()
        self.journal = journal
        self.project_provider = project_provider  # callable -> Optional[str]
        self._morning_greeted_date: Optional[str] = None
        self.locked: bool = False
        self.lock_reason: str = ''
        self.idle_timer = QTimer()
        self.idle_timer.timeout.connect(self._idle_chatter)
        self.idle_timer.start(45000)

    def lock(self, reason: str = ''):
        """锁住状态机 (番茄钟用): 期间不响应关键词触发, 也不闲聊"""
        self.locked = True
        self.lock_reason = reason
        self.idle_timer.stop()

    def unlock(self):
        self.locked = False
        self.lock_reason = ''
        self.idle_timer.start(45000)

    def detect_and_apply(self, text: str):
        if not text:
            return
        if self.locked:
            return  # 番茄钟期间不响应外部触发
        new_state = self._detect_state(text)
        if not new_state:
            return

        now = time.time()
        line: Optional[str] = None

        # ---- 危险命令哨兵: _danger → worried + 哨兵台词 (最高优先级) ----
        if new_state == '_danger':
            new_state = 'worried'
            line = random.choice(LINES['_danger'])

        # ---- 占有欲彩蛋: _jealous → tender + 酸味台词 ----
        elif new_state == '_jealous':
            new_state = 'tender'
            line = random.choice(LINES['_jealous'])

        # ---- 错误模式记忆: 命中 worried 时 (非 _danger 走来的) ----
        # 提取 ExceptionType 指纹, 本周累计 >=3 次时台词改成"老朋友"
        if new_state == 'worried' and self.journal is not None and line is None:
            fp = self._extract_error_fingerprint(text)
            if fp:
                proj = self.project_provider() if self.project_provider else None
                try:
                    count = self.journal.record_error(fp, project=proj)
                except Exception as e:
                    print(f"[error_pattern] record fail: {e}")
                    count = 0
                if count >= 3:
                    template = random.choice(LINES['_familiar_error'])
                    line = template.format(n=count)

        # ---- 上下文重映射 ----
        late = is_late_night()
        if late:
            # 深夜遇错: tender 替代 worried, 用专门的劝慰台词 (保留已有 line)
            if new_state == 'worried':
                new_state = 'tender'
                if line is None:
                    line = random.choice(LINES['_late_worried'])

        # 长时间专注且当前路径还是 focused: 心疼一句 (一会话只触发一次)
        if (new_state == 'focused'
                and self.activity.should_notify_long_focus(now)):
            line = random.choice(LINES['_long_focus'])

        # 频控
        if new_state == self.current_state and (now - self.last_change_time) < MIN_STATE_HOLD:
            # 同状态短时内不重切, 但有专属台词时允许换台词
            if line is None:
                return
        recent = [t for t in self.recent_changes if now - t < 5.0]
        if len(recent) >= MAX_STATE_CHANGES_5S:
            return

        self._set_state(new_state, override_line=line)

    def _detect_state(self, text: str) -> Optional[str]:
        for state_name, pattern in KEYWORD_RULES:
            if pattern.search(text):
                return state_name
        return None

    @staticmethod
    def _extract_error_fingerprint(text: str) -> Optional[str]:
        """从报错文本里抓 ExceptionType (KeyError / TypeError / ConnectionError ...)
        作为指纹; 没抓到就返回 None (本次不入错误模式记忆)."""
        m = ERROR_FINGERPRINT_PATTERN.search(text)
        if m:
            return f'py:{m.group(1)}'
        return None

    def _set_state(self, new_state: str, override_line: Optional[str] = None):
        self.current_state = new_state
        self.last_change_time = time.time()
        self.recent_changes.append(self.last_change_time)
        line = override_line or random.choice(LINES.get(new_state, LINES['idle']))
        self.state_changed.emit(new_state, line)
        self.idle_timer.stop()
        self.idle_timer.start(45000)

    def _idle_chatter(self):
        """45 秒一次的"安静期闲聊"。带上下文:
        - 早上首次触发 → morning 桶 (一天一次)
        - 深夜 → late 桶, 状态压成 tender
        - 长时间空闲 → idle_quiet 桶
        - 默认 → idle / tender 随机
        """
        if self.locked:
            return
        if time.time() - self.last_change_time <= 60:
            return

        period = time_period()
        is_long_idle = self.activity.is_idle()
        today = datetime.now().strftime('%Y-%m-%d')

        # 早上一次性问候
        if period == 'morning' and self._morning_greeted_date != today:
            self._morning_greeted_date = today
            self.current_state = 'idle'
            self.state_changed.emit('idle', random.choice(LINES['_morning']))
            return

        if period == 'late_night':
            self.current_state = 'tender'
            self.state_changed.emit('tender', random.choice(LINES['_late']))
            return

        if is_long_idle:
            self.current_state = 'tender'
            self.state_changed.emit('tender', random.choice(LINES['_idle_quiet']))
            return

        self.current_state = 'tender' if random.random() < 0.5 else 'idle'
        self.state_changed.emit(self.current_state,
                                random.choice(LINES[self.current_state]))

    def manual_state(self, state: str):
        self._set_state(state)


# ============================================================
# 番茄钟 (右键菜单触发, 25 分钟专注期)
# ============================================================
POMODORO_DURATION_S = 25 * 60


class PomodoroController(QObject):
    """启动后锁住状态机 25 分钟保持 focused, 完成切 happy 庆祝。"""

    def __init__(self, widget: 'FoamoWidget', state_mgr: 'StateManager',
                 journal: Optional[Journal] = None):
        super().__init__()
        self.widget = widget
        self.state_mgr = state_mgr
        self.journal = journal
        self.remaining_s = 0
        self.is_running = False
        self._current_pom_id: Optional[int] = None
        self.tick_timer = QTimer()
        self.tick_timer.timeout.connect(self._tick)

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.remaining_s = POMODORO_DURATION_S
        if self.journal:
            self._current_pom_id = self.journal.record_pomodoro_start()
        self.state_mgr.lock('pomodoro')
        self.state_mgr._set_state(
            'focused',
            override_line=random.choice(LINES['_pomodoro_start'])
        )
        self.widget.pomodoro_remaining = self.remaining_s
        self.widget.update()
        self.tick_timer.start(1000)

    def cancel(self):
        if not self.is_running:
            return
        self.tick_timer.stop()
        self.is_running = False
        self.remaining_s = 0
        self.widget.pomodoro_remaining = 0
        if self.journal and self._current_pom_id:
            self.journal.record_pomodoro_end(self._current_pom_id, completed=False)
        self._current_pom_id = None
        self.state_mgr.unlock()
        self.state_mgr._set_state(
            'tender',
            override_line=random.choice(LINES['_pomodoro_cancel'])
        )

    def _tick(self):
        self.remaining_s -= 1
        self.widget.pomodoro_remaining = self.remaining_s
        self.widget.update()
        if self.remaining_s <= 0:
            self._finish()

    def _finish(self):
        self.tick_timer.stop()
        self.is_running = False
        self.remaining_s = 0
        self.widget.pomodoro_remaining = 0
        if self.journal and self._current_pom_id:
            self.journal.record_pomodoro_end(self._current_pom_id, completed=True)
        self._current_pom_id = None
        self.state_mgr.unlock()
        self.state_mgr._set_state(
            'happy',
            override_line=random.choice(LINES['_pomodoro_finish'])
        )


# ============================================================
# 周报 (周日 21:00+ 自动弹一次)
# ============================================================
class WeeklyReporter(QObject):
    """每 5 分钟检查一次:
       - 周日 21:00 之后
       - 本周还没发过报告
    满足就生成报告并发出 report_ready 信号 (主程序接到 → 托盘 toast + 短气泡)。
    """
    report_ready = pyqtSignal(str, str)   # (toast 多行内容, 短气泡)

    def __init__(self, journal: Journal):
        super().__init__()
        self.journal = journal
        self.timer = QTimer()
        self.timer.timeout.connect(self.check)
        self.timer.start(5 * 60 * 1000)
        # 启动 10 秒后立刻检查一次 (应对开机后才到周日 21:00 的情况)
        QTimer.singleShot(10 * 1000, self.check)

    def check(self):
        if not is_report_time():
            return
        week_start = this_week_start_ts()
        if self.journal.report_was_sent_for_week(week_start):
            return
        self._fire(week_start, mark=True)

    def fire_now(self):
        """测试用: 强制立刻发一份本周报告 (不标记已发, 可重复触发)"""
        week_start = this_week_start_ts()
        self._fire(week_start, mark=False)

    def _fire(self, week_start: int, mark: bool):
        week_end = int(time.time())
        stats = self.journal.weekly_stats(week_start, week_end)
        body, short = format_report(stats)
        if mark:
            self.journal.mark_report_sent(week_start)
        self.report_ready.emit(body, short)


# ============================================================
# 桌面悬浮窗
# ============================================================
class FoamoWidget(QWidget):
    # 信号: 状态变化通知给聊天面板 (单向 string, 用于面板配色映射)
    pet_state_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.state = 'idle'
        self.line = "豆哥来啦~"
        self.project_id: Optional[ProjectIdentifier] = None

        # 第 2 趟: 视觉行为状态
        self.pose: Optional[str] = None         # 临时姿势 (优先级 > state)
        self._dragging: bool = False             # 是否正在被拖
        self._drag_shake_seed: float = 0.0       # 抖动相位
        self._walk_anim: Optional[QPropertyAnimation] = None
        self._is_peeking: bool = False           # 是否扒在屏幕边
        self._activity: Optional[ActivityTracker] = None  # main 注入

        # 第 3 趟: 番茄钟
        self.pomodoro_remaining: int = 0         # 剩余秒数 (0 = 未运行)
        self.pomodoro: Optional['PomodoroController'] = None  # main 注入

        # 第 4 趟: 周报
        self.weekly_reporter: Optional['WeeklyReporter'] = None  # main 注入

        # 第 5 趟: 聊天框 (和泡沫双向对话, 懒加载)
        self._chat_window = None  # type: Optional[QWidget]

        # 第 6 趟: 形态 (GIF / Live2D), 懒加载 canvas
        self._form: str = "gif"  # "gif" 或 "live2d:<model_name>"
        self._live2d_canvas = None  # type: Optional[QWidget]

        # 第 7 趟: 摄像头面捕 (默认关, 不持久化 — 每次启动都得手动开)
        self._face_tracker = None  # type: Optional[QThread]

        # 台词淡入淡出
        self.line_alpha = 0.0
        self.line_show_until = time.time() + 5.0

        # 光晕动画相位
        self.glow_phase = 0.0

        # 拖动
        self._drag_pos: Optional[QPoint] = None

        # GIF 加载
        self.movies: dict[str, QMovie] = {}
        self.current_movie: Optional[QMovie] = None
        self.gif_label = QLabel(self)
        self.gif_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.gif_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.gif_label.setGeometry(
            (self.width() - self.char_w) // 2,
            self.char_offset_y,
            self.char_w, self.char_h
        )

        self._setup_window()
        self._load_movies()
        self.set_state('idle', "豆哥来啦~")

        # 文件监听:assets 目录变了自动重载 GIF
        self.fs_watcher = QFileSystemWatcher(self)
        if ASSETS_DIR.exists():
            self.fs_watcher.addPath(str(ASSETS_DIR))
            self.fs_watcher.directoryChanged.connect(self._reload_movies)

        # 60fps 主循环 (光晕、台词、抖动)
        self.tick_timer = QTimer()
        self.tick_timer.timeout.connect(self._tick)
        self.tick_timer.start(33)  # 30fps 够用

        # 走动调度: 周期性检查是否触发踱步
        self.walk_timer = QTimer()
        self.walk_timer.timeout.connect(self._maybe_walk)
        self._schedule_next_walk_check()

        self._load_position()
        self._apply_form()  # 如果 settings 里存的是 live2d:xxx, 启动就切过去

    def set_activity_tracker(self, activity: ActivityTracker):
        """main 注入,走动判定用 activity.is_idle()"""
        self._activity = activity

    # ---------- 角色区域比例属性 (随 widget size 等比缩放) ----------
    @property
    def char_w(self) -> int:
        return int(self.width() * CHAR_W_RATIO)

    @property
    def char_h(self) -> int:
        return int(self.height() * CHAR_H_RATIO)

    @property
    def char_offset_y(self) -> int:
        return int(self.height() * CHAR_OFFSET_Y_RATIO)

    def _setup_window(self):
        self.setWindowTitle("Foamo")
        # 透明属性必须在 setWindowFlags 之前生效
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        # 不再 setFixedSize — 桌宠可调大小. 用 min/max 做安全边界
        self.setMinimumSize(MIN_W, MIN_H)
        self.setMaximumSize(MAX_W, MAX_H)
        self.resize(DEFAULT_W, DEFAULT_H)  # 默认 size, _load_position 后会按存的覆盖

    def _sync_gif_scaling(self):
        """同步 gif_label geometry + 全部 movies 的 scaledSize, 并强制 reload 当前 movie.

        QMovie.setScaledSize 在正在播放的 movie 上不会立即生效 — Qt 的 GIF reader
        要等下一帧解码循环才应用. 所以 size 变了就 stop+start 强制 reload.

        resizeEvent 调一次; _switch_form 切回 GIF 时也调一次 (因为可能 widget 尺寸
        没变, resize 不触发 event 但 movies 上次设的 scaledSize 还是 live2d 模式下的).
        """
        target = QSize(self.char_w, self.char_h)
        self.gif_label.setGeometry(
            (self.width() - target.width()) // 2,
            self.char_offset_y,
            target.width(), target.height(),
        )
        prev_size = getattr(self, '_last_movie_scaled_size', None)
        size_changed = prev_size != target
        for movie in self.movies.values():
            movie.setScaledSize(target)
        self._last_movie_scaled_size = target
        if size_changed and self.current_movie is not None:
            if self.current_movie.state() == QMovie.MovieState.Running:
                self.current_movie.stop()
                self.current_movie.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_gif_scaling()
        # Live2D canvas 占满整个 widget — 没有 GIF 模式的光晕/状态条边框
        # (受限到 char_w x char_h 反而显得局促, 跟透明边框冲突)
        live2d_canvas = getattr(self, '_live2d_canvas', None)
        if live2d_canvas is not None:
            live2d_canvas.setGeometry(0, 0, self.width(), self.height())

    def _set_size(self, w: int, h: int):
        """设定 widget 大小并持久化"""
        w = max(MIN_W, min(MAX_W, w))
        h = max(MIN_H, min(MAX_H, h))
        self.resize(w, h)
        self._save_position()
        self.update()

    def _set_size_custom(self):
        """弹输入对话框让用户填正方形边长"""
        from PyQt6.QtWidgets import QInputDialog
        cur = self.width()
        size, ok = QInputDialog.getInt(
            self, "桌宠大小",
            f"宽=高 ({MIN_W} ~ {MAX_W} px):",
            cur, MIN_W, MAX_W
        )
        if ok:
            self._set_size(size, size)

    # ---------- 形态切换 (GIF / Live2D) ----------
    def _is_live2d_mode(self) -> bool:
        return isinstance(self._form, str) and self._form.startswith("live2d:")

    def _apply_form(self):
        """按 self._form 切换 GIF/Live2D 渲染层. _live2d_canvas 懒加载."""
        if self._is_live2d_mode():
            name = self._form.split(":", 1)[1]
            models = {m["name"]: m for m in scan_live2d_models(ROOT)}
            if name not in models:
                print(f"[form] live2d 模型未找到: {name}, 退回 gif")
                self._form = "gif"
                self._apply_form()
                return
            model_path = models[name]["path"]
            self.gif_label.hide()
            if self._live2d_canvas is None:
                try:
                    from live2d_canvas import Live2DCanvas
                except Exception as e:
                    print(f"[form] live2d_canvas 加载失败: {e}, 退回 gif")
                    self._form = "gif"
                    self.gif_label.show()
                    return
                self._live2d_canvas = Live2DCanvas(model_path, parent=self)
                # Live2D 占满整个 widget — SDK 会按模型 canvas size 自动居中缩放
                self._live2d_canvas.setGeometry(0, 0, self.width(), self.height())
            else:
                self._live2d_canvas.switch_model(model_path)
            self._live2d_canvas.show()
            self._live2d_canvas.start()
            # GL 初始化 + _scan_outfit_parts 在 paintGL 第一次后才完成,
            # 延后 400ms 加载持久化的穿搭 (此时白名单已扫好)
            QTimer.singleShot(400, self._load_outfit_after_init)
        else:
            # GIF 模式
            if self._live2d_canvas is not None:
                self._live2d_canvas.stop()
                self._live2d_canvas.hide()
            self.gif_label.show()
            self._apply_movie(self.state)  # 重挂 movie 防止丢

    def _switch_form(self, form: str):
        prev_form = self._form
        self._form = form
        # 切回 GIF: 强制正方形. 因为 GIF 用 CHAR_W_RATIO/CHAR_H_RATIO 算 scaled size,
        # 二者比例不同 (0.643 / 0.714), 在竖长 widget 下 GIF 会被拉伸.
        # live2d 模式下豆哥常把窗口拉成竖长配合立绘, 切回 GIF 要恢复正方形.
        if form == "gif" and prev_form != "gif":
            side = max(MIN_W, min(MAX_W, self.width()))
            if self.height() != side:
                self.resize(side, side)
        self._save_position()
        self._apply_form()
        # 即使 widget 尺寸没变也要同步一次 GIF — Live2D 模式下 _last_movie_scaled_size
        # 可能是旧的 (上次 resize 时按 live2d 巨大尺寸算的), 现在要按当前 GIF 模式重置
        if form == "gif":
            self._sync_gif_scaling()

    def showEvent(self, event):
        super().showEvent(event)
        self._kill_win11_backdrop()
        # setWindowFlags + DWM 调用会重建 native handle,
        # 之前挂在 QLabel 上的 movie 关联会被冲掉, 这里强制重挂。
        self._apply_movie(self.state)

    def _kill_win11_backdrop(self):
        """Win11 默认给顶层窗口铺 Mica/Acrylic 背景,会把 Qt 的透明压住。
        用 DWM API 关掉 backdrop + 圆角即可,千万别动 NCRendering(会把客户区一起刷没)。"""
        if sys.platform != 'win32':
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            dwmapi = ctypes.windll.dwmapi

            # 关 backdrop (Mica/Acrylic/Tabbed)
            DWMWA_SYSTEMBACKDROP_TYPE = 38
            DWMSBT_NONE = 1
            val = ctypes.c_int(DWMSBT_NONE)
            dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_SYSTEMBACKDROP_TYPE,
                ctypes.byref(val), ctypes.sizeof(val)
            )

            # 关 Win11 默认圆角
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_DONOTROUND = 1
            val2 = ctypes.c_int(DWMWCP_DONOTROUND)
            dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(val2), ctypes.sizeof(val2)
            )
        except Exception as e:
            print(f"[backdrop] disable failed: {e}")

    def _load_movies(self):
        """加载所有状态对应的 GIF;支持 .gif / .png 后备。
        额外姿势 (EXTRA_POSES) 是可选的, 找不到就跳过, 不报错。"""
        # 1) 主状态: 必须存在 (找不到 print warn)
        for state in STATES:
            self._try_load_one(state, warn_if_missing=True)
        # 2) 额外姿势: 可选, 找到就启用, 找不到 fallback 到主状态
        for pose in EXTRA_POSES:
            self._try_load_one(pose, warn_if_missing=False)

        if not self.movies:
            print(f"[assets] no images found in {ASSETS_DIR}")

    def _try_load_one(self, name: str, warn_if_missing: bool):
        gif_path = ASSETS_DIR / f'{name}.gif'
        png_path = ASSETS_DIR / f'{name}.png'

        if gif_path.exists():
            movie = QMovie(str(gif_path))
            movie.setScaledSize(QSize(self.char_w, self.char_h))
            movie.setCacheMode(QMovie.CacheMode.CacheAll)
            self.movies[name] = movie
        elif png_path.exists():
            movie = QMovie(str(png_path))
            movie.setScaledSize(QSize(self.char_w, self.char_h))
            self.movies[name] = movie
        elif warn_if_missing:
            print(f"[assets] missing: {name}.gif / {name}.png")

    def _reload_movies(self):
        """assets 目录变化时,重新加载当前状态的 GIF"""
        print("[assets] reloading...")
        # 停掉当前播放
        if self.current_movie:
            self.current_movie.stop()
        self.movies.clear()
        self._load_movies()
        self._apply_movie(self.state)

    def _apply_movie(self, state: str):
        """切换显示的 GIF。pose (如 walk_left/peek_right/shake) 优先于 state.
        Live2D 模式下: 切表情 / 触发同名 motion group (默认随机表情)."""
        if self._is_live2d_mode():
            if self._live2d_canvas is not None:
                # pose 优先 (拖动/扒边/走路), 没 pose 用 state
                key = self.pose or state
                self._live2d_canvas.apply_state(key)
            return
        if self.current_movie:
            self.current_movie.stop()

        # pose 优先, 找不到 pose GIF 自动 fallback 到 state
        name = self.pose or state
        movie = self.movies.get(name)
        if movie is None and self.pose:
            # 有 pose 但没对应 GIF — 退回 state
            movie = self.movies.get(state)
        if movie is None:
            movie = self.movies.get('idle') or self.movies.get('tender')

        if movie:
            self.gif_label.setMovie(movie)
            movie.start()
            self.current_movie = movie

    def _set_pose(self, pose: Optional[str]):
        """设置临时姿势; None = 清除回 state. 切换 GIF (找不到对应 GIF 自动 fallback)"""
        if self.pose == pose:
            return
        self.pose = pose
        self._apply_movie(self.state)

    def set_state(self, state: str, line: str):
        prev = self.state
        self.state = state
        self.line = line
        self.line_show_until = time.time() + 6.0
        # current_movie is None: 初次或 native handle 被重建后丢了关联,得重挂
        if prev != state or self.current_movie is None:
            self._apply_movie(state)
        self.update()
        # 状态真切了才通知聊天面板 (避免 idle → idle 等无效信号)
        if prev != state:
            try: self.pet_state_changed.emit(state)
            except Exception: pass

    def _on_pet_request_from_chat(self, state: str, line: str):
        """聊天面板 monitor 请求桌宠切状态. 绕过 StateManager 频控直接切"""
        self.set_state(state, line)

    # ---------- 位置 / 大小 / 形态 存储 ----------
    def _load_position(self):
        s = QSettings(ORG_NAME, APP_NAME)
        # 先恢复 size (再调位置, 因为位置是基于 size 算屏幕外的)
        if s.contains("widget_w") and s.contains("widget_h"):
            try:
                w = max(MIN_W, min(MAX_W, int(s.value("widget_w", type=int))))
                h = max(MIN_H, min(MAX_H, int(s.value("widget_h", type=int))))
                self.resize(w, h)
            except Exception: pass
        # 形态 (gif / live2d:<name>)
        form = s.value("form", "gif", type=str)
        if isinstance(form, str) and form:
            self._form = form
        if s.contains("pos_x") and s.contains("pos_y"):
            x = s.value("pos_x", type=int)
            y = s.value("pos_y", type=int)
            if self._is_position_visible(x, y):
                self.move(x, y)
                return
        # 没存过 / 存的位置已在屏幕外 (换屏、副屏拔掉、peek 残留): 回默认右下角
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 30, screen.bottom() - self.height() - 50)

    def _is_position_visible(self, x: int, y: int) -> bool:
        """窗口矩形和任一屏幕的可视区域相交 = 可见。
        peek 状态故意把半个身体放屏外, 所以只要有交集就算 OK。"""
        from PyQt6.QtCore import QRect
        win_rect = QRect(x, y, self.width(), self.height())
        for screen in QGuiApplication.screens():
            if screen.availableGeometry().intersects(win_rect):
                return True
        return False

    def _save_position(self):
        s = QSettings(ORG_NAME, APP_NAME)
        s.setValue("pos_x", self.x())
        s.setValue("pos_y", self.y())
        s.setValue("widget_w", self.width())
        s.setValue("widget_h", self.height())
        s.setValue("form", self._form)

    def closeEvent(self, event):
        # 摄像头先关掉, 否则线程不退出主进程会挂
        try:
            self._stop_face_tracking()
        except Exception: pass
        self._save_position()
        super().closeEvent(event)

    # ---------- 摄像头面捕 ----------
    def _start_face_tracking(self):
        if self._face_tracker is not None:
            return
        if not self._is_live2d_mode() or self._live2d_canvas is None:
            self._show_line("面捕只在 Live2D 形态下能用哦", 4.0)
            return
        try:
            from face_tracker import FaceTracker, MODEL_PATH
        except Exception as e:
            self._show_line(f"face_tracker 导入失败: {e}", 4.0)
            return
        if not MODEL_PATH.exists():
            self._show_line(f"模型缺失: {MODEL_PATH.name}", 5.0)
            return
        tracker = FaceTracker(parent=self)
        canvas = self._live2d_canvas
        tracker.params_ready.connect(canvas.apply_face_params)
        tracker.error.connect(self._on_face_error)
        tracker.started_ok.connect(
            lambda: self._show_line("📷 摄像头开了, 看着豆哥呢", 3.0)
        )
        canvas.set_face_tracking(True)
        self._face_tracker = tracker
        tracker.start()
        self.update()  # 触发红点指示

    def _stop_face_tracking(self):
        tracker = self._face_tracker
        if tracker is None:
            return
        self._face_tracker = None
        try:
            tracker.stop()
            tracker.wait(2000)  # 给 2 秒收尾
        except Exception: pass
        canvas = self._live2d_canvas
        if canvas is not None:
            try: canvas.set_face_tracking(False)
            except Exception: pass
        self.update()  # 取消红点

    def _on_face_error(self, msg: str):
        print(f"[face] error: {msg}", flush=True)
        self._show_line(f"摄像头出问题: {msg}", 5.0)
        self._stop_face_tracking()

    def _toggle_face_tracking(self):
        if self._face_tracker is None:
            self._start_face_tracking()
        else:
            self._stop_face_tracking()

    # ---------- 穿搭 ----------
    def _toggle_outfit_mode(self):
        canvas = self._live2d_canvas
        if canvas is None: return
        if canvas.is_outfit_mode():
            # 关 = 自动保存当前穿搭
            canvas.set_outfit_mode(False)
            self._save_outfit()
            self._show_line("👗 穿搭存好啦", 3.0)
        else:
            canvas.set_outfit_mode(True)
            self._show_line("👗 穿搭模式开了, 点身上看看", 4.0)

    def _outfit_settings_key(self) -> str | None:
        """穿搭按形态独立存. v2 用 group 聚合, 不兼容 v1 旧数据."""
        if not self._is_live2d_mode(): return None
        name = self._form.split(":", 1)[1] if ":" in self._form else self._form
        return f"outfit_v2/{name}"

    def _save_outfit(self):
        canvas = self._live2d_canvas
        key = self._outfit_settings_key()
        if canvas is None or key is None: return
        try:
            state = canvas.get_outfit_state()
            QSettings(ORG_NAME, APP_NAME).setValue(key, json.dumps(state))
        except Exception as e:
            print(f"[outfit] save failed: {e}", flush=True)

    def _load_outfit_after_init(self):
        """canvas + 模型 GL 初始化好后调. 从 QSettings 读 group 状态并 apply."""
        canvas = self._live2d_canvas
        key = self._outfit_settings_key()
        if canvas is None or key is None: return
        s = QSettings(ORG_NAME, APP_NAME).value(key, "", type=str)
        if not s: return
        try:
            state = json.loads(s)
            if not isinstance(state, dict): return
            canvas.makeCurrent()
            try: canvas.apply_outfit_state(state)
            finally: canvas.doneCurrent()
            canvas.update()
        except Exception as e:
            print(f"[outfit] load failed: {e}", flush=True)

    def _reset_outfit(self):
        """穿回默认全身装 + 清掉持久化."""
        canvas = self._live2d_canvas
        if canvas is None: return
        try:
            canvas.makeCurrent()
            try: canvas.reset_outfit()
            finally: canvas.doneCurrent()
            canvas.update()
        except Exception: pass
        # 清掉存档
        key = self._outfit_settings_key()
        if key:
            QSettings(ORG_NAME, APP_NAME).remove(key)
        self._show_line("👗 穿回默认啦", 3.0)

    # ---------- tick ----------
    def _tick(self):
        self.glow_phase += 0.033
        now = time.time()

        if now < self.line_show_until:
            self.line_alpha = min(1.0, self.line_alpha + 0.08)
        else:
            self.line_alpha = max(0.0, self.line_alpha - 0.05)

        # 拖动时让 gif_label 抖动 (没有 shake.gif 时也能感觉到反应)
        if self._dragging:
            phase = (now - self._drag_shake_seed) * 30  # ~30Hz 视觉抖动
            shake_x = math.sin(phase) * 3
            shake_y = math.cos(phase * 1.3) * 2
            base_x = (self.width() - self.char_w) // 2
            base_y = self.char_offset_y
            self.gif_label.move(int(base_x + shake_x), int(base_y + shake_y))

        # Live2D 鼠标跟随: 模型眼神/头跟着鼠标. 超出跟随范围回中, 不让眼睛瞪到天上去.
        # 面捕开启时让位 — 鼠标跟随和摄像头驱头会打架
        if (self._is_live2d_mode() and self._live2d_canvas is not None
                and self._face_tracker is None):
            self._update_live2d_gaze()

        self.update()

    def _update_live2d_gaze(self):
        """把鼠标位置映射到模型的视线方向. 全屏跟随, 不回中.
        - 用 (鼠标 - canvas 中心) 偏移, 按桌宠所在屏幕的尺寸归一化到 [-1, 1]
        - 再映射回 canvas 像素坐标 (Cubism Drag 接受像素), 超出 [-1,1] clamp 到极限
        - 鼠标贴屏幕边 → 模型脖子转到最大; 鼠标越靠近桌宠中心 → 越直视
        """
        canvas = self._live2d_canvas
        w, h = canvas.width(), canvas.height()
        center_g = canvas.mapToGlobal(QPoint(w // 2, h // 2))
        cur = QCursor.pos()
        dx = cur.x() - center_g.x()
        dy = cur.y() - center_g.y()
        # 用桌宠所在屏幕做归一化范围 (多屏友好)
        screen = QGuiApplication.screenAt(center_g) or QGuiApplication.primaryScreen()
        geom = screen.geometry()
        range_x = max(geom.width() // 2, w)
        range_y = max(geom.height() // 2, h)
        nx = max(-1.0, min(1.0, dx / range_x))
        ny = max(-1.0, min(1.0, dy / range_y))
        canvas.drag_to((nx + 1) * w / 2, (ny + 1) * h / 2)

    # ============================================================
    # 走动 (沿屏幕底边随机踱步, 仅长空闲 + 非深夜 + idle/tender 触发)
    # ============================================================
    def _schedule_next_walk_check(self):
        delay_ms = random.randint(WALK_INTERVAL_MIN_S, WALK_INTERVAL_MAX_S) * 1000
        self.walk_timer.start(delay_ms)

    def _can_walk(self) -> bool:
        if self._dragging or self._is_peeking:
            return False
        if self._walk_anim is not None:
            return False
        if is_late_night():
            return False
        if self.state not in ('idle', 'tender'):
            return False
        if self._activity is None or not self._activity.is_idle():
            return False
        return True

    def _maybe_walk(self):
        # 单次触发, 重新调度下一次
        try:
            if not self._can_walk():
                return
            self._start_walk()
        finally:
            self._schedule_next_walk_check()

    def _start_walk(self):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        cur = self.pos()
        # 选方向: 离边远的方向更可能被选
        space_left = cur.x() - screen.left()
        space_right = screen.right() - (cur.x() + self.width())
        if space_left < 50:
            direction = 1
        elif space_right < 50:
            direction = -1
        else:
            direction = random.choice([-1, 1])

        distance = random.randint(WALK_DISTANCE_MIN, WALK_DISTANCE_MAX)
        target_x = cur.x() + direction * distance
        target_x = max(screen.left() + 5,
                       min(screen.right() - self.width() - 5, target_x))
        target = QPoint(target_x, cur.y())

        # 切到走路 pose (没对应 GIF 自动 fallback 到 idle, 仅靠位移也能看出"走")
        self._set_pose('walk_left' if direction < 0 else 'walk_right')

        anim = QPropertyAnimation(self, b'pos', self)
        anim.setDuration(WALK_DURATION_MS)
        anim.setStartValue(cur)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        anim.finished.connect(self._end_walk)
        self._walk_anim = anim
        anim.start()

    def _end_walk(self):
        self._walk_anim = None
        self._set_pose(None)
        self._save_position()

    def _stop_walk(self):
        if self._walk_anim is not None:
            self._walk_anim.stop()
            self._walk_anim = None
            self._set_pose(None)

    # ---------- 鼠标 ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            # 记录起点 — 用于区分"拖动" vs "单击" (单击交给穿搭模式切 part)
            self._press_local = e.position()
            # 按下时先打断走动
            self._stop_walk()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            # 进入拖动: 切 shake pose + 冒一句台词
            if not self._dragging:
                self._dragging = True
                self._drag_shake_seed = time.time()
                self._set_pose('shake')
                self._show_line(random.choice(DRAG_LINES), 4.0)
            e.accept()

    def mouseReleaseEvent(self, e):
        was_dragging = self._dragging
        # 单击检测: 没拖过 + 位移 < 5px (避免手抖触发)
        is_click = False
        press = getattr(self, '_press_local', None)
        if not was_dragging and press is not None:
            dx = e.position().x() - press.x()
            dy = e.position().y() - press.y()
            if dx * dx + dy * dy < 25:
                is_click = True
        self._drag_pos = None
        self._dragging = False
        self._save_position()
        # 抖动结束: 把 gif_label 复位
        self._reset_gif_label_position()
        # 检查是否扒到了屏幕边
        if was_dragging:
            self._check_peek()
        # 不在扒边状态就清除 pose
        if not self._is_peeking:
            self._set_pose(None)
        # 单击 + Live2D 穿搭模式 → 切 part 显隐
        if (is_click and self._is_live2d_mode()
                and self._live2d_canvas is not None
                and self._live2d_canvas.is_outfit_mode()):
            canvas = self._live2d_canvas
            local = canvas.mapFrom(self, e.position().toPoint())
            name = canvas.try_toggle_part_at(local.x(), local.y())
            if name:
                self._show_line(f"切了「{name}」", 2.5)
        e.accept()

    def _show_line(self, line: str, duration: float = 5.0):
        """主动冒一个台词气泡 (本地, 不走状态机)"""
        self.line = line
        self.line_show_until = time.time() + duration
        self.update()

    def _reset_gif_label_position(self):
        """抖动 / 扒边偏移完毕后, 把 gif_label 还原到中心位置"""
        self.gif_label.move(
            (self.width() - self.char_w) // 2,
            self.char_offset_y,
        )

    # ---------- 扒边 ----------
    def _check_peek(self):
        """松手时如果窗口贴近屏幕边, 把窗口推半个身体到屏幕外, 切 peek pose。
        多屏: 用窗口当前所在的屏幕做边界, 不是 primary screen,
        否则在扩展屏上松手会被误判贴 primary 边而弹回。"""
        center = QPoint(self.x() + self.width() // 2, self.y() + self.height() // 2)
        cur_screen = QGuiApplication.screenAt(center)
        if cur_screen is None:
            # 不在任何屏幕中 (比如部分越界)... 用 primary 兜底
            cur_screen = QGuiApplication.primaryScreen()
        screen = cur_screen.availableGeometry()
        offscreen = int(self.width() * PEEK_OFFSCREEN_RATIO)

        # 左边
        if self.x() < screen.left() + PEEK_SNAP_THRESHOLD:
            self.move(screen.left() - offscreen, self.y())
            self._is_peeking = True
            self._set_pose('peek_right')
            self._show_line(random.choice(PEEK_LINES_LEFT), 5.0)
            self._save_position()
            return
        # 右边
        if self.x() + self.width() > screen.right() - PEEK_SNAP_THRESHOLD:
            self.move(screen.right() - self.width() + offscreen, self.y())
            self._is_peeking = True
            self._set_pose('peek_left')
            self._show_line(random.choice(PEEK_LINES_RIGHT), 5.0)
            self._save_position()
            return
        # 没贴边: 清除扒边状态
        if self._is_peeking:
            self._is_peeking = False

    def mouseDoubleClickEvent(self, e):
        # 双击 = 开/关聊天框
        if e.button() == Qt.MouseButton.LeftButton:
            self.open_chat()
        e.accept()

    _MENU_QSS = """
        QMenu { background: #1f1f3a; color: #ede5dd;
                border: 1px solid #3a3a5a; padding: 4px; }
        QMenu::item { padding: 6px 18px; border-radius: 4px; }
        QMenu::item:selected { background: #3a3a5a; }
        QMenu::separator { height: 1px; background: #3a3a5a; margin: 4px 0; }
    """

    def build_context_menu(self, menu: QMenu) -> None:
        """填充 menu (in place) — 桌宠右键 + 托盘右键共用同一来源.
        每次右键前重新 build (clear + 重填), 状态文字自动跟实时数据."""
        menu.clear()
        menu.setStyleSheet(self._MENU_QSS)

        # 顶部: 显示 / 隐藏 (动态文字, 跟当前可见状态走)
        if self.isVisible():
            show_hide = QAction("🙈 隐藏窗口", menu)
            show_hide.triggered.connect(self.hide)
        else:
            show_hide = QAction("👀 显示泡沫", menu)
            show_hide.triggered.connect(self._show_and_raise)
        menu.addAction(show_hide)
        menu.addSeparator()

        # 和泡沫聊天
        chat_act = QAction("和泡沫聊天 💬", menu)
        chat_act.triggered.connect(self.open_chat)
        menu.addAction(chat_act)
        menu.addSeparator()

        # 番茄钟
        if self.pomodoro is not None:
            if self.pomodoro.is_running:
                m = self.pomodoro.remaining_s // 60
                s = self.pomodoro.remaining_s % 60
                cancel_act = QAction(f"取消番茄钟 (剩 {m:02d}:{s:02d})", menu)
                cancel_act.triggered.connect(self.pomodoro.cancel)
                menu.addAction(cancel_act)
            else:
                start_act = QAction("开始番茄钟 (25 分钟)", menu)
                start_act.triggered.connect(self.pomodoro.start)
                menu.addAction(start_act)
            menu.addSeparator()

        # 本周功劳簿
        if self.weekly_reporter is not None:
            report_act = QAction("看看本周功劳簿", menu)
            report_act.triggered.connect(self.weekly_reporter.fire_now)
            menu.addAction(report_act)
            menu.addSeparator()

        # 手动切状态
        for st in STATES:
            label = STATE_LABELS.get(st, st)
            act = QAction(f"切到 {label}", menu)
            act.triggered.connect(lambda _, s=st: self._manual_state(s))
            menu.addAction(act)

        menu.addSeparator()

        # 形态子菜单 (GIF / Live2D)
        form_menu = QMenu("形态", menu)
        form_menu.setStyleSheet(self._MENU_QSS)
        cur_form = self._form
        gif_act = QAction(
            ("● " if cur_form == "gif" else "  ") + "🎞 GIF (默认)",
            form_menu,
        )
        gif_act.triggered.connect(lambda: self._switch_form("gif"))
        form_menu.addAction(gif_act)
        models = scan_live2d_models(ROOT)
        if models:
            form_menu.addSeparator()
            for m in models:
                key = f"live2d:{m['name']}"
                lbl = ("● " if cur_form == key else "  ") + f"🎀 {m['name']}"
                act = QAction(lbl, form_menu)
                act.triggered.connect(lambda _, k=key: self._switch_form(k))
                form_menu.addAction(act)
        else:
            no_act = QAction("(没找到 live2d 模型)", form_menu)
            no_act.setEnabled(False)
            form_menu.addAction(no_act)
        menu.addMenu(form_menu)

        # 表情 / Motion 子菜单 (仅 Live2D 模式有意义)
        if self._is_live2d_mode() and self._live2d_canvas is not None:
            exp_menu = QMenu("表情 / 动作", menu)
            exp_menu.setStyleSheet(self._MENU_QSS)
            ids = self._live2d_canvas.get_expression_ids()
            if ids:
                for eid in ids:
                    act = QAction(f"😀 {eid}", exp_menu)
                    act.triggered.connect(
                        lambda _, e=eid: self._live2d_canvas.set_expression(e)
                    )
                    exp_menu.addAction(act)
                exp_menu.addSeparator()
                rand_act = QAction("🎲 随机表情", exp_menu)
                rand_act.triggered.connect(
                    lambda: self._live2d_canvas.set_expression(None)
                )
                exp_menu.addAction(rand_act)
                reset_act = QAction("♻ 重置", exp_menu)
                reset_act.triggered.connect(self._live2d_canvas.reset_expression)
                exp_menu.addAction(reset_act)
            else:
                no_exp = QAction("(模型没声明 expression)", exp_menu)
                no_exp.setEnabled(False)
                exp_menu.addAction(no_exp)
            groups = self._live2d_canvas.get_motion_groups()
            if groups:
                exp_menu.addSeparator()
                for g in groups:
                    mact = QAction(f"🎬 {g}", exp_menu)
                    mact.triggered.connect(
                        lambda _, gg=g: self._live2d_canvas.start_motion(gg)
                    )
                    exp_menu.addAction(mact)
            menu.addMenu(exp_menu)

            # 摄像头面捕
            face_on = self._face_tracker is not None
            face_act = QAction(
                ("● 关闭摄像头面捕" if face_on else "📷 开启摄像头面捕"),
                menu,
            )
            face_act.triggered.connect(self._toggle_face_tracking)
            menu.addAction(face_act)

            # 穿搭模式
            outfit_on = self._live2d_canvas.is_outfit_mode()
            outfit_act = QAction(
                ("✓ 退出穿搭 (保存当前)" if outfit_on else "👗 进入穿搭模式"),
                menu,
            )
            outfit_act.triggered.connect(self._toggle_outfit_mode)
            menu.addAction(outfit_act)
            reset_outfit_act = QAction("♻ 重置默认穿搭", menu)
            reset_outfit_act.triggered.connect(self._reset_outfit)
            menu.addAction(reset_outfit_act)

        # 大小子菜单
        size_menu = QMenu("大小", menu)
        size_menu.setStyleSheet(self._MENU_QSS)
        cur = self.width()
        for label, w in [
            ("小  (200)",  200),
            ("中  (280) — 默认", 280),
            ("大  (360)",  360),
            ("特大 (480)", 480),
            ("巨大 (640)", 640),
        ]:
            txt = ("● " if w == cur else "  ") + label
            act = QAction(txt, size_menu)
            act.triggered.connect(lambda _, ww=w: self._set_size(ww, ww))
            size_menu.addAction(act)
        size_menu.addSeparator()
        custom_act = QAction("自定义...", size_menu)
        custom_act.triggered.connect(self._set_size_custom)
        size_menu.addAction(custom_act)
        menu.addMenu(size_menu)

        toggle_top = QAction("切换置顶", menu)
        toggle_top.triggered.connect(self._toggle_topmost)
        menu.addAction(toggle_top)

        menu.addSeparator()

        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_action)

    def _show_and_raise(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        self.build_context_menu(menu)
        menu.exec(e.globalPos())

    def _manual_state(self, state):
        line = random.choice(LINES.get(state, LINES['tender']))
        self.set_state(state, line)

    def _toggle_topmost(self):
        flags = self.windowFlags()
        if flags & Qt.WindowType.WindowStaysOnTopHint:
            self.setWindowFlags(flags & ~Qt.WindowType.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint)
        self.show()

    def open_chat(self):
        """切换聊天面板 (独立窗口, 懒加载, 不再贴着桌宠跟随)"""
        if self._chat_window is None:
            try:
                from chat_window import ChatWindow
            except Exception as e:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "聊天框启不来", f"加载 chat_window 失败:\n{e}")
                return
            self._chat_window = ChatWindow()
        self._chat_window.toggle()

    # ============================================================
    # 绘制 (光晕 + 状态条 + 台词气泡)
    # ============================================================
    def paintEvent(self, event):
        # Live2D 模式: 模型本身就是角色, 不画 GIF 模式那套光晕/状态条/项目名/台词气泡
        # (这些装饰跟立绘视觉冲突). FoamoWidget 完全透明, 只透出底下 Live2DCanvas.
        # 但面捕开启时画一个红点指示, 提醒豆哥摄像头是开着的.
        if self._is_live2d_mode():
            if self._face_tracker is not None:
                p = QPainter(self)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(QColor(255, 60, 60, 220)))
                p.drawEllipse(QRectF(self.width() - 18, 6, 10, 10))
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        self._draw_glow(p)
        self._draw_state_label(p)
        self._draw_project_label(p)
        self._draw_speech_bubble(p)
        self._draw_no_asset_hint(p)

    def _draw_glow(self, p: QPainter):
        """角色脚下/身后的光晕,颜色随状态变,呼吸感"""
        c1, c2 = STATE_COLORS.get(self.state, STATE_COLORS['idle'])
        breath = 0.5 + 0.5 * abs((self.glow_phase * 0.4) % 2 - 1)  # 0..1..0
        intensity = 0.6 + breath * 0.4

        # 角色身后大光晕 (中心在角色中下)
        cx = self.width() / 2
        cy = self.char_offset_y + self.char_h * 0.65
        radius = 130 + breath * 20

        grad = QRadialGradient(cx, cy, radius)
        c_inner = QColor(c1)
        c_inner.setAlpha(int(70 * intensity))
        c_outer = QColor(c1)
        c_outer.setAlpha(0)
        grad.setColorAt(0.0, c_inner)
        grad.setColorAt(0.5, c_inner)
        grad.setColorAt(1.0, c_outer)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

        # 角色脚下椭圆光环
        floor_y = self.char_offset_y + self.char_h - 5
        floor_w = 100 + breath * 15
        floor_h = 18

        floor_grad = QRadialGradient(cx, floor_y, floor_w)
        fc_in = QColor(c1)
        fc_in.setAlpha(int(120 * intensity))
        fc_out = QColor(c1)
        fc_out.setAlpha(0)
        floor_grad.setColorAt(0.0, fc_in)
        floor_grad.setColorAt(1.0, fc_out)
        p.setBrush(QBrush(floor_grad))
        p.drawEllipse(QRectF(cx - floor_w, floor_y - floor_h / 2,
                             floor_w * 2, floor_h))

    def _draw_state_label(self, p: QPainter):
        c1, _ = STATE_COLORS.get(self.state, STATE_COLORS['idle'])
        label = STATE_LABELS.get(self.state, '~')

        rect_w, rect_h = 76, 22
        rect_x = (self.width() - rect_w) // 2
        rect_y = 4

        path = QPainterPath()
        path.addRoundedRect(QRectF(rect_x, rect_y, rect_w, rect_h), 11, 11)

        bg = QColor(20, 20, 42, 180)
        p.fillPath(path, bg)
        border = QColor(c1)
        border.setAlpha(180)
        p.setPen(QPen(border, 1))
        p.drawPath(path)

        p.setPen(QColor(c1))
        font = QFont("Microsoft YaHei", 9)
        font.setWeight(QFont.Weight.Medium)
        p.setFont(font)
        p.drawText(QRectF(rect_x, rect_y, rect_w, rect_h),
                   Qt.AlignmentFlag.AlignCenter, label)

    def _draw_project_label(self, p: QPainter):
        """状态标签下方的小字。优先级:
        1) 番茄钟运行中 → 红橙色倒计时 'mm:ss · pomodoro'
        2) 否则有项目名 → 灰色 '在 xxx'
        """
        # ---- 番茄钟优先 ----
        if self.pomodoro_remaining > 0:
            m = self.pomodoro_remaining // 60
            s = self.pomodoro_remaining % 60
            text = f"🍅  {m:02d}:{s:02d}"
            font = QFont("Microsoft YaHei", 8)
            font.setWeight(QFont.Weight.Bold)
            col = QColor(255, 140, 100, 230)
            p.setFont(font)
            fm = QFontMetrics(font)
            text_w = fm.horizontalAdvance(text)
            text_y = 4 + 22 + 2
            p.setPen(col)
            p.drawText(QRectF((self.width() - text_w) // 2, text_y, text_w, fm.height()),
                       Qt.AlignmentFlag.AlignCenter, text)
            return

        # ---- 项目名 ----
        if self.project_id is None:
            return
        name = self.project_id.name()
        if not name:
            return

        font = QFont("Microsoft YaHei", 7)
        p.setFont(font)
        fm = QFontMetrics(font)
        text = f"在 {name}"
        max_w = self.width() - 30
        if fm.horizontalAdvance(text) > max_w:
            while name and fm.horizontalAdvance(f"在 {name}…") > max_w:
                name = name[:-1]
            text = f"在 {name}…"

        text_w = fm.horizontalAdvance(text)
        text_y = 4 + 22 + 2
        col = QColor(200, 195, 215, 170)
        p.setPen(col)
        p.drawText(QRectF((self.width() - text_w) // 2, text_y, text_w, fm.height()),
                   Qt.AlignmentFlag.AlignCenter, text)

    def _draw_speech_bubble(self, p: QPainter):
        if self.line_alpha < 0.05 or not self.line:
            return

        text = self.line
        font = QFont("Microsoft YaHei", 10)
        p.setFont(font)
        fm = QFontMetrics(font)

        pad_x, pad_y = 14, 7
        max_text_w = self.width() - 40
        text_w = min(fm.horizontalAdvance(text), max_text_w)
        text_h = fm.height()
        bw = text_w + pad_x * 2
        bh = text_h + pad_y * 2

        bx = (self.width() - bw) // 2
        by = self.height() - bh - 14  # 距底部留点边

        c1, _ = STATE_COLORS.get(self.state, STATE_COLORS['idle'])
        alpha = self.line_alpha

        # 气泡背景
        path = QPainterPath()
        path.addRoundedRect(QRectF(bx, by, bw, bh), bh / 2, bh / 2)

        bg = QColor(20, 20, 42)
        bg.setAlphaF(0.85 * alpha)
        p.fillPath(path, bg)

        border = QColor(c1)
        border.setAlphaF(0.7 * alpha)
        p.setPen(QPen(border, 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        # 上方小尖角(指向角色)
        arrow_size = 6
        arrow = QPolygonF([
            QPointF(bx + bw / 2 - arrow_size, by + 1),
            QPointF(bx + bw / 2, by - arrow_size + 1),
            QPointF(bx + bw / 2 + arrow_size, by + 1),
        ])
        bg2 = QColor(20, 20, 42)
        bg2.setAlphaF(0.85 * alpha)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(bg2))
        p.drawPolygon(arrow)

        # 文字
        text_color = QColor(237, 229, 221)
        text_color.setAlphaF(alpha)
        p.setPen(text_color)
        p.drawText(QRectF(bx, by, bw, bh),
                   Qt.AlignmentFlag.AlignCenter, text)

    def _draw_no_asset_hint(self, p: QPainter):
        """如果没加载到任何 GIF, 在角色区域画个提示"""
        if self.movies:
            return

        c1, _ = STATE_COLORS.get(self.state, STATE_COLORS['idle'])
        font = QFont("Microsoft YaHei", 9)
        p.setFont(font)
        p.setPen(QColor(c1))
        rect = QRectF(20, self.char_offset_y, self.width() - 40, self.char_h)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                   f"未找到角色图\n请把 GIF 放到\n{ASSETS_DIR.name}/ 目录\n(idle.gif, focused.gif 等)")


# ============================================================
# 系统托盘
# ============================================================
class FoamoTray(QSystemTrayIcon):
    def __init__(self, widget: FoamoWidget, state_mgr: StateManager):
        icon = self._make_icon()
        super().__init__(icon)

        self.widget = widget
        self.state_mgr = state_mgr

        # 跟桌宠右键共用 widget.build_context_menu — 每次右键手动 build + popup.
        # 不用 setContextMenu: 它在 Windows 下走原生 NotifyIcon, aboutToShow 信号
        # 不一定触发, 导致动态状态 (番茄钟倒计时 / 形态选中标记 / 显示-隐藏标签)
        # 跟桌宠右键不一致.
        self._menu = QMenu()
        self.setToolTip("泡沫 · 桌面陪伴")
        self.activated.connect(self._on_activated)

    def _make_icon(self) -> QIcon:
        """临时托盘图标:画一个紫色圆"""
        size = 32
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QRadialGradient(size / 2 - 4, size / 2 - 4, size)
        grad.setColorAt(0, QColor('#ebe1f8'))
        grad.setColorAt(0.5, QColor('#b39ddb'))
        grad.setColorAt(1, QColor('#7e5bb8'))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(2, 2, size - 4, size - 4)
        p.end()
        return QIcon(pix)

    def _on_activated(self, reason):
        # 右键: 每次都 rebuild 一次 (跟桌宠右键 contextMenuEvent 行为一致)
        if reason == QSystemTrayIcon.ActivationReason.Context:
            from PyQt6.QtGui import QCursor
            self.widget.build_context_menu(self._menu)
            self._menu.popup(QCursor.pos())
            return
        # 左键单击 / 双击 = 显示桌宠
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.widget._show_and_raise()

    def show_weekly_report(self, body: str):
        """报告就绪 → 弹一条 toast (主程序通过 signal 调用)"""
        self.showMessage(
            "泡沫 · 本周功劳簿",
            body,
            QSystemTrayIcon.MessageIcon.Information,
            15 * 1000  # 停留 15 秒
        )


# ============================================================
# main
# ============================================================
def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setQuitOnLastWindowClosed(False)  # 关窗口不退出,留托盘

    # 创建组件 (上下文感知 → 状态机 → widget → 持久化)
    activity = ActivityTracker()
    project_id = ProjectIdentifier()
    journal = Journal()

    widget = FoamoWidget()
    widget.project_id = project_id
    widget.set_activity_tracker(activity)

    state_mgr = StateManager(
        activity=activity,
        journal=journal,
        project_provider=lambda: project_id.name(),
    )
    pomodoro = PomodoroController(widget, state_mgr, journal=journal)
    widget.pomodoro = pomodoro
    reporter = WeeklyReporter(journal)
    widget.weekly_reporter = reporter
    watcher = ClaudeLogWatcher(CLAUDE_PROJECTS_DIR)

    # 信号串起来
    watcher.text_detected.connect(state_mgr.detect_and_apply)
    watcher.text_detected.connect(lambda _: activity.mark_active())
    watcher.jsonl_active.connect(
        lambda path: (project_id.update_from_jsonl(Path(path)), widget.update())
    )
    state_mgr.state_changed.connect(widget.set_state)
    # 状态变更 → 写日志 (带项目名)
    state_mgr.state_changed.connect(
        lambda s, l: journal.record_state(s, project=project_id.name(), line=l)
    )

    # 托盘
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("[warn] 系统不支持托盘")
        tray = None
    else:
        tray = FoamoTray(widget, state_mgr)
        tray.show()

    # 周报弹出: toast + 切 proud 短气泡 (锁定时不抢状态)
    def _on_weekly_report(body: str, short: str):
        if tray is not None:
            tray.show_weekly_report(body)
        if not state_mgr.locked:
            state_mgr._set_state('proud', override_line=short)
    reporter.report_ready.connect(_on_weekly_report)

    # 启动
    widget.show()
    watcher.start()

    print("=" * 50)
    print(f"  泡沫已上线")
    print(f"  监听目录: {CLAUDE_PROJECTS_DIR}")
    print(f"  素材目录: {ASSETS_DIR}")
    print(f"  托盘图标: {'已启用' if tray else '不可用'}")
    print("=" * 50)

    if not CLAUDE_PROJECTS_DIR.exists():
        print(f"[提示] 没找到 {CLAUDE_PROJECTS_DIR}")
        print(f"        如果你还没用过 Claude Code, 跑一次 'claude' 即可")

    try:
        ret = app.exec()
    finally:
        watcher.stop()
        journal.close()

    sys.exit(ret)


if __name__ == "__main__":
    main()
