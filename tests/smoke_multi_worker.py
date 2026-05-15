"""Manual smoke test for the sidebar refactor.

Run: `python tests/smoke_multi_worker.py` from project root.
Expected: ChatWindow opens with 闲聊 + 2 dummy projects in left sidebar.
"""
import sys
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication

import chat_window
from conversation_store import ConversationStore


def main():
    app = QApplication(sys.argv)

    # Use a temp state dir so we don't pollute user's real data.
    tmp = Path(tempfile.mkdtemp(prefix="foamo_smoke_"))
    chat_window.STATE_DIR = tmp
    chat_window.CONV_DIR = tmp / "conv"
    chat_window.DEFAULT_CWD = str(tmp)
    (tmp / "conv").mkdir(parents=True, exist_ok=True)

    # Pre-create two dummy project entries so the sidebar has content.
    store = ConversationStore(state_dir=tmp)
    p1 = tmp / "demo_alpha"
    p1.mkdir()
    store.add_project(str(p1), "demo_alpha", "DA", "#E07A5F")
    p2 = tmp / "demo_beta"
    p2.mkdir()
    store.add_project(str(p2), "demo_beta", "DB", "#4A6FA5")

    # NOTE: ChatWindow creates its own store at construction. To make it see
    # our pre-seeded entries, the constructor reads from STATE_DIR — which we
    # already pointed at tmp. So new store inside ChatWindow will load these.
    win = chat_window.ChatWindow()
    win.setWindowTitle("Foamo Sidebar Smoke Test")
    win.show()

    print(f"[smoke] state dir: {tmp}")
    print("[smoke] expected: sidebar has 闲聊 + DA + DB cards")
    print("[smoke] click cards to switch panels; right-click project card for menu")
    print("[smoke] click + button to open AddProjectDialog")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
