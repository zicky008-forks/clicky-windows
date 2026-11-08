"""The forecast for head office on the synthetic month folder: actual months to October (the current month and the
year to date from the books), November and December from the latest approved forecast version, the full year
against the annual plan, one SƠ BỘ workbook reopened and checked."""
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

import fpa_samples as fs
import sales_samples as ss
from engine import forecast, fpa, saved_check
from test_fpa_report import RULES, _with_ytd

M = 1_000_000
D = Decimal
REST = ("2026-11", "2026-12")


def case(tmp_path, plans=None, tb=True, ytd=False, sku_le=False, rules=RULES) -> Path:
    d = tmp_path / "F01"
    fs.folder(d, plans=plans or {"AOP_2026.csv": fs.plan_rows("AOP"),
                                 "LE_T09_2026.csv": fs.plan_rows("LE T09", approved="20/09/2026", scale=2)})
    ss.folder(d)
    if sku_le:
        ss.write_xlsx(d / "KeHoach_LE_T09_2026.xlsx", {"KH theo ma hang": ss.plan_rows("LE T09")})
    ss.write_xlsx(d / "DanhMuc.xlsx", {"Ma hang": ss.catalogue_rows(), "Tham so": rules})
    if tb:
        rows = fs.tb_rows()[:-1] + ss.tb_rows()[1:]
        fs.write_csv(d / "TB T10 2026.csv", _with_ytd(rows) if ytd else rows)
    return d


def plan_total(months, scale=1) -> Decimal:
    return D(sum(fs.plan_month(dept, el, int(m[5:])) for dept, el, _a, _p in fs.PLAN for m in months) * scale)


def test_the_months_to_come_come_from_the_latest_approved_forecast(tmp_path):
    f = forecast.compute(case(tmp_path))
    assert (f.target.version, f.basis.version, f.basis.approved) == ("AOP", "LE T09", "2026-09-20")
    assert f.done[-1] == "2026-10" and tuple(f.rest) == REST
    opex = f.row("opex")
    assert opex.rest(f.rest) == plan_total(REST, scale=2)                  # the forecast, not the annual plan
    assert opex.plan_full == plan_total([f"2026-{m:02d}" for m in range(1, 13)])
    assert opex.months["2026-10"] == f.month.row("opex").actual and opex.ytd == f.month.row("opex").ytd_actual
    assert opex.full(f.rest) == opex.ytd + opex.rest(f.rest)
    assert all(isinstance(v, Decimal) for r in f.rows for v in list(r.months.values()) + [r.ytd])
    gp, op = f.row("gp"), f.row("op")
    assert op.full(f.rest) == gp.full(f.rest) - opex.full(f.rest)
    said = " ".join(f.said)
    assert "Các tháng 11/2026–12/2026 lấy theo bản LE T09, duyệt ngày 20/09/2026" in said
    # the forecast has no plan by SKU: revenue for the months to come is the annual plan's, and that is said
    assert "LE T09 không có số doanh thu và giá vốn tháng 11/2026, 12/2026; em lấy theo kế hoạch năm" in said
    assert f.row("revenue").rest(f.rest) == sum((f.row("revenue").plan[m] for m in REST), D(0))


def test_a_forecast_with_a_plan_by_sku_is_used_for_revenue_too(tmp_path):
    f = forecast.compute(case(tmp_path, sku_le=True))
    assert f.fallback == []
    assert "không có số doanh thu" not in " ".join(f.said)


def test_without_a_forecast_the_annual_plan_is_used_and_it_says_so(tmp_path):
    f = forecast.compute(case(tmp_path, plans={"AOP_2026.csv": fs.plan_rows("AOP")}))
    assert f.basis is f.target and f.row("opex").rest(f.rest) == plan_total(REST)
    assert any(s.startswith("Thư mục chưa có bản dự báo nào, nên các tháng 11/2026–12/2026 lấy theo kế hoạch năm")
               for s in f.said)


def test_two_forecasts_are_told_apart_by_approval_only(tmp_path):
    two = {"AOP_2026.csv": fs.plan_rows("AOP"), "LE_T08_2026.csv": fs.plan_rows("LE T08", scale=3),
           "LE_T09_2026.csv": fs.plan_rows("LE T09", scale=2)}
    with pytest.raises(fpa.PickVersion) as asked:
        forecast.compute(case(tmp_path / "a", plans=two))
    assert sorted(v.split(" (")[0] for v in asked.value.versions) == ["LE T08", "LE T09"]
    two["LE_T08_2026.csv"] = fs.plan_rows("LE T08", approved="15/08/2026", scale=3)
    two["LE_T09_2026.csv"] = fs.plan_rows("LE T09", approved="20/09/2026", scale=2)
    assert forecast.compute(case(tmp_path / "b", plans=two)).basis.version == "LE T09"     # the latest approval
    named = RULES + [["Bản dự báo", "LE T08"]]
    assert forecast.compute(case(tmp_path / "c", plans=two, rules=named)).basis.version == "LE T08"


def test_the_books_year_to_date_differs_from_the_months_and_it_is_its_own_line(tmp_path):
    f = forecast.compute(case(tmp_path, ytd=True))
    opex = f.row("opex")
    assert opex.ytd_books and opex.ytd == f.month.row("opex").ytd_actual
    g = forecast.gap(opex, f.done)
    assert g and g == opex.ytd - sum(opex.months[m] for m in f.done)
    assert any(s.startswith("Chi phí hoạt động: lũy kế theo sổ cân đối khác cộng các tháng") for s in f.said)
    out = forecast.write(f)
    assert out["verified"]
    rows = list(load_workbook(out["xlsx"])["Chi phi theo bo phan"].iter_rows(values_only=True))
    total, gap_row = rows[-1], rows[-2]
    ytd_col = 1 + len(f.year)
    assert total[0] == "Tổng cộng" and D(str(total[ytd_col])) == opex.ytd
    assert gap_row[0] == "Chênh lệch sổ cân đối chưa giải thích"
    assert sum(D(str(r[ytd_col])) for r in rows[3:-1]) == opex.ytd           # the department lines add up


def test_the_pack_is_new_reopened_and_checked(tmp_path, monkeypatch):
    d = case(tmp_path)
    before = {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()}
    f = forecast.compute(d)
    out = forecast.write(f)
    assert out["verified"] and out["xlsx"].parent.name == "Clicky_Forecast_T10-2026"
    wb = load_workbook(out["xlsx"])
    assert wb.sheetnames == ["Du bao KQKD", "Chi phi theo bo phan", "Phieu nhan file", "Bien ban Clicky"]
    ws = wb["Du bao KQKD"]
    assert ws["A1"].value == "SƠ BỘ · Dự báo kết quả kinh doanh năm tài chính, cập nhật T10/2026"
    head = [c.value for c in ws[3]]
    assert head[10] == "TH 10/2026" and head[11] == "DB 11/2026" and head[-4:] == ["Cả năm ước", "Kế hoạch năm",
                                                                                     "Chênh lệch", "%"]
    assert [ws[f"A{r}"].value for r in range(4, 9)] == ["Doanh thu thuần", "Giá vốn hàng bán", "Lợi nhuận gộp",
                                                        "Chi phí hoạt động", "Lợi nhuận hoạt động"]
    assert {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()} == before          # the customer's files
    monkeypatch.setenv(saved_check.TAMPER_ENV, "'Du bao KQKD'!N4=1000")
    bad = forecast.write(f)
    assert not bad["verified"] and bad["xlsx"].parent.name == "Clicky_Forecast_T10-2026 (2)"


def test_the_chat_words_the_robot_flag_and_the_beats(tmp_path, monkeypatch):
    import asyncio
    import re
    from office import forecast_skill, month_close
    from skills import load_all, match
    assert forecast_skill.args(["Clicky.exe", "--forecast", "D:/case"]) == "D:/case"
    rx = re.compile(forecast_skill.TRIGGER, re.I)
    for said in ("làm forecast gửi HO", "cập nhật dự báo tháng này", "ước KQKD cả năm", "dự báo cả năm"):
        assert rx.search(said), said
    for other in ("làm báo cáo FP&A tháng", "hôm nay làm gì", "dự báo thời tiết hôm nay"):
        assert not rx.search(other), other
    load_all()
    assert match("làm forecast gửi HO tháng này")["name"] == "Dự báo cả năm gửi HO"
    assert match("làm báo cáo FP&A tháng từ thư mục này")["name"] == "Báo cáo FP&A tháng"
    played, logged = [], []

    async def play(manager, beats, **k):
        played.extend(beats)
    monkeypatch.setattr(month_close, "play", play)
    monkeypatch.setattr(forecast_skill.scene_log, "record", lambda event, **data: logged.append((event, data)))
    d = case(tmp_path)
    asyncio.run(forecast_skill.run(object(), d))
    texts = [b.text for b in played]
    assert texts[0] == "Em đã làm dự báo cả năm, cập nhật đến tháng 10/2026, mở lại kiểm tra thấy khớp."
    assert texts[1] == "Các tháng còn lại em lấy theo bản LE T09, duyệt ngày 20/09/2026, so với kế hoạch năm."
    assert texts[-1].endswith("File của chị em không sửa gì.") and "không gửi đi đâu" in texts[-1]
    assert [e for e, _d in logged] == ["forecast_start", "forecast"] and str(d) not in repr(logged)
