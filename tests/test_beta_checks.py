"""Robot checks for the six screen jobs before the 16/11 beta."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))

import beta_checks   # noqa: E402

HERE = Path(__file__).resolve().parents[1] / "tools" / "dien_tap"
BETA = json.loads((HERE / "case01_beta_expected.json").read_text(encoding="utf-8"))

INTAKE = {"t": 3.0, "event": "intake", "text": "Em đọc được những gì",
          "files": [{"name": "CDPS_T10.xlsx", "kind": "Sổ cân đối", "total": 1000, "used": 1000, "dropped": 0},
                    {"name": "CDPS_T9.xlsx", "kind": "Sổ cân đối", "total": 900, "used": 900, "dropped": 0},
                    {"name": "BanHang_T10.xlsx", "kind": "Bán hàng", "total": 500, "used": 380, "dropped": 120}],
          "dropped": [{"what": "dòng Tổng cộng", "amount": 100, "reason": "dòng tổng"},
                      {"what": "đơn trùng HCM-HD10341", "amount": 20, "reason": "trùng số hóa đơn"}]}


def run(*extra, intake=INTAKE):
    ev = [{"t": 1.0, "event": "turn", "text": "làm báo cáo tháng"}]
    if intake:
        ev.append(intake)
        ev.append({"t": 3.1, "event": "choices", "question": "Đúng chưa?", "labels": ["Đúng rồi, làm tiếp", "Có chỗ sai"]})
    ev.append({"t": 5.0, "event": "draw", "shape": {"kind": "spotlight"}})
    return ev + list(extra)


def names(checks):
    return {c.name: c for c in checks}


def test_good_intake_and_coverage_pass():
    out = beta_checks.evaluate(run(), {}, {"intake": BETA["intake"], "coverage": BETA["coverage"]},
                               untouched_before_confirm=True)
    assert all(c.ok for c in out), [c for c in out if not c.ok]


def test_missing_intake_or_cells_changed_early_fail():
    out = names(beta_checks.evaluate(run(intake=None), {}, {"intake": BETA["intake"]}))
    assert not out["Phiếu nhận file hiện trước khi tính"].ok
    out = names(beta_checks.evaluate(run(), {}, {"intake": BETA["intake"]}, untouched_before_confirm=False))
    assert not out["Chưa bấm xác nhận thì chưa ô nào bị sửa"].ok


def test_silently_dropped_rows_fail_coverage():
    bad = json.loads(json.dumps(INTAKE))
    bad["files"][2]["dropped"] = 100          # 20 đ of the duplicate order vanish without a mention
    out = beta_checks.evaluate(run(intake=bad), {}, {"coverage": BETA["coverage"]})
    assert not out[0].ok and "BanHang" in out[0].detail


def test_audit_sheet_status_and_original_copy(tmp_path):
    wb = tmp_path / "BaoCao_T10_dientap.xlsx"
    wb.write_bytes(b"x")
    (tmp_path / "BaoCao_T10_dientap_ban-goc.xlsx").write_bytes(b"x")
    sheets = {"Bien ban Clicky": [["STT", "Lúc", "Ô", "Chỉ tiêu", "Trước", "Sau", "Lý do"],
                                  [1, "09:00", "C12", "Nợ 5111 → 521", 120000000, 0, "chiết khấu ghi sai TK"],
                                  [2, "09:00", "C20", "Doanh thu thuần", 8400000000, 8280000000, "Tự động"],
                                  [3, "09:00", "C30", "Lợi nhuận sau thuế", 556000000, 436120000, "Tự động"]],
              "BC": [["BÁO CÁO THÁNG 10 · SƠ BỘ"]]}
    out = beta_checks.evaluate([], sheets, {"audit": BETA["audit"]}, workbook=wb)
    assert all(c.ok for c in out), [c for c in out if not c.ok]
    out = names(beta_checks.evaluate([{"t": 9, "event": "status", "label": "CHÍNH THỨC"}],
                                     {"BC": [["x"]]}, {"audit": BETA["audit"]}, workbook=tmp_path / "khac.xlsx"))
    assert not out['Có sheet "Bien ban Clicky"'].ok
    assert not out['Không tự chuyển sang "CHÍNH THỨC"'].ok
    assert not out["Lưu bản gốc _ban-goc.xlsx"].ok


def test_verify_must_not_claim_match_after_a_failed_reread():
    ev = run({"t": 50, "event": "verify", "ok": False, "diffs": [{"cell": "C30"}]},
             {"t": 51, "event": "beat", "title": "Khớp sổ cái", "text": "Khớp sổ cái 0 đ"})
    out = names(beta_checks.evaluate(ev, {}, {"verify": {"ok": False}}))
    assert not out["Không báo khớp khi file lưu bị lệch"].ok
    out = beta_checks.evaluate(run({"t": 50, "event": "verify", "ok": True}), {}, {"verify": BETA["verify"]})
    assert all(c.ok for c in out)


def test_roles_ask_and_photo_figure_absent():
    ev = run({"t": 4, "event": "choices", "question": "Có 2 sổ cân đối T10, dùng bản nào?", "labels": ["A", "B"]})
    out = beta_checks.evaluate(ev, {"BC": [[777777777]]}, {"roles": {"ask_contains": "sổ cân đối",
                                                                      "numbers_absent": [777777777]}})
    assert out[0].ok and not out[1].ok


def test_egress_masks_names_ids_and_never_prints_them():
    spec = {"egress": {"names": ["Công ty Minh Phát"], "ids": ["0101234567"]}}
    calls = run({"t": 4, "event": "ai_call", "chars": 900, "preview_shown": True, "approved": True})
    ok = beta_checks.evaluate(calls, {}, spec, egress_text='{"text": "Khách #07 nợ 120 triệu, MST [đã che]"}')
    assert all(c.ok for c in ok)
    leak = beta_checks.evaluate(calls, {}, spec,
                                egress_text='{"text": "Công ty Minh Phát MST 0101.234.567 a@b.vn"}')
    bad = [c for c in leak if not c.ok][0]
    assert "0101234567" not in bad.detail and "Minh Phát" not in bad.detail
    assert "tên" in bad.detail and "mã số" in bad.detail and "email" in bad.detail


def test_big_amounts_are_not_mistaken_for_ids():
    calls = run({"t": 4, "event": "ai_call", "preview_shown": True, "approved": True})
    out = beta_checks.evaluate(calls, {}, {"egress": {}}, egress_text="doanh thu 8280000000 đ; mã lạ 0312345678")
    loose = [c for c in out if c.name.startswith("Chuỗi 10/12/13")][0]
    assert not loose.ok and not loose.blocking and loose.detail == "1 chỗ"


def test_local_only_means_no_call():
    spec = {"egress": {"local_only": True}}
    assert beta_checks.evaluate(run(), {}, spec, egress_text="")[0].ok
    assert not beta_checks.evaluate(run({"t": 4, "event": "ai_call"}), {}, spec)[0].ok
    out = beta_checks.evaluate(run(), {}, spec, network={"connections": 2, "ports": [443]})
    assert not out[1].ok and "443" in out[1].detail
    assert beta_checks.evaluate(run(), {}, spec, network={"connections": 0, "ports": []})[1].ok


def test_cell_write_before_confirm_fails():
    ev = run({"t": 3.5, "event": "write", "ref": "BC!C30"},
             {"t": 6, "event": "chosen", "label": "Đúng rồi, làm tiếp"}, {"t": 7, "event": "write", "ref": "BC!C31"})
    out = names(beta_checks.evaluate(ev, {}, {"intake": BETA["intake"]}))
    assert out["Không có lệnh ghi ô nào trước khi bấm xác nhận"].detail == "1 lệnh ghi"


def test_tamper_must_be_caught_with_cell_and_amount():
    spec = {"tamper": {"cell": "BC!C30", "delta": 1000000}}
    good = run({"t": 50, "event": "verify", "ok": False,
                "diffs": [{"cell": "BC!$C$30", "want": 436120000, "got": 437120000}]},
               {"t": 51, "event": "beat", "text": "Ô C30 lệch 1 triệu, em chưa giao file"})
    assert all(c.ok for c in beta_checks.evaluate(good, {}, spec))
    missed = run({"t": 50, "event": "verify", "ok": True}, {"t": 51, "event": "delivered", "path": "x.xlsx"})
    assert not any(c.ok for c in beta_checks.evaluate(missed, {}, spec))


def test_robot_must_get_to_choose_the_named_book():
    spec = {"roles": {"choose": ["TB_T10.xlsx"]}}
    assert beta_checks.evaluate(run({"t": 5, "event": "chosen", "label": "Dùng TB_T10"}), {}, spec)[0].ok
    assert not beta_checks.evaluate(run(), {}, spec)[0].ok


def test_checks_only_run_for_jobs_the_case_asks_for():
    assert beta_checks.evaluate(run(), {}, {"fix_label": "Sửa giúp tôi"}) == []


def test_choose_check_does_not_accept_the_old_copy():
    spec = {"roles": {"choose": ["TB_T10.xlsx"]}}
    assert not beta_checks.evaluate(run({"t": 5, "event": "chosen", "label": "Dùng TB_T10_cu"}), {}, spec)[0].ok
    assert beta_checks.evaluate(run({"t": 5, "event": "chosen", "label": "Dùng TB_T10"}), {}, spec)[0].ok


def test_choose_check_needs_the_exact_file_name():
    spec = {"roles": {"choose": ["TB_T10.xlsx"]}}
    assert not beta_checks.evaluate(run({"t": 5, "event": "chosen", "label": "Dùng TB_T10 (1)"}), {}, spec)[0].ok
