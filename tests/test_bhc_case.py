"""The four-stage BHC-style rehearsal case: every stage passes on the engine alone, draft and clean."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))


@pytest.mark.parametrize("plant", [True, False])
def test_the_four_stage_case_passes_every_stage_on_the_engine(tmp_path, plant):
    import bhc_case
    bhc_case.make(tmp_path, plant=plant)
    res = bhc_case.check(tmp_path)
    assert res.ok and len(res.lines) == 4, res.markdown("bhc")


def test_the_case_csvs_look_like_real_exports_and_only_the_order_file_is_read_as_orders(tmp_path):
    """UTF-8 with BOM, comma separated, like an ERP export; the cost ledger CSV beside it is not an order file."""
    import bhc_case
    from engine import sources
    bhc_case.make(tmp_path)
    for name in ("Mastersale_T07-T10.csv", "GL_ChiTiet_ChiPhi_T07-T10.csv"):
        raw = (tmp_path / name).read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf") and b";" not in raw.splitlines()[0], name
    assert [f.name for f in sources.find_order_files(tmp_path)] == ["Mastersale_T07-T10.csv"]


def _after_month_close(folder):
    """What stages 1–2 leave in the folder: the report workbook now holds Clicky's order query table
    (the export's rows, typed, footer dropped) and Clicky's untouched copy of it sits beside it."""
    import csv
    import shutil
    from datetime import date
    from decimal import Decimal
    import openpyxl
    report = folder / "BaoCao_QuanTri_T10.xlsx"
    shutil.copy(report, folder / "BaoCao_QuanTri_T10_ban-goc.xlsx")
    with open(folder / "Mastersale_T07-T10.csv", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    wb = openpyxl.load_workbook(report)
    ws = wb.create_sheet("DonHang")
    ws.append(rows[0])
    money = {rows[0].index(c) for c in ("So_luong", "Doanh_thu", "Chiet_khau", "Doanh_thu_thuan")}
    for r in rows[1:]:
        if not any(ch.isdigit() for ch in r[0]):
            continue                                  # the export's total row: the query drops it
        y, m, d = (int(x) for x in r[0].split("-"))
        if (y, m) != (2026, 10):
            continue                                  # the query keeps the ledger's month only (PC: DonHang 25×10)
        ws.append([date(y, m, d)] + [Decimal(v) if i in money else v for i, v in enumerate(r) if i])
    wb.save(report)


@pytest.mark.parametrize("plant", [True, False])
def test_the_bod_review_after_the_month_close_counts_the_sales_once(tmp_path, plant):
    """bhc_e2e on 84603e7: stage 3 ran on the folder stages 1–2 had written to. The order table Clicky
    loads into the report workbook was read as a second sales file, so every revenue base doubled:
    a wrong %DTT at 'Tong hop'!C9, a missed wrong base on 'Theo kenh' and a false alarm beside it."""
    import json
    import bhc_case
    from decimal import Decimal
    from engine import bod_check, bod_inputs
    bhc_case.make(tmp_path, plant=plant)
    _after_month_close(tmp_path)
    key = json.loads((tmp_path / "dap_an.json").read_text(encoding="utf-8"))
    found = bod_check.review(tmp_path)[0].findings
    for a in key["bod"]:
        m = [x for x in found if x.where == a["where"] and x.group == a["group"]]
        assert m, a
        tol = Decimal("0.0001") if a["code"] in ("wrong_base", "missing_pct", "missing_pp", "wrong_pp") else Decimal(1)
        assert m[0].expected is not None and abs(Decimal(str(m[0].expected)) - Decimal(str(a["expected"]))) <= tol, a
    assert len(found) == len(key["bod"]), [(x.group, x.where) for x in found]
    b = bod_inputs.load(tmp_path)
    assert [e[0] for e in b.receipt if e[1] == "Bán hàng"] == ["Mastersale_T07-T10.csv"]
    copy = [why for name, why in b.unused if name == "BaoCao_QuanTri_T10.xlsx"]
    assert copy and "bản sao" in copy[0] and "Mastersale_T07-T10.csv" in copy[0]


def test_a_sales_table_with_rows_of_its_own_is_not_a_copy(tmp_path):
    """Only a table whose every row is already in another one is dropped: a second file with new rows adds up."""
    from decimal import Decimal
    from engine import bod_inputs
    head = "Ngay,Khoi,Kenh,Doanh_thu_thuan\n"
    (tmp_path / "BanHang_T10_mien_Bac.csv").write_text(head + "2026-10-02,TM,GT,100\n2026-10-03,TM,MT,200\n", encoding="utf-8")
    (tmp_path / "BanHang_T10_mien_Nam.csv").write_text(head + "2026-10-02,TM,GT,100\n2026-10-05,TM,MT,300\n", encoding="utf-8")
    b = bod_inputs.load(tmp_path)
    assert sum(s.net for s in b.sales) == Decimal(700)
    assert not [n for n, why in b.unused if "bản sao" in why]


def test_a_second_copy_of_the_sales_export_is_named_not_added(tmp_path):
    """The robot's repro: 'Mastersale_T07-T10 (1).csv' beside the export. Still 9/9 at the right numbers."""
    import json
    import shutil
    import bhc_case
    from engine import bod_check, bod_inputs
    bhc_case.make(tmp_path)
    shutil.copy(tmp_path / "Mastersale_T07-T10.csv", tmp_path / "Mastersale_T07-T10 (1).csv")
    key = json.loads((tmp_path / "dap_an.json").read_text(encoding="utf-8"))
    found = bod_check.review(tmp_path)[0].findings
    assert sorted((x.group, x.where) for x in found) == sorted((a["group"], a["where"]) for a in key["bod"])
    c9 = next(x for x in found if x.where == "'Tong hop'!C9")
    assert c9.expected is not None
    b = bod_inputs.load(tmp_path)
    assert len([e for e in b.receipt if e[1] == "Bán hàng"]) == 1
    assert any("bản sao" in why for _, why in b.unused)


def test_a_missing_pct_without_the_right_figure_says_why(tmp_path):
    """PC 84603e7: missing_pct at 'Tong hop'!C9 came with expected=null and no word on why."""
    import bhc_case
    from engine import bod_check
    bhc_case.make(tmp_path)
    (tmp_path / "Mastersale_T07-T10.csv").unlink()            # no revenue to divide by
    c9 = next(x for x in bod_check.review(tmp_path)[0].findings if x.where == "'Tong hop'!C9")
    assert c9.code == "missing_pct" and c9.expected is None and "chưa tính được con số đúng" in c9.message
