"""Technical mode: the switch that shows provider, key, model and engine choices.

A beta user is an accountant. They never see an AI provider, an API key, a model name or an engine
(CLAUDE.md rules 7 and 8), so the tray, the chat panel and Settings show plain Vietnamese items only.
Testers and developers turn on technical mode in one of two ways:

* CLICKY_TECH_MODE=1 in the environment (robot and dev runs). It cannot be turned off from the app.
* Holding Shift while opening Cài đặt shows a "Kỹ thuật" tab with the switch. There is no visible
  checkbox for it otherwise.

The older "advanced_panel" setting (chat panel only) still counts, so existing test machines keep their view.
"""
import os

ENV = "CLICKY_TECH_MODE"
SETTING = "tech_mode"
LEGACY_SETTING = "advanced_panel"

# Words a beta user must never meet while technical mode is off (tests/test_plain_ui.py).
HIDDEN_WORDS = ("api", "key", "keys", "ollama", "llm", "stt", "tts", "copilot", "model", "models", "wizard")


def forced() -> bool:
    return os.getenv(ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def on() -> bool:
    if forced():
        return True
    try:
        from storage import setting
        return bool(setting(SETTING, False) or setting(LEGACY_SETTING, False))
    except Exception:
        return False


def set_on(value: bool) -> None:
    from storage import set_setting
    set_setting(SETTING, bool(value))
    if not value:
        set_setting(LEGACY_SETTING, False)
