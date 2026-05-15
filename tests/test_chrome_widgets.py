from pathlib import Path
from PyQt6.QtCore import Qt
from chrome_widgets import TitleBar, ThemeToggleButton, WinControls
from theme import ThemeManager


def test_titlebar_constructs(tmp_path, qapp, qtbot):
    mgr = ThemeManager(tmp_path / "theme")
    bar = TitleBar(mgr, app_label="和泡沫聊")
    qtbot.addWidget(bar)
    assert bar.height() == 32
    assert bar.label.text() == "和泡沫聊"


def test_theme_toggle_button_flips(tmp_path, qapp, qtbot):
    mgr = ThemeManager(tmp_path / "theme")
    btn = ThemeToggleButton(mgr)
    qtbot.addWidget(btn)
    assert mgr.name == "warm"
    btn.click()
    assert mgr.name == "glass"


def test_winctrls_close_signal(qapp, qtbot):
    ctrls = WinControls()
    qtbot.addWidget(ctrls)
    closed = []
    ctrls.close_clicked.connect(lambda: closed.append(True))
    ctrls.close_btn.click()
    assert closed == [True]


def test_winctrls_minimize_signal(qapp, qtbot):
    ctrls = WinControls()
    qtbot.addWidget(ctrls)
    minned = []
    ctrls.minimize_clicked.connect(lambda: minned.append(True))
    ctrls.min_btn.click()
    assert minned == [True]


def test_winctrls_maximize_signal(qapp, qtbot):
    ctrls = WinControls()
    qtbot.addWidget(ctrls)
    maxed = []
    ctrls.maximize_clicked.connect(lambda: maxed.append(True))
    ctrls.max_btn.click()
    assert maxed == [True]
