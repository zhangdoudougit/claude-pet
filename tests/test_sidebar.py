from pathlib import Path
from PyQt6.QtGui import QIcon
from conversation_store import ConversationStore
from sidebar import Sidebar, AddProjectDialog


def test_sidebar_renders_chat_card(tmp_path, qapp, qtbot):
    store = ConversationStore(state_dir=tmp_path)
    sb = Sidebar(store, QIcon())
    qtbot.addWidget(sb)
    assert "chat" in sb._cards
    assert sb._cards["chat"].entry.name == "闲聊"


def test_sidebar_adds_card_on_store_add(tmp_path, qapp, qtbot):
    store = ConversationStore(state_dir=tmp_path)
    sb = Sidebar(store, QIcon())
    qtbot.addWidget(sb)
    proj = tmp_path / "x"; proj.mkdir()
    e = store.add_project(str(proj), "x", "X", "#E07A5F")
    assert e.key in sb._cards


def test_add_dialog_default_code(tmp_path, qapp, qtbot):
    store = ConversationStore(state_dir=tmp_path)
    dlg = AddProjectDialog(store, QIcon())
    qtbot.addWidget(dlg)
    proj = tmp_path / "smart_plc_v2"; proj.mkdir()
    dlg.path_edit.setText(str(proj))
    assert dlg.code_edit.text() == "SPV"


def test_card_click_emits_signal(tmp_path, qapp, qtbot):
    store = ConversationStore(state_dir=tmp_path)
    sb = Sidebar(store, QIcon())
    qtbot.addWidget(sb)
    sb.show()
    received = []
    sb.card_clicked.connect(received.append)
    card = sb._cards["chat"]
    qtbot.mouseClick(card, qt_button := __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.MouseButton.LeftButton)
    assert received == ["chat"]


def test_sidebar_removes_card_on_store_delete(tmp_path, qapp, qtbot):
    store = ConversationStore(state_dir=tmp_path)
    sb = Sidebar(store, QIcon())
    qtbot.addWidget(sb)
    proj = tmp_path / "y"; proj.mkdir()
    e = store.add_project(str(proj), "y", "Y", "#5B7553")
    assert e.key in sb._cards
    store.delete_project(e.key, purge_history=False)
    assert e.key not in sb._cards


def test_set_current_updates_selection(tmp_path, qapp, qtbot):
    store = ConversationStore(state_dir=tmp_path)
    sb = Sidebar(store, QIcon())
    qtbot.addWidget(sb)
    proj = tmp_path / "z"; proj.mkdir()
    e = store.add_project(str(proj), "z", "Z", "#7B6CA8")
    sb.set_current(e.key)
    assert sb._cards[e.key]._selected
    assert not sb._cards["chat"]._selected
