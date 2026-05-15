import json
import pytest
from PyQt6.QtNetwork import QLocalSocket
from permission_router import PermissionRouter, SOCKET_NAME


def _read_response(socket: QLocalSocket, qtbot) -> dict:
    """Wait for server to write response, then parse it. Pumps Qt event loop."""
    # waitForReadyRead pumps the event loop and returns when bytes arrive.
    qtbot.waitUntil(lambda: socket.bytesAvailable() > 0
                            or socket.state() == QLocalSocket.LocalSocketState.UnconnectedState,
                    timeout=3000)
    raw = bytes(socket.readAll()).decode("utf-8", errors="replace")
    return json.loads(raw)


def test_router_starts_and_routes_allow(qapp, qtbot):
    router = PermissionRouter()
    assert router.is_available()
    received = []

    def on_request(key, payload, responder):
        received.append((key, payload))
        responder("allow")

    router.permission_requested.connect(on_request)

    socket = QLocalSocket()
    socket.connectToServer(SOCKET_NAME)
    assert socket.waitForConnected(1000)

    msg = json.dumps({
        "conv_key": "proj_demo",
        "payload": {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": "."},
    }).encode("utf-8")
    socket.write(msg)
    socket.flush()
    qtbot.waitUntil(lambda: len(received) == 1, timeout=2000)
    assert received[0][0] == "proj_demo"
    assert received[0][1]["tool_name"] == "Bash"

    resp = _read_response(socket, qtbot)
    assert resp["decision"] == "allow"


def test_router_routes_deny(qapp, qtbot):
    router = PermissionRouter()
    assert router.is_available()

    def on_request(key, payload, responder):
        responder("deny")

    router.permission_requested.connect(on_request)

    socket = QLocalSocket()
    socket.connectToServer(SOCKET_NAME)
    assert socket.waitForConnected(1000)
    msg = json.dumps({"conv_key": "x", "payload": {}}).encode("utf-8")
    socket.write(msg)
    socket.flush()

    resp = _read_response(socket, qtbot)
    assert resp["decision"] == "deny"


def test_router_handles_bad_json(qapp, qtbot):
    router = PermissionRouter()
    assert router.is_available()

    socket = QLocalSocket()
    socket.connectToServer(SOCKET_NAME)
    assert socket.waitForConnected(1000)
    socket.write(b"NOT JSON")
    socket.flush()

    resp = _read_response(socket, qtbot)
    assert resp["decision"] == "deny"
    assert resp.get("reason") == "bad_json"
