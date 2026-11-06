"""The four-stage robot's grading, off Windows: the scene log of a month close on the BHC-style case."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))

import bhc_e2e  # noqa: E402
from office import drop  # noqa: E402

KEY = {"report": "BaoCao_QuanTri_T10.xlsx", "close": {"lnst": "1869116800"}}


def _events(profit="1,87 tỷ", ok=True, receipt_first=True, report="C:/case/BaoCao_QuanTri_T10.xlsx"):
    intake = {"event": "intake", "files": [{"name": "TB_T10.xlsx", "total": 25, "used": 23, "dropped": 2}]}
    run = [{"event": "choices", "labels": ["Đúng rồi, làm tiếp", "Có chỗ sai"]},
           {"event": "beat", "title": "Lợi nhuận sau thuế", "value": profit},
           {"event": "verify", "ok": ok, "path": report, "diffs": [] if ok else [{"cell": "'BC thang'!C23"}]}]
    run += [{"event": "delivered", "path": report}] if ok else []
    return [intake] + run if receipt_first else run + [intake]


def test_a_clean_month_close_passes_every_line():
    from engine.monthly_report import spoken
    from decimal import Decimal
    res = bhc_e2e.grade_close(_events(spoken(Decimal("1869116800"))), KEY, True, True)
    assert res.ok, res.markdown("x")


def test_each_way_the_close_can_go_wrong_is_its_own_failed_line():
    from engine.monthly_report import spoken
    from decimal import Decimal
    right = spoken(Decimal("1869116800"))
    cases = [
        (bhc_e2e.grade_close(_events(right, receipt_first=False), KEY, True, True), "1a."),
        (bhc_e2e.grade_close(_events(right, report="C:/case/Pack_BOD_T10_ban_nhap.xlsx"), KEY, True, True), "1c."),
        (bhc_e2e.grade_close(_events("2,1 tỷ"), KEY, True, True), "2a."),
        (bhc_e2e.grade_close(_events(right, ok=False), KEY, True, True), "2b."),
        (bhc_e2e.grade_close(_events(right), KEY, True, False), "2c."),
        (bhc_e2e.grade_close(_events(right), KEY, False, True), "2d."),
    ]
    for res, line in cases:
        bad = [l.name for l in res.lines if not l.ok]
        assert not res.ok and len(bad) == 1 and bad[0].startswith(line), (line, bad)


def test_no_scene_log_at_all_fails_without_crashing():
    res = bhc_e2e.grade_close([], KEY, True, True)
    assert not res.ok and any("intake" in l.detail for l in res.lines)


def test_a_stage_that_could_not_run_fails_the_summary():
    good = bhc_e2e.Result()
    good.add("x", True)
    res = bhc_e2e.summary([("Chặng 1–2", good, ""), ("Chặng 3", None, "")])
    assert not res.ok and [l.ok for l in res.lines] == [True, False]


def test_the_app_takes_a_folder_to_drop_from_its_command_line():
    assert drop.args(["Clicky.exe", "--drop", "C:/case"]) == "C:/case"
    assert drop.args(["Clicky.exe"]) is None and drop.args(["Clicky.exe", "--drop"]) is None


def test_a_close_stuck_on_a_query_says_where_it_stopped():
    events = [{"event": "intake", "files": []}, {"event": "close_step", "step": "read_folder"},
              {"event": "close_step", "step": "query_load", "state": "start"},
              {"event": "close_step", "step": "query_load", "state": "timeout", "dialog": "Privacy levels"}]
    assert bhc_e2e.stopped(events)
    res = bhc_e2e.grade_close(events, KEY, True, True)
    line = next(l for l in res.lines if l.name.startswith("2b."))
    assert not line.ok and "query_load (timeout, Excel hỏi: Privacy levels)" in line.detail
    assert not bhc_e2e.stopped(events[:3])


def test_the_power_query_smoke_check_queries_are_plain_m():
    import pq_smoke
    from engine.m_code import steps
    m = pq_smoke.csv_query(Path("C:/tam/DonHang \"gia\" lap.csv"))
    assert [s.name for s in steps(m)] == ["Source", "Headers"] and '""gia""' in m
    assert "Function.InvokeAfter" in pq_smoke.slow_query(20) and "#duration(0, 0, 0, 20)" in pq_smoke.slow_query(20)
    assert pq_smoke.amounts([["PX001", "x", "1200000"], ["PX002", "x", 3400000.0]]) == 4_600_000
    assert pq_smoke.main([]) == 2 or sys.platform == "win32"      # off Windows it says it cannot run
