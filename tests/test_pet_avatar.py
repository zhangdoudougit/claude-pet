from pet_avatar import PetAvatar


def test_avatar_constructs(qapp, qtbot):
    a = PetAvatar(size=28, mood="idle")
    qtbot.addWidget(a)
    assert a.width() == 28
    assert a.height() == 28
    assert a.mood == "idle"


def test_mood_can_change(qapp, qtbot):
    a = PetAvatar(size=32, mood="idle")
    qtbot.addWidget(a)
    a.set_mood("talking")
    assert a.mood == "talking"


def test_invalid_mood_falls_back(qapp, qtbot):
    a = PetAvatar(size=28, mood="grumpy")
    qtbot.addWidget(a)
    assert a.mood == "idle"


def test_set_dark_does_not_crash(qapp, qtbot):
    a = PetAvatar(size=28, mood="idle")
    qtbot.addWidget(a)
    a.set_dark(True)
    a.set_dark(False)
