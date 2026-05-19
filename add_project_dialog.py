"""AddProjectDialog — 添加 / 编辑项目对话框

从老 sidebar.py 抽出来 (整个 PyQt 自绘 Sidebar 主类已废, 只有这个 dialog 还活).
被 chat_web_window.py 复用: open_add_project_dialog / open_edit_project_dialog.
"""

from __future__ import annotations
import re
from pathlib import Path

from PyQt6.QtCore import Qt, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QFont, QIcon
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit,
    QLabel, QDialog, QFileDialog, QMessageBox,
)

from conversation_store import ConversationStore, ConversationEntry


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
        for e in self.store.list_entries():
            if e.kind == "project" and e.path == path:
                QMessageBox.information(self, "已存在", f"项目「{e.name}」已经在列表里")
                self.accept()
                return
        self.store.add_project(path=path, name=Path(path).name,
                               short_code=code, color=self._selected_color)
        self.accept()
