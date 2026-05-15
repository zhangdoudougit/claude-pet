"""3-态状态胶囊: idle / thinking (with pulse) / online."""

from __future__ import annotations
from typing import Literal

from PyQt6.QtCore import Qt, QTimer, QRectF
from PyQt6.QtGui import QPainter, QColor
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel


State = Literal["idle", "thinking", "online"]

_TEXT = {"idle": "待机", "thinking": "思考中…", "online": "在线"}
_DOT = {"idle": "#c9c2b2", "thinking": "#e9b949", "online": "#7fb993"}
_FG = {"idle": "#6b6457", "thinking": "#7a5a18", "online": "#2d5a3b"}


class _Dot(QWidget):
    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(8, 8)
        self._color = QColor(color)
        self._pulse = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._tick)

    def set_color(self, color: str):
        self._color = QColor(color)
        self.update()

    def set_pulsing(self, pulsing: bool):
        if pulsing and not self._pulse_timer.isActive():
            self._pulse_timer.start(50)
        elif not pulsing and self._pulse_timer.isActive():
            self._pulse_timer.stop()
            self._pulse = 0.0
            self.update()

    def _tick(self):
        self._pulse = (self._pulse + 0.06) % 1.0
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._pulse > 0:
            halo = QColor(self._color)
            halo.setAlphaF(1.0 - self._pulse)
            r = 1 + self._pulse * 4
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            p.drawEllipse(QRectF(3 - r, 3 - r, 2 + r * 2, 2 + r * 2))
        p.setBrush(self._color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(1, 1, 6, 6))


class StatusPill(QWidget):
    def __init__(self, state: State = "idle", parent=None):
        super().__init__(parent)
        self.state: State = state if state in _TEXT else "idle"
        self.setFixedHeight(22)
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 0, 9, 0)
        h.setSpacing(6)
        self.dot = _Dot(_DOT[self.state])
        h.addWidget(self.dot)
        self.label = QLabel(_TEXT[self.state])
        h.addWidget(self.label)
        self._apply_style()
        if self.state == "thinking":
            self.dot.set_pulsing(True)

    def set_state(self, state):
        if state not in _TEXT or state == self.state:
            return
        self.state = state
        self.dot.set_color(_DOT[state])
        self.dot.set_pulsing(state == "thinking")
        self.label.setText(_TEXT[state])
        self._apply_style()

    def _apply_style(self):
        self.label.setStyleSheet(
            f"color: {_FG[self.state]}; font-size: 9pt; font-weight: 500;"
        )
        self.setStyleSheet(
            "StatusPill { background: white; border: 1px solid #e8e3d6; "
            "border-radius: 11px; }"
        )
