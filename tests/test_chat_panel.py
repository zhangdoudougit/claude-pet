import json
import pytest
from pathlib import Path
from conversation_store import ConversationStore
from claude_worker import ClaudeWorker
from chat_panel import ConversationPanel


def test_panel_loads_existing_history(tmp_path, qapp, qtbot):
    """Panel constructed with existing history.json should render N rows."""
    proj = tmp_path / "demo_proj"
    proj.mkdir()
    store = ConversationStore(state_dir=tmp_path)
    entry = store.add_project(str(proj), "demo_proj", "DEM", "#E07A5F")
    history_file = tmp_path / "conv" / entry.key / "history.json"
    history_file.write_text(json.dumps([
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "hello back"},
        {"role": "user", "text": "second turn"},
    ], ensure_ascii=False), encoding="utf-8")

    worker = ClaudeWorker(entry.key, history_file.parent, "claude", str(proj))
    panel = ConversationPanel(entry, store, worker)
    qtbot.addWidget(panel)

    assert panel.loaded_history_count() == 3


def test_panel_no_history_empty(tmp_path, qapp, qtbot):
    """Panel for a fresh entry has zero loaded rows."""
    store = ConversationStore(state_dir=tmp_path)
    entry = store.get("chat")
    conv_dir = tmp_path / "conv" / "chat"
    conv_dir.mkdir(parents=True, exist_ok=True)
    worker = ClaudeWorker("chat", conv_dir, "claude", str(tmp_path))
    panel = ConversationPanel(entry, store, worker)
    qtbot.addWidget(panel)
    assert panel.loaded_history_count() == 0


def test_panel_text_chunk_appends_to_assistant_bubble(tmp_path, qapp, qtbot):
    """Receiving text_chunk should create/extend an assistant bubble."""
    store = ConversationStore(state_dir=tmp_path)
    entry = store.get("chat")
    conv_dir = tmp_path / "conv" / "chat"
    conv_dir.mkdir(parents=True, exist_ok=True)
    worker = ClaudeWorker("chat", conv_dir, "claude", str(tmp_path))
    panel = ConversationPanel(entry, store, worker)
    qtbot.addWidget(panel)

    # Simulate text_chunk emission
    worker.text_chunk.emit("Hello ")
    worker.text_chunk.emit("world!")
    # Internal aggregation should contain the full text
    assert panel._current_text == "Hello world!"


def test_panel_assistant_done_persists_history(tmp_path, qapp, qtbot):
    """assistant_done event should write to history.json."""
    proj = tmp_path / "p"; proj.mkdir()
    store = ConversationStore(state_dir=tmp_path)
    entry = store.add_project(str(proj), "p", "P", "#7C8290")
    conv_dir = tmp_path / "conv" / entry.key
    worker = ClaudeWorker(entry.key, conv_dir, "claude", str(proj))
    panel = ConversationPanel(entry, store, worker)
    qtbot.addWidget(panel)

    worker.text_chunk.emit("answer text")
    worker.tool_event.emit({
        "kind": "assistant_done",
        "full_text": "answer text",
        "char_count": 11,
        "session_id": None,
        "tool_count": 0,
    })

    history_file = conv_dir / "history.json"
    assert history_file.exists()
    data = json.loads(history_file.read_text(encoding="utf-8"))
    assert any(row.get("role") == "assistant" and row.get("text") == "answer text"
               for row in data)


def test_panel_has_redesigned_header(tmp_path, qapp, qtbot):
    from conversation_store import ConversationStore
    from claude_worker import ClaudeWorker
    from chat_panel import ConversationPanel
    from pet_avatar import PetAvatar
    from status_pill import StatusPill
    store = ConversationStore(state_dir=tmp_path)
    entry = store.get("chat")
    conv_dir = tmp_path / "conv" / "chat"
    conv_dir.mkdir(parents=True, exist_ok=True)
    worker = ClaudeWorker("chat", conv_dir, "claude", str(tmp_path))
    panel = ConversationPanel(entry, store, worker)
    qtbot.addWidget(panel)
    assert isinstance(panel.header_avatar, PetAvatar)
    assert isinstance(panel.status_pill, StatusPill)
    assert panel.mood_line.text().startswith("正在和你")
