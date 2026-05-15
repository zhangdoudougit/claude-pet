from pathlib import Path
import pytest
from theme import ThemeManager, WARM, GLASS


@pytest.fixture
def tmp_mgr(tmp_path):
    return ThemeManager(state_file=tmp_path / "theme")


def test_default_is_warm(tmp_mgr):
    assert tmp_mgr.name == "warm"
    assert tmp_mgr.palette is WARM


def test_palette_warm_has_required_keys():
    for k in ("ink", "inkSoft", "inkMute", "paper", "paperWarm",
              "line", "lineSoft", "accent", "accentSoft", "accentLine",
              "codeBg", "codeInk"):
        assert k in WARM, f"missing key {k}"


def test_palette_glass_has_required_keys():
    for k in ("ink", "inkSoft", "inkMute", "glass1", "glass2", "glass3",
              "line", "lineSoft", "accent", "accentSoft", "accentLine",
              "codeBg", "codeInk"):
        assert k in GLASS, f"missing key {k}"


def test_set_persists(tmp_mgr, tmp_path):
    tmp_mgr.set("glass")
    assert (tmp_path / "theme").read_text(encoding="utf-8").strip() == "glass"


def test_load_existing(tmp_path):
    (tmp_path / "theme").write_text("glass", encoding="utf-8")
    m = ThemeManager(state_file=tmp_path / "theme")
    assert m.name == "glass"
    assert m.palette is GLASS


def test_signal_fires_on_change(tmp_mgr, qtbot):
    received = []
    tmp_mgr.theme_changed.connect(received.append)
    tmp_mgr.set("glass")
    assert received == ["glass"]


def test_toggle_flips(tmp_mgr):
    assert tmp_mgr.name == "warm"
    tmp_mgr.toggle()
    assert tmp_mgr.name == "glass"
    tmp_mgr.toggle()
    assert tmp_mgr.name == "warm"


def test_invalid_name_ignored(tmp_mgr):
    tmp_mgr.set("rainbow")
    assert tmp_mgr.name == "warm"
