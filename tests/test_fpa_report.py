"""The FP&A month report on one synthetic folder (cost detail, opex plan, sales ledger, plan by SKU, catalogue, trial
balance): the month's P&L figures come from the books and the ledgers only split them; the receipt says the books
against each ledger in one sentence; every variance is classified; the saved file is reopened and checked."""
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

import fpa_samples as fs
import sales_samples as ss
from engine import fpa_report, saved_check

M = 1_000_000
D = Decimal
RULES = [["Tham số", "Giá trị"], ["Tháng bắt đầu năm tài chính", 1]]
PROVISION = ["2294", "Dự phòng giảm giá hàng tồn kho", 0, 0, 0, 3 * M, 0, 3 * M]


def case(tmp_path, tb=True, cost_shift=None, sales_shift=None, sales_extra=()) -> Path:
    d = tmp_path / "F01"
    fs.folder(d)
    ss.folder(d)
    ss.write_xlsx(d / "DanhMuc.xlsx", {"Ma hang": ss.catalogue_rows(), "Tham so": RULES})
    if tb:
        fs.write_csv(d / "TB T10 2026.csv", fs.tb_rows(cost_shift)[:-1] + ss.tb_rows(sales_shift, sales_extra)[1:])
    return d


def _ledger(m, code):
    return next(l for l in m.sales.lines() if l.code == code)


def test_the_month_comes_from_the_books_and_the_ledgers_split_it(tmp_path):
    m = fpa_report.compute(case(tmp_path, sales_shift={"632": 3 * M}, sales_extra=[PROVISION]))
    rev, cogs, gp, opex, op = (m.row(c) for c in ("revenue", "cogs", "gp", "opex", "op"))
    assert rev.actual == _ledger(m, "revenue").actual and rev.source.startswith("sổ cân đối T10/2026 (Có TK 5111")
    assert cogs.actual == _ledger(m, "cogs").actual + 3 * M and cogs.source == "sổ cân đối T10/2026 (Nợ TK 632)"
    assert gp.actual == rev.actual - cogs.actual and op.actual == gp.actual - opex.actual
    assert opex.actual == sum(l.actual for l in m.costs.month) and opex.source.startswith("sổ cân đối")
    assert all(isinstance(x, Decimal) for r in m.pnl for x in (r.actual, r.plan, r.ytd_actual, r.ytd_plan))
    said = " ".join(m.said)
    assert said.startswith("Số tháng 10/2026 trên báo cáo lấy từ bảng cân đối số phát sinh (file TB T10 2026.csv)")
    assert "bằng đúng trích lập dự phòng giảm giá hàng tồn kho (Có TK 2294) 3.000.000 đ" in said
    assert ("Cầu nối lợi nhuận gộp tính trên sổ bán hàng: chênh lệch -370.000 đ. Chênh lệch trên báo cáo (theo sổ cân "
            "đối) là -3.370.000 đ; phần khác -3.000.000 đ") in said


def test_without_a_trial_balance_the_ledgers_are_used_and_it_says_so(tmp_path):
    m = fpa_report.compute(case(tmp_path, tb=False))
    assert [r.source for r in m.pnl[:2]] == ["sổ bán hàng", "sổ bán hàng"]
    assert m.row("opex").source == "sổ chi tiết chi phí"
    assert m.said[0].startswith("Thư mục chưa có bảng cân đối số phát sinh tháng 10/2026")


def test_costs_that_do_not_match_the_books_are_not_put_in_the_report_as_books(tmp_path):
    m = fpa_report.compute(case(tmp_path, cost_shift={"6418": 4 * M}))
    assert m.row("opex").source == "sổ chi tiết chi phí"
    assert any("lệch sổ cân đối ở TK 6418" in s for s in m.said)
    assert any(s.startswith("Vì chi phí chưa khớp sổ cân đối") for s in m.said)


def test_every_variance_is_classified_and_the_receipt_counts_them(tmp_path):
    m = fpa_report.compute(case(tmp_path))
    by = {(c.line.dept, c.line.element): c for c in m.classified}
    assert by[("MKT", "QC")].verdict == "vượt kế hoạch" and by[("MKT", "QC")].alarm
    assert by[("MKT", "CTP")].verdict == "khoản một lần" and not by[("MKT", "CTP")].alarm
    assert by[("HC", "THUE")].verdict == "nhỏ dưới ngưỡng"
    assert any(s.startswith("Chi phí: 4 dòng chênh trên ngưỡng, số báo cáo 89.500.000 đ, số thực chất 70.000.000 đ; "
                            "2 dòng cần hỏi;") for s in m.said)


def test_the_workbook_is_new_reopened_and_checked(tmp_path, monkeypatch):
    d = case(tmp_path)
    before = {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()}
    m = fpa_report.compute(d)
    out = fpa_report.write(m)
    assert out["verified"] and out["cells"] >= 12 and out["xlsx"].parent.name == "Clicky_FPA_T10-2026"
    wb = load_workbook(out["xlsx"])
    assert wb.sheetnames == ["P&L", "Chi phi theo bo phan", "Chenh lech", "Cau noi LNG", "Phieu nhan file",
                             "Bien ban Clicky"]
    pnl = wb["P&L"]
    assert pnl["A1"].value == "SƠ BỘ · Báo cáo FP&A tháng 10/2026"
    assert [pnl[f"A{r}"].value for r in range(4, 9)] == ["Doanh thu thuần", "Giá vốn hàng bán", "Lợi nhuận gộp",
                                                         "Chi phí hoạt động", "Lợi nhuận hoạt động"]
    assert D(str(pnl["B4"].value)) == m.row("revenue").actual
    bridge = [r for r in wb["Cau noi LNG"].iter_rows(values_only=True)]
    assert bridge[-2][0].startswith("Tổng 4 phần") and D(str(bridge[-2][1])) == _ledger(m, "gp").variance
    receipt = [r for r in wb["Phieu nhan file"].iter_rows(values_only=True)]
    assert any(r[:2] == ("TB T10 2026.csv", "Bảng cân đối số phát sinh: số chính thức của tháng") for r in receipt)
    assert not any(r[1] == "Không dùng" and r[0] == "TB T10 2026.csv" for r in receipt)
    assert ("Trạng thái", "SƠ BỘ") in [r[:2] for r in wb["Bien ban Clicky"].iter_rows(values_only=True)]
    assert {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()} == before          # the customer's files
    assert fpa_report.out_folder(d, m.period).name == "Clicky_FPA_T10-2026 (2)"
    with pytest.raises(FileExistsError):
        fpa_report.write(m, out["xlsx"].parent)


def test_a_figure_changed_after_saving_fails_the_check(tmp_path, monkeypatch):
    m = fpa_report.compute(case(tmp_path))
    monkeypatch.setenv(saved_check.TAMPER_ENV, "'P&L'!B4=1000")
    out = fpa_report.write(m)
    assert not out["verified"] and out["diffs"][0]["cell"] == "'P&L'!B4"


def test_no_name_from_a_pay_line_reaches_the_report(tmp_path):
    d = case(tmp_path)
    rows = fs.gl_rows()
    rows[-1:-1] = [["25/10/2026", "PL77702", "10", "2026", "6411", "3341", "KD", "THUONG", "Thưởng – Trần Thị Bình",
                    str(25 * M)]]
    rows[-1][9] = str(int(rows[-1][9]) + 25 * M)
    fs.write_csv(d / "SoChiTietChiPhi_2026.csv", rows)
    fs.write_csv(d / "TB T10 2026.csv", fs.tb_rows({"6411": 25 * M})[:-1] + ss.tb_rows()[1:])
    m = fpa_report.compute(d)
    out = fpa_report.write(m)
    text = "\n".join(str(c.value) for ws in load_workbook(out["xlsx"]).worksheets for row in ws.iter_rows()
                     for c in row if c.value is not None)
    assert "Trần Thị Bình" not in text and "PL77702" not in text
    assert "Khoản một lần ở THUONG: 25.000.000 đ (chi tiết do phòng nhân sự giữ)" in text


def test_the_chat_words_the_robot_flag_and_the_beats(tmp_path, monkeypatch):
    import asyncio
    import re
    from office import fpa_skill, month_close
    from skills import load_all, match
    assert fpa_skill.args(["Clicky.exe", "--fpa-report", "D:/case"]) == "D:/case"
    rx = re.compile(fpa_skill.TRIGGER, re.I)
    for said in ("làm báo cáo FP&A tháng 10", "báo cáo thực hiện so với kế hoạch", "so với AOP tháng này"):
        assert rx.search(said), said
    assert not rx.search("làm báo cáo tháng từ thư mục này")              # the month close keeps its words
    load_all()
    assert match("làm báo cáo FP&A tháng từ thư mục này")["name"] == "Báo cáo FP&A tháng"
    played, logged = [], []

    async def play(manager, beats, **k):
        played.extend(beats)
    monkeypatch.setattr(month_close, "play", play)
    monkeypatch.setattr(fpa_skill.scene_log, "record", lambda event, **data: logged.append((event, data)))
    d = case(tmp_path)
    asyncio.run(fpa_skill.run(object(), d))
    texts = [b.text for b in played]
    assert texts[0] == "Em đã làm báo cáo FP&A tháng 10/2026, mở lại kiểm tra thấy khớp."
    assert texts[1].startswith("Số tháng trên báo cáo lấy từ bảng cân đối số phát sinh; doanh thu, giá vốn và chi phí")
    assert texts[2] == ("Chi phí có 4 dòng chênh trên ngưỡng. Sau khi tách lệch thời điểm, khoản một lần và biến động "
                        "theo doanh thu, còn 2 dòng cần hỏi.")
    assert texts[3] == "MKT · QC: vượt kế hoạch 90.000.000 đ."
    assert texts[-1].endswith("File của chị em không sửa gì.") and "SƠ BỘ" in texts[-1]
    assert [e for e, _d in logged] == ["fpa_start", "fpa_report"] and str(d) not in repr(logged)
