"""First GitHub Release (v1.6.0): notes in plain Vietnamese whose first line Clicky says after a quiet update, and a
version base that keeps later CI builds above the release so a quiet update never replaces a newer test build."""
import re
from pathlib import Path

import updates

NOTES = Path(__file__).resolve().parents[1] / "docs" / "phat-hanh"


def test_every_release_note_opens_with_one_short_plain_line():
    files = sorted(p for p in NOTES.glob("*.md") if re.fullmatch(r"\d+\.\d+\.\d+\.md", p.name))
    assert files and files[0].name == "1.6.0.md"
    for path in files:
        text = path.read_text(encoding="utf-8")
        first = text.splitlines()[0]
        assert not first.startswith("#") and len(first) <= 120, path.name
        assert updates.first_note(text) == first
        assert not re.search(r"\b(api|key|ollama|llm|model|copilot)\b", text, re.I), path.name


def test_ci_builds_after_the_release_sort_above_it_and_the_old_ones_below():
    released = updates.version_key("1.6.0")
    assert updates.version_key(updates.VERSION + ".ci12") > released
    assert updates.version_key("1.6.0-preview.ci350") < released


def test_the_version_shown_is_the_installed_one(monkeypatch):
    monkeypatch.setattr(updates, "velopack_manager", lambda: None)
    assert updates.current_version() == updates.VERSION

    class Manager:
        def get_current_version(self):
            return "1.6.0"
    monkeypatch.setattr(updates, "velopack_manager", lambda: Manager())
    assert updates.current_version() == "1.6.0"
    from engine import bug_report, feedback
    assert bug_report._version() == feedback._version() == "1.6.0"
