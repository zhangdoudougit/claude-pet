"""SVG-painted PetAvatar widget. Mood: idle / talking / sleep.

设计取自 Claude Design 输出 (pet-warm.jsx PetAvatar 函数). viewBox 64×64."""

from __future__ import annotations
from typing import Literal

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QPen, QColor, QPainterPath, QRadialGradient
from PyQt6.QtWidgets import QWidget


Mood = Literal["idle", "talking", "sleep"]


class PetAvatar(QWidget):
    def __init__(self, size: int = 28, mood: Mood = "idle",
                 dark: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.mood: Mood = mood if mood in ("idle", "talking", "sleep") else "idle"
        self._dark = dark

    def set_mood(self, mood: Mood):
        if mood in ("idle", "talking", "sleep") and mood != self.mood:
            self.mood = mood
            self.update()

    def set_dark(self, dark: bool):
        if dark != self._dark:
            self._dark = dark
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.width()
        scale = s / 64.0

        # face gradient
        grad = QRadialGradient(s * 0.35, s * 0.35, s * 0.70)
        if self._dark:
            grad.setColorAt(0.0, QColor("#fff2d4"))
            grad.setColorAt(1.0, QColor("#d4b774"))
        else:
            grad.setColorAt(0.0, QColor("#fff8e7"))
            grad.setColorAt(1.0, QColor("#ecd9a8"))
        p.setBrush(grad)
        p.setPen(QPen(QColor("#1a1610" if self._dark else "#d9c693"),
                      max(1.0, scale * 1)))
        p.drawEllipse(QRectF(0, 0, s, s))

        # hair lock
        hair = QPainterPath()
        hair.moveTo(14 * scale, 22 * scale)
        hair.quadTo(32 * scale, 8 * scale, 50 * scale, 22 * scale)
        hair.lineTo(50 * scale, 22 * scale)
        hair.quadTo(44 * scale, 18 * scale, 32 * scale, 18 * scale)
        hair.quadTo(20 * scale, 18 * scale, 14 * scale, 22 * scale)
        hair.closeSubpath()
        p.setBrush(QColor("#1a1610" if self._dark else "#2a241d"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(hair)

        # eyes
        eye_pen = QPen(QColor("#1a1610" if self._dark else "#2a241d"),
                       max(1.5, 2.5 * scale))
        eye_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(eye_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self.mood == "idle":
            self._draw_smile_eye(p, scale, 22, 26, 28)
            self._draw_smile_eye(p, scale, 40, 26, 46)
        elif self.mood == "talking":
            p.drawEllipse(QRectF(22 * scale, 24 * scale, 6 * scale, 4 * scale))
            p.drawEllipse(QRectF(40 * scale, 24 * scale, 6 * scale, 4 * scale))
        else:  # sleep
            p.drawLine(int(22 * scale), int(27 * scale),
                       int(28 * scale), int(27 * scale))
            p.drawLine(int(40 * scale), int(27 * scale),
                       int(46 * scale), int(27 * scale))

        # blush
        blush_col = QColor("#e88c84" if self._dark else "#f3a8a0")
        blush_col.setAlphaF(0.6)
        p.setBrush(blush_col)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF((22 - 2.5) * scale, (38 - 2.5) * scale,
                             5 * scale, 5 * scale))
        p.drawEllipse(QRectF((46 - 2.5) * scale, (38 - 2.5) * scale,
                             5 * scale, 5 * scale))

        # mouth
        p.setPen(eye_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        mouth = QPainterPath()
        mouth.moveTo(30 * scale, 42 * scale)
        mouth.quadTo(32 * scale, 44 * scale, 34 * scale, 42 * scale)
        p.drawPath(mouth)

    def _draw_smile_eye(self, p, scale, x1, y1, x2):
        """Smile-shaped eye via quadratic bezier."""
        path = QPainterPath()
        path.moveTo(x1 * scale, y1 * scale)
        path.quadTo(((x1 + x2) / 2) * scale, (y1 + 2) * scale,
                    x2 * scale, y1 * scale)
        p.drawPath(path)
