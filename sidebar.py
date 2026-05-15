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


SIDEBAR_WIDTH = 240
CARD_HEIGHT = 64
AVATAR_SIZE = 40
BADGE_SIZE = 10


# ---------- AvatarWidget ----------

class AvatarWidget(QWidget):
    def __init__(self, entry: ConversationEntry, foamo_icon: QIcon, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.foamo_icon = foamo_icon
        self.setFixedSize(AVATAR_SIZE, AVATAR_SIZE)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, AVATAR_SIZE, AVATAR_SIZE)
        if self.entry.kind == "chat":
            path = QPainterPath()
            path.addEllipse(rect)
            p.setClipPath(path)
            if not self.foamo_icon.isNull():
                pm = self.foamo_icon.pixmap(QSize(AVATAR_SIZE, AVATAR_SIZE))
                p.drawPixmap(rect.toRect(), pm)
            else:
                p.fillRect(rect, QColor("#E07A5F"))
            # 右下角小气泡 (低调装饰)
            p.setClipping(False)
            p.setBrush(QColor("#F5B544"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(AVATAR_SIZE - 9, AVATAR_SIZE - 9, 6, 6))
        else:
            p.setBrush(QColor(self.entry.color or "#7C8290"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(rect, 10, 10)
            f = QFont()
            f.setPointSize(11)
            f.setWeight(QFont.Weight.DemiBold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.3)
            p.setFont(f)
            p.setPen(QColor(255, 255, 255, 242))
            code = (self.entry.short_code or "")[:4]
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, code)


# ---------- ConversationCard ----------

class ConversationCard(QFrame):
    clicked = pyqtSignal(str)
    rightClicked = pyqtSignal(str, object)

    def __init__(self, entry: ConversationEntry, foamo_icon: QIcon, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.setFixedHeight(CARD_HEIGHT)
        self.setMouseTracking(True)
        self._selected = False
        self._hover = False
        self._pulse_phase = 0.0
        self._build(foamo_icon)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)

    def _build(self, foamo_icon: QIcon):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 12, 12)
        layout.setSpacing(12)
        self.avatar = AvatarWidget(self.entry, foamo_icon)
        layout.addWidget(self.avatar)
        self.label = QLabel(self.entry.name)
        f = self.label.font()
        f.setPointSize(10)
        f.setWeight(QFont.Weight.Medium)
        self.label.setFont(f)
        layout.addWidget(self.label, 1)

    def set_selected(self, sel: bool):
        if self._selected != sel:
            self._selected = sel
            f = self.label.font()
            f.setWeight(QFont.Weight.DemiBold if sel else QFont.Weight.Medium)
            self.label.setFont(f)
            self.update()

    def refresh(self):
        self.label.setText(self.entry.name)
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
        if self._selected:
            p.fillRect(rect, QColor(194, 65, 12, 26))
            bar_h = int(rect.height() * 0.8)
            bar_y = (rect.height() - bar_h) // 2
            p.setBrush(QColor("#c2410c"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(0, bar_y, 3, bar_h), 1.5, 1.5)
        elif self._hover:
            p.fillRect(rect, QColor(0, 0, 0, 10))
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

    def __init__(self, store: ConversationStore, foamo_icon: QIcon, parent=None):
        super().__init__(parent)
        self.store = store
        self.foamo_icon = foamo_icon
        self._cards: dict[str, ConversationCard] = {}
        self._current_key = "chat"
        self.setFixedWidth(SIDEBAR_WIDTH)
        self.setStyleSheet(
            "Sidebar { background: rgba(247, 247, 248, 0.72); }"
        )
        self._build()
        store.entry_added.connect(self._rebuild)
        store.entry_removed.connect(self._rebuild)
        store.entry_changed.connect(self._on_entry_changed)
        self._rebuild()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.search = QLineEdit(placeholderText="搜索 / 切换项目")
        self.search.setFixedHeight(32)
        self.search.setStyleSheet(
            "QLineEdit { background: rgba(0,0,0,0.04); border: none;"
            " border-radius: 16px; padding: 0 12px; font-size: 12px; }"
            "QLineEdit:focus { border: 1.5px solid #c2410c; }"
        )
        top.addWidget(self.search, 1)
        self.add_btn = QPushButton("＋")
        self.add_btn.setFixedSize(32, 32)
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.setStyleSheet(
            "QPushButton { background: rgba(0,0,0,0.04); border-radius: 16px;"
            " font-size: 16px; color: #555; }"
            "QPushButton:hover { background: rgba(0,0,0,0.08); }"
        )
        self.add_btn.clicked.connect(self.add_project_requested.emit)
        top.addWidget(self.add_btn)
        layout.addLayout(top)
        self.card_area = QVBoxLayout()
        self.card_area.setSpacing(6)
        self.card_area.setContentsMargins(0, 4, 0, 0)
        layout.addLayout(self.card_area)
        layout.addStretch(1)

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
        for i, entry in enumerate(entries):
            card = ConversationCard(entry, self.foamo_icon)
            card.clicked.connect(self.card_clicked.emit)
            card.rightClicked.connect(self._on_right_click)
            card.set_selected(entry.key == self._current_key)
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
