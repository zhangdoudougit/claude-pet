# chrome_widgets.py
"""Frameless 窗口的自绘 chrome: TitleBar + ThemeToggleButton + WinControls."""

from __future__ import annotations
import math

from PyQt6.QtCore import Qt, QRectF, pyqtSignal, QPoint
from PyQt6.QtGui import QPainter, QPen, QColor, QPainterPath
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel

from theme import ThemeManager


CHROME_HEIGHT = 32


def _draw_moon(p: QPainter, r, color: QColor):
    # 月牙 = 大圆 - 偏移小圆 (QPainterPath.subtracted)
    cx = r.center().x()
    cy = r.center().y()
    big = QPainterPath()
    big.addEllipse(QRectF(cx - 6.5, cy - 7.0, 13.0, 13.0))
    small = QPainterPath()
    # 小圆偏右上, 咬掉一块 → 月牙
    small.addEllipse(QRectF(cx - 2.5, cy - 9.0, 13.0, 13.0))
    moon = big.subtracted(small)
    p.setBrush(color)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPath(moon)


def _draw_sun(p: QPainter, r, color: QColor):
    pen = QPen(color, 1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    cx = r.center().x()
    cy = r.center().y()
    p.drawEllipse(QRectF(cx - 3.5, cy - 3.5, 7.0, 7.0))
    for i in range(8):
        a = i * math.pi / 4
        x1 = cx + 5.5 * math.cos(a)
        y1 = cy + 5.5 * math.sin(a)
        x2 = cx + 8.5 * math.cos(a)
        y2 = cy + 8.5 * math.sin(a)
        p.drawLine(int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2)))


def _draw_minimize(p: QPainter, r, color: QColor):
    p.setPen(QPen(color, 1))
    cx, cy = r.center().x(), r.center().y()
    p.drawLine(cx - 5, cy, cx + 5, cy)


def _draw_maximize(p: QPainter, r, color: QColor):
    p.setPen(QPen(color, 1))
    cx, cy = r.center().x(), r.center().y()
    p.drawRect(cx - 5, cy - 5, 10, 10)


def _draw_close(p: QPainter, r, color: QColor):
    p.setPen(QPen(color, 1))
    cx, cy = r.center().x(), r.center().y()
    p.drawLine(cx - 5, cy - 5, cx + 5, cy + 5)
    p.drawLine(cx + 5, cy - 5, cx - 5, cy + 5)


class IconGlyph(QWidget):
    """共用 hover-bg + paint glyph pattern."""

    def __init__(self, draw_fn, width=44, parent=None):
        super().__init__(parent)
        self.setFixedSize(width, CHROME_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._draw_fn = draw_fn
        self._hover = False
        self._color = QColor("#6b6457")
        self._clicked_handlers = []

    def set_color(self, hex_or_qcolor):
        self._color = QColor(hex_or_qcolor) if isinstance(hex_or_qcolor, str) else hex_or_qcolor
        self.update()

    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            for fn in self._clicked_handlers:
                fn()

    def add_click_handler(self, fn):
        self._clicked_handlers.append(fn)

    def click(self):
        for fn in self._clicked_handlers:
            fn()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._hover:
            p.fillRect(self.rect(), QColor(0, 0, 0, 12))
        self._draw_fn(p, self.rect(), self._color)


class ThemeToggleButton(IconGlyph):
    """点击 manager.toggle(). 当前 light 显示月亮; dark 显示太阳."""

    def __init__(self, manager: ThemeManager, parent=None):
        # initial draw_fn picked based on current theme
        super().__init__(self._pick_draw(manager), width=36, parent=parent)
        self.manager = manager
        self.add_click_handler(manager.toggle)
        self._refresh_color()
        manager.theme_changed.connect(self._on_theme_changed)

    @staticmethod
    def _pick_draw(manager):
        # light -> moon (clicking goes to dark); dark -> sun (clicking goes to light)
        return _draw_moon if manager.name == "warm" else _draw_sun

    def _on_theme_changed(self, _n):
        self._draw_fn = self._pick_draw(self.manager)
        self._refresh_color()
        self.update()

    def _refresh_color(self):
        self.set_color(self.manager.palette["inkSoft"])


class WinControls(QWidget):
    """min / max / close — 三个 44×32 按钮."""

    minimize_clicked = pyqtSignal()
    maximize_clicked = pyqtSignal()
    close_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.min_btn = IconGlyph(_draw_minimize, width=44)
        self.max_btn = IconGlyph(_draw_maximize, width=44)
        self.close_btn = IconGlyph(_draw_close, width=44)
        self.min_btn.add_click_handler(self.minimize_clicked.emit)
        self.max_btn.add_click_handler(self.maximize_clicked.emit)
        self.close_btn.add_click_handler(self.close_clicked.emit)
        h.addWidget(self.min_btn)
        h.addWidget(self.max_btn)
        h.addWidget(self.close_btn)


class TitleBar(QWidget):
    """32px title bar with icon+label (left) and theme toggle + win ctrls (right).
    Draggable: pressing on the bar (outside the buttons) drags the parent window."""

    def __init__(self, theme_mgr: ThemeManager, app_label: str = "和泡沫聊",
                 parent=None):
        super().__init__(parent)
        self.theme_mgr = theme_mgr
        self.setFixedHeight(CHROME_HEIGHT)
        self._press_global = None
        self._win_start_pos = None

        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 0, 0)
        h.setSpacing(8)

        self.icon_label = QLabel("泡")
        self.icon_label.setFixedSize(16, 16)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(self.icon_label)

        self.label = QLabel(app_label)
        h.addWidget(self.label)
        h.addStretch(1)

        self.theme_btn = ThemeToggleButton(theme_mgr)
        h.addWidget(self.theme_btn)

        self.win_ctrls = WinControls()
        h.addWidget(self.win_ctrls)

        self._apply_theme(theme_mgr.name)
        theme_mgr.theme_changed.connect(self._apply_theme)

    def _apply_theme(self, _name: str):
        p = self.theme_mgr.palette
        self.icon_label.setStyleSheet(
            f"background: {p['accent']}; color: #fff; "
            f"font-weight: 700; font-size: 9pt; border-radius: 4px;"
        )
        self.label.setStyleSheet(
            f"color: {p['inkSoft']}; font-size: 11pt; font-weight: 500;"
        )
        bar_bg = p.get("paperWarm") or p.get("glass1") or "#f3efe6"
        line = p["line"]
        self.setStyleSheet(
            f"TitleBar {{ background: {bar_bg}; border-bottom: 1px solid {line}; }}"
        )

    # ---- drag the window ----
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            win = self.window()
            self._press_global = e.globalPosition().toPoint()
            self._win_start_pos = win.pos() if win is not None else QPoint()

    def mouseMoveEvent(self, e):
        if (e.buttons() & Qt.MouseButton.LeftButton) and self._press_global is not None:
            win = self.window()
            if win is None:
                return
            delta = e.globalPosition().toPoint() - self._press_global
            win.move(self._win_start_pos + delta)

    def mouseReleaseEvent(self, _e):
        self._press_global = None
        self._win_start_pos = None
