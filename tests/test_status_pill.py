from status_pill import StatusPill


def test_pill_constructs_with_state(qapp, qtbot):
    pill = StatusPill(state="idle")
    qtbot.addWidget(pill)
    assert pill.state == "idle"
    assert pill.label.text() == "待机"


def test_pill_set_state_updates(qapp, qtbot):
    pill = StatusPill(state="idle")
    qtbot.addWidget(pill)
    pill.set_state("thinking")
    assert pill.state == "thinking"
    assert pill.label.text() == "思考中…"


def test_pill_online(qapp, qtbot):
    pill = StatusPill(state="online")
    qtbot.addWidget(pill)
    assert pill.label.text() == "在线"


def test_pill_invalid_state_ignored(qapp, qtbot):
    pill = StatusPill(state="idle")
    qtbot.addWidget(pill)
    pill.set_state("foobar")
    assert pill.state == "idle"  # unchanged
