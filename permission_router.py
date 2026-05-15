# permission_router.py
"""主进程内 QLocalServer, 监听 hook 调起的权限请求并路由到对应卡片.

启动失败时降级 — 不抛, 不污染调用方. hook 端连不上时自然走兜底独立弹窗."""

from __future__ import annotations
import json

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket


SOCKET_NAME = "foamo_perm_v1"


class PermissionRouter(QObject):
    """主进程持有一个实例. 收到请求 emit signal, 等响应回写 socket.

    Signal contract:
        permission_requested(conv_key: str, payload: dict, responder: callable)
        — responder 接收 'allow' / 'deny' 字符串
        started — 监听成功
        start_failed(error_msg: str) — 监听失败, hook 端会自动降级
    """

    permission_requested = pyqtSignal(str, dict, object)
    started = pyqtSignal()
    start_failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._available = False
        # 清理上次没关掉的同名监听
        QLocalServer.removeServer(SOCKET_NAME)
        if self._server.listen(SOCKET_NAME):
            self._available = True
            self._server.newConnection.connect(self._on_new_connection)
            self.started.emit()
        else:
            self.start_failed.emit(self._server.errorString())

    def is_available(self) -> bool:
        return self._available

    def socket_name(self) -> str:
        return SOCKET_NAME

    def _on_new_connection(self):
        socket = self._server.nextPendingConnection()
        if socket is None:
            return
        # 每个 connection 只 dispatch 一次, 避免 chunked read 时 readyRead 多触发.
        handled = {"v": False}

        def on_ready():
            if handled["v"]:
                return
            # 等待对端写完一整条 JSON (我们的协议: 一条 JSON 一次写入)
            if socket.bytesAvailable() == 0:
                return
            handled["v"] = True
            self._handle_socket(socket)

        socket.readyRead.connect(on_ready)
        socket.disconnected.connect(socket.deleteLater)

    def _handle_socket(self, socket: QLocalSocket):
        raw = bytes(socket.readAll()).decode("utf-8", errors="replace")
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            self._respond(socket, "deny", reason="bad_json")
            return

        conv_key = req.get("conv_key", "")
        payload = req.get("payload", {}) or {}

        def responder(decision: str):
            self._respond(socket, decision)

        try:
            self.permission_requested.emit(conv_key, payload, responder)
        except Exception:
            self._respond(socket, "deny", reason="emit_failed")

    def _respond(self, socket: QLocalSocket, decision: str, reason: str = ""):
        msg = {"decision": decision}
        if reason:
            msg["reason"] = reason
        try:
            socket.write(json.dumps(msg).encode("utf-8"))
            socket.flush()
            socket.disconnectFromServer()
        except Exception:
            pass
