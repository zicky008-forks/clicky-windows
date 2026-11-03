"""What leaves the machine: masking, the first-send card, the log, and "Chỉ chạy trên máy".

Pass criteria of the feature:
1. The outgoing log never holds a name from the catalogue, nor a tax code, CCCD, bank account,
   phone or email planted in the case data; amounts (8400000000, 1.400.000.000) go out unchanged.
2. With local-only on, no provider is ever invoked and the month close says and writes exactly
   what it does with the switch off.
"""
import asyncio
import csv
import json
import re
import shutil
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ai.base_provider import Message
from engine import redact
from engine import sources
from office import month_close, outgoing
from test_month_close import FakeBridge, FakePQ, folder  # noqa: F401  (fixture)

# Planted in the case data: none of these may ever reach the log.
CUSTOMERS = [("KH01", "Công ty TNHH Minh Anh", "0101234567", "0912345678", "ketoan@minhanh.vn"),
             ("KH02", "Cửa hàng Hoà Bình", "0312345678-001", "+84 903 123 456", ""),
             ("KH09", "Siêu thị Đức Thành", "3600123456", "028 3823 4567", "")]
SUPPLIERS = [("NCC01", "Nhà phân phối Sữa Việt", "190312345678901"), ("NCC02", "Kho lạnh Phương Nam", "0021000123456")]
EMPLOYEES = [("NV01", "Trần Thị Lan", "001087012345", "0987654321")]
SECRETS = ["0101234567", "0312345678-001", "0312345678", "3600123456", "0912345678", "+84 903 123 456",
           "903123456", "028 3823 4567", "ketoan@minhanh.vn", "190312345678901", "0021000123456",
           "001087012345", "0987654321"]


def _write(path, header, rows, delimiter=","):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=delimiter)
        w.writerow(header)
        w.writerows(rows)


@pytest.fixture
def case(folder):  # noqa: F811
    """The month-close folder plus the catalogues an accountant keeps beside it."""
    _write(folder / "DanhMucKhachHang.csv", ["MaKH", "TenKH", "MST", "DienThoai", "Email"], CUSTOMERS)
    _write(folder / "DanhMucNCC.csv", ["MaNCC", "NhaCungCap", "So tai khoan"], SUPPLIERS, delimiter=";")
    _write(folder / "NhanVien.csv", ["MaNV", "Họ tên", "CCCD", "SĐT"], EMPLOYEES)
    return folder


def _excel_block():
    """What format_for_prompt would put in the prompt for a selection over the debtors sheet."""
    return ("\n\nEXCEL CONTEXT (read from Excel itself, exact):\n"
            "  workbook: CongNo_T10.xlsx · active sheet: CongNo · used range: A1:F40\n"
            "  selection: A4:F7\n"
            "    A4='KH01'  B4='CÔNG TY TNHH MINH ANH'  C4=8400000000  D4='1.400.000.000'\n"
            "    A5='KH02'  B5='cua hang hoa binh'  C5=1400000000  E5='MST 0312345678-001'\n"
            "    A6='KH09'  B6='Siêu thị Đức Thành'  C6=2800000000  E6='Liên hệ: 028 3823 4567, ĐT: 0912 345 678'\n"
            "    A7='NV01'  B7='Trần Thị Lan nhận hàng'  E7='CCCD 001087012345, chuyển STK 190312345678901'\n"
            "    A8='Lan'  B8='hóa đơn năm 2026 của Nhà phân phối Sua Viet, email ketoan@minhanh.vn'")


class FakeProvider:
    """Stands in for the network: records what it was given and answers with codes."""

    def __init__(self, answer="Khách #01 nợ nhiều nhất, sau đó là NCC #01."):
        self.calls, self.answer = [], answer

    async def stream_response(self, user_text, screenshots_b64, history, system_prompt, model=None):
        self.calls.append({"user": user_text, "system": system_prompt, "history": history})
        for i in range(0, len(self.answer), 7):
            yield self.answer[i:i + 7]

    async def health_check(self):
        return True


async def _drain(provider, **kwargs):
    return "".join([c async for c in provider.stream_response(**kwargs)])


def _gate_for(case, ask):
    gate = outgoing.Outgoing(ask=ask)
    bridge = FakeBridge(case)
    bridge.sheets["CongNo"] = [["Bảng công nợ"], [], ["MaKH", "Tên khách hàng", "Nợ"],
                               ["KH01", "Công ty TNHH Minh Anh", 8400000000.0]]
    gate.learn_workbook(bridge, "CongNo_T10.xlsx")
    return gate


# ── the recogniser ───────────────────────────────────────────────────────────

def test_columns_are_told_apart_by_their_header():
    assert redact.column_kind("TenKH") == "khach" and redact.column_kind("Khách hàng") == "khach"
    assert redact.column_kind("Nhà cung cấp") == "ncc" and redact.column_kind("Họ và tên") == "nv"
    assert redact.column_kind("Mã số thuế") == "mst" and redact.column_kind("MST người mua") == "mst"
    assert redact.column_kind("Số tài khoản") == "stk" and redact.column_kind("CCCD/CMND") == "cccd"
    assert redact.column_kind("SĐT") == "sdt" and redact.column_kind("E-mail") == "email"
    for amount in ("ThanhTien", "Doanh thu", "GiaVon", "Nợ", "Có", "Số dư cuối kỳ", "SoCT", "Ngay", "Tài khoản"):
        assert redact.column_kind(amount) is None, amount


def test_amounts_survive_and_identifiers_do_not():
    m = redact.Masker()
    text = ("Doanh thu 8400000000 (1.400.000.000 và 8,4 tỷ), DT: 2800000000, TK 5111, số tài khoản 131, "
            "MST: 0101234567, mã số thuế 3600123456, chi nhánh 0312345678-001, SĐT 0912 345 678, "
            "ĐT +84903123456, máy bàn 02838234567, CCCD 001087012345, STK 190312345678901, a.b@x.com.vn")
    out = m.mask(text)
    for kept in ("8400000000", "1.400.000.000", "8,4 tỷ", "DT: 2800000000", "TK 5111", "số tài khoản 131"):
        assert kept in out, kept
    for gone in ("0101234567", "3600123456", "0312345678", "0912 345 678", "903123456", "02838234567",
                 "001087012345", "190312345678901", "a.b@x.com.vn"):
        assert gone not in out, gone
    assert "MST: [MST]" in out and "SĐT [SĐT]" in out and "CCCD [CCCD]" in out and "STK [STK]" in out
    assert m.counts == {"mst": 3, "sdt": 3, "cccd": 1, "stk": 1, "email": 1}


def test_names_match_without_case_or_accents_and_keep_one_code_per_send():
    c = redact.Catalogue()
    c.add_rows([["MaKH", "TenKH"], ["KH01", "Công ty TNHH Minh Anh"], ["KH02", "Cửa hàng Hoà Bình"]])
    c.add_rows([["NhanVien"], ["Lan"]])
    m = redact.Masker(c)
    out = m.mask("CÔNG TY TNHH MINH ANH, cong ty tnhh minh  anh và KH01; cửa hàng hoà bình. "
                 "Lan duyệt, lần sau hóa đơn năm 2026.")
    assert out == "Khách #01, Khách #01 và Khách #01; Khách #02. NV #01 duyệt, lần sau hóa đơn năm 2026."
    assert m.counts == {"khach": 2, "nv": 1}
    assert redact.summary(14, {"khach": 3, "mst": 2}) == "Gửi 14 dòng · đã che 3 tên khách, 2 mã số thuế"


def test_codes_turn_back_into_names_on_the_machine_even_when_split_across_chunks():
    c = redact.Catalogue()
    c.add_rows([["MaKH", "TenKH"], ["KH01", "Công ty TNHH Minh Anh"]])
    c.add_rows([["NhaCungCap"], ["Kho lạnh Phương Nam"]])
    redact.Masker(c).mask("Công ty TNHH Minh Anh mua của Kho lạnh Phương Nam")
    back = redact.Unmasker(c)
    said = "".join(back.feed(x) for x in ["Khá", "ch #0", "1 nợ, N", "CC #01 chờ. NV #09 không rõ. Kh"]) + back.finish()
    assert said == "Công ty TNHH Minh Anh nợ, Kho lạnh Phương Nam chờ. NV #09 không rõ. Kh"


# ── criterion 1: the log ─────────────────────────────────────────────────────

def test_the_outgoing_log_never_holds_a_catalogue_name_or_an_identifier(case):
    asked = []

    async def ask(preview):
        asked.append(preview)
        return True

    gate = _gate_for(case, ask)
    names = gate.catalogue.names()
    assert {"Công ty TNHH Minh Anh", "Nhà phân phối Sữa Việt", "Trần Thị Lan"} <= set(names)
    block = _excel_block()
    gate.file_text("Excel", block)
    fake = FakeProvider()
    provider = outgoing.GuardedProvider(fake, gate)
    history = [Message("user", "Trần Thị Lan hỏi về Siêu thị Đức Thành"), Message("assistant", "Dạ.")]
    answer = asyncio.run(_drain(provider, user_text="Ai nợ nhiều nhất? Công ty TNHH Minh Anh à?",
                                screenshots_b64=["img"], history=history,
                                system_prompt="Bạn là trợ lý." + block + "\n  To point at a cell use [CELL:...]"))
    # The user reads real names again; the AI never saw them.
    assert answer == "Công ty TNHH Minh Anh nợ nhiều nhất, sau đó là Nhà phân phối Sữa Việt."
    assert len(asked) == 1 and asked[0]["lines"][0] == "EXCEL CONTEXT (read from Excel itself, exact):"
    assert asked[0]["summary"].startswith(f"Gửi {len(asked[0]['lines'])} dòng · đã che ")
    assert "tên khách" in asked[0]["summary"] and asked[0]["summary"].endswith("kèm 1 ảnh màn hình")

    raw = outgoing.log_path().read_text(encoding="utf-8")
    entry = json.loads(raw.splitlines()[-1])
    sent = fake.calls[0]
    assert entry["text"]["system"] == sent["system"] and entry["text"]["user"] == sent["user"]
    assert entry["chars"] == len(sent["system"]) + len(sent["user"]) + sum(len(m.content) for m in sent["history"])
    assert entry["masked"]["khach"] >= 3 and entry["masked"]["mst"] >= 1 and entry["masked"]["cccd"] == 1
    whole = raw + json.dumps([vars(m) for m in sent["history"]], ensure_ascii=False)
    folded = redact.fold(whole)
    for name in names:
        assert redact.fold(name) not in folded, name
    for secret in SECRETS:
        assert secret not in whole, secret
    # Every long digit run left is an amount from an amount column.
    assert set(re.findall(r"(?<!\d)\d{10,}(?!\d)", whole)) == {"8400000000", "1400000000", "2800000000"}
    assert "1.400.000.000" in whole
    assert not re.search(r"(?<![\d.,])0\d{8,10}(?!\d)|\+84", whole)
    assert not re.search(r"\d{9,}-\d{3}", whole)


def test_first_send_waits_for_the_card_then_later_sends_are_only_logged(case):
    asked = []

    async def ask(preview):
        asked.append(preview)
        return True

    gate = _gate_for(case, ask)
    fake = FakeProvider("Dạ.")
    provider = outgoing.GuardedProvider(fake, gate)
    block = gate.file_text("Excel", _excel_block())
    for _ in range(2):
        asyncio.run(_drain(provider, user_text="Tóm tắt", screenshots_b64=[], history=[], system_prompt=block))
    assert len(asked) == 1 and len(fake.calls) == 2
    entries = outgoing.read_log()
    assert [e["card"] for e in entries] == [False, True] and all(e["sent"] for e in entries)
    # A question without file content never shows the card.
    asyncio.run(_drain(provider, user_text="Xin chào", screenshots_b64=[], history=[], system_prompt="Bạn là trợ lý."))
    assert len(asked) == 1 and outgoing.read_log()[0]["lines"] == 0


def test_dont_send_keeps_everything_on_the_machine(case):
    async def ask(preview):
        return False

    gate = _gate_for(case, ask)
    fake = FakeProvider()
    provider = outgoing.GuardedProvider(fake, gate)
    block = gate.file_text("Excel", _excel_block())
    with pytest.raises(outgoing.Declined) as stopped:
        asyncio.run(_drain(provider, user_text="Tóm tắt", screenshots_b64=[], history=[], system_prompt=block))
    assert str(stopped.value) == outgoing.DECLINED_MESSAGE
    assert fake.calls == []
    entry = outgoing.read_log()[0]
    assert entry["sent"] is False and "text" not in entry
    from storage import setting
    assert not setting(outgoing.APPROVED_KEY, False)          # the card comes back next time


def test_each_attempt_is_reported_to_the_scene_log(case, monkeypatch):
    records = []
    monkeypatch.setattr(outgoing, "scene_log", SimpleNamespace(record=lambda kind, **f: records.append((kind, f))))

    async def ask(preview):
        return True

    gate = _gate_for(case, ask)
    provider = outgoing.GuardedProvider(FakeProvider("Dạ."), gate)
    block = gate.file_text("Excel", _excel_block())
    asyncio.run(_drain(provider, user_text="Tóm tắt", screenshots_b64=[], history=[], system_prompt=block))
    monkeypatch.setenv("CLICKY_LOCAL_ONLY", "1")
    with pytest.raises(outgoing.LocalOnly):
        asyncio.run(_drain(provider, user_text="Tóm tắt", screenshots_b64=[], history=[], system_prompt=block))
    assert [k for k, _ in records] == ["ai_call", "ai_call"]
    assert records[0][1]["preview_shown"] is True and records[0][1]["approved"] is True
    assert records[0][1]["local_only"] is False and records[0][1]["chars"] > 0
    assert records[1][1]["local_only"] is True and records[1][1]["approved"] is False


# ── criterion 2: local-only ──────────────────────────────────────────────────

def test_local_only_never_reaches_the_provider(monkeypatch):
    from storage import set_setting
    fake = FakeProvider()
    provider = outgoing.GuardedProvider(fake, outgoing.Outgoing(ask=Mock(side_effect=AssertionError)))
    set_setting("local_only", True)
    with pytest.raises(outgoing.LocalOnly) as stopped:
        asyncio.run(_drain(provider, user_text="Xin chào", screenshots_b64=[], history=[], system_prompt=""))
    assert str(stopped.value).startswith("Đang ở chế độ chỉ chạy trên máy, em không gửi gì ra ngoài.")
    assert fake.calls == [] and not outgoing.log_path().exists()
    set_setting("local_only", False)
    monkeypatch.setenv("CLICKY_LOCAL_ONLY", "1")
    assert outgoing.local_only() and outgoing.local_only_forced()


def test_local_only_keeps_speech_on_the_machine(monkeypatch):
    from config import cfg
    from storage import set_setting
    set_setting("tts_provider", "edge_tts")
    monkeypatch.setenv("CLICKY_STT", "deepgram")
    assert cfg.tts_provider() == "edge_tts" and cfg.stt_provider() == "deepgram"
    monkeypatch.setenv("CLICKY_LOCAL_ONLY", "1")
    assert cfg.tts_provider() == "sapi" and cfg.stt_provider() not in {"deepgram", "openai"}


def _manager(bridge, replies, llm):
    async def reply(text):
        replies.append(text)
        return False
    signals = Mock()
    return SimpleNamespace(
        _current_task=None, _cancel_flag=False, _emit_state=lambda s: None, _desktop_context={"title": "Book1 - Excel"},
        _MAX_COMMAND_SECONDS=2.5, _guided=False, _pending_review=None, _last_response="", _reply_local=reply,
        _excel=bridge, _get_llm=llm, _privacy_guard=True, stop=Mock(),
        **{name: getattr(signals, name) for name in (
            "sig_turn_started", "sig_caption", "sig_point_release", "sig_error", "sig_clear_drawings", "sig_draw",
            "sig_point_hold", "sig_point_at", "sig_choices", "sig_guided", "sig_response_chunk")},
    )


def _month_close_through_the_manager(case_dir, monkeypatch, local):
    """"làm báo cáo tháng từ thư mục này", "đúng rồi, làm tiếp", then "sửa giúp tôi", as the user would say them."""
    import companion_manager as module
    import skills as skills_pkg
    if local:
        monkeypatch.setenv("CLICKY_LOCAL_ONLY", "1")
    else:
        monkeypatch.delenv("CLICKY_LOCAL_ONLY", raising=False)
    monkeypatch.setattr(skills_pkg, "_loaded", [])
    for skill in month_close.SKILLS:
        skills_pkg._register(dict(skill))
    monkeypatch.setattr(month_close, "BEAT_GAP_S", 0)
    monkeypatch.setattr(month_close, "machine_date_order", lambda: sources.DAY_FIRST)
    monkeypatch.setattr(module, "capture_all_screens", Mock(side_effect=AssertionError("no screenshot")))
    bridge = FakeBridge(case_dir)
    pq = FakePQ(bridge, case_dir)
    monkeypatch.setattr(month_close, "_pq", lambda manager: pq)
    llm = Mock(side_effect=AssertionError("month close never asks the AI"))
    records = []
    monkeypatch.setattr(outgoing, "scene_log", SimpleNamespace(record=lambda kind, **f: records.append((kind, f))))
    replies = []
    manager = _manager(bridge, replies, llm)
    month_close._PENDING = None

    async def run():
        await module.CompanionManager._process_query(manager, "làm báo cáo tháng từ thư mục này")
        await module.CompanionManager._process_query(manager, "Đúng rồi, làm tiếp")
        await module.CompanionManager._process_query(manager, "Sửa giúp tôi")
        if local:
            # A question that needs AI: with the switch on it must stop before the screenshot and the provider.
            await module.CompanionManager._process_query(manager, "ai nợ nhiều nhất?")
    asyncio.run(run())
    llm_calls = llm.call_count
    month_close._PENDING = None
    return replies, bridge.sheets, pq.queries, llm_calls, records


def test_local_only_month_close_numbers_and_beats_are_identical(case, tmp_path, monkeypatch):
    other = tmp_path.parent / (tmp_path.name + "-copy")   # beside the case, not inside it: the receipt lists every file
    shutil.copytree(case, other)
    off_replies, off_sheets, off_queries, off_llm, off_rec = _month_close_through_the_manager(case, monkeypatch, local=False)
    on_replies, on_sheets, on_queries, on_llm, on_rec = _month_close_through_the_manager(other, monkeypatch, local=True)
    assert off_rec == [] and [(k, f["local_only"]) for k, f in on_rec] == [("ai_call", True)]
    assert on_replies[-1] == outgoing.LOCAL_ONLY_MESSAGE and on_llm == off_llm == 0
    assert "Excel khớp sổ cái đến từng đồng" in " ".join(on_replies)
    assert on_replies[:-1] == off_replies and len(off_replies) > 5
    assert json.dumps(on_sheets, default=str) == json.dumps(off_sheets, default=str).replace(str(case), str(other))
    assert {k: v.replace(str(other), "") for k, v in on_queries.items()} == \
           {k: v.replace(str(case), "") for k, v in off_queries.items()}


# ── the card ─────────────────────────────────────────────────────────────────

def test_card_shows_the_masked_lines_and_answers(qt_app):
    from ui.outgoing_card import DONT_SEND, SEND, OutgoingCard, format_entry
    card = OutgoingCard()
    answers = []
    card.answered.connect(answers.append)
    lines = [f"A{i}='Khách #{i:02d}'  C{i}=8400000000" for i in range(20)]
    card.show_preview({"summary": "Gửi 20 dòng · đã che 20 tên khách", "lines": lines, "images": 0})
    assert card.isVisible() and card.lines_text().splitlines() == lines
    assert card.summary_text() == "Gửi 20 dòng · đã che 20 tên khách"
    assert (card.send_button.text(), card.keep_button.text()) == (SEND, DONT_SEND) == ("Gửi đi", "Không gửi")
    card.send_button.click()
    assert answers == [True] and not card.isVisible()
    card.show_preview({"summary": "x", "lines": ["y"]})
    card.show_preview({})                      # cancelled elsewhere (Esc): hides without answering
    assert answers == [True] and not card.isVisible()
    assert "KHÔNG GỬI" in format_entry({"time": "2026-11-03T10:00:00", "summary": "Gửi 1 dòng", "sent": False})
    assert outgoing.LOCAL_ONLY_LABEL == "Chỉ chạy trên máy, không gửi gì"


def test_the_sheet_in_front_is_reread_every_turn(case):
    gate = _gate_for(case, None)
    bridge = FakeBridge(case)
    bridge.sheets["CongNo"] = [["MaKH", "TenKH"], ["KH77", "Công ty Mới Thêm"]]
    gate.learn_workbook(bridge, "CongNo_T10.xlsx", "CongNo")          # within the cache window
    assert "Công ty Mới Thêm" in gate.catalogue.names()


def test_an_online_voice_never_hears_the_names_put_back(case, monkeypatch):
    import companion_manager as module
    gate = _gate_for(case, None)
    manager = SimpleNamespace(_outgoing=gate)
    monkeypatch.setattr(module.cfg, "tts_provider", lambda: "edge_tts")
    spoken = module.CompanionManager._voice_text(manager, "Công ty TNHH Minh Anh nợ nhiều nhất.")
    assert "Minh Anh" not in spoken and "Khách" in spoken
    monkeypatch.setattr(module.cfg, "tts_provider", lambda: "sapi")
    assert "Minh Anh" in module.CompanionManager._voice_text(manager, "Công ty TNHH Minh Anh nợ nhiều nhất.")


def test_pointing_that_calls_anthropic_directly_is_masked_and_logged(case):
    gate = _gate_for(case, None)
    asked = asyncio.run(gate.cleared_text("Ô nào có Công ty TNHH Minh Anh, MST 0101234567?", images=1))
    assert "Minh Anh" not in asked and "0101234567" not in asked
    entry = outgoing.read_log()[0]
    assert entry["sent"] and entry["text"]["user"] == asked and entry["images"] == 1
