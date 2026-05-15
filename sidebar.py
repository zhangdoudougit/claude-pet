"""左侧会话列表 + 卡片(头像/简码/角标) + 添加项目弹窗触发."""

from __future__ import annotations
import re
from pathlib import Path

from PyQt6.QtCore import (
    Qt, QSize, QRectF, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QPainter, QPainterPath, QFont, QPen, QBrush, QIcon,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QFrame,
    QLabel, QDialog, QFileDialog, QMessageBox, QMenu,
)

from conversation_store import ConversationStore, ConversationEntry


# ---------- icon helpers (自绘 SVG icon → QPixmap) ----------

def _make_search_icon_pixmap(size: int = 13) -> "QPixmap":
    from PyQt6.QtGui import QPixmap
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#9a9387"), 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # 圆 + 手柄
    p.drawEllipse(QRectF(1.5, 1.5, 7, 7))
    p.drawLine(8, 8, 11, 11)
    p.end()
    return pm


def _make_brain_icon_pixmap(size: int = 15) -> "QPixmap":
    from PyQt6.QtGui import QPixmap
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#6b6457"), 1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # 大脑近似: 两个半圆叠加 + 中线
    p.drawEllipse(QRectF(2.0, 3.0, 5.5, 9.0))
    p.drawEllipse(QRectF(7.5, 3.0, 5.5, 9.0))
    p.drawLine(7, 5, 8, 5)
    p.drawLine(7, 9, 8, 9)
    p.end()
    return pm


SIDEBAR_WIDTH = 248        # design line 156: width: 248
CARD_HEIGHT = 50           # padding 9 + avatar 28 + padding 9 ≈ 46, +6 margin
AVATAR_SIZE = 28           # design line 207: PetAvatar size={28} / ProjectBadge size={28}
BADGE_SIZE = 10


# ---------- AvatarWidget ----------
# 严格照设计 (pet-warm.jsx PetAvatar / ProjectBadge), 但闲聊保留小丸子 foamo.ico
# (用户偏好覆盖设计稿: 闲聊就是小丸子, 不用 PetAvatar 那张米黄脸)

class AvatarWidget(QWidget):
    def __init__(self, entry: ConversationEntry, foamo_icon: QIcon,
                 size: int = AVATAR_SIZE, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.foamo_icon = foamo_icon
        self._size = size
        self.setFixedSize(size, size)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self._size
        rect = QRectF(0, 0, s, s)
        if self.entry.kind == "chat":
            # 闲聊: 圆形 + foamo.ico (无小气泡装饰, 设计稿也没有)
            path = QPainterPath()
            path.addEllipse(rect)
            p.setClipPath(path)
            if not self.foamo_icon.isNull():
                pm = self.foamo_icon.pixmap(QSize(s, s))
                p.drawPixmap(rect.toRect(), pm)
            else:
                p.fillRect(rect, QColor("#E07A5F"))
        else:
            # 项目: 圆角方块 radius **8** (design line 110), 文字色 rgba(0,0,0,0.7), font 11pt 700
            p.setBrush(QColor(self.entry.color or "#7C8290"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(rect, 8, 8)
            f = QFont("JetBrains Mono")
            f.setPointSize(9)  # 28×28 内, fontSize 11 / 28 比例
            f.setWeight(QFont.Weight.Bold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.3)
            p.setFont(f)
            p.setPen(QColor(0, 0, 0, 178))  # rgba(0,0,0,0.7)
            code = (self.entry.short_code or "")[:4]
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, code)


# ---------- ConversationCard ----------

class ConversationCard(QFrame):
    clicked = pyqtSignal(str)
    rightClicked = pyqtSignal(str, object)

    def __init__(self, entry: ConversationEntry, foamo_icon: QIcon,
                 theme_mgr=None, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.theme_mgr = theme_mgr
        # 不固定高度, 让 padding + content 决定 (design padding: 9px 10px)
        self.setMouseTracking(True)
        self._selected = False
        self._hover = False
        self._pulse_phase = 0.0
        self._build(foamo_icon)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)
        if theme_mgr is not None:
            theme_mgr.theme_changed.connect(self.apply_theme)
            # 初始应用一次, 别等下次切换
            self.apply_theme(theme_mgr.name)

    def _build(self, foamo_icon: QIcon):
        # design padding: 9px 10px, gap 10, borderRadius 8
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(10)
        self.avatar = AvatarWidget(self.entry, foamo_icon, size=28)
        layout.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        text_col.setContentsMargins(0, 0, 0, 0)

        self.label = QLabel(self.entry.name)
        f = self.label.font()
        f.setPointSize(10)   # design 13px / px-to-pt 约 9.75 → 用 10pt
        f.setWeight(QFont.Weight.Medium)
        self.label.setFont(f)
        text_col.addWidget(self.label)

        self.sub_label = QLabel(self._format_sub())
        sf = self.sub_label.font()
        # design 11px sub, project 用 mono
        sf.setPointSize(8)
        if self.entry.kind == "project":
            sf.setFamily("JetBrains Mono")
        self.sub_label.setFont(sf)
        self.sub_label.setStyleSheet("color: #9a9387; background: transparent;")
        text_col.addWidget(self.sub_label)

        layout.addLayout(text_col, 1)

    def _format_sub(self) -> str:
        if self.entry.kind == "chat":
            return "·_·  和泡沫聊点什么"
        import time as _t
        ts = self.entry.last_active_ts
        if not ts:
            return self.entry.path or ""
        diff = _t.time() - ts
        if diff < 60:
            return "刚刚"
        if diff < 3600:
            return f"{int(diff // 60)} 分钟前"
        if diff < 86400:
            return f"{int(diff // 3600)} 小时前"
        if diff < 86400 * 7:
            return f"{int(diff // 86400)} 天前"
        return "更早"

    def set_selected(self, sel: bool):
        if self._selected != sel:
            self._selected = sel
            f = self.label.font()
            f.setWeight(QFont.Weight.DemiBold if sel else QFont.Weight.Medium)
            self.label.setFont(f)
            self.update()

    def refresh(self):
        self.label.setText(self.entry.name)
        self.sub_label.setText(self._format_sub())
        self.update()

    def apply_theme(self, name: str):
        """主题切换时刷新 label/sub_label 颜色, 避免 dark 模式下文字消失."""
        if name == "glass":
            self.label.setStyleSheet("color: #ecebe7;")
            self.sub_label.setStyleSheet("color: #6e6b62; background: transparent;")
        else:
            self.label.setStyleSheet("color: #1d1b16;")
            self.sub_label.setStyleSheet("color: #9a9387; background: transparent;")
        self.update()

    def enterEvent(self, _ev):
        self._hover = True
        self.update()

    def leaveEvent(self, _ev):
        self._hover = False
        self.update()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.entry.key)
        elif ev.button() == Qt.MouseButton.RightButton:
            self.rightClicked.emit(self.entry.key, ev.globalPosition().toPoint())

    def _tick(self):
        win = self.window()
        if not (win and win.isVisible() and win.isActiveWindow()):
            return
        if self.entry.badge in ("thinking", "permission"):
            self._pulse_phase = (self._pulse_phase + 0.04) % 1.0
            self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        name = self.theme_mgr.name if self.theme_mgr else "warm"
        if name == "glass":
            selected_bg = QColor(255, 255, 255, 18)
            hover_bg = QColor(255, 255, 255, 12)
            accent = QColor("#5ea8c9")
        else:
            selected_bg = QColor("#ffffff")
            hover_bg = QColor(0, 0, 0, 10)
            accent = QColor("#7fb993")

        if self._selected:
            # design: bg #fff + boxShadow 0 1px 2px rgba(40,30,20,0.04) + radius 8
            r = QRectF(rect)
            path = QPainterPath()
            path.addRoundedRect(r, 8, 8)
            p.fillPath(path, selected_bg)
            # 软阴影 (在 bottom 画 1px 浅线模拟)
            if name != "glass":
                p.setPen(QPen(QColor(40, 30, 20, 12), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawLine(int(rect.left() + 4), int(rect.bottom() - 1),
                           int(rect.right() - 4), int(rect.bottom() - 1))
            # 左侧 accent 竖条 (design left:-8, top:10, bottom:10, width 3, radius 2)
            # Qt 没法画到 widget 外, 改为 left:0
            bar_top = 10
            bar_bot = rect.height() - 10
            p.setBrush(accent)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(0, bar_top, 3, bar_bot - bar_top), 1.5, 1.5)
        elif self._hover:
            r = QRectF(rect)
            path = QPainterPath()
            path.addRoundedRect(r, 8, 8)
            p.fillPath(path, hover_bg)

        if self.entry.badge != "none":
            self._paint_badge(p)

    def _paint_badge(self, p: QPainter):
        ax = self.avatar.x() + AVATAR_SIZE - BADGE_SIZE
        ay = self.avatar.y() - BADGE_SIZE // 2 + 4
        color_map = {
            "thinking": "#F5B544",
            "permission": "#E5484D",
            "unread": "#3B7DD8",
        }
        base_color = QColor(color_map.get(self.entry.badge, "#888"))
        p.setPen(QPen(QColor(247, 247, 248, 240), 1.5))
        p.setBrush(QBrush(base_color))
        p.drawEllipse(QRectF(ax, ay, BADGE_SIZE, BADGE_SIZE))
        if self.entry.badge == "thinking":
            radius = BADGE_SIZE / 2 + self._pulse_phase * 4
            alpha = int(255 * (1 - self._pulse_phase))
            ring_color = QColor(base_color)
            ring_color.setAlpha(alpha)
            p.setPen(QPen(ring_color, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            cx = ax + BADGE_SIZE / 2
            cy = ay + BADGE_SIZE / 2
            p.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))


# ---------- ColorSwatch ----------

class ColorSwatch(QWidget):
    clicked = pyqtSignal(str)

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.color = color
        self._selected = False
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_selected(self, s: bool):
        self._selected = s
        self.update()

    def mousePressEvent(self, _ev):
        self.clicked.emit(self.color)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(self.color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(2, 2, 24, 24))
        if self._selected:
            p.setPen(QPen(QColor(255, 255, 255, 240), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(1, 1, 26, 26))


# ---------- AddProjectDialog ----------

class AddProjectDialog(QDialog):
    def __init__(self, store: ConversationStore, foamo_icon: QIcon,
                 editing: ConversationEntry | None = None, parent=None):
        super().__init__(parent)
        self.store = store
        self.foamo_icon = foamo_icon
        self.editing = editing
        self.setWindowTitle("编辑项目" if editing else "添加项目")
        self.setFixedSize(420, 380)
        self.setModal(True)
        self._selected_color = (editing.color if editing
                                else store.COLOR_PALETTE[0])
        self._build()
        if editing:
            self.path_edit.setText(editing.path or "")
            self.path_edit.setEnabled(False)
            self.code_edit.setText(editing.short_code or "")
        self._update_preview()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        title = QLabel("编辑项目" if self.editing else "添加项目")
        f = title.font()
        f.setPointSize(15)
        f.setWeight(QFont.Weight.DemiBold)
        title.setFont(f)
        v.addWidget(title)

        v.addWidget(QLabel("项目目录"))
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        path_row.addWidget(self.path_edit, 1)
        pick_btn = QPushButton("选择...")
        pick_btn.clicked.connect(self._pick_dir)
        path_row.addWidget(pick_btn)
        v.addLayout(path_row)
        self.path_edit.textChanged.connect(self._on_path_changed)

        v.addWidget(QLabel("项目简码 (2-4 字符)"))
        self.code_edit = QLineEdit()
        self.code_edit.setMaxLength(4)
        self.code_edit.setFixedWidth(80)
        self.code_edit.textChanged.connect(self._update_preview)
        v.addWidget(self.code_edit)

        v.addWidget(QLabel("头像配色"))
        color_row = QHBoxLayout()
        color_row.setSpacing(6)
        self._swatches = []
        for c in self.store.COLOR_PALETTE:
            sw = ColorSwatch(c)
            sw.clicked.connect(self._on_color)
            sw.set_selected(c == self._selected_color)
            color_row.addWidget(sw)
            self._swatches.append(sw)
        color_row.addStretch()
        v.addLayout(color_row)

        v.addWidget(QLabel("预览"))
        self.preview_label = QLabel("")
        self.preview_label.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(self.preview_label)

        v.addStretch(1)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn = QPushButton("保存" if self.editing else "添加")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.ok_btn)
        v.addLayout(btn_row)

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择项目目录")
        if d:
            self.path_edit.setText(d)

    def _on_path_changed(self, text: str):
        if self.editing:
            return
        name = Path(text).name
        if not name:
            return
        parts = re.split(r"[_\-\s]+", name)
        if len(parts) >= 2:
            code = "".join(p[0] for p in parts if p)[:4].upper()
        else:
            code = name[:3].upper()
        self.code_edit.setText(code)
        self._update_preview()

    def _on_color(self, color: str):
        self._selected_color = color
        for sw in self._swatches:
            sw.set_selected(sw.color == color)
        self._update_preview()

    def _update_preview(self):
        path = self.path_edit.text()
        code = self.code_edit.text()
        name = Path(path).name if path else "(未选目录)"
        html = (
            f'<span style="font-family: monospace; padding: 6px 12px; '
            f'background: {self._selected_color}; color: white; '
            f'border-radius: 8px;">{code}</span>  {name}'
        )
        self.preview_label.setText(html)

    def _on_ok(self):
        path = self.path_edit.text().strip()
        code = self.code_edit.text().strip().upper()
        if not path or not Path(path).is_dir():
            QMessageBox.warning(self, "无效", "请选择有效的项目目录")
            return
        if len(code) < 2:
            QMessageBox.warning(self, "无效", "简码至少 2 个字符")
            return
        if self.editing:
            self.store.update_entry(self.editing.key,
                name=Path(path).name, short_code=code, color=self._selected_color)
            self.accept()
            return
        # 路径重复 → 跳转
        for e in self.store.list_entries():
            if e.kind == "project" and e.path == path:
                QMessageBox.information(self, "已存在", f"项目「{e.name}」已经在列表里")
                self.accept()
                return
        self.store.add_project(path=path, name=Path(path).name,
                               short_code=code, color=self._selected_color)
        self.accept()


# ---------- Sidebar ----------

class Sidebar(QWidget):
    card_clicked = pyqtSignal(str)
    add_project_requested = pyqtSignal()
    delete_project_requested = pyqtSignal(str)
    edit_project_requested = pyqtSignal(str)

    def __init__(self, store: ConversationStore, foamo_icon: QIcon,
                 theme_mgr=None, parent=None):
        super().__init__(parent)
        self.store = store
        self.foamo_icon = foamo_icon
        self.theme_mgr = theme_mgr
        self._cards: dict[str, ConversationCard] = {}
        self._current_key = "chat"
        self.setFixedWidth(SIDEBAR_WIDTH)
        self._apply_theme()
        if theme_mgr is not None:
            theme_mgr.theme_changed.connect(self._apply_theme)
        self._build()
        store.entry_added.connect(self._rebuild)
        store.entry_removed.connect(self._rebuild)
        store.entry_changed.connect(self._on_entry_changed)
        self._rebuild()

    def _apply_theme(self, *_args):
        # design: sidebar 白底胶囊搜索框 + "+" 白底 + 1px border
        name = self.theme_mgr.name if self.theme_mgr is not None else "warm"
        if name == "glass":
            bg = "rgba(22, 24, 28, 0.95)"
            input_bg = "rgba(255,255,255,0.06)"
            input_border = "rgba(255,255,255,0.10)"
            input_focus = "1.5px solid #5ea8c9"
            input_text = "#ecebe7"
            placeholder_color = "#6e6b62"
            add_color = "#a8a59b"
            settings_color = "#ecebe7"
            settings_hover = "rgba(255,255,255,0.06)"
            section_color = "#6e6b62"
            sep_color = "rgba(255,255,255,0.08)"
        else:
            bg = "#f3efe6"
            input_bg = "#ffffff"
            input_border = "#e8e3d6"
            input_focus = "1.5px solid #7fb993"
            input_text = "#1d1b16"
            placeholder_color = "#9a9387"
            add_color = "#6b6457"
            settings_color = "#1d1b16"
            settings_hover = "#ffffff"
            section_color = "#9a9387"
            sep_color = "#e8e3d6"

        self.setStyleSheet(
            f"Sidebar {{ background: {bg}; border-right: 1px solid {sep_color}; }}"
            f" QLineEdit {{ background: {input_bg}; border: 1px solid {input_border}; "
            f"   border-radius: 8px; padding: 0 10px; "
            f"   font-size: 11pt; color: {input_text}; }}"
            f" QLineEdit:focus {{ border: {input_focus}; }}"
        )
        # placeholder 用 palette 单独设 (QLineEdit::placeholder 不是所有 Qt 版本支持)
        if hasattr(self, "search"):
            from PyQt6.QtGui import QPalette
            pal = self.search.palette()
            pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(placeholder_color))
            self.search.setPalette(pal)

        if hasattr(self, "add_btn"):
            self.add_btn.setStyleSheet(
                f"QPushButton {{ background: {input_bg}; border: 1px solid {input_border};"
                f"  border-radius: 8px; font-size: 14pt; color: {add_color}; }}"
                f"QPushButton:hover {{ background: {settings_hover}; }}"
            )
        if hasattr(self, "settings_btn"):
            self.settings_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; "
                f"text-align: left; padding: 0 10px; font-size: 11pt; color: {settings_color}; }}"
                f"QPushButton:hover {{ background: {settings_hover}; border-radius: 8px; }}"
            )
        if hasattr(self, "memory_btn"):
            self.memory_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; "
                f"border-radius: 8px; }}"
                f"QPushButton:hover {{ background: {settings_hover}; }}"
            )
        if hasattr(self, "section_label"):
            self.section_label.setStyleSheet(
                f"color: {section_color}; background: transparent;"
            )
        for card in self._cards.values():
            if hasattr(card, "apply_theme"):
                card.apply_theme(name)

    def _build(self):
        # 总外 padding 0 (design aside 没 padding), 列表区单独 padding 2 8
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ----- 顶部搜索 + "+" (design padding 12 12 10) -----
        top_wrap = QWidget()
        top = QHBoxLayout(top_wrap)
        top.setContentsMargins(12, 12, 12, 10)
        top.setSpacing(8)

        # 搜索框: 白底 + 1px border + radius 8 + search SVG icon
        self.search = QLineEdit(placeholderText="搜索 / 切换项目")
        self.search.setFixedHeight(32)
        # 加 search icon (QLineEdit addAction)
        from PyQt6.QtGui import QAction
        from PyQt6.QtCore import QSize as _QSize
        # 自绘 search icon 作 QPixmap 当 leading icon
        try:
            _search_icon_pm = _make_search_icon_pixmap()
            self.search.addAction(QIcon(_search_icon_pm),
                                  QLineEdit.ActionPosition.LeadingPosition)
        except Exception:
            pass
        top.addWidget(self.search, 1)

        self.add_btn = QPushButton("＋")
        self.add_btn.setFixedSize(32, 32)
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.clicked.connect(self.add_project_requested.emit)
        top.addWidget(self.add_btn)
        layout.addWidget(top_wrap)

        # ----- "最近" section label -----
        self.section_label = QLabel("最近")
        self.section_label.setContentsMargins(16, 6, 16, 4)
        slf = self.section_label.font()
        slf.setPointSize(7)
        slf.setWeight(QFont.Weight.DemiBold)
        slf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
        slf.setCapitalization(QFont.Capitalization.AllUppercase)
        self.section_label.setFont(slf)
        self.section_label.setStyleSheet("color: #9a9387;")
        layout.addWidget(self.section_label)

        # ----- 卡片列表 (design padding 2 8, gap 1) -----
        self.card_area = QVBoxLayout()
        self.card_area.setSpacing(1)
        self.card_area.setContentsMargins(8, 2, 8, 2)
        layout.addLayout(self.card_area)

        layout.addStretch(1)

        # ----- footer: 设置 + 记忆 icon (design borderTop, padding 8 10) -----
        footer_sep = QFrame()
        footer_sep.setFrameShape(QFrame.Shape.HLine)
        footer_sep.setStyleSheet("background: #e8e3d6; max-height: 1px;")
        layout.addWidget(footer_sep)

        footer_wrap = QWidget()
        footer_row = QHBoxLayout(footer_wrap)
        footer_row.setContentsMargins(10, 8, 10, 8)
        footer_row.setSpacing(4)
        self.settings_btn = QPushButton("⚙  设置")
        self.settings_btn.setFixedHeight(32)
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        footer_row.addWidget(self.settings_btn, 1)
        # 记忆 icon (大脑 svg 装饰按钮)
        self.memory_btn = QPushButton()
        self.memory_btn.setFixedSize(32, 32)
        self.memory_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.memory_btn.setToolTip("泡沫 · 记忆")
        try:
            self.memory_btn.setIcon(QIcon(_make_brain_icon_pixmap()))
            self.memory_btn.setIconSize(_QSize(15, 15))
        except Exception:
            self.memory_btn.setText("脑")
        footer_row.addWidget(self.memory_btn)
        layout.addWidget(footer_wrap)

    def _rebuild(self, *_args):
        for c in list(self._cards.values()):
            c.setParent(None)
            c.deleteLater()
        self._cards.clear()
        while self.card_area.count():
            item = self.card_area.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        entries = self.store.list_entries()
        name = self.theme_mgr.name if self.theme_mgr is not None else "warm"
        for i, entry in enumerate(entries):
            card = ConversationCard(entry, self.foamo_icon, theme_mgr=self.theme_mgr)
            card.clicked.connect(self.card_clicked.emit)
            card.rightClicked.connect(self._on_right_click)
            card.set_selected(entry.key == self._current_key)
            card.apply_theme(name)
            self._cards[entry.key] = card
            self.card_area.addWidget(card)
            if i == 0 and len(entries) > 1:
                line = QFrame()
                line.setFrameShape(QFrame.Shape.HLine)
                line.setStyleSheet(
                    "background: rgba(0,0,0,0.06); max-height: 1px;"
                )
                self.card_area.addWidget(line)

    def _on_entry_changed(self, key: str):
        card = self._cards.get(key)
        if card:
            card.refresh()

    def _on_right_click(self, key: str, global_pos):
        menu = QMenu(self)
        if key == "chat":
            act = menu.addAction("闲聊不能删除")
            act.setEnabled(False)
        else:
            edit_act = menu.addAction("编辑项目")
            edit_act.triggered.connect(lambda: self.edit_project_requested.emit(key))
            menu.addSeparator()
            del_act = menu.addAction("删除项目")
            del_act.triggered.connect(lambda: self._confirm_delete(key))
        menu.exec(global_pos)

    def _confirm_delete(self, key: str):
        entry = self.store.get(key)
        if not entry:
            return
        m = QMessageBox(self)
        m.setIcon(QMessageBox.Icon.Question)
        m.setWindowTitle("删除项目")
        m.setText(f"删除项目「{entry.name}」?")
        keep_btn = m.addButton("仅从列表移除", QMessageBox.ButtonRole.AcceptRole)
        purge_btn = m.addButton("同时删除会话历史", QMessageBox.ButtonRole.DestructiveRole)
        m.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        m.exec()
        clicked = m.clickedButton()
        if clicked == keep_btn:
            self.store.delete_project(key, purge_history=False)
            self.delete_project_requested.emit(key)
        elif clicked == purge_btn:
            self.store.delete_project(key, purge_history=True)
            self.delete_project_requested.emit(key)

    def set_current(self, key: str):
        if self._current_key == key:
            return
        prev = self._cards.get(self._current_key)
        if prev:
            prev.set_selected(False)
        cur = self._cards.get(key)
        if cur:
            cur.set_selected(True)
        self._current_key = key
