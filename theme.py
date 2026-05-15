# theme.py
"""集中两套主题 (warm / glass) 配色变量, 持久化, signal 通知 widgets 刷新.

调色板来自 Claude Design 输出:
- WARM:  pet-warm.jsx 顶部 const PW
- GLASS: pet-glass.jsx 顶部 const PG
"""

from __future__ import annotations
from pathlib import Path
from typing import Literal

from PyQt6.QtCore import QObject, pyqtSignal


WARM: dict[str, str] = {
    "ink":        "#1d1b16",
    "inkSoft":    "#6b6457",
    "inkMute":    "#9a9387",
    "paper":      "#fafaf6",
    "paperWarm":  "#f3efe6",
    "line":       "#e8e3d6",
    "lineSoft":   "#f0ebdd",
    "accent":     "#7fb993",
    "accentSoft": "#e8f1e9",
    "accentLine": "#c4dccb",
    "codeBg":     "#efeadd",
    "codeInk":    "#5b4632",
}

GLASS: dict[str, str] = {
    "ink":        "#ecebe7",
    "inkSoft":    "#a8a59b",
    "inkMute":    "#6e6b62",
    "glass1":     "rgba(22,24,28,0.72)",
    "glass2":     "rgba(255,255,255,0.035)",
    "glass3":     "rgba(255,255,255,0.06)",
    "line":       "rgba(255,255,255,0.08)",
    "lineSoft":   "rgba(255,255,255,0.05)",
    "accent":     "#5ea8c9",
    "accentSoft": "rgba(94,168,201,0.20)",
    "accentLine": "rgba(94,168,201,0.45)",
    "codeBg":     "rgba(255,255,255,0.06)",
    "codeInk":    "#d7e7e8",
}


_THEMES = {"warm": WARM, "glass": GLASS}
ThemeName = Literal["warm", "glass"]


class ThemeManager(QObject):
    """每个 ChatWindow 持有一个. widgets 监听 theme_changed 重刷样式."""

    theme_changed = pyqtSignal(str)

    def __init__(self, state_file: Path, parent=None):
        super().__init__(parent)
        self.state_file = Path(state_file)
        self._name: ThemeName = "warm"
        self._load()

    def _load(self):
        if self.state_file.exists():
            n = self.state_file.read_text(encoding="utf-8").strip()
            if n in _THEMES:
                self._name = n  # type: ignore

    @property
    def name(self) -> ThemeName:
        return self._name

    @property
    def palette(self) -> dict[str, str]:
        return _THEMES[self._name]

    def set(self, name: str):
        if name not in _THEMES or name == self._name:
            return
        self._name = name  # type: ignore
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(self._name, encoding="utf-8")
        except Exception:
            pass
        self.theme_changed.emit(self._name)

    def toggle(self):
        self.set("glass" if self._name == "warm" else "warm")
