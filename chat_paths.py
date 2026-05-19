"""chat_paths — 聊天链路用到的路径常量 + 工具函数

从老 chat_window.py 拆出来. 新 web 链路 (chat_bridge / chat_web_window /
settings_dialog) 全走这里, 不再 import chat_window.
"""

from __future__ import annotations
import json
import os
import shutil
import time
import uuid
from pathlib import Path


# ---------------- 路径常量 ----------------
ROOT = Path(__file__).parent
STATE_DIR = ROOT / ".chat_state"
STATE_DIR.mkdir(exist_ok=True)
CONV_DIR = STATE_DIR / "conv"
CONV_DIR.mkdir(exist_ok=True)
PASTED_DIR = STATE_DIR / "pasted"
PASTED_DIR.mkdir(exist_ok=True)
PASTED_KEEP = 50
PASTED_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

DEBUG_LOG = STATE_DIR / "debug.log"
PROXY_FILE = STATE_DIR / "proxy"
PERMISSION_MODE_FILE = STATE_DIR / "permission_mode"
HOOK_SETTINGS_FILE = STATE_DIR / "hook_settings.json"
THEME_FILE = STATE_DIR / "theme"   # 内容 "dark" / "light", 兼容老 ThemeManager

CLAUDE_BIN = shutil.which("claude") or "claude"
DEFAULT_CWD = str(ROOT)


# ---------------- 配置选项 ----------------
PERMISSION_MODES = [
    ("default", "严格 (每个敏感工具弹确认)"),
    ("acceptEdits", "自动接受改动 (Bash 仍弹)"),
    ("bypassPermissions", "全放行 (危险!)"),
]

# "" 表示不传 --model, 跟 Claude Code 全局走
MODEL_OPTIONS = [
    ("",       "默认"),
    ("sonnet", "Sonnet"),
    ("opus",   "Opus"),
    ("haiku",  "Haiku"),
]
MODEL_KEYS = [k for k, _ in MODEL_OPTIONS]


# ---------------- 工具函数 ----------------
def _log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with DEBUG_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _resolve_proxy() -> str | None:
    if PROXY_FILE.exists():
        url = PROXY_FILE.read_text(encoding="utf-8").strip()
        if url:
            return url
    return (
        os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    )


# ---------------- 权限模式 ----------------
def load_permission_mode() -> str:
    if PERMISSION_MODE_FILE.exists():
        m = PERMISSION_MODE_FILE.read_text(encoding="utf-8").strip()
        if m in [k for k, _ in PERMISSION_MODES]:
            return m
    return "default"


def save_permission_mode(mode: str):
    if mode not in [k for k, _ in PERMISSION_MODES]:
        return
    try:
        PERMISSION_MODE_FILE.write_text(mode, encoding="utf-8")
    except Exception:
        pass


# ---------------- 深浅色 (B6 持久化) ----------------
def load_dark() -> bool:
    """读取 .chat_state/theme. 文件内容 "dark" / "light", 缺省 light."""
    try:
        if THEME_FILE.exists():
            v = THEME_FILE.read_text(encoding="utf-8").strip().lower()
            return v == "dark"
    except Exception:
        pass
    return False


def save_dark(dark: bool):
    try:
        THEME_FILE.parent.mkdir(parents=True, exist_ok=True)
        THEME_FILE.write_text("dark" if dark else "light", encoding="utf-8")
    except Exception:
        pass


# ---------------- hook settings 自举 ----------------
def _ensure_hook_settings():
    """保证 hook_settings.json 存在且 command 指向当前 ROOT 的 permission_dialog.py.
    让 clone 后零配置可用 — 路径会跟当前安装位置走.
    """
    dialog_script = (ROOT / "permission_dialog.py").resolve()
    if not dialog_script.exists():
        return
    expected_cmd = f'python "{dialog_script.as_posix()}"'
    expected_config = {
        "hooks": {
            "PreToolUse": [{
                "matcher": "Bash|Edit|MultiEdit|Write|NotebookEdit|WebFetch",
                "hooks": [{
                    "type": "command",
                    "command": expected_cmd,
                    "timeout": 180,
                }]
            }]
        }
    }
    if HOOK_SETTINGS_FILE.exists():
        try:
            cur = json.loads(HOOK_SETTINGS_FILE.read_text(encoding="utf-8"))
            cur_cmd = (cur.get("hooks", {})
                       .get("PreToolUse", [{}])[0]
                       .get("hooks", [{}])[0]
                       .get("command", ""))
            if cur_cmd == expected_cmd:
                return
        except Exception:
            pass
    try:
        HOOK_SETTINGS_FILE.write_text(
            json.dumps(expected_config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _log(f"hook_settings.json regenerated → {dialog_script}")
    except Exception as e:
        _log(f"hook_settings 写入失败: {e}")


# ---------------- 粘贴图片落盘 ----------------
def _pasted_stem() -> str:
    return time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]


def _trim_pasted_dir(keep: int = PASTED_KEEP):
    """保留最近 keep 张, 多余按 mtime 旧的先删. 静默吞错."""
    try:
        files = [f for f in PASTED_DIR.iterdir() if f.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files[keep:]:
            try:
                f.unlink()
            except Exception:
                pass
    except Exception:
        pass
