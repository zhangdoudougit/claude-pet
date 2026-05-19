"""ChatBridge — Python ↔ JS 通信桥, 通过 QWebChannel 暴露给 web 前端.

职责:
- 管理 per-conversation ClaudeWorker (复用现有 ClaudeWorker 协议)
- 转发 worker signals 到 JS (text_chunk / finished / tool_event / error)
- 接收 JS slots (send_message / switch / get_history / request_bootstrap / ...)
- 与 ConversationStore 同步, 转发 conversations_changed
- 截图: JS 粘贴/拖入 → attach_image_b64 落盘到 .chat_state/pasted/ →
  send_message 时把路径拼成 markdown 图引用注入 prompt
"""

from __future__ import annotations
import base64
import json
import os
import time
import shutil
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from conversation_store import ConversationStore
from claude_worker import ClaudeWorker
from chat_paths import (
    ROOT, CLAUDE_BIN, DEFAULT_CWD, _resolve_proxy, HOOK_SETTINGS_FILE,
    _ensure_hook_settings,
    PASTED_DIR, PASTED_KEEP, PASTED_EXTS,
    _pasted_stem, _trim_pasted_dir,
    MODEL_OPTIONS, MODEL_KEYS, PERMISSION_MODES,
    load_permission_mode, save_permission_mode,
    load_dark, save_dark,
)
from mcp_manager import build_effective_mcp_file


# header select 用的短标签 — 避免原生 popup 因为长文本撑宽溢出窗口右侧
_PERM_SHORT_LABELS = {
    "default": "严格",
    "acceptEdits": "自动接受",
    "bypassPermissions": "全放行",
}
_MODEL_SHORT_LABELS = {
    "": "默认",
    "sonnet": "Sonnet",
    "opus": "Opus",
    "haiku": "Haiku",
}


def _entry_to_dict(e) -> dict:
    return {
        "key": e.key,
        "kind": e.kind,
        "name": e.name,
        "short_code": e.short_code,
        "color": e.color,
        "path": e.path,
        "last_active_ts": e.last_active_ts,
        "badge": e.badge,
        "unread_count": e.unread_count,
    }


class ChatBridge(QObject):
    # ====== signals → JS ======
    bootstrap = pyqtSignal(str)                  # JSON: {conversations, current_key, theme, dark, model_options, perm_modes, current_model, current_perm_mode}
    conversations_changed = pyqtSignal(str)      # JSON: {conversations}
    history_loaded = pyqtSignal(str, str)        # conv_key, JSON list[{role,text}]
    message_chunk = pyqtSignal(str, str)         # conv_key, text delta
    message_finished = pyqtSignal(str)           # conv_key
    status_changed = pyqtSignal(str, str)        # conv_key, status (idle/thinking/online)
    error_occurred = pyqtSignal(str, str)        # conv_key, msg
    tool_event_occurred = pyqtSignal(str, str)   # conv_key, JSON event
    theme_changed = pyqtSignal(str, bool)        # theme_name, dark
    model_changed = pyqtSignal(str, str)         # conv_key, model

    def __init__(self, store: ConversationStore, parent=None):
        super().__init__(parent)
        self.store = store
        self._workers: dict[str, ClaudeWorker] = {}
        self._current_key: Optional[str] = None
        self._stream_buf: dict[str, str] = {}
        self._theme: str = "warm"
        self._dark: bool = load_dark()   # B6: 从 .chat_state/theme 读

        # store signals → conversations_changed
        store.entry_added.connect(self._broadcast_conversations)
        store.entry_removed.connect(self._broadcast_conversations)
        store.entry_changed.connect(self._broadcast_conversations)

    # ------------------------------------------------------------------ slots

    @pyqtSlot()
    def request_bootstrap(self):
        """JS 启动时调用一次, 拿到全量状态."""
        current_key = self._current_key or self._default_current_key()
        payload = {
            "conversations": [_entry_to_dict(e) for e in self.store.list_entries()],
            "current_key": current_key,
            "theme": self._theme,
            "dark": self._dark,
            "model_options": [
                {"key": k, "label": _MODEL_SHORT_LABELS.get(k, v)}
                for k, v in MODEL_OPTIONS
            ],
            "perm_modes": [
                {"key": k, "label": _PERM_SHORT_LABELS.get(k, v)}
                for k, v in PERMISSION_MODES
            ],
            "current_model": self._load_model(current_key),
            "current_perm_mode": load_permission_mode(),
        }
        if self._current_key is None:
            self._current_key = current_key
        self.bootstrap.emit(json.dumps(payload, ensure_ascii=False))

    @pyqtSlot(str)
    def switch_conversation(self, key: str):
        if not key or key == self._current_key:
            return
        self._current_key = key
        self.store.touch(key)
        # 切会话 → 通知 JS 当前 model
        self.model_changed.emit(key, self._load_model(key))

    @pyqtSlot(str)
    def get_history(self, key: str):
        """JS 切换会话或刷新时拉历史."""
        hf = self.store.conv_dir / key / "history.json"
        rows = []
        if hf.exists():
            try:
                rows = json.loads(hf.read_text(encoding="utf-8"))
            except Exception:
                rows = []
        self.history_loaded.emit(key, json.dumps(rows, ensure_ascii=False))

    @pyqtSlot(str, str, str)
    def send_message(self, key: str, text: str, image_paths_json: str = ""):
        """发送消息. image_paths_json: JSON 字符串 list[str] 绝对路径, 拼成 markdown 注入 prompt."""
        if not key:
            return
        images: list[str] = []
        if image_paths_json:
            try:
                arr = json.loads(image_paths_json)
                if isinstance(arr, list):
                    images = [str(p) for p in arr if p]
            except Exception:
                images = []
        if not text.strip() and not images:
            return
        worker = self._ensure_worker(key)
        # 写历史 (user). 文本里把图片路径附在末尾, 让历史也看得到
        history_text = text
        if images:
            tail = "\n".join(f"![pasted]({p})" for p in images)
            history_text = (text + "\n\n" + tail) if text else tail
        self._append_history(key, "user", history_text)
        self.store.touch(key)
        self.store.set_badge(key, "thinking")
        self.status_changed.emit(key, "thinking")
        self._stream_buf[key] = ""
        # 注入代理 (国内连 Anthropic API 必须走代理, 否则 403)
        env_extra = self._build_env_extra()
        # hook settings: 首跑没文件 → 自动生成 (B10), 否则 permission_router 不会被走到
        try:
            _ensure_hook_settings()
            hook_settings = HOOK_SETTINGS_FILE if HOOK_SETTINGS_FILE.exists() else None
        except Exception:
            hook_settings = None
        # 加载 MCP 配置 (B1): build_effective_mcp_file 拼出 enabled=true 的 server
        try:
            mcp_file = build_effective_mcp_file(ROOT)
        except Exception:
            mcp_file = None
        perm_mode = load_permission_mode()
        # 当前 conv 的 model (空串=默认)
        model = self._load_model(key) or None
        # 拼最终 prompt: text + 图片 markdown 引用
        prompt = text
        if images:
            tail = "\n".join(f"![pasted]({p})" for p in images)
            prompt = (text + "\n\n" + tail) if text else tail
        try:
            worker.send(
                prompt=prompt,
                perm_mode=perm_mode,
                model=model,
                hook_settings=hook_settings,
                mcp_file=mcp_file,
                env_extra=env_extra,
            )
        except Exception as e:
            self.error_occurred.emit(key, f"启动失败: {e}")

    @pyqtSlot(str)
    def stop_message(self, key: str):
        w = self._workers.get(key)
        if w:
            try:
                w.stop()
            except Exception:
                pass

    @pyqtSlot(str)
    def clear_history(self, key: str):
        """清空指定会话的 history.json + session 文件 (下次发消息开新 Claude session).

        老 ChatWindow 的"🆕 开始新对话 (清空当前)"在新 web 版的等价入口.
        """
        if not key:
            return
        # 1. 先停 worker, 避免清完又被 finished 回写历史
        w = self._workers.get(key)
        if w:
            try:
                w.stop()
            except Exception:
                pass
        # 2. 删历史 + session (session 文件让下次 claude 不带 --resume, 开新会话)
        conv_dir = self.store.conv_dir / key
        for fn in ("history.json", "session"):
            f = conv_dir / fn
            try:
                if f.exists():
                    f.unlink()
            except Exception:
                pass
        # 3. 清流式 buf, 重置 badge / status
        self._stream_buf.pop(key, None)
        try:
            self.store.set_badge(key, "none")
        except Exception:
            pass
        # 4. 推空历史给 JS, 让 chat-body 重渲
        self.history_loaded.emit(key, "[]")
        self.status_changed.emit(key, "idle")
        self.store.touch(key)

    @pyqtSlot()
    def request_add_project(self):
        """JS 点 "+", 委托给 parent (ChatWebWindow) 弹原生 AddProjectDialog."""
        parent = self.parent()
        if parent and hasattr(parent, "open_add_project_dialog"):
            parent.open_add_project_dialog()

    @pyqtSlot(str)
    def request_edit_project(self, key: str):
        """JS 项目卡片右键 → 编辑项目, 委托给 parent 弹 AddProjectDialog(editing=...)."""
        parent = self.parent()
        if parent and hasattr(parent, "open_edit_project_dialog"):
            parent.open_edit_project_dialog(key)

    @pyqtSlot(str)
    def request_delete_project(self, key: str):
        """JS 项目卡片右键 → 删除项目, 委托 parent 弹原生确认对话框."""
        parent = self.parent()
        if parent and hasattr(parent, "confirm_delete_project"):
            parent.confirm_delete_project(key)

    @pyqtSlot()
    def request_settings(self):
        parent = self.parent()
        if parent and hasattr(parent, "open_settings_dialog"):
            parent.open_settings_dialog()

    @pyqtSlot(bool)
    def set_dark(self, dark: bool):
        """JS / 标题栏月亮按钮触发, 切换深浅色 (B6: 持久化)."""
        self._dark = bool(dark)
        save_dark(self._dark)
        self.theme_changed.emit(self._theme, self._dark)

    @pyqtSlot(str)
    def set_theme(self, theme: str):
        if theme in ("warm", "glass"):
            self._theme = theme
            self.theme_changed.emit(self._theme, self._dark)

    # ------------ 截图 / 附件 -----------------------------------

    @pyqtSlot(str, result=str)
    def attach_image_b64(self, data_url: str) -> str:
        """JS 粘贴或拖入图片 → 落盘到 .chat_state/pasted/.
        data_url: "data:image/png;base64,xxxxx" 或 纯 base64
        return: 落盘后的绝对路径 (失败返回 "")
        """
        if not data_url:
            return ""
        try:
            # 解析 data URL
            if data_url.startswith("data:"):
                head, b64 = data_url.split(",", 1)
                # 从 head 提 mime → 后缀
                mime = head[5:].split(";")[0]  # "image/png"
                ext_map = {
                    "image/png": ".png",
                    "image/jpeg": ".jpg",
                    "image/jpg": ".jpg",
                    "image/gif": ".gif",
                    "image/webp": ".webp",
                    "image/bmp": ".bmp",
                }
                suffix = ext_map.get(mime.lower(), ".png")
            else:
                b64 = data_url
                suffix = ".png"
            raw = base64.b64decode(b64)
            path = PASTED_DIR / f"{_pasted_stem()}{suffix}"
            path.write_bytes(raw)
            _trim_pasted_dir()
            return str(path)
        except Exception:
            return ""

    # ------------ 模型 / 权限模式 ----------------------------------

    @pyqtSlot(str, result=str)
    def get_model(self, key: str) -> str:
        return self._load_model(key)

    @pyqtSlot(str, str)
    def set_model(self, key: str, model: str):
        self._save_model(key, model or "")

    @pyqtSlot(str)
    def set_perm_mode(self, mode: str):
        if mode in [k for k, _ in PERMISSION_MODES]:
            save_permission_mode(mode)

    @pyqtSlot(result=str)
    def get_perm_mode(self) -> str:
        return load_permission_mode()

    # ------------------------------------------------------------------ helpers

    def _default_current_key(self) -> str:
        entries = self.store.list_entries()
        if entries:
            return entries[0].key
        return "chat"

    def _broadcast_conversations(self, *_args):
        payload = {
            "conversations": [_entry_to_dict(e) for e in self.store.list_entries()],
        }
        self.conversations_changed.emit(json.dumps(payload, ensure_ascii=False))

    def _ensure_worker(self, key: str) -> ClaudeWorker:
        if key in self._workers:
            return self._workers[key]
        entry = self.store.get(key)
        if entry is None:
            raise RuntimeError(f"未知会话: {key}")
        cwd = entry.path or DEFAULT_CWD
        conv_dir = self.store.conv_dir / key
        conv_dir.mkdir(parents=True, exist_ok=True)
        worker = ClaudeWorker(key, conv_dir, CLAUDE_BIN, cwd, parent=self)
        # wire worker → bridge signals
        worker.text_chunk.connect(lambda t, k=key: self._on_chunk(k, t))
        worker.tool_event.connect(lambda ev, k=key: self._on_tool(k, ev))
        worker.error.connect(lambda msg, k=key: self._on_error(k, msg))
        worker.finished.connect(lambda _code, k=key: self._on_finished(k))
        self._workers[key] = worker
        return worker

    def _on_chunk(self, key: str, text: str):
        self._stream_buf[key] = self._stream_buf.get(key, "") + text
        self.message_chunk.emit(key, text)

    def _on_tool(self, key: str, ev: dict):
        try:
            self.tool_event_occurred.emit(key, json.dumps(ev, ensure_ascii=False))
        except Exception:
            pass

    def _on_error(self, key: str, msg: str):
        self.error_occurred.emit(key, msg)
        self.store.set_badge(key, "none")
        self.status_changed.emit(key, "idle")

    def _on_finished(self, key: str):
        full = self._stream_buf.get(key, "")
        if full.strip():
            self._append_history(key, "assistant", full)
        self._stream_buf[key] = ""
        self.message_finished.emit(key)
        self.store.set_badge(key, "none")
        self.status_changed.emit(key, "idle")
        self.store.touch(key)

    def _append_history(self, key: str, role: str, text: str):
        hf = self.store.conv_dir / key / "history.json"
        hf.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        if hf.exists():
            try:
                rows = json.loads(hf.read_text(encoding="utf-8"))
            except Exception:
                rows = []
        rows.append({"role": role, "text": text, "ts": time.time()})
        hf.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    def _build_env_extra(self) -> dict:
        env_extra = {}
        try:
            proxy = _resolve_proxy()
        except Exception:
            proxy = None
        if proxy:
            env_extra["HTTP_PROXY"] = proxy
            env_extra["HTTPS_PROXY"] = proxy
            env_extra["http_proxy"] = proxy
            env_extra["https_proxy"] = proxy
        return env_extra

    # ---------- 模型存储: 每会话 conv_dir/<key>/model 一个文本文件 -----------
    def _model_file(self, key: str) -> Path:
        return self.store.conv_dir / key / "model"

    def _load_model(self, key: str) -> str:
        if not key:
            return ""
        f = self._model_file(key)
        if f.exists():
            try:
                v = f.read_text(encoding="utf-8").strip()
                if v:
                    return v
            except Exception:
                pass
        return ""

    def _save_model(self, key: str, model: str):
        if not key:
            return
        f = self._model_file(key)
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            if model:
                f.write_text(model, encoding="utf-8")
            else:
                if f.exists():
                    f.unlink()
        except Exception:
            pass
