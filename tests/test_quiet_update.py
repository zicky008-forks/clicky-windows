"""Quiet updates: downloaded only while idle, applied on exit, one line after, nothing in local-only mode."""
import asyncio
import sys
import types
from unittest import mock

with mock.patch.dict(sys.modules, {"httpx": sys.modules.get("httpx") or types.ModuleType("httpx")}):
    import updates                      # httpx is not needed by these paths; nothing leaks to other tests


class _Asset:
    Version, NotesMarkdown = "1.7.0", "## Có gì mới\n- **Ô thả file** cho tháng đầu tiên\n- Sửa lỗi nhỏ"


class _Info:
    TargetFullRelease, IsDowngrade = _Asset(), False


class _Velopack:
    def __init__(self, current="1.6.0", info=_Info()):
        self.current, self.info, self.calls = current, info, []

    def check_for_updates(self):
        self.calls.append("check")
        return self.info

    def download_updates(self, info):
        self.calls.append("download")

    def wait_exit_then_apply_updates(self, info, silent=False, restart=True):
        self.calls.append(("apply_on_exit", silent, restart))

    def get_current_version(self):
        return self.current


def _store(monkeypatch):
    import storage
    box = {}
    monkeypatch.setattr(storage, "setting", lambda k, d=None: box.get(k, d))
    monkeypatch.setattr(storage, "set_setting", lambda k, v: box.__setitem__(k, v))
    return box


def test_idle_downloads_and_applies_only_on_exit(monkeypatch):
    box = _store(monkeypatch)
    vp = _Velopack()
    assert asyncio.run(updates.quiet_check(lambda: False, manager=vp)) == "1.7.0"
    assert vp.calls == ["check", "download", ("apply_on_exit", True, False)]
    assert box[updates.PENDING_KEY] == {"version": "1.7.0", "note": "Ô thả file cho tháng đầu tiên"}


def test_busy_or_local_only_never_touches_the_network(monkeypatch):
    _store(monkeypatch)
    vp = _Velopack()
    assert asyncio.run(updates.quiet_check(lambda: True, manager=vp)) is None
    assert asyncio.run(updates.quiet_check(lambda: False, local_only=True, manager=vp)) is None
    assert vp.calls == []


def test_work_started_during_the_check_stops_the_download(monkeypatch):
    _store(monkeypatch)
    vp = _Velopack()
    answers = iter([False, True])
    assert asyncio.run(updates.quiet_check(lambda: next(answers), manager=vp)) is None
    assert vp.calls == ["check"]


def test_one_line_after_the_update_then_silence(monkeypatch):
    box = _store(monkeypatch)
    box[updates.PENDING_KEY] = {"version": "1.7.0", "note": "Ô thả file cho tháng đầu tiên"}
    assert updates.after_update_notice(_Velopack(current="1.6.0")) is None            # not applied yet
    line = updates.after_update_notice(_Velopack(current="1.7.0"))
    assert line == "Đã cập nhật bản 1.7.0. Có gì mới: Ô thả file cho tháng đầu tiên."
    assert updates.after_update_notice(_Velopack(current="1.7.0")) is None             # shown once


def test_notes_without_text_get_a_plain_line():
    assert updates.first_note("") == updates._FALLBACK_NOTE
    assert updates.first_note("**Full Changelog**: https://x") == updates._FALLBACK_NOTE
