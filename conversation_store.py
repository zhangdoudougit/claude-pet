# conversation_store.py
"""会话条目元数据与持久化. 集中管理闲聊 + 多项目的角标、排序、增删改."""

from __future__ import annotations
import hashlib
import json
import shutil
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Literal, Optional

from PyQt6.QtCore import QObject, pyqtSignal


BadgeState = Literal["none", "thinking", "permission", "unread"]


@dataclass
class ConversationEntry:
    key: str
    kind: Literal["chat", "project"]
    name: str
    path: Optional[str] = None
    short_code: Optional[str] = None
    color: Optional[str] = None
    last_active_ts: int = 0
    created_ts: int = 0
    badge: BadgeState = "none"
    unread_count: int = 0


class ConversationStore(QObject):
    """单例式. 由 ChatWindow 持有, sidebar/chat_panel/worker 共享."""

    COLOR_PALETTE = [
        "#E07A5F", "#E8A87C", "#3D5A6C", "#5B7553",
        "#7B6CA8", "#4A6FA5", "#C25B56", "#7C8290",
    ]

    entry_changed = pyqtSignal(str)        # key
    entry_added = pyqtSignal(str)
    entry_removed = pyqtSignal(str)

    def __init__(self, state_dir: Path, parent=None):
        super().__init__(parent)
        self.state_dir = Path(state_dir)
        self.conv_dir = self.state_dir / "conv"
        self.conv_dir.mkdir(parents=True, exist_ok=True)
        self._entries: dict[str, ConversationEntry] = {}
        self._load_all()

    # ----- load / migrate -----

    def _load_all(self):
        now = int(time.time())
        # 闲聊永远存在
        self._entries["chat"] = ConversationEntry(
            key="chat", kind="chat", name="闲聊",
            last_active_ts=now, created_ts=now,
        )
        if not self.conv_dir.exists():
            return
        for d in self.conv_dir.iterdir():
            if not d.is_dir() or d.name == "chat":
                continue
            meta_path = d / "meta.json"
            if not meta_path.exists():
                continue
            try:
                raw = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            entry = self._migrate(raw, d)
            self._entries[d.name] = entry

    def _migrate(self, raw: dict, conv_dir: Path) -> ConversationEntry:
        key = conv_dir.name
        path = raw.get("path")
        name = raw.get("name") or Path(path or key).name
        short_code = raw.get("short_code") or _default_short_code(name)
        if "color" in raw:
            color = raw["color"]
        else:
            color = self._auto_color(path or key)
        mtime = int((conv_dir / "meta.json").stat().st_mtime)
        last_active_ts = int(raw.get("last_active_ts") or mtime)
        created_ts = int(raw.get("created_ts") or mtime)
        entry = ConversationEntry(
            key=key, kind="project", name=name, path=path,
            short_code=short_code, color=color,
            last_active_ts=last_active_ts, created_ts=created_ts,
        )
        # 把迁移结果写回, 避免下次再算
        self._write_meta(entry)
        return entry

    def _auto_color(self, seed: str) -> str:
        idx = int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(self.COLOR_PALETTE)
        return self.COLOR_PALETTE[idx]

    def _write_meta(self, entry: ConversationEntry):
        if entry.kind != "project":
            return
        meta_path = self.conv_dir / entry.key / "meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps({
            "path": entry.path, "name": entry.name,
            "short_code": entry.short_code, "color": entry.color,
            "last_active_ts": entry.last_active_ts,
            "created_ts": entry.created_ts,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ----- public api -----

    def list_entries(self) -> list[ConversationEntry]:
        chat = self._entries["chat"]
        projects = sorted(
            (e for e in self._entries.values() if e.kind == "project"),
            key=lambda e: e.last_active_ts, reverse=True,
        )
        return [chat] + projects

    def get(self, key: str) -> Optional[ConversationEntry]:
        return self._entries.get(key)

    def add_project(self, path: str, name: str, short_code: str, color: str) -> ConversationEntry:
        # 项目目录路径作为 key 哈希
        key = "proj_" + hashlib.md5(path.encode()).hexdigest()[:8]
        if key in self._entries:
            return self._entries[key]
        now = int(time.time())
        entry = ConversationEntry(
            key=key, kind="project", name=name, path=path,
            short_code=short_code, color=color,
            last_active_ts=now, created_ts=now,
        )
        (self.conv_dir / key).mkdir(parents=True, exist_ok=True)
        self._write_meta(entry)
        self._entries[key] = entry
        self.entry_added.emit(key)
        return entry

    def delete_project(self, key: str, purge_history: bool = False):
        if key == "chat":
            raise ValueError("不能删除闲聊")
        if key not in self._entries:
            return
        del self._entries[key]
        if purge_history:
            shutil.rmtree(self.conv_dir / key, ignore_errors=True)
        self.entry_removed.emit(key)

    def update_entry(self, key: str, **fields):
        entry = self._entries.get(key)
        if entry is None or entry.kind != "project":
            return
        for k, v in fields.items():
            if hasattr(entry, k):
                setattr(entry, k, v)
        self._write_meta(entry)
        self.entry_changed.emit(key)

    def touch(self, key: str):
        entry = self._entries.get(key)
        if entry is None:
            return
        entry.last_active_ts = int(time.time())
        self._write_meta(entry)
        self.entry_changed.emit(key)

    def set_badge(self, key: str, state: BadgeState):
        entry = self._entries.get(key)
        if entry is None or entry.badge == state:
            return
        entry.badge = state
        if state != "unread":
            entry.unread_count = 0
        self.entry_changed.emit(key)

    def bump_unread(self, key: str):
        entry = self._entries.get(key)
        if entry is None:
            return
        entry.unread_count += 1
        self.entry_changed.emit(key)


def _default_short_code(name: str) -> str:
    """取 'smart_plc_v2' → 'SPV', 'demo' → 'DEM'."""
    import re
    parts = re.split(r"[_\-\s]+", name)
    if len(parts) >= 2:
        initials = "".join(p[0] for p in parts if p)[:4]
        if initials:
            return initials.upper()
    return name[:3].upper().ljust(2, "X")
