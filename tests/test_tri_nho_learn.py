"""Company memory learns only what the owner approves, and measures the report month over month: the bosses' usual
questions tried on the saved FP&A report, progress in TIEN_BO.csv, proposals unticked in BAI_HOC.md."""
import datetime as dt

from present import tri_nho


def test_unticked_proposals_are_never_lessons(tmp_path):
    d = tri_nho.ensure(str(tmp_path))
    (d / tri_nho.BAI_HOC).write_text("# Bài học\n- Tháng 9 thiếu lương bình quân\n\n"
                                     f"{tri_nho.PROPOSED_HEAD}\n- [ ] Đề xuất A (đề xuất kỳ 10/2026)\n"
                                     "- [x] Đề xuất B (đề xuất kỳ 10/2026)\n", encoding="utf-8")
    mem = tri_nho.load(str(tmp_path))
    assert mem.lessons == ["Tháng 9 thiếu lương bình quân", "Đề xuất B"]
    assert tri_nho.read_proposed((d / tri_nho.BAI_HOC).read_text(encoding="utf-8")) == [
        "Đề xuất A (đề xuất kỳ 10/2026)"]


def test_proposals_come_from_facts_once(tmp_path):
    d = tri_nho.ensure(str(tmp_path))
    tri_nho.add_questions(["Vì sao CNTT tăng 12%?"], asked_by="anh Hùng", path=str(tmp_path))
    head, _sep, tail = (d / tri_nho.TREO).read_text(encoding="utf-8").rpartition("\n  Trả lời: ")
    treo = head + "\n  Trả lời: Do ERP" + tail
    treo += "- Hứa: Gửi chi tiết lương\n  Hạn: 01/11/2026\n"
    (d / tri_nho.TREO).write_text(treo, encoding="utf-8")
    got = tri_nho.propose_lessons("10/2026", [("Hùng (CFO)", "Headcount khối bán hàng?")], str(tmp_path),
                                  today=dt.date(2026, 11, 15))
    assert len(got["added"]) == 3 and got["waiting"] == 3
    assert any("Headcount" in x for x in got["added"]) and any("Do ERP" in x for x in got["added"])
    assert any("quá hạn 01/11/2026" in x for x in got["added"])
    again = tri_nho.propose_lessons("11/2026", [("Hùng (CFO)", "Headcount khối bán hàng?")], str(tmp_path),
                                    today=dt.date(2026, 11, 15))
    assert again["added"] == [] and tri_nho.load(str(tmp_path)).lessons == []     # nothing approved, nothing used


def test_progress_compares_with_the_month_before_and_a_rebuild_replaces_its_row(tmp_path):
    tri_nho.ensure(str(tmp_path))
    tri_nho.record_progress("09/2026", 10, 6, str(tmp_path))
    tri_nho.record_progress("10/2026", 10, 7, str(tmp_path))
    got = tri_nho.record_progress("10/2026", 10, 8, str(tmp_path))
    assert got["said"].startswith("Kỳ 10/2026: báo cáo trả lời được 8/10 câu sếp hay hỏi (kỳ 09/2026: 6/10)")
    rows = (tmp_path / tri_nho.TIEN_BO).read_text(encoding="utf-8-sig").splitlines()
    assert len(rows) == 3 and rows[2].startswith("10/2026,10,8")


def test_the_fpa_report_tries_the_bosses_questions_and_records_progress(tmp_path, monkeypatch):
    from engine import fpa_report
    from office import fpa_skill
    from test_fpa_report import case
    monkeypatch.setattr(fpa_skill.scene_log, "record", lambda *a, **k: None)
    mem = tmp_path / "mem"
    assert fpa_skill.learn(None, {}, str(mem)) is None                 # no memory folder: nothing created
    d = tri_nho.ensure(str(mem))
    (d / tri_nho.SEP).write_text("## Anh Hùng (CFO)\n- Lợi nhuận gộp tháng này so kế hoạch?\n"
                                 "- Dòng tiền tháng này?\n", encoding="utf-8")
    m = fpa_report.compute(case(tmp_path))
    out = fpa_report.write(m)
    beat = fpa_skill.learn(m, out, str(mem))
    assert beat.text.startswith("Kỳ 10/2026: báo cáo trả lời được 1/2 câu sếp hay hỏi") and beat.mark == "warn"
    proposed = tri_nho.read_proposed((d / tri_nho.BAI_HOC).read_text(encoding="utf-8"))
    assert len(proposed) == 1 and "Dòng tiền" in proposed[0]
