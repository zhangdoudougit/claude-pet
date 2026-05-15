"""Manual smoke: open new ChatWindow with redesigned visuals + dummy projects.

Run: python tests/smoke_visual_redesign.py
Expected visuals:
- 32px self-drawn title bar (icon + 和泡沫聊 + sun/moon + min/max/close)
- Sidebar warm bg, cards with subtitle, footer 设置 button
- ChatHeader with PetAvatar + mood line + StatusPill
- Composer rounded card + toolbar + green 发送 button
- Click ☀/🌙 to flip themes
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication
import chat_window
from conversation_store import ConversationStore


def main():
    app = QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="foamo_visual_"))
    chat_window.STATE_DIR = tmp
    chat_window.CONV_DIR = tmp / "conv"
    (tmp / "conv").mkdir(parents=True, exist_ok=True)
    chat_window.DEFAULT_CWD = str(tmp)

    store = ConversationStore(state_dir=tmp)
    p1 = tmp / "smart_tpm_edge_api"; p1.mkdir()
    store.add_project(str(p1), "smart_tpm_edge_api", "API", "#7fb993")
    p2 = tmp / "foamo_pet"; p2.mkdir()
    store.add_project(str(p2), "foamo_pet", "FP", "#e6c386")
    p3 = tmp / "smart_tpm_web_v2"; p3.mkdir()
    store.add_project(str(p3), "smart_tpm_web_v2", "V2", "#e6b186")

    win = chat_window.ChatWindow()
    win.show()
    print(f"[visual smoke] tmp state: {tmp}")
    print("[visual smoke] click sun/moon icon to flip theme")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
