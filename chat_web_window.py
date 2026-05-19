"""ChatWebWindow — 新一代聊天窗 (替代 PyQt 自绘 ChatWindow / ConversationPanel).

架构:
- frameless QMainWindow
- Win11 Mica 半透明 backdrop
- 32px 自绘 title bar (拖动 + min/max/close + theme toggle)
- QWebEngineView 加载 web/index.html, 透明背景 (Mica 透出)
- QWebChannel 把 ChatBridge 暴露给 JS, 双向通信

Python 后端 (ClaudeWorker / ConversationStore) 完全保留, 不动。
"""

from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Optional

# 开 Chromium 远程调试 — 这里 setdefault 只在单独跑 chat_web_window 时兜底,
# 正常路径 foamo_pet.py 已经在最早处设置好了.
# 注意: 只能走 chromium flags, 不能再额外设 QTWEBENGINE_REMOTE_DEBUGGING,
# 否则 Chromium 会试图 bind 两次同端口报 WSAEADDRINUSE.
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--remote-debugging-port=9876 --remote-allow-origins=*",
)

from PyQt6.QtCore import Qt, QUrl, QPoint, QSize, QEvent, QRectF
from PyQt6.QtGui import (
    QIcon, QPainter, QPainterPath, QRegion, QColor, QPen, QFont, QMouseEvent,
)
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel,
    QSizePolicy,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings, QWebEngineProfile
from PyQt6.QtWebChannel import QWebChannel

from conversation_store import ConversationStore
from chat_bridge import ChatBridge
from permission_router import PermissionRouter
from chat_paths import load_dark


ROOT = Path(__file__).parent
WEB_DIR = ROOT / "web"
INDEX_HTML = WEB_DIR / "index.html"


# ============================================================
# Title bar widget — 32px, drag, win controls, theme toggle
# ============================================================

class _TitleBar(QWidget):
    def __init__(self, parent_window: QMainWindow):
        super().__init__(parent_window)
        self._win = parent_window
        self.setFixedHeight(32)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # 透明让 Mica 透出, 不画底色
        self.setStyleSheet("background: transparent;")

        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 0, 0)
        h.setSpacing(0)

        # 左侧 app 名 (淡墨色, 小字)
        self.title_label = QLabel("泡沫")
        f = QFont()
        f.setPointSize(9)
        f.setWeight(QFont.Weight.Medium)
        self.title_label.setFont(f)
        self.title_label.setStyleSheet("color: #6b6457; background: transparent;")
        h.addWidget(self.title_label)
        h.addStretch(1)

        # theme toggle (月→深色 / 日→浅色)
        self.theme_btn = _IconBtn(parent=self, glyph="moon", tooltip="切到深色")
        self.theme_btn.clicked.connect(
            lambda: self._win.toggle_dark() if hasattr(self._win, "toggle_dark") else None
        )
        h.addWidget(self.theme_btn)

        # min / max / close
        self.min_btn = _IconBtn(parent=self, glyph="min", tooltip="最小化")
        self.max_btn = _IconBtn(parent=self, glyph="max", tooltip="最大化")
        self.close_btn = _IconBtn(parent=self, glyph="close", tooltip="关闭")
        self.min_btn.clicked.connect(self._win.showMinimized)
        self.max_btn.clicked.connect(self._toggle_max)
        self.close_btn.clicked.connect(self._win.close)
        h.addWidget(self.min_btn)
        h.addWidget(self.max_btn)
        h.addWidget(self.close_btn)

        self._drag_origin: Optional[QPoint] = None

    def _toggle_max(self):
        if self._win.isMaximized():
            self._win.showNormal()
        else:
            self._win.showMaximized()

    # 拖动
    def mousePressEvent(self, ev: QMouseEvent):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = ev.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            ev.accept()

    def mouseMoveEvent(self, ev: QMouseEvent):
        if (ev.buttons() & Qt.MouseButton.LeftButton) and self._drag_origin is not None:
            if self._win.isMaximized():
                self._win.showNormal()
            self._win.move(ev.globalPosition().toPoint() - self._drag_origin)
            ev.accept()

    def mouseReleaseEvent(self, ev: QMouseEvent):
        self._drag_origin = None

    def mouseDoubleClickEvent(self, ev: QMouseEvent):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._toggle_max()


class _IconBtn(QPushButton):
    """标题栏图标按钮 — 44x32 (close 同尺寸, hover 红)."""

    GLYPH_CLOSE_HOVER = "#c42b1c"

    def __init__(self, parent: QWidget, glyph: str, tooltip: str = ""):
        super().__init__(parent)
        self._glyph = glyph
        self._hover = False
        self.setFixedSize(44, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setFlat(True)
        self.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
        )

    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        from PyQt6.QtCore import QLineF, QRectF, QPointF
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # hover bg
        if self._hover:
            if self._glyph == "close":
                p.fillRect(self.rect(), QColor(self.GLYPH_CLOSE_HOVER))
            else:
                p.fillRect(self.rect(), QColor(0, 0, 0, 18))
        # glyph: 深色模式下取淡墨, 浅色取深墨
        win = self.window()
        is_dark = getattr(win, "_dark", False)
        base_stroke = QColor("#f0ebdd") if is_dark else QColor("#1d1b16")
        stroke = (QColor("#ffffff") if (self._hover and self._glyph == "close")
                  else base_stroke)
        pen = QPen(stroke, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        if self._glyph == "min":
            p.drawLine(QLineF(cx - 5, cy, cx + 5, cy))
        elif self._glyph == "max":
            p.drawRect(QRectF(cx - 5, cy - 5, 10, 10))
        elif self._glyph == "close":
            p.drawLine(QLineF(cx - 5, cy - 5, cx + 5, cy + 5))
            p.drawLine(QLineF(cx + 5, cy - 5, cx - 5, cy + 5))
        elif self._glyph == "moon":
            # crescent: 大圆 - 偏移小圆
            full = QPainterPath()
            full.addEllipse(QRectF(cx - 5, cy - 5, 10, 10))
            cut = QPainterPath()
            cut.addEllipse(QRectF(cx - 2.5, cy - 6, 11, 11))
            crescent = full.subtracted(cut)
            p.setBrush(stroke)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(crescent)
        elif self._glyph == "sun":
            # 圆 + 8 道短线 (太阳)
            p.drawEllipse(QPointF(cx, cy), 3.0, 3.0)
            import math
            for i in range(8):
                a = i * math.pi / 4
                r1, r2 = 5.0, 7.0
                p.drawLine(QLineF(cx + r1 * math.cos(a), cy + r1 * math.sin(a),
                                  cx + r2 * math.cos(a), cy + r2 * math.sin(a)))


# ============================================================
# ChatWebWindow
# ============================================================

class ChatWebWindow(QMainWindow):
    """带 Mica + frameless + WebView 聊天窗."""

    def __init__(self, state_dir: Path, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(900, 600)
        self.resize(1100, 720)
        self.setWindowTitle("泡沫")
        try:
            self.setWindowIcon(QIcon(str(ROOT / "foamo.ico")))
        except Exception:
            pass
        self._corner_radius = 12
        # B6: 从磁盘恢复 dark 状态. ChatBridge 也读同一份, JS bootstrap 时同步.
        self._dark = load_dark()

        # ---------- store ----------
        self.store = ConversationStore(state_dir, parent=self)

        # ---------- central layout: title bar + web view ----------
        central = QWidget(self)
        central.setObjectName("central_container")
        central.setStyleSheet("#central_container { background: transparent; }")
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self.title_bar = _TitleBar(self)
        v.addWidget(self.title_bar)

        # ---------- web view ----------
        self.web = QWebEngineView(self)
        self.web.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # 透明背景: Mica 透出
        self.web.page().setBackgroundColor(QColor(0, 0, 0, 0))
        # 关 cache — file:// 本地资源更新后, 重启不要还吃旧版 HTML/CSS/JS
        profile = self.web.page().profile()
        try:
            profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
            profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
            )
            profile.clearHttpCache()
        except Exception:
            pass
        # 关掉缓存上下文菜单 (dev 可改 True)
        s = self.web.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        v.addWidget(self.web, 1)

        self.setCentralWidget(central)

        # ---------- bridge + channel ----------
        self.bridge = ChatBridge(self.store, parent=self)
        self.channel = QWebChannel(self.web.page())
        self.channel.registerObject("bridge", self.bridge)
        self.web.page().setWebChannel(self.channel)

        # ---------- permission router ----------
        # 起一个本地 socket server (foamo_perm_v1), 让 hook 子进程
        # (permission_dialog.py) 把权限请求路由到主进程, 主进程统一弹原生
        # PyQt 对话框. 跟老 ChatWindow 行为一致.
        self.router = PermissionRouter(self)
        self.router.permission_requested.connect(self._on_perm_request)

        # ---------- load page ----------
        # cachebust: 用 index.html 的 mtime 作 query, 改动文件后强制重新加载
        try:
            ver = str(int(INDEX_HTML.stat().st_mtime))
        except Exception:
            ver = "0"
        url = QUrl.fromLocalFile(str(INDEX_HTML))
        url.setQuery(f"v={ver}")
        self.web.load(url)

        # ---------- mica ----------
        self._enable_mica()
        # B6: 启动恢复 dark, 同步标题栏 chrome (glyph 月亮→太阳, 字色变浅)
        if self._dark:
            self._apply_dark_chrome()

    # ----- Mica + rounded corners (复用 ChatWindow 的实现) -----

    def _enable_mica(self):
        if not sys.platform.startswith("win"):
            return
        try:
            import ctypes
            from ctypes import wintypes
            hwnd = int(self.winId())
            backdrop = ctypes.c_int(2)  # DWMSBT_MAINWINDOW (Mica)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                wintypes.DWORD(38),  # DWMWA_SYSTEMBACKDROP_TYPE
                ctypes.byref(backdrop), ctypes.sizeof(backdrop),
            )
            # 跟随当前 _dark 同步 mica 颜色
            self._apply_dark_backdrop(getattr(self, "_dark", False))
        except Exception:
            pass

    def _apply_dark_backdrop(self, dark: bool):
        """让 Mica 跟着主题变深/变浅 (DWMWA_USE_IMMERSIVE_DARK_MODE = 20).

        无标题栏窗口也吃这个 attribute — 它控制 mica/acrylic 的底色,
        不只是 caption 颜色。
        """
        if not sys.platform.startswith("win"):
            return
        try:
            import ctypes
            from ctypes import wintypes
            hwnd = int(self.winId())
            value = ctypes.c_int(1 if dark else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                wintypes.DWORD(20),  # DWMWA_USE_IMMERSIVE_DARK_MODE
                ctypes.byref(value), ctypes.sizeof(value),
            )
        except Exception:
            pass

    def _apply_rounded_mask(self):
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self.width(), self.height()),
                            self._corner_radius, self._corner_radius)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.isMaximized():
            self.clearMask()
        else:
            self._apply_rounded_mask()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.Type.WindowStateChange:
            if self.isMaximized():
                self.clearMask()
            else:
                self._apply_rounded_mask()

    def showEvent(self, e):
        super().showEvent(e)
        self._apply_rounded_mask()
        self._enable_mica()

    # ----- bridge callbacks (供 ChatBridge 反向调用) -----

    def open_add_project_dialog(self):
        """Bridge.request_add_project → 弹原生 AddProjectDialog (在 sidebar.py)."""
        from add_project_dialog import AddProjectDialog
        foamo_icon = QIcon(str(ROOT / "foamo.ico"))
        dlg = AddProjectDialog(self.store, foamo_icon, parent=self)
        dlg.exec()

    def open_edit_project_dialog(self, key: str):
        """Bridge.request_edit_project → 弹 AddProjectDialog(editing=entry)."""
        if not key or key == "chat":
            return
        entry = self.store.get(key)
        if entry is None or entry.kind != "project":
            return
        from add_project_dialog import AddProjectDialog
        foamo_icon = QIcon(str(ROOT / "foamo.ico"))
        dlg = AddProjectDialog(self.store, foamo_icon, editing=entry, parent=self)
        dlg.exec()

    def confirm_delete_project(self, key: str):
        """Bridge.request_delete_project → 弹 QMessageBox, 三档 (仅移除/连历史/取消).
        行为对齐老版 sidebar._confirm_delete (sidebar.py:730).
        """
        if not key or key == "chat":
            return
        entry = self.store.get(key)
        if entry is None or entry.kind != "project":
            return
        from PyQt6.QtWidgets import QMessageBox
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
        elif clicked == purge_btn:
            self.store.delete_project(key, purge_history=True)

    def open_settings_dialog(self):
        try:
            from settings_dialog import SettingsDialog
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "设置加载失败", str(e))
            return
        dlg = SettingsDialog(self)
        dlg.exec()

    # ----- permission routing (主进程内统一弹原生 dialog) -----

    def _on_perm_request(self, conv_key: str, payload: dict, responder):
        """收到 hook 子进程的权限请求 — 弹原生 PyQt dialog 让用户决定."""
        # 1. 给那条对话挂 permission badge (sidebar 红点) → JS 会收到 conversations_changed
        try:
            self.store.set_badge(conv_key, "permission")
        except Exception:
            pass
        # 2. 把窗口拉前台, 避免弹框出现在背景里被忽略
        try:
            self.show()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass
        # 3. 弹原生 dialog (复用 permission_dialog.show_dialog)
        try:
            from permission_dialog import show_dialog
        except Exception:
            responder("deny")
            self._clear_perm_badge(conv_key)
            return
        tool_name = payload.get("tool_name", "?")
        tool_input = payload.get("tool_input", {}) or {}
        cwd = payload.get("cwd", "") or ""
        try:
            allowed = show_dialog(tool_name, tool_input, cwd)
        except Exception:
            responder("deny")
            self._clear_perm_badge(conv_key)
            return
        responder("allow" if allowed else "deny")
        self._clear_perm_badge(conv_key)

    def _clear_perm_badge(self, conv_key: str):
        try:
            entry = self.store.get(conv_key)
            if entry is not None and entry.badge == "permission":
                self.store.set_badge(conv_key, "none")
        except Exception:
            pass

    def toggle(self):
        """Show / hide. FoamoWidget.open_chat 直接调."""
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def toggle_dark(self):
        """标题栏月亮/太阳按钮触发, 切换 web 侧的 .dark + 标题栏 glyph + mica 底色."""
        self._dark = not getattr(self, "_dark", False)
        # mica 跟着变 (顶部一起变深)
        self._apply_dark_backdrop(self._dark)
        # 通知 bridge → JS (bridge.set_dark 同时写盘 — B6)
        self.bridge.set_dark(self._dark)
        # 标题栏 chrome 同步
        self._apply_dark_chrome()

    def _apply_dark_chrome(self):
        """同步标题栏按钮 / glyph / 字色到当前 self._dark.
        toggle_dark 和 __init__ (启动恢复 dark 状态) 都调.
        """
        # 强制所有标题栏按钮 repaint — paintEvent 读 window._dark 决定 glyph 颜色,
        # 不刷的话要等 hover 才换色.
        for btn in (self.title_bar.min_btn, self.title_bar.max_btn,
                    self.title_bar.close_btn, self.title_bar.theme_btn):
            btn.update()
        # 月亮/太阳切换
        new_glyph = "sun" if self._dark else "moon"
        self.title_bar.theme_btn._glyph = new_glyph
        self.title_bar.theme_btn.setToolTip(
            "切到浅色" if self._dark else "切到深色"
        )
        self.title_bar.theme_btn.update()
        # 字色调亮 (深色模式)
        if self._dark:
            self.title_bar.title_label.setStyleSheet(
                "color: rgba(245,240,230,0.78); background: transparent;"
            )
        else:
            self.title_bar.title_label.setStyleSheet(
                "color: #6b6457; background: transparent;"
            )
