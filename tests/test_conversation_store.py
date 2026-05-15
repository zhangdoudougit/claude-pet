# tests/test_conversation_store.py
import time
import tempfile
from pathlib import Path
import pytest
from conversation_store import ConversationStore, ConversationEntry


@pytest.fixture
def tmp_store(tmp_path: Path):
    return ConversationStore(state_dir=tmp_path)


def test_default_chat_entry_exists(tmp_store):
    entries = tmp_store.list_entries()
    assert len(entries) == 1
    assert entries[0].key == "chat"
    assert entries[0].kind == "chat"
    assert entries[0].name == "闲聊"


def test_add_project(tmp_store, tmp_path):
    proj_path = tmp_path / "demo_proj"
    proj_path.mkdir()
    entry = tmp_store.add_project(
        path=str(proj_path), name="demo_proj",
        short_code="DEM", color="#E07A5F",
    )
    assert entry.kind == "project"
    assert entry.short_code == "DEM"
    assert entry.color == "#E07A5F"
    assert entry.last_active_ts > 0
    # meta.json 落盘
    meta = (tmp_path / "conv" / entry.key / "meta.json")
    assert meta.exists()


def test_list_sort_chat_pinned_projects_by_active(tmp_store, tmp_path):
    p1 = tmp_path / "p1"; p1.mkdir()
    p2 = tmp_path / "p2"; p2.mkdir()
    e1 = tmp_store.add_project(str(p1), "p1", "P1", "#E07A5F")
    e2 = tmp_store.add_project(str(p2), "p2", "P2", "#5B7553")
    # 给 e1 更新时间, 让 e2 反而看起来更新
    tmp_store.touch(e2.key)
    time.sleep(0.01)
    tmp_store.touch(e1.key)
    entries = tmp_store.list_entries()
    assert entries[0].key == "chat"           # 闲聊置顶
    assert entries[1].key == e1.key           # 最近活跃在前
    assert entries[2].key == e2.key


def test_set_badge_emits_signal(tmp_store, qtbot):
    proj_path = tmp_store.state_dir / "conv" / "tmp"
    proj_path.mkdir(parents=True, exist_ok=True)
    e = tmp_store.add_project(str(proj_path), "x", "X", "#E07A5F")
    with qtbot.waitSignal(tmp_store.entry_changed, timeout=500) as blocker:
        tmp_store.set_badge(e.key, "thinking")
    assert blocker.args[0] == e.key


def test_migrate_legacy_meta(tmp_path):
    """老 meta.json 只有 path/name 字段, 启动时迁移补全."""
    conv_dir = tmp_path / "conv" / "proj_legacy"
    conv_dir.mkdir(parents=True)
    (conv_dir / "meta.json").write_text(
        '{"path": "C:/legacy", "name": "legacy_proj"}',
        encoding="utf-8",
    )
    store = ConversationStore(state_dir=tmp_path)
    entries = [e for e in store.list_entries() if e.kind == "project"]
    assert len(entries) == 1
    assert entries[0].short_code is not None
    assert entries[0].color in ConversationStore.COLOR_PALETTE
    assert entries[0].last_active_ts > 0


def test_delete_project(tmp_store, tmp_path):
    p = tmp_path / "todelete"; p.mkdir()
    e = tmp_store.add_project(str(p), "todelete", "TOD", "#7C8290")
    key = e.key
    tmp_store.delete_project(key, purge_history=True)
    assert all(en.key != key for en in tmp_store.list_entries())
    assert not (tmp_store.state_dir / "conv" / key).exists()


def test_cannot_delete_chat(tmp_store):
    with pytest.raises(ValueError):
        tmp_store.delete_project("chat", purge_history=False)


def test_update_entry_only_mutates_whitelist(tmp_store, tmp_path):
    p = tmp_path / "u"; p.mkdir()
    e = tmp_store.add_project(str(p), "u", "U", "#E07A5F")
    tmp_store.update_entry(e.key, name="renamed", color="#3D5A6C",
                           kind="chat", badge="thinking")  # last two ignored
    updated = tmp_store.get(e.key)
    assert updated.name == "renamed"
    assert updated.color == "#3D5A6C"
    assert updated.kind == "project"      # not mutated
    assert updated.badge == "none"        # not mutated


def test_bump_unread_sets_badge_and_increments(tmp_store, tmp_path):
    p = tmp_path / "b"; p.mkdir()
    e = tmp_store.add_project(str(p), "b", "B", "#7C8290")
    tmp_store.bump_unread(e.key)
    tmp_store.bump_unread(e.key)
    updated = tmp_store.get(e.key)
    assert updated.badge == "unread"
    assert updated.unread_count == 2
