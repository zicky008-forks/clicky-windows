"""Rules 7 and 8: a beta user never sees an AI provider, an API key, a model or an engine name in the tray, the chat
panel or Cài đặt. Those items show only in technical mode: CLICKY_TECH_MODE=1, or the switch on the "Kỹ thuật" tab,
which appears only when Cài đặt is opened with Shift held."""
import re
from unittest.mock import Mock

import pytest

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtWidgets import QAbstractButton, QComboBox, QLabel, QLineEdit, QTabWidget  # noqa: E402

import tech_mode  # noqa: E402

HIDDEN = re.compile(r"\b(" + "|".join(tech_mode.HIDDEN_WORDS) + r")\b", re.I)


@pytest.fixture
def plain(qt_app, monkeypatch):
    monkeypatch.delenv(tech_mode.ENV, raising=False)
    return qt_app


def _texts(root):
    out = []
    for w in root.findChildren(QtWidgets.QWidget):
        if not w.isVisibleTo(root):
            continue
        if isinstance(w, (QLabel, QAbstractButton)):
            out.append(w.text())
        elif isinstance(w, QLineEdit):
            out += [w.text(), w.placeholderText()]
        elif isinstance(w, QComboBox):
            out += [w.itemText(i) for i in range(w.count())]
    return [t for t in out if t]


def _settings_texts(window):
    out = [window.windowTitle()]
    tabs = window.findChild(QTabWidget)
    for i in range(tabs.count()):
        if tabs.isTabVisible(i):
            out.append(tabs.tabText(i))
            out += _texts(tabs.widget(i))
    return out


def _menu_texts(menu):
    out = []
    for action in menu.actions():
        if not action.isVisible():
            continue
        out.append(action.text())
        if action.menu():
            out += _menu_texts(action.menu())
    return out


def _tray_texts(tray):
    return [tray._tray.toolTip()] + _menu_texts(tray._menu)


def _leaks(texts):
    return [t for t in texts if HIDDEN.search(t)]


def _settings():
    from ui.settings_window import SettingsWindow
    return SettingsWindow(Mock())


def test_tray_panel_and_settings_say_nothing_technical(plain):
    from ui.panel import CompanionPanel
    from ui.tray import TrayManager
    tray, panel, settings = TrayManager(), CompanionPanel(), _settings()
    assert not _leaks(_tray_texts(tray))
    assert not _leaks(_texts(panel))
    settings.present(shift=False)
    texts = _settings_texts(settings)
    assert not _leaks(texts)
    assert "Kỹ thuật" not in texts and "Cài đặt Clicky" in texts
    assert {"Mở Clicky", "Nhật ký gửi đi", "Cài đặt…", "Thoát Clicky"} <= set(_menu_texts(tray._menu))
    settings.close()
    panel.close()
    tray.hide_icon()


def test_the_technical_tab_appears_only_with_shift_and_brings_the_items_back(plain):
    from ui.panel import CompanionPanel
    from ui.tray import TrayManager
    tray, panel, settings = TrayManager(), CompanionPanel(), _settings()
    settings.present(shift=True)
    assert "Kỹ thuật" in _settings_texts(settings)
    assert not _leaks(_settings_texts(settings))          # the switch itself names nothing technical
    settings.tech_mode_changed.connect(lambda _on: (tray.rebuild_menu(), panel.apply_tech_mode()))
    settings.tech_switch.setChecked(True)
    assert tech_mode.on()
    assert "API Keys…" in _menu_texts(tray._menu)
    assert _leaks(_settings_texts(settings)) and _leaks(_texts(panel))
    settings.tech_switch.setChecked(False)
    assert not tech_mode.on()
    assert not _leaks(_tray_texts(tray)) and not _leaks(_texts(panel)) and not _leaks(_settings_texts(settings))
    settings.close()
    assert "Kỹ thuật" not in _settings_texts(settings)     # closed without technical mode: the tab hides again
    panel.close()
    tray.hide_icon()


def test_the_environment_switch_keeps_robot_and_dev_runs_technical(qt_app, monkeypatch):
    monkeypatch.setenv(tech_mode.ENV, "1")
    from ui.tray import TrayManager
    tray, settings = TrayManager(), _settings()
    menu = _menu_texts(tray._menu)
    assert "API Keys…" in menu and any(t.startswith("Model:") for t in menu)
    settings.present()
    assert "Kỹ thuật" in _settings_texts(settings) and not settings.tech_switch.isEnabled()
    settings.close()
    tray.hide_icon()


def test_the_old_panel_setting_still_counts(plain):
    from storage import set_setting
    set_setting("advanced_panel", True)
    assert tech_mode.on()
    tech_mode.set_on(False)
    assert not tech_mode.on()


def test_a_saved_engine_choice_stays_listed_without_its_name(plain):
    from storage import set_setting
    set_setting("tts_provider", "elevenlabs")
    settings = _settings()
    assert settings.provider.currentData() == "elevenlabs"
    assert settings.provider.currentText() == "Lựa chọn đang dùng"
    assert not _leaks(_settings_texts(settings))
    settings.close()


def test_update_errors_are_plain_unless_technical(plain):
    settings = _settings()
    settings.show_update({"error": "ConnectError: [Errno -2] Name or service not known"})
    assert settings.update_status.text() == "Em chưa kiểm tra được bản mới. Chị thử lại sau nhé."
    settings.show_update(None)
    assert settings.update_status.text() == "Chị đang dùng bản mới nhất."
    settings.close()


def test_the_tray_local_only_item_follows_the_setting(plain, monkeypatch):
    from office.outgoing import LOCAL_ONLY_LABEL
    from storage import set_setting
    from ui.tray import TrayManager
    set_setting("local_only", True)
    tray = TrayManager()
    item = next(a for a in tray._menu.actions() if a.text() == LOCAL_ONLY_LABEL)
    assert item.isCheckable() and item.isChecked() and item.isEnabled()
    seen = []
    tray.on_toggle_local_only.connect(seen.append)
    item.trigger()
    assert seen == [False]
    monkeypatch.setenv("CLICKY_LOCAL_ONLY", "1")
    tray.rebuild_menu()
    item = next(a for a in tray._menu.actions() if a.text() == LOCAL_ONLY_LABEL)
    assert item.isChecked() and not item.isEnabled()
    tray.hide_icon()
