"""Tests for the new top-level ChatWindow shell (Task 6)."""
import json
from pathlib import Path
import pytest
from conversation_store import ConversationStore
from chat_panel import ConversationPanel


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Point chat_window's STATE_DIR / CONV_DIR / etc. at tmp_path."""
    import chat_window
    monkeypatch.setattr(chat_window, "STATE_DIR", tmp_path)
    monkeypatch.setattr(chat_window, "CONV_DIR", tmp_path / "conv")
    monkeypatch.setattr(chat_window, "DEFAULT_CWD", str(tmp_path))
    (tmp_path / "conv").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_chat_window_constructs_with_chat_entry(isolated_state, qapp, qtbot):
    from chat_window import ChatWindow
    win = ChatWindow()
    qtbot.addWidget(win)
    # Wait for deferred panel construction via QTimer.singleShot
    qtbot.waitUntil(lambda: "chat" in win._panels, timeout=2000)
    # Sidebar has chat card
    assert "chat" in win.sidebar._cards
    # Stack has one panel (chat)
    assert "chat" in win._panels
    assert isinstance(win._panels["chat"], ConversationPanel)


def test_chat_window_initial_current_is_chat(isolated_state, qapp, qtbot):
    from chat_window import ChatWindow
    win = ChatWindow()
    qtbot.addWidget(win)
    qtbot.waitUntil(lambda: "chat" in win._panels, timeout=2000)
    assert win._current_key == "chat"
    assert win.stack.currentWidget() is win._panels["chat"]


def test_adding_project_creates_panel(isolated_state, qapp, qtbot, tmp_path):
    from chat_window import ChatWindow
    win = ChatWindow()
    qtbot.addWidget(win)
    proj = tmp_path / "demo_x"
    proj.mkdir()
    entry = win.store.add_project(str(proj), "demo_x", "DEM", "#E07A5F")
    # Panel should be created (deferred construction via QTimer.singleShot 0 in test ctx)
    qtbot.waitUntil(lambda: entry.key in win._panels, timeout=2000)
    assert entry.key in win._panels


def test_switching_to_panel_updates_current(isolated_state, qapp, qtbot, tmp_path):
    from chat_window import ChatWindow
    win = ChatWindow()
    qtbot.addWidget(win)
    proj = tmp_path / "switchme"
    proj.mkdir()
    entry = win.store.add_project(str(proj), "switchme", "SWI", "#5B7553")
    qtbot.waitUntil(lambda: entry.key in win._panels, timeout=2000)
    win._switch_to(entry.key)
    assert win._current_key == entry.key
    assert win.stack.currentWidget() is win._panels[entry.key]


def test_deleting_project_removes_panel(isolated_state, qapp, qtbot, tmp_path):
    from chat_window import ChatWindow
    win = ChatWindow()
    qtbot.addWidget(win)
    proj = tmp_path / "del_me"
    proj.mkdir()
    entry = win.store.add_project(str(proj), "del_me", "DEL", "#7C8290")
    qtbot.waitUntil(lambda: entry.key in win._panels, timeout=2000)
    win.store.delete_project(entry.key, purge_history=False)
    assert entry.key not in win._panels
