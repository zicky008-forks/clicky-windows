"""The company's own configuration folder (engine.company_config) on a made-up company: checks with their codes and
stop rule, a threshold, the calendar with a due date on a weekend and a point still marked TODO, the account
segments with a cost element segment of its own, and one broken file that must not stop the others."""
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

import fpa_samples as fs
from engine import bod_inputs, company_config as cc, fpa_report, variance_class

M = 1_000_000
D = Decimal

CHECKS = """
kiem_tra:
  - {ma: C1, ten: "Chi phí 641/642 khớp sổ cân đối", dung_sai: 0, chan: true}
  - {ma: C2, ten: "Sổ chi phí đã ánh xạ đủ", dung_sai: "0 dòng", chan: false}
  - {ma: C3, ten: "Tổng chi phí từ đầu năm", clicky: luy_ke_khop_so, chan: true}
  - {ma: C4, ten: "Chênh lệch lớn có giải trình", nguong: {pct: 8, trieu: 40}}
  - {ma: C5, ten: "Doanh thu theo kênh khớp", dung_sai_trieu: 0.5}
bay_ke_toan:
  - {ma: T1, ten: "Trích trước chưa hoàn nhập", dau_hieu: "tháng sau âm gần bằng tháng trước"}
"""
CALENDAR = """
buoc:
  - {id: S1, ten: Nhận sổ chi phí, ngay: "2027-04-05", kiem: [C1, C2]}
  - {id: S2, ten: Đối chiếu doanh thu, ngay: "2027-04-06/2027-04-07", kiem: [C5]}
  - {id: S3, ten: Gửi dự báo, ngay: "2027-04-08/2027-04-10", han: "2027-04-10", duyet: "TODO: trưởng phòng hay giám đốc"}
  - {id: S4, ten: Họp tháng, ngay: "2027-04-20"}
"""
MAPPING = """
phan_doan_tai_khoan:
  phan_cach: "-"
  doan:
    - {so: 1, ten: Đơn vị, vai_tro: site}
    - {so: 2, ten: Trung tâm chi phí, vai_tro: bo_phan}
    - {so: 3, ten: Tài khoản, vai_tro: tai_khoan}
    - {so: 4, ten: Khoản mục, vai_tro: yeu_to}
khi_thieu_ma:
  khong_tim_thay: {gia_tri: "ZZTHIEU", hanh_dong: "báo lỗi, không đoán"}
"""


def config_dir(tmp_path, broken=True) -> Path:
    d = tmp_path / "cau-hinh"
    d.mkdir()
    (d / "kiem_tra.yaml").write_text(CHECKS, encoding="utf-8")
    (d / "lich_thang.yaml").write_text(CALENDAR, encoding="utf-8")
    (d / "anh_xa.yaml").write_text(MAPPING, encoding="utf-8")
    (d / "cau_hoi.yaml").write_text("cau_hoi:\n  - {id: Q1, cau: [chưa đóng\n" if broken else
                                    "cau_hoi:\n  - {id: Q1, nhom: chi_phi, cau: \"Vì sao chi phí tăng?\"}\n",
                                    encoding="utf-8")
    return d


@pytest.fixture
def company(tmp_path, monkeypatch):
    d = config_dir(tmp_path)
    monkeypatch.setenv(cc.ENV, str(d))
    return d


def test_the_folder_is_read_and_what_is_still_open_is_listed(company):
    c = cc.find()
    assert c.read == ["kiem_tra.yaml", "lich_thang.yaml", "anh_xa.yaml"]
    assert c.problems == ["Em chưa đọc được file cấu hình cau_hoi.yaml (dòng 3); các file cấu hình khác vẫn dùng."]
    assert c.check("C1").stop and not c.check("C2").stop and c.check("C5").tolerance == D("500000.0")
    assert c.variance_threshold() == (40 * M, 8)
    assert [k.kind for k in c.checks.values()] == ["chi_phi_khop_so", "anh_xa_du", "luy_ke_khop_so",
                                                   "chenh_lech_giai_trinh", ""]
    assert c.segments == [(1, "Đơn vị", "site"), (2, "Trung tâm chi phí", "dept"), (3, "Tài khoản", "account"),
                          (4, "Khoản mục", "element")]
    assert c.delimiter == "-" and c.unmapped == ["ZZTHIEU"] and c.traps[0].code == "T1"
    assert "S3 Gửi dự báo: hạn 10/04/2027 rơi vào thứ Bảy" in c.open
    assert any("TODO: trưởng phòng hay giám đốc" in x for x in c.open)


def test_no_folder_means_no_company(tmp_path, monkeypatch):
    monkeypatch.setenv(cc.ENV, str(tmp_path / "khong-co"))
    assert cc.find() is None
    monkeypatch.delenv(cc.ENV)
    monkeypatch.setenv("CLICKY_DATA_DIR", str(tmp_path / "data"))
    assert cc.find() is None
    config_dir(tmp_path / "data", broken=False)
    assert cc.find().questions[0]["id"] == "Q1"                 # 'cau-hinh' in Clicky's data folder


def test_the_threshold_reaches_the_variance_rules_unless_the_folder_says_its_own(company):
    cfg = bod_inputs.Config()
    cc.apply(cc.find(), cfg)
    assert variance_class.thresholds(cfg) == (40 * M, 8)
    own = bod_inputs.Config(rules={"nguong chenh lech": "70000000"})
    cc.apply(cc.find(), own)
    assert variance_class.thresholds(own) == (70 * M, variance_class.PCT_DEFAULT)


def test_the_calendar_says_today_what_is_due_and_what_is_next(company):
    c = cc.find()
    assert cc.today(c, dt.date(2027, 4, 5)) == ["Hôm nay 05/04: S1 Nhận sổ chi phí. Kiểm C1, C2.",
                                                "Sắp tới hạn: S2 Đối chiếu doanh thu, hạn 07/04 (thứ Tư)."]
    assert cc.today(c, dt.date(2027, 4, 8)) == ["Hôm nay 08/04: S3 Gửi dự báo.",
                                                "S3 có hạn 10/04, rơi vào thứ Bảy: chị xác nhận ngày gửi thật."]
    assert cc.today(c, dt.date(2027, 4, 12)) == ["Hôm nay 12/04 chưa có bước nào. Bước tới: S4 Họp tháng, từ 20/04."]
    assert cc.today(c, dt.date(2027, 4, 21)) == ["Lịch tháng không còn bước nào sau 21/04."]


def test_a_segmented_ledger_reads_with_the_configured_segments_and_its_cost_element_segment(company, tmp_path):
    d = tmp_path / "thang"
    d.mkdir()
    fs.write_csv(d / "SoCai_T10.csv", [
        ["Tổ hợp tài khoản", "Ngày hạch toán", "Phát sinh Nợ", "Phát sinh Có", "Diễn giải"],
        ["01-KD-6421-VPP", "15/10/2026", "3000000", "0", "Văn phòng phẩm"],
        ["01-0000-1111-0000", "15/10/2026", "0", "3000000", "Chi tiền mặt"],
        ["01-HC-6427-DIEN", "20/10/2026", "5000000", "0", "Tiền điện"],
        ["01-0000-331-0000", "20/10/2026", "0", "5000000", "Phải trả"]])
    b = bod_inputs.load(d)
    assert b.company is not None and b.segment_map.file == "anh_xa.yaml"
    assert sorted((e.dept, e.element, e.account, e.amount) for e in b.gl) == [("HC", "DIEN", "6427", 5 * M),
                                                                             ("KD", "VPP", "6421", 3 * M)]


def _case(tmp_path, unmapped=0, shift=None):
    from test_fpa_report import case
    d = case(tmp_path, tb=False)
    import sales_samples as ss
    tb_shift = dict(shift or {})
    if unmapped:
        rows = fs.gl_rows()
        account = fs.PLAN[0][2]
        rows.insert(-1, ["20/10/2026", "PC77001", "10", "2026", account, "331", "ZZTHIEU", fs.PLAN[0][1],
                         "Chưa gán bộ phận", str(unmapped)])
        fs.write_csv(d / "SoChiTietChiPhi_2026.csv", rows)
        tb_shift[account] = tb_shift.get(account, 0) + unmapped
    fs.write_csv(d / "TB T10 2026.csv", fs.tb_rows(tb_shift)[:-1] + ss.tb_rows()[1:])
    return d


def test_the_report_runs_the_company_checks_by_their_codes(company, tmp_path):
    m = fpa_report.compute(_case(tmp_path))
    got = {code: (result, stop) for code, _n, result, _note, stop in m.checks}
    assert got == {"C1": ("ĐẠT", True), "C2": ("ĐẠT", False), "C3": ("CHƯA KIỂM", True), "C4": ("CẦN GIẢI TRÌNH", False)}
    note = next(n for c, _x, _r, n, _s in m.checks if c == "C4")
    assert note.startswith("ngưỡng 40.000.000 đ và 8%;")
    out = fpa_report.write(m)
    rows = [r[:5] for r in load_workbook(out["xlsx"])["Phieu nhan file"].iter_rows(values_only=True)]
    assert ("C1", "Chi phí 641/642 khớp sổ cân đối", "ĐẠT", "chi phí tháng 10/2026 khớp sổ cân đối", "Có") in rows
    assert ("Cấu hình công ty: điểm cần chị xác nhận", None, None, None, None) in rows
    assert ("S3 Gửi dự báo: hạn 10/04/2027 rơi vào thứ Bảy", None, None, None, None) in rows


def test_a_failed_stop_check_says_the_next_step_waits(company, tmp_path, monkeypatch):
    m = fpa_report.compute(_case(tmp_path, unmapped=2 * M, shift={"6418": 4 * M}))
    got = {code: result for code, _n, result, _note, _s in m.checks}
    assert got["C1"] == "KHÔNG ĐẠT" and got["C2"] == "KHÔNG ĐẠT"
    assert next(n for c, _x, _r, n, _s in m.checks if c == "C2") == "1 dòng chi phí còn ZZTHIEU, 2.000.000 đ"
    assert any(s.startswith("Kiểm C1 không đạt, mà cấu hình công ty ghi phải dừng") for s in m.said)
    from office import fpa_skill
    texts = [b.text for b in fpa_skill.beats_for(m, {"verified": True, "xlsx": tmp_path / "x" / "y.xlsx"})]
    assert "Kiểm C1 không đạt. Theo cấu hình của chị, em dừng ở đây, chưa làm bước sau." in texts


def test_without_a_company_folder_the_report_has_no_company_checks(tmp_path, monkeypatch):
    monkeypatch.setenv(cc.ENV, str(tmp_path / "khong-co"))
    m = fpa_report.compute(_case(tmp_path))
    assert m.checks == [] and m.bundle.company is None


def test_the_calendar_skill_words_and_beats(company, monkeypatch):
    import re
    from office import lich_skill
    from skills import load_all, match
    rx = re.compile(lich_skill.TRIGGER, re.I)
    for said in ("hôm nay làm gì", "hôm nay có việc gì", "lịch tháng này", "xem lịch FP&A"):
        assert rx.search(said), said
    load_all()
    assert match("hôm nay làm gì vậy em")["name"] == "Lịch FP&A tháng"
    logged = []
    monkeypatch.setattr(lich_skill.scene_log, "record", lambda event, **data: logged.append((event, data)))
    beats = lich_skill.beats(dt.date(2027, 4, 9))
    assert beats[0].text == "Hôm nay 09/04: S3 Gửi dự báo." and beats[1].mark == "warn"
    assert beats[-1].text.startswith("Em chưa đọc được file cấu hình cau_hoi.yaml")
    assert logged == [("lich_thang", {"steps": 4, "said": 2, "problems": 1})]
