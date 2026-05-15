"""单个会话的 claude -p 子进程封装. UI 无关, 通过信号交流."""

from __future__ import annotations
import json
import os
import uuid
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, pyqtSignal


class ClaudeWorker(QObject):
    """Wraps a single `claude -p` QProcess subprocess for one conversation.

    Emits structured signals; never touches UI directly.
    """

    text_chunk = pyqtSignal(str)        # streaming text delta
    tool_event = pyqtSignal(dict)       # tool-related event dict
    started = pyqtSignal()              # process started
    finished = pyqtSignal(int)          # exit code
    error = pyqtSignal(str)             # error message string
    session_captured = pyqtSignal(str)  # session_id from first event that has one

    def __init__(
        self,
        conv_key: str,
        conv_dir: Path,
        claude_bin: str,
        cwd: str,
        parent=None,
    ):
        super().__init__(parent)
        self.conv_key = conv_key
        self.conv_dir = Path(conv_dir)
        self.claude_bin = claude_bin
        self.cwd = cwd
        self._proc: Optional[QProcess] = None
        self._stopping: bool = False
        self._reset_state()

    # ------------------------------------------------------------------
    # Worker-local state — reset before every send()
    # ------------------------------------------------------------------

    def _reset_state(self) -> None:
        self._stdout_buf: bytes = b""
        self._captured_sid: Optional[str] = None
        self._current_text: str = ""
        self._tool_input_accum: dict[int, str] = {}   # index -> accumulated partial_json
        self._tool_index_to_id: dict[int, str] = {}   # index -> tool_use_id
        self._tool_count: int = 0

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------

    def _session_file(self) -> Path:
        return self.conv_dir / "session"

    def _load_session(self) -> Optional[str]:
        f = self._session_file()
        if f.exists():
            sid = f.read_text(encoding="utf-8").strip()
            return sid or None
        return None

    def _save_session(self, sid: str) -> None:
        self.conv_dir.mkdir(parents=True, exist_ok=True)
        self._session_file().write_text(sid, encoding="utf-8")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        return self._proc is not None

    def send(
        self,
        prompt: str,
        perm_mode: str,
        model: Optional[str],
        hook_settings: Optional[Path],
        mcp_file: Optional[Path],
        env_extra: dict,
    ) -> None:
        """Start a claude -p subprocess for this conversation turn."""
        if self.is_running():
            self.error.emit("worker busy")
            return

        self._stopping = False
        self._reset_state()

        sid = self._load_session()

        args = [
            "-p",
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",
            "--permission-mode", perm_mode,
        ]
        if model:
            args += ["--model", model]
        if hook_settings is not None and Path(hook_settings).exists():
            args += ["--settings", str(hook_settings)]
        if mcp_file is not None and Path(mcp_file).exists():
            args += ["--mcp-config", str(mcp_file)]
        if sid:
            args += ["--resume", sid]
        else:
            args += ["--session-id", str(uuid.uuid4())]

        proc = QProcess(self)
        proc.setProgram(self.claude_bin)
        proc.setArguments(args)
        proc.setWorkingDirectory(self.cwd)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)

        # Build environment
        env = QProcessEnvironment.systemEnvironment()
        env.insert("FOAMO_CONV_KEY", self.conv_key)
        for k, v in (env_extra or {}).items():
            env.insert(str(k), str(v))
        proc.setProcessEnvironment(env)

        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_proc_error)

        prompt_bytes = prompt.encode("utf-8")

        def _feed_and_signal():
            self.started.emit()
            try:
                proc.write(prompt_bytes)
                proc.closeWriteChannel()
            except Exception as exc:
                self.error.emit(f"feed stdin failed: {exc}")

        proc.started.connect(_feed_and_signal)

        self._proc = proc
        proc.start()

    def stop(self) -> None:
        """Kill the subprocess if running."""
        if self.is_running():
            self._stopping = True
            try:
                self._proc.kill()
                self._proc.waitForFinished(2000)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # QProcess slots
    # ------------------------------------------------------------------

    def _on_stdout(self) -> None:
        if self._proc is None:
            return
        self._stdout_buf += bytes(self._proc.readAllStandardOutput())
        while b"\n" in self._stdout_buf:
            line, self._stdout_buf = self._stdout_buf.split(b"\n", 1)
            line = line.decode("utf-8", errors="replace").strip()
            if line:
                self._handle_event(line)

    def _on_finished(self, exit_code: int, _exit_status) -> None:
        was_stopped = self._stopping
        self._stopping = False  # reset for next send

        # Flush any partial line remaining in buffer
        if self._stdout_buf:
            for ln in self._stdout_buf.decode("utf-8", errors="replace").splitlines():
                ln = ln.strip()
                if ln:
                    self._handle_event(ln)
            self._stdout_buf = b""

        # Persist session id if newly captured or rotated
        if self._captured_sid and self._captured_sid != self._load_session():
            self._save_session(self._captured_sid)

        # Emit fallback text if claude produced nothing (skip on user-initiated stop)
        if not self._current_text and not was_stopped:
            self.text_chunk.emit(f"⚠️ (claude 退出码 {exit_code},无输出)")

        self.tool_event.emit({
            "kind": "assistant_done",
            "full_text": self._current_text,
            "char_count": len(self._current_text),
            "session_id": self._captured_sid or self._load_session(),
            "tool_count": self._tool_count,
        })

        self._proc = None
        self.finished.emit(exit_code)

    def _on_proc_error(self, err) -> None:
        self.error.emit(str(err))

    # ------------------------------------------------------------------
    # Event parsing
    # ------------------------------------------------------------------

    def _handle_event(self, line: str) -> None:
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            return
        try:
            self._handle_parsed_event(evt)
        except Exception as e:
            self.error.emit(f"event handling failed: {e}")

    def _handle_parsed_event(self, evt: dict) -> None:
        # Capture session id from the very first event that carries it
        if not self._captured_sid:
            sid = evt.get("session_id")
            if sid:
                self._captured_sid = sid
                self.session_captured.emit(sid)

        t = evt.get("type")

        # --include-partial-messages fine-grained stream events
        if t == "stream_event":
            inner = evt.get("event") or {}
            et = inner.get("type")

            if et == "content_block_delta":
                delta = inner.get("delta") or {}
                dtype = delta.get("type")
                if dtype == "text_delta":
                    txt = delta.get("text", "")
                    if txt:
                        self._current_text += txt
                        self.text_chunk.emit(txt)
                elif dtype == "input_json_delta":
                    idx = inner.get("index")
                    pj = delta.get("partial_json") or ""
                    self._tool_input_accum[idx] = (
                        self._tool_input_accum.get(idx, "") + pj
                    )
                    self.tool_event.emit({
                        "kind": "input_chunk",
                        "index": idx,
                        "partial_json": pj,
                    })

            elif et == "content_block_start":
                cb = inner.get("content_block") or {}
                if cb.get("type") == "tool_use":
                    idx = inner.get("index")
                    name = cb.get("name", "?")
                    tid = cb.get("id", "")
                    self._tool_index_to_id[idx] = tid
                    self._tool_count += 1
                    self.tool_event.emit({
                        "kind": "use_start",
                        "name": name,
                        "tool_use_id": tid,
                        "index": idx,
                    })
                    # If start already carries input, emit input_ready immediately
                    pre_input = cb.get("input")
                    if pre_input:
                        self.tool_event.emit({
                            "kind": "input_ready",
                            "name": name,
                            "tool_use_id": tid,
                            "input": pre_input,
                        })
                        # Already emitted; remove so content_block_stop won't double-fire
                        self._tool_index_to_id.pop(idx, None)

            elif et == "content_block_stop":
                idx = inner.get("index")
                tid = self._tool_index_to_id.pop(idx, None)
                input_text = self._tool_input_accum.pop(idx, "")
                if tid is not None:
                    parsed: dict = {}
                    if input_text:
                        try:
                            parsed = json.loads(input_text)
                        except Exception:
                            pass
                    self.tool_event.emit({
                        "kind": "input_ready",
                        "name": "",   # consumer must look up name via tid
                        "tool_use_id": tid,
                        "input": parsed,
                    })
            return  # stream_event handled; don't fall through

        # Fallback: full assistant message (when --include-partial-messages absent)
        if t == "assistant":
            if self._current_text:
                return  # already got streaming text; skip duplicate
            msg = evt.get("message") or {}
            for c in (msg.get("content") or []):
                ct = c.get("type")
                if ct == "text":
                    txt = c.get("text", "")
                    if txt:
                        self._current_text += txt
                        self.text_chunk.emit(txt)
                elif ct == "tool_use":
                    tid = c.get("id", "")
                    name = c.get("name", "?")
                    self._tool_count += 1
                    self.tool_event.emit({
                        "kind": "use_start",
                        "name": name,
                        "tool_use_id": tid,
                        "index": -1,
                    })
                    inp = c.get("input")
                    if inp:
                        self.tool_event.emit({
                            "kind": "input_ready",
                            "name": name,
                            "tool_use_id": tid,
                            "input": inp,
                        })

        elif t == "user":
            # user messages may contain tool_result blocks
            msg = evt.get("message") or {}
            for c in (msg.get("content") or []):
                if c.get("type") == "tool_result":
                    tid = c.get("tool_use_id", "")
                    content = c.get("content")
                    result_text = ""
                    if isinstance(content, str):
                        result_text = content
                    elif isinstance(content, list):
                        parts = []
                        for ci in content:
                            if isinstance(ci, dict) and ci.get("type") == "text":
                                parts.append(ci.get("text", ""))
                        result_text = "\n".join(parts)
                    self.tool_event.emit({
                        "kind": "result",
                        "tool_use_id": tid,
                        "name": "",
                        "content": result_text,
                        "is_error": bool(c.get("is_error")),
                    })

        elif t == "result":
            res = evt.get("result")
            if isinstance(res, str) and res and not self._current_text:
                self._current_text += res
                self.text_chunk.emit(res)
