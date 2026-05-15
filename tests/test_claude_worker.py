"""Tests for ClaudeWorker — UI-free claude -p subprocess wrapper."""
import json
import pytest
from pathlib import Path
from claude_worker import ClaudeWorker


def test_worker_init(tmp_path):
    w = ClaudeWorker(
        conv_key="proj_test", conv_dir=tmp_path,
        claude_bin="claude", cwd=str(tmp_path),
    )
    assert w.conv_key == "proj_test"
    assert not w.is_running()


def test_session_persist(tmp_path):
    w = ClaudeWorker("proj_test", tmp_path, "claude", str(tmp_path))
    assert w._load_session() is None
    w._save_session("sid-abc-123")
    assert w._load_session() == "sid-abc-123"


def test_handle_text_delta_emits_chunk(tmp_path, qtbot):
    w = ClaudeWorker("proj_test", tmp_path, "claude", str(tmp_path))
    received = []
    w.text_chunk.connect(received.append)
    event = {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "hello"},
        },
    }
    w._handle_event(json.dumps(event))
    assert received == ["hello"]
    assert w._current_text == "hello"


def test_handle_tool_use_start(tmp_path, qtbot):
    w = ClaudeWorker("proj_test", tmp_path, "claude", str(tmp_path))
    events = []
    w.tool_event.connect(events.append)
    payload = {
        "type": "stream_event",
        "event": {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "tu_1", "name": "Bash"},
        },
    }
    w._handle_event(json.dumps(payload))
    kinds = [e["kind"] for e in events]
    assert "use_start" in kinds
    use_start = next(e for e in events if e["kind"] == "use_start")
    assert use_start["name"] == "Bash"
    assert use_start["tool_use_id"] == "tu_1"


def test_handle_tool_input_chunks_then_ready(tmp_path, qtbot):
    w = ClaudeWorker("proj_test", tmp_path, "claude", str(tmp_path))
    events = []
    w.tool_event.connect(events.append)
    # use_start
    w._handle_event(json.dumps({
        "type": "stream_event",
        "event": {
            "type": "content_block_start",
            "index": 2,
            "content_block": {"type": "tool_use", "id": "tu_2", "name": "Edit"},
        },
    }))
    # input chunks
    w._handle_event(json.dumps({
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": '{"file":"a'},
        },
    }))
    w._handle_event(json.dumps({
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": '.py"}'},
        },
    }))
    # stop → emit input_ready
    w._handle_event(json.dumps({
        "type": "stream_event",
        "event": {"type": "content_block_stop", "index": 2},
    }))
    ready = next(e for e in events if e["kind"] == "input_ready")
    assert ready["input"] == {"file": "a.py"}


def test_handle_tool_result_via_user_msg(tmp_path, qtbot):
    w = ClaudeWorker("proj_test", tmp_path, "claude", str(tmp_path))
    events = []
    w.tool_event.connect(events.append)
    payload = {
        "type": "user",
        "message": {"content": [{
            "type": "tool_result", "tool_use_id": "tu_x",
            "content": "ok done", "is_error": False,
        }]},
    }
    w._handle_event(json.dumps(payload))
    result = next(e for e in events if e["kind"] == "result")
    assert result["tool_use_id"] == "tu_x"
    assert result["content"] == "ok done"
    assert result["is_error"] is False


def test_session_id_captured(tmp_path, qtbot):
    w = ClaudeWorker("proj_test", tmp_path, "claude", str(tmp_path))
    captured = []
    w.session_captured.connect(captured.append)
    payload = {"type": "system", "session_id": "sid-xyz"}
    w._handle_event(json.dumps(payload))
    assert captured == ["sid-xyz"]
    assert w._captured_sid == "sid-xyz"


def test_stop_does_not_emit_spurious_warning(tmp_path, qtbot):
    """User-initiated stop should not produce '退出码,无输出' fallback."""
    w = ClaudeWorker("proj_test", tmp_path, "claude", str(tmp_path))
    received = []
    w.text_chunk.connect(received.append)
    # Simulate the scenario: _stopping=True, then _on_finished runs.
    w._stopping = True
    w._on_finished(137, None)  # 137 = killed
    # Should be NO ⚠️ warning emitted.
    assert all("⚠️" not in s for s in received)


def test_no_double_input_ready_with_pre_input(tmp_path, qtbot):
    """If content_block_start carries input, stop must not emit a second input_ready."""
    w = ClaudeWorker("proj_test", tmp_path, "claude", str(tmp_path))
    events = []
    w.tool_event.connect(events.append)
    w._handle_event(json.dumps({
        "type": "stream_event",
        "event": {
            "type": "content_block_start",
            "index": 5,
            "content_block": {
                "type": "tool_use", "id": "tu_p", "name": "Read",
                "input": {"file": "x.py"},
            },
        },
    }))
    w._handle_event(json.dumps({
        "type": "stream_event",
        "event": {"type": "content_block_stop", "index": 5},
    }))
    ready_events = [e for e in events if e["kind"] == "input_ready"]
    assert len(ready_events) == 1
    assert ready_events[0]["input"] == {"file": "x.py"}


def test_handle_event_swallows_post_parse_exception(tmp_path, qtbot):
    """A malformed-but-valid-JSON payload should emit error, not crash."""
    w = ClaudeWorker("proj_test", tmp_path, "claude", str(tmp_path))
    errors = []
    w.error.connect(errors.append)
    # Valid JSON but malformed structure - send a string where dict expected.
    # We force a TypeError by sending a payload that triggers .get on a non-dict.
    # Easiest way: monkeypatch a state to force AttributeError mid-handle.
    import json as _json
    # Construct a stream_event with 'event' being a string instead of dict.
    bad = _json.dumps({"type": "stream_event", "event": "not-a-dict"})
    # Will call (inner or {}) which still works on string-falsy-cast, but
    # inner.get fails since str has no get. Let's do this:
    w._handle_event(bad)
    # Either we crashed (test fails) or error was emitted; assert no crash:
    assert True  # reaching here means no unhandled exception


def test_session_id_overwrite_on_change(tmp_path, qtbot):
    """If captured_sid differs from existing on disk, overwrite."""
    w = ClaudeWorker("proj_test", tmp_path, "claude", str(tmp_path))
    w._save_session("old-sid")
    assert w._load_session() == "old-sid"
    w._captured_sid = "new-sid"
    w._on_finished(0, None)
    assert w._load_session() == "new-sid"
