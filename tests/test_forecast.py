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


def test_a_line_the_forecast_lacks_is_taken_from_the_annual_plan_counted_and_said(tmp_path, monkeypatch):
    gone = (11, "MKT", "QC")
    le = fs.plan_rows("LE T09", approved="20/09/2026", scale=2, drop=[gone])
    f = forecast.compute(case(tmp_path, plans={"AOP_2026.csv": fs.plan_rows("AOP"), "LE_T09_2026.csv": le}))
    assert [x for x in f.fallback if x[0] == "chi phí"] == [] and [(w, m) for w, m, _l in f.filled] == [
        ("chi phí", "2026-11")]
    aop_line = fs.plan_month("MKT", "QC", 11)
    assert f.row("opex").months["2026-11"] == plan_total(["2026-11"], scale=2) - 2 * aop_line + aop_line
    assert f.row("opex").months["2026-12"] == plan_total(["2026-12"], scale=2)
    said = " ".join(f.said)
    assert ("Bản LE T09 không có 1 dòng chi phí (bộ phận × yếu tố) của tháng 11/2026; em lấy theo kế hoạch năm cho "
            "các dòng đó") in said
    out = forecast.write(f)
    assert out["verified"]
    rows = list(load_workbook(out["xlsx"])["Phieu nhan file"].iter_rows(values_only=True))
    at = next(i for i, r in enumerate(rows) if r[0] == "Dòng LE T09 không có, em lấy theo AOP")
    assert rows[at + 1][1:3] == ("11/2026", "chi phí") and rows[at + 1][0] == f.filled[0][2]
    from office import forecast_skill
    texts = [b.text for b in forecast_skill.beats_for(f, out)]
    assert any(t.startswith("Bản LE T09 thiếu 1 dòng so với kế hoạch năm") for t in texts)


def test_a_sku_line_the_forecast_lacks_is_taken_from_the_annual_plan(tmp_path):
    d = case(tmp_path, sku_le=True)
    le = [r if i == 0 else r[:5] + [r[5] * 2] + r[6:] for i, r in enumerate(ss.plan_rows("LE T09", total=None))]
    le = [r for r in le if not (r[2] == 12 and (r[3], r[4]) == ("NC01", "MT"))]
    ss.write_xlsx(d / "KeHoach_LE_T09_2026.xlsx", {"KH theo ma hang": le})
    f = forecast.compute(d)
    assert [(w, m, l) for w, m, l in f.filled] == [("doanh thu và giá vốn", "2026-12", "NC01 · MT")]

    def month(mo, factor, keep=lambda k: True):
        return sum(ss.plan_month(s, c, mo) * ss.SKUS[s][1] * factor for (s, c) in ss.PLAN_QTY if keep((s, c)))
    nc01_mt = ss.plan_month("NC01", "MT", 12) * ss.SKUS["NC01"][1]
    assert f.row("revenue").months["2026-12"] == month(12, 2, lambda k: k != ("NC01", "MT")) + nc01_mt
    assert f.row("revenue").months["2026-11"] == month(11, 2)
    assert "dòng doanh thu và giá vốn (mã hàng × kênh) của tháng 12/2026" in " ".join(f.said)


def test_a_forecast_the_catalogue_names_but_the_folder_lacks_stops_with_one_sentence(tmp_path):
    from office import forecast_skill
    from office.fpa_skill import Stop
    d = case(tmp_path, rules=RULES + [["Bản dự báo", "LE T07"]])
    with pytest.raises(forecast.NamedVersionMissing) as missing:
        forecast.compute(d)
    assert missing.value.named == "LE T07" and missing.value.found == ["AOP", "LE T09"]
    with pytest.raises(Stop) as stop:
        forecast_skill.prepare(d)
    assert stop.value.beat.text == ("Danh mục của chị ghi Bản dự báo là “LE T07”, nhưng thư mục không có bản đó; em "
                                    "chỉ thấy: AOP, LE T09. Chị sửa danh mục hoặc chép bản đó vào thư mục, rồi làm lại.")
    assert not any(p.name.startswith(forecast.OUT_PREFIX) for p in d.iterdir())


def test_the_receipt_gives_the_reason_for_every_line_left_out(tmp_path):
    f = forecast.compute(case(tmp_path))
    rows = list(load_workbook(forecast.write(f)["xlsx"])["Phieu nhan file"].iter_rows(values_only=True))
    head = rows.index(("File", "Vai trò", "Dòng đã dùng", "Dòng bỏ", "Lý do bỏ"))
    files = [r for r in rows[head + 1:] if r[0]]
    assert {r[0] for r in files} >= {"SoChiTietChiPhi_2026.csv", "AOP_2026.csv", "LE_T09_2026.csv", "TB T10 2026.csv"}
    for r in files:
        assert (r[3] == 0) == (not r[4]) or r[1] == "Không dùng", r                  # every line left out has a reason
    gl = next(r for r in files if r[0] == "SoChiTietChiPhi_2026.csv")
    assert gl[3] and "không có số tiền" in gl[4]


def test_the_robot_grades_a_real_run_against_its_own_sum_of_the_case(tmp_path, monkeypatch):
    import json
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))
    import forecast_robot
    import tao_case_chi_phi
    from office import forecast_skill
    d = tao_case_chi_phi.make(tmp_path / "case", forecast=True)
    assert tao_case_chi_phi.main(["--out", str(d), "--du-bao"]) == 2          # never written over
    env = forecast_robot.robot_env(tmp_path / "x.jsonl")
    assert env["CLICKY_LOCAL_ONLY"] == "1" and env["CLICKY_OUTLOOK"] == "0" and "cau-hinh-robot" in env["CLICKY_CONFIG_DIR"]
    expected = forecast_robot.expected_full_year(d)
    month, dept, element = tao_case_chi_phi.FORECAST_MISSING
    rest = 2 * plan_total(REST) - fs.plan_month(dept, element, month)      # the line the forecast lacks: AOP, once
    assert expected - rest == forecast.compute(d).row("opex").ytd
    log = tmp_path / "scene.jsonl"
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(log))
    f, out = forecast_skill.prepare(d)
    assert f.row("opex").full(f.rest) == expected
    events = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] + [{"event": "story_end"}]
    assert forecast_robot.finished(events)
    res = forecast_robot.grade(events, True, out["xlsx"].parent, expected)
    assert res.ok, res.markdown("x")
    bad = forecast_robot.grade([e for e in events if e["event"] != "forecast"], False, out["xlsx"].parent,
                               expected + 1)
    assert {l.name for l in bad.lines if not l.ok} == {"Làm xong dự báo, mở lại kiểm tra thấy khớp",
                                                       "Chi phí cả năm ước đúng từng đồng",
                                                       "Nói rõ dòng bản dự báo thiếu lấy theo kế hoạch năm",
                                                       "File của case không bị sửa"}
