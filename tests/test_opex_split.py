"""The month's operating costs split per department (synthetic Case F01 shape): one workbook each, reopened and checked;
an Outlook draft each that is saved and never sent; and a local tracker of who answered what."""
import asyncio
import email
import email.policy
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

import fpa_samples as fs
from engine import bod_inputs, fpa, opex_feedback, opex_split
from office import opex_skill, outlook_drafts

M = fs.M
PAYROLL_TOTAL, PERSONAL = fs.PAYROLL_TOTAL, fs.PERSONAL
_xlsx = fs.write_xlsx


def case(tmp_path, tb=True, shift=None, payroll=False) -> Path:
    return fs.opex_folder(tmp_path / "F01", tb=tb, shift=shift, payroll=payroll)


class FakeItem:
    def __init__(self, log):
        self.__dict__["log"] = log
        self.__dict__["Attachments"] = self

    def Add(self, path):
        self.log.append(("attach", path))

    def Save(self):
        self.log.append(("save",))

    def Send(self):                                     # the test fails if anything reaches these
        raise AssertionError("Send was called")

    def Display(self, *a):
        raise AssertionError("Display was called")

    def __setattr__(self, name, value):
        self.log.append(("set", name, value))


class FakeOutlook:
    def __init__(self):
        self.items = []

    def CreateItem(self, kind):
        assert kind == 0
        log = []
        self.items.append(log)
        return FakeItem(log)


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CLICKY_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()


def _variance(d):
    b = bod_inputs.load(d, fy_start=1)
    return b, fpa.compute(b.gl, b.plans, fy_start=1, config=b.config)


def test_each_department_gets_its_own_lines_and_the_totals_add_up(tmp_path):
    b, v = _variance(case(tmp_path))
    packs = opex_split.split(v, b.gl, b.config)
    assert [p.dept for p in packs] == ["HC", "KD", "MKT", ""]                     # unassigned last
    kd = next(p for p in packs if p.dept == "KD")
    assert (kd.name, kd.email, kd.cc) == ("Kinh doanh", "kd@example.com", "truongkd@example.com")
    assert packs[-1].email == "ketoan@example.com"                                 # accounting assigns those
    assert sum(p.totals[0] for p in packs) == fpa.Variance.totals(v.month)[0]
    for p in packs:
        assert sum(x.amount for x in p.details) == p.totals[0]
        assert all(isinstance(x.amount, Decimal) for x in p.details)
    mkt = next(p for p in packs if p.dept == "MKT")
    assert mkt.totals[0] == sum(fs.actual_month("MKT", e, 10) for d, e, _a, _p in fs.PLAN if d == "MKT") \
        + fs.UNPLANNED[3] - fs.WRITTEN_DOWN[3]
    assert opex_split.no_address(packs) == ["Hành chính"]


def test_the_workbook_is_new_reopened_and_never_overwritten(tmp_path):
    b, v = _variance(case(tmp_path))
    p = next(x for x in opex_split.split(v, b.gl, b.config) if x.dept == "MKT")
    out = tmp_path / "out"
    path = opex_split.write_pack(p, v, out)
    wb = load_workbook(path)
    s = wb[opex_split.SUMMARY]
    assert s["A1"].value.startswith("SƠ BỘ · Chi phí hoạt động tháng 10/2026 · Marketing")
    assert [c.value for c in s[3]][8:] == ["Phản hồi", "Ghi chú"]
    total = next(r for r in s.iter_rows(values_only=True) if r[0] == "Tổng cộng")
    assert Decimal(str(total[1])) == p.totals[0] and Decimal(str(total[2])) == p.totals[1]
    assert opex_split.read_tag(wb.properties.keywords) == ("2026-10", "MKT")
    lists = [dv for dv in s.data_validations.dataValidation]
    assert lists and lists[0].formula1 == '"Đúng,Sai,Cần hỏi"'
    detail = wb[opex_split.DETAIL]
    assert detail.max_row - 2 == len(p.details)
    with pytest.raises(FileExistsError):
        opex_split.write_pack(p, v, out)


def test_a_file_that_does_not_reopen_to_the_figures_is_not_written(tmp_path, monkeypatch):
    b, v = _variance(case(tmp_path))
    p = next(x for x in opex_split.split(v, b.gl, b.config) if x.dept == "KD")
    p.details.pop()                                   # the detail no longer adds up to the summary
    with pytest.raises(ValueError, match="không khớp"):
        opex_split.write_pack(p, v, tmp_path / "out")


def test_the_draft_says_the_month_the_biggest_moves_and_what_to_do(tmp_path):
    b, v = _variance(case(tmp_path))
    packs = opex_split.split(v, b.gl, b.config)
    mkt = next(p for p in packs if p.dept == "MKT")
    d = opex_split.draft(mkt, v, Path("Chi phi MKT.xlsx"), reply_by="18/11/2026", sender="Phòng FP&A")
    assert d.to == "mkt@example.com" and d.subject == "[SƠ BỘ] Chi phí tháng 10/2026 - Marketing: nhờ kiểm tra"
    assert "Chào anh/chị phòng Marketing," in d.body and "trước ngày 18/11/2026" in d.body
    assert "- Quảng cáo: thực hiện" in d.body.split("Các khoản chênh nhiều nhất:")[1].splitlines()[1]
    assert "Đúng, Sai hoặc Cần hỏi" in d.body and d.body.rstrip().endswith("Phòng FP&A")
    un = opex_split.draft(packs[-1], v, Path("x.xlsx"))
    assert un.subject == "[SƠ BỘ] Chi phí tháng 10/2026: 1 dòng chưa gán bộ phận" and "Chào anh/chị kế toán," in un.body


def test_outlook_only_ever_saves_drafts(tmp_path):
    b, v = _variance(case(tmp_path))
    packs = opex_split.split(v, b.gl, b.config)
    out = tmp_path / "out"
    drafts = [opex_split.draft(p, v, opex_split.write_pack(p, v, out)) for p in packs]
    fake = FakeOutlook()
    saved = outlook_drafts.save_all(drafts, out, outlook=lambda: fake)
    assert [s.how for s in saved] == ["outlook"] * len(packs)
    for log, d in zip(fake.items, drafts):
        assert log[-1] == ("save",) and ("set", "Subject", d.subject) in log and ("set", "Body", d.body) in log
        assert ("set", "To", d.to) in log and (("set", "CC", d.cc) in log) == bool(d.cc)
        assert not any(x[0] == "set" and x[1] not in ("To", "CC", "Subject", "Body") for x in log)
        assert any(x[0] == "attach" and x[1].endswith(".xlsx") for x in log)


def test_the_wrapper_refuses_send_and_display():
    item = outlook_drafts._NoSend(FakeItem([]))
    for name in ("Send", "Display"):
        with pytest.raises(outlook_drafts.SendRefused):
            getattr(item, name)()
    with pytest.raises(AttributeError):
        item.Recipients = "x"                          # nothing but the four fields can be set


def test_send_and_display_appear_nowhere_but_the_refusal():
    src = (Path(__file__).parent.parent / "office" / "outlook_drafts.py").read_text(encoding="utf-8")
    code = "\n".join(line.split("#")[0] for line in src.splitlines())
    assert ".Send(" not in code and ".Display(" not in code and "Send()" not in code


def test_without_outlook_each_draft_is_an_unsent_eml(tmp_path):
    b, v = _variance(case(tmp_path))
    p = next(x for x in opex_split.split(v, b.gl, b.config) if x.dept == "KD")
    out = tmp_path / "out"
    d = opex_split.draft(p, v, opex_split.write_pack(p, v, out))
    [s] = outlook_drafts.save_all([d], out, outlook=lambda: None)
    assert s.how == "eml"
    m = email.message_from_bytes(Path(s.path).read_bytes())
    assert m["X-Unsent"] == "1" and m["To"] == "kd@example.com" and m["Cc"] == "truongkd@example.com"
    parts = [x for x in m.walk() if x.get_filename()]
    assert [x.get_filename() for x in parts] == ["Chi phi KD.xlsx"]


def test_the_split_stops_when_the_costs_do_not_match_the_books(tmp_path):
    d = case(tmp_path, shift={"6418": 4 * M})
    with pytest.raises(opex_skill.Stop) as stop:
        opex_skill.prepare(d, outlook=lambda: FakeOutlook())
    assert "lệch sổ cân đối ở TK 6418" in stop.value.beat.text and "chưa tách" in stop.value.beat.text
    assert not list(d.glob(opex_skill.OUT_PREFIX + "*"))                          # nothing written


def test_the_whole_split_on_the_folder(tmp_path):
    d = case(tmp_path)
    before = {p.name: p.read_bytes() for p in d.iterdir()}
    fake = FakeOutlook()
    r = opex_skill.prepare(d, outlook=lambda: fake)
    assert r["out"].name == "Clicky_chi_phi_phong_ban_T10-2026" and r["matched"].ok
    assert sorted(p.name for p in r["out"].iterdir()) == ["Chi phi HC.xlsx", "Chi phi KD.xlsx", "Chi phi MKT.xlsx",
                                                          "Chi phi chua_gan.xlsx"]
    assert len(fake.items) == 4
    assert {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()} == before          # the customer's files
    texts = [x.text for x in opex_skill.beats_for(r)]
    assert texts[0] == "Em đã tách chi phí tháng 10/2026 cho 3 phòng, mỗi phòng một file, mở lại kiểm tra thấy khớp."
    assert texts[1].startswith("Chi phí theo bộ phận tháng 10/2026 khớp sổ cân đối")
    assert any("Em không gửi thư nào" in t for t in texts)
    assert any(t.startswith("Danh mục chưa có email của Hành chính") for t in texts)
    assert [e["dept"] for e in opex_feedback.events()] == ["HC", "KD", "MKT", ""]
    assert opex_skill.out_folder(d, "2026-10").name == "Clicky_chi_phi_phong_ban_T10-2026 (2)"   # a rerun: new
    assert opex_feedback.sentence("2026-10") == ("0/4 phòng đã xác nhận; chưa trả lời: Hành chính, Kinh doanh, "
                                                 "Marketing, Chưa gán bộ phận.")


def test_without_a_trial_balance_it_says_so(tmp_path):
    r = opex_skill.prepare(case(tmp_path, tb=False), outlook=lambda: FakeOutlook())
    assert r["matched"] is None
    assert opex_skill.beats_for(r)[1].text.startswith("Thư mục chưa có bảng cân đối tài khoản")


def _answer(src: Path, dst: Path, answers: dict, change: tuple = ()) -> Path:
    """What a department does: choose in the Phản hồi column, maybe type over a figure, save under another name."""
    wb = load_workbook(src)
    for (sheet, cell), value in answers.items():
        wb[sheet][cell] = value
    for sheet, cell, value in change:
        wb[sheet][cell] = value
    wb.save(dst)
    return dst


def test_replies_are_read_counted_and_hand_edits_named(tmp_path):
    d = case(tmp_path)
    r = opex_skill.prepare(d, outlook=lambda: FakeOutlook())
    out = r["out"]
    s, t = opex_split.SUMMARY, opex_split.DETAIL
    kd = load_workbook(out / "Chi phi KD.xlsx")
    n_sum = sum(1 for row in kd[s].iter_rows(min_row=4, values_only=True) if row[0] and row[0] != "Tổng cộng")
    all_ok = {(s, f"I{4 + i}"): "Đúng" for i in range(n_sum)}
    all_ok.update({(t, f"G{3 + i}"): "đúng" for i in range(kd[t].max_row - 2)})
    _answer(out / "Chi phi KD.xlsx", d / "RE Chi phi KD.xlsx", all_ok)
    _answer(out / "Chi phi MKT.xlsx", d / "Chi phi MKT - phan hoi.xlsx",
            {(s, "I4"): "Sai", (s, "J4"): "Lương T10 chưa có thưởng", (t, "G3"): "Cần hỏi", (t, "G4"): "ok"},
            change=((t, "E5", 1),))
    found = opex_skill.replies(d)
    by = {x.dept: x for x in found}
    assert set(by) == {"KD", "MKT"}
    assert by["KD"].done and by["KD"].counts["Đúng"] == len(by["KD"].rows)
    mkt = by["MKT"]
    assert not mkt.done and (mkt.counts["Sai"], mkt.counts["Cần hỏi"], mkt.counts["khác"]) == (1, 1, 1)
    assert [r.row for r in mkt.to_follow] == [4, 3, 4] and mkt.to_follow[0].note == "Lương T10 chưa có thưởng"
    assert [(c[0], c[1]) for c in mkt.changed] == [(t, 5)]
    assert opex_feedback.sentence("2026-10") == ("1/4 phòng đã xác nhận; cần xử lý: Marketing; chưa trả lời: "
                                                 "Hành chính, Chưa gán bộ phận.")
    names = {"KD": "Kinh doanh", "MKT": "Marketing"}
    texts = [b.text for b in opex_skill.reply_beats(found, names)]
    assert texts[0].startswith("Em đã đọc 2 file phản hồi: 1/4 phòng đã xác nhận")
    assert "Marketing đã sửa 1 số trong file. Em không lấy số sửa tay; chị hỏi lại phòng này." in texts
    assert "Marketing: 1 dòng Sai, 1 dòng Cần hỏi, 1 dòng khác. Em ghi lại để chị chuyển kế toán." in texts


def test_a_workbook_that_is_not_one_of_ours_is_not_an_answer(tmp_path):
    p = _xlsx(tmp_path / "x.xlsx", {"Tổng hợp": [["a"]]})
    assert opex_feedback.read_reply(p) is None


def test_the_robot_flags_and_the_chat_words():
    assert opex_skill.args(["Clicky.exe", "--opex-split", "D:/case"]) == "D:/case"
    assert opex_skill.reply_args(["Clicky.exe", "--opex-replies", "D:/case"]) == "D:/case"
    import re
    split = re.compile(opex_skill.SPLIT_TRIGGER, re.I)
    reply = re.compile(opex_skill.REPLY_TRIGGER, re.I)
    for said in ("tách chi phí cho các phòng ban", "gửi chi phí tháng 10 cho từng phòng", "chia chi phí theo bộ phận"):
        assert split.search(said) and not reply.search(said), said
    for said in ("xem phản hồi chi phí", "các phòng đã trả lời chi phí chưa"):
        assert reply.search(said), said


def test_run_plays_the_beats_and_logs_counts_only(tmp_path, monkeypatch):
    d = case(tmp_path)
    played, logged = [], []
    monkeypatch.setattr(opex_skill.mc, "play", lambda m, beats, **k: _done(played.extend(beats)))
    monkeypatch.setattr(opex_skill.scene_log, "record", lambda event, **data: logged.append((event, data)))
    monkeypatch.setattr(outlook_drafts, "_outlook", lambda: FakeOutlook())
    asyncio.run(opex_skill.run(object(), d))
    assert played[0].mark == "ok" and [e for e, _d in logged] == ["opex_start", "drafts_saved", "opex_split"]
    flat = repr(logged)
    assert "example.com" not in flat and "Marketing" not in flat and str(d) not in flat


async def _noop():
    return None


def _done(_):
    return _noop()


def _cells(path: Path) -> str:
    wb = load_workbook(path)
    return "\n".join(str(c.value) for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.value is not None)


def test_pay_lines_never_reach_a_department_and_the_totals_stay(tmp_path):
    d = case(tmp_path, payroll=True)
    r = opex_skill.prepare(d, outlook=lambda: None)
    assert r["matched"].ok
    for p, path in zip(r["packs"], r["paths"]):
        text = _cells(path) + "\n" + opex_split.draft(p, r["v"], path).body
        if p.dept:
            assert not any(x in text for x in PERSONAL), p.dept
        held = [x for x in p.details if x.text.startswith(opex_split.HELD)]
        if p.dept:
            assert sorted(x.element for x in held) == ["LUONG"]
            assert all(not x.ref and not x.where for x in held)
        else:
            assert not held                               # accounting keeps every line
    kd = next(p for p in r["packs"] if p.dept == "KD")
    pay = next(x for x in kd.details if x.element == "LUONG")
    assert pay.amount == fs.actual_month("KD", "LUONG", 10) + PAYROLL_TOTAL and pay.text == f"{opex_split.HELD} (4 dòng)"
    assert sum(p.totals[0] for p in r["packs"]) == fpa.Variance.totals(r["v"].month)[0]
    for p in r["packs"]:
        assert sum(x.amount for x in p.details) == p.totals[0]
    for eml in r["out"].glob("*.eml"):
        if "chua_gan" not in eml.name:
            body = email.message_from_bytes(eml.read_bytes(), policy=email.policy.default).get_body(("plain",)).get_content()
            assert not any(x in body for x in PERSONAL)


def test_what_counts_as_pay_and_the_catalogue_can_add_more():
    c = bod_inputs.Config(elements={"QC": {"ten": "Quảng cáo", "tk": "6418"}, "CL": {"ten": "Kiểm tra chất lượng"},
                                    "TM": {"ten": "Phí thương mại"}, "TH": {"ten": "Thưởng KPI"},
                                    "BH": {"ten": "Bảo hiểm xã hội"}, "DT": {"ten": "Đào tạo"}},
                          rules={"yeu to nhay cam": "DT"})
    assert [e for e in ("QC", "CL", "TM", "TH", "BH", "DT", "LUONG", "KH") if opex_split.sensitive(e, "", c)] == \
        ["TH", "BH", "DT", "LUONG"]
    assert opex_split.sensitive("CTP", "6421", c) and not opex_split.sensitive("CTP", "6418", c)


def test_outlook_is_never_started_when_the_flag_or_setting_says_eml(tmp_path, monkeypatch):
    b, v = _variance(case(tmp_path))
    p = next(x for x in opex_split.split(v, b.gl, b.config) if x.dept == "KD")
    out = tmp_path / "out"
    path = opex_split.write_pack(p, v, out)

    def no_dispatch():
        raise AssertionError("Outlook was started")
    monkeypatch.setattr(outlook_drafts, "_outlook", no_dispatch)
    monkeypatch.setenv("CLICKY_OUTLOOK", "0")
    [s] = outlook_drafts.save_all([opex_split.draft(p, v, path)], out)
    assert s.how == "eml"
    monkeypatch.delenv("CLICKY_OUTLOOK")
    from storage import set_setting
    set_setting("drafts", "eml")
    (out / "Chi phi KD.eml").unlink()
    [s] = outlook_drafts.save_all([opex_split.draft(p, v, path)], out)
    assert s.how == "eml"


def test_the_robot_runs_without_outlook_and_grades_a_real_run(tmp_path, monkeypatch):
    import json
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))
    import opex_robot
    log = tmp_path / "scene.jsonl"
    env = opex_robot.robot_env(log)
    assert env["CLICKY_OUTLOOK"] == "0" and env["CLICKY_LOCAL_ONLY"] == "1"
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(log))
    monkeypatch.setenv("CLICKY_OUTLOOK", "0")
    monkeypatch.setattr(outlook_drafts, "_outlook", lambda: (_ for _ in ()).throw(AssertionError("Outlook")))
    r = opex_skill.prepare(case(tmp_path))
    events = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] + [{"event": "story_end"}]
    assert opex_robot.finished(events)
    res = opex_robot.grade(events, True, r["out"])
    assert res.ok, res.markdown("x")
    bad = opex_robot.grade([e for e in events if e["event"] != "drafts_saved"], False, r["out"])
    assert {l.name for l in bad.lines if not l.ok} == {"Không mở Outlook, không gửi thư nào",
                                                       "Mỗi phòng một file và một thư nháp", "File của case không bị sửa"}


def test_the_robot_case_has_pay_lines_and_a_leak_fails_the_run(tmp_path, monkeypatch):
    import json
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))
    import opex_robot
    import tao_case_chi_phi
    assert opex_robot.PERSONAL == fs.PERSONAL == tao_case_chi_phi.PERSONAL
    d = tao_case_chi_phi.make(tmp_path / "case")
    with pytest.raises(FileExistsError):
        tao_case_chi_phi.make(d)                                  # never written over
    assert tao_case_chi_phi.main(["--out", str(d)]) == 2
    assert any(x in (d / "SoChiTietChiPhi_2026.csv").read_text(encoding="utf-8-sig") for x in fs.PERSONAL)
    log = tmp_path / "scene.jsonl"
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(log))
    monkeypatch.setenv("CLICKY_OUTLOOK", "0")
    r = opex_skill.prepare(d)
    assert r["matched"].ok                                        # the books move with the pay lines
    events = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] + [{"event": "story_end"}]
    res = opex_robot.grade(events, True, r["out"])
    assert res.ok, res.markdown("x")
    eml = sorted(r["out"].glob("*.eml"))[0]
    eml.write_bytes(eml.read_bytes().replace(b"X-Unsent: 1", "X-Unsent: 1\nX-Note: PL77701".encode()))
    bad = opex_robot.grade(events, True, r["out"])
    [line] = [l for l in bad.lines if not l.ok]
    assert line.name == "Không lộ tên người hay chứng từ lương" and eml.name in line.detail
    assert not any(x in line.detail for x in fs.PERSONAL)          # the robot never repeats a name
