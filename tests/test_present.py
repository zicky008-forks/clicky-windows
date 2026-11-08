"""Present a report pack: numbers in Vietnamese, placeholders, question bank, what-if safety, deck shapes, talk."""
import pytest

from present import deck as deck_mod
from present import preflight
from present.placeholders import Resolver, kind_from_number_format
from present.qa import Bank, fuzzy_names, parse_percent, pick_option
from present.talk import Stop, Talk, estimate_seconds
from present.vn_format import fmt, int_words, speakable
from present.whatif import WhatIf, WhatIfRefused, expand


# ---- numbers --------------------------------------------------------------------------------
def test_display_formats():
    assert fmt(1234.56, "bn") == "1.234,6 tỷ"
    assert fmt(7400, "bn0") == "7.400 tỷ"
    assert fmt(0.07034, "pct2") == "7,03%"
    assert fmt(-0.123, "pp") == "-0,12 điểm %"
    with pytest.raises(ValueError):
        fmt("#REF!", "bn")
    with pytest.raises(ValueError):
        fmt(None, "bn")


@pytest.mark.parametrize("n,words", [(15, "mười lăm"), (21, "hai mươi mốt"), (24, "hai mươi tư"), (105, "một trăm linh năm"),
                                     (1015, "một nghìn không trăm mười lăm"), (120_000_000_000, "một trăm hai mươi tỷ")])
def test_int_words(n, words):
    assert int_words(n) == words


def test_speakable_reads_numbers_as_words():
    assert speakable("xin 520,0 tỷ, bằng 7,03% DTT") == "xin năm trăm hai mươi tỷ, bằng bảy phẩy không ba phần trăm DTT"
    assert "một nghìn hai trăm ba mươi tư phẩy sáu tỷ" in speakable("1.234,6 tỷ")


def test_kind_from_excel_number_format():
    assert kind_from_number_format("0.00%") == "pct2"
    assert kind_from_number_format("0.0%") == "pct1"
    assert kind_from_number_format("#,##0.0") == "bn"


# ---- placeholders ---------------------------------------------------------------------------
class DictSource:
    def __init__(self, summary, cells=None, formats=None, sheets=("Tong_quan", "Kich_ban")):
        self.summary, self.cells, self.formats, self.sheets = summary, dict(cells or {}), formats or {}, list(sheets)

    def rows(self, sheet, first_row, cols):
        if sheet == "Tong_quan":
            return [[k, v, f] for k, (v, f) in self.summary.items()]
        return [[v] * cols for v in self.cells.get(sheet + "!rows", [])]

    def cell(self, sheet, address):
        return self.cells.get(f"{sheet}!{address}")

    def number_format(self, sheet, address):
        return self.formats.get(f"{sheet}!{address}", "#,##0.0")

    def sheet_names(self):
        return self.sheets


SUMMARY = {"cost": (520.0, "bn"), "ratio": (0.0703, "pct2"), "s2": (0.0, "bn"), "s1": (2.0, "bn")}


def test_fill_and_missing_never_spoken():
    r = Resolver(DictSource(SUMMARY, {"Kich_ban!B32": 0.07}, {"Kich_ban!B32": "0.00%"}))
    out = r.fill("Xin {tq:cost}, bằng {tq:ratio}; sau rule {xl:Kich_ban!B32}.")
    assert out.text == "Xin 520,0 tỷ, bằng 7,03%; sau rule 7,00%." and not out.missing
    bad = r.fill("Năm trước {tq:khong_co} và {xl:Kich_ban!Z99}")
    assert [t for t, _ in bad.missing] == ["{tq:khong_co}", "{xl:Kich_ban!Z99}"]


def test_optional_part_dropped_when_zero():
    r = Resolver(DictSource(SUMMARY))
    assert r.fill("Bước một {tq:s1}[[, bước hai {tq:s2}]].").text == "Bước một 2,0 tỷ."
    assert r.fill("Bước một {tq:s2}[[, bước hai {tq:s1}]].").text == "Bước một 0,0 tỷ, bước hai 2,0 tỷ."


# ---- question bank --------------------------------------------------------------------------
BANK = {"cau_hoi": [
    {"id": "TOTAL", "hoi_mau": "Tổng chi phí?", "tu_khoa": {"va": [["tong", "bao nhieu"], ["aopr", "xin"]]}, "tra_loi": "Tổng {tq:cost}."},
    {"id": "VS_AOP", "hoi_mau": "So với AOP gốc?", "tu_khoa": {"va": [["tang", "chenh"], ["aop goc", "aop"]]}, "tra_loi": "x"},
    {"id": "DEPT", "hoi_mau": "Phòng X?", "tu_khoa": {"va": [["phong"], ["cat", "xin"]]},
     "tham_so": {"ten_phong": {"sheet": "De_xuat", "cot": 1, "tu_dong": 4}}, "tra_loi": "{ten_phong}: {xl:De_xuat!B<dong>}."},
    {"id": "WHATIF", "hoi_mau": "Nếu DTT kênh giảm?", "tu_khoa": {"va": [["neu", "gia su"], ["doanh thu", "dtt"]]},
     "what_if": {"ghi": [{"o": "Kich_ban!F{dong_kenh}", "gia_tri": "{pct}"}], "dong_kenh": {"GT": 15, "MT": 16},
                 "tat_ca": "Kich_ban!F15:F16", "doc": ["Kich_ban!B27"]},
     "tra_loi": "Với doanh thu {kenh} {pct}: cần cắt {xl:Kich_ban!B27}."}],
    "khong_khop": {"tra_loi": "Câu này em ghi lại."}}


def test_whole_word_match_aop_is_not_aopr():
    b = Bank(BANK)
    assert b.ask("Tổng chi phí AOPR là bao nhiêu?").best.entry["id"] == "TOTAL"
    assert b.ask("Tăng bao nhiêu so với AOP gốc?").best.entry["id"] == "VS_AOP"
    assert b.ask("Giá đường tháng sau?").no_match


@pytest.mark.parametrize("text,value", [("giảm 5%", -0.05), ("tăng 2,5 phần trăm", 0.025), ("giảm năm phần trăm", -0.05),
                                        ("tăng mười lăm phần trăm", 0.15), ("giảm hai mươi lăm phần trăm", -0.25), ("thế nào", None)])
def test_parse_percent(text, value):
    assert parse_percent(text) == (pytest.approx(value) if value is not None else None)


def test_names_and_channels():
    names = ["P. Brand MKT", "P. Trade MKT", "P. IT"]
    assert fuzzy_names("Phòng Brand MKT bị cắt bao nhiêu", names) == ["P. Brand MKT"]
    assert fuzzy_names("Phòng IT xin bao nhiêu", names) == ["P. IT"]
    assert fuzzy_names("Phòng MKT bị cắt bao nhiêu", names) == ["P. Brand MKT", "P. Trade MKT"]   # ask which
    assert pick_option("kênh HRC KA giảm 3%", ["GT", "HRC+KA"], {"HRC+KA": ["hrc ka"]}) == ["HRC+KA"]


# ---- what-if on a fake COM workbook ---------------------------------------------------------
class FakeRange:
    def __init__(self, book, key):
        object.__setattr__(self, "book", book)
        object.__setattr__(self, "key", key)

    def __getattr__(self, name):
        if name in ("Formula", "Value2"):
            return self.book.cells.get(self.key, "")
        raise AttributeError(name)

    def __setattr__(self, name, value):
        self.book.cells[self.key] = value
        self.book.writes.append((self.key, value))


class FakeBook:
    def __init__(self, cells, autosave=False, broken=None):
        self.cells, self.writes, self.AutoSaveOn, self.Saved, self.broken = dict(cells), [], autosave, True, broken

    def Worksheets(self, sheet):
        book = self
        return type("S", (), {"Range": lambda _s, a: FakeRange(book, f"{sheet}!{a}")})()

    def calculate(self):
        f = self.cells.get("Kich_ban!F15", 0) or 0
        self.cells["Kich_ban!B27"] = 10.0 + (-f) * 100


def test_whatif_restores_inputs_and_keeps_results():
    book = FakeBook({"Kich_ban!F15": 0, "Kich_ban!F16": "=0", "Kich_ban!B27": 10.0})
    run = WhatIf(book, calculate=book.calculate).run({"Kich_ban!F15:F16": -0.05}, ["Kich_ban!B27"])
    assert run.results == {"Kich_ban!B27": 15.0}
    assert run.restored and book.cells["Kich_ban!F15"] == 0 and book.cells["Kich_ban!F16"] == "=0"
    assert book.cells["Kich_ban!B27"] == 10.0 and book.Saved


def test_whatif_refuses_autosave_and_writes_nothing():
    book = FakeBook({"Kich_ban!F15": 0}, autosave=True)
    with pytest.raises(WhatIfRefused):
        WhatIf(book, calculate=book.calculate).run({"Kich_ban!F15": -0.05}, ["Kich_ban!B27"])
    assert book.writes == []


def test_expand_range():
    assert expand("Kich_ban!F12:F14") == ["Kich_ban!F12", "Kich_ban!F13", "Kich_ban!F14"]


# ---- deck shapes ----------------------------------------------------------------------------
def S(i, text="", top=0, left=0, w=100, h=50, **kw):
    return deck_mod.ShapeInfo(i, f"s{i}", left, top, w, h, text, **kw)


def test_find_shapes():
    shapes = [S(1, "Tiêu đề", is_title=True), S(2, "1 Duyệt trần", top=100), S(3, "Năm 2025", top=50),
              S(4, top=200, w=300, h=200, is_chart=True), S(5, top=200, w=100, h=100, is_chart=True, chart_type=119),
              S(6, "a | b", top=300, is_table=True)]
    assert deck_mod.find(shapes, kind="tiêu đề").index == 1
    assert deck_mod.find(shapes, contains="1").index == 2          # numbered item, not "2025"
    assert deck_mod.find(shapes, kind="biểu đồ chính").index == 4
    assert deck_mod.find(shapes, kind="biểu đồ thác nước").index == 5
    assert deck_mod.find(shapes, kind="bảng").index == 6
    assert deck_mod.find(shapes, contains="không có") is None


def test_to_screen_letterboxed():
    shape = S(1, left=0, top=0, w=960, h=540)
    # 16:9 slide in a 4:3 window: bars top and bottom, full width
    x, y, w, h = deck_mod.to_screen(shape, 960, 540, 0, 0, 960, 720)
    assert (x, w) == (0, 1280) and y == round(90 * 96 / 72)


# ---- preflight and talk ---------------------------------------------------------------------
class FakeDeck:
    def __init__(self, slides):
        self.slides = slides

    def count(self):
        return len(self.slides)

    def goto(self, n):
        return n

    def slide_shapes(self, n):
        return self.slides[n - 1]

    def title(self, n):
        t = deck_mod.find(self.slides[n - 1], kind="tiêu đề")
        return t.text if t else ""

    def screen_rect(self, shape):
        return (1, 2, 3, 4)


def test_preflight_numbers_on_slides_and_safety(monkeypatch):
    monkeypatch.delenv("CLICKY_LOCAL_ONLY", raising=False)
    deck = FakeDeck([[S(1, "Tổng", is_title=True), S(2, "AOPR 520,0 tỷ, ngưỡng 0,5 tỷ, lạ 999,9")]])
    r = Resolver(DictSource(SUMMARY, {"Kich_ban!B27": 2.0, "Kich_ban!B28": 2.0, "Kich_ban!B29": 0.0}))
    rep = preflight.run([{"loai": "so_slide", "bang": 1},
                         {"loai": "so_tren_slide", "slide": [1], "bo_qua": ["0,5"]},
                         {"loai": "tong", "cac_o": ["Kich_ban!B28", "Kich_ban!B29"], "bang_o": "Kich_ban!B27"},
                         {"loai": "an_toan"}], r, deck, {}, {}, {"voice_offline": True, "ai_on": False})
    assert [c.ok for c in rep.checks] == [True, False, True, False]
    assert "999,9" in rep.checks[1].detail and not rep.ok
    monkeypatch.setenv("CLICKY_LOCAL_ONLY", "1")
    assert preflight.run([{"loai": "an_toan"}], r, deck, safety={"voice_offline": True, "ai_on": False}).ok


def test_talk_slides_questions_whatif_and_stop():
    said, pointed = [], []
    deck = FakeDeck([[S(1, "Thông điệp chính", is_title=True), S(2, "AOPR 520,0", top=100)]])
    book = FakeBook({"Kich_ban!F15": 0, "Kich_ban!F16": 0, "Kich_ban!B27": 10.0})
    src = DictSource(SUMMARY, {"Kich_ban!B27": 10.0, "De_xuat!rows": []})
    src.cells = book.cells                       # what-if writes land where the resolver reads
    src.rows = lambda sheet, first, cols: [["P. IT", 3.0]] if sheet == "De_xuat" else DictSource.rows(src, sheet, first, cols)
    src.cells["De_xuat!B4"] = 3.0
    script = {"meta": {}, "slides": [{"so": 1, "loi_noi": "{slide:title}: xin {tq:cost}.", "chi_vao": [{"shape_chua": "{tq:cost}"}]}]}
    talk = Talk(script, BANK, Resolver(src), deck, say=lambda shown, spoken: said.append(shown),
                point=lambda rect, label: pointed.append(label), wait=lambda s: None,
                whatif=WhatIf(book, calculate=book.calculate))
    talk.present()
    assert said == ["Thông điệp chính: xin 520,0 tỷ."] and pointed == ["520,0 tỷ"]     # found "520,0" without unit
    assert talk.answer("Tổng chi phí AOPR là bao nhiêu?")["text"] == "Tổng 520,0 tỷ."
    assert talk.answer("Phòng IT xin bao nhiêu?")["text"] == "P. IT: 3,0 tỷ."
    r = talk.answer("Nếu doanh thu kênh GT giảm 5% thì sao?")
    assert r["text"] == "Với doanh thu kênh GT giảm 5%: cần cắt 15,0 tỷ."                # result read before restore
    assert book.cells["Kich_ban!F15"] == 0 and book.cells["Kich_ban!B27"] == 10.0
    assert talk.answer("Giá đường?")["text"] == "Câu này em ghi lại." and talk.minutes.open_questions == ["Giá đường?"]
    talk.script = {"slides": [{"so": 1, "loi_noi": "Số {tq:khong_co}"}]}
    with pytest.raises(Stop):
        talk.present()
    assert said[-1] != "Số {tq:khong_co}"
    assert "Câu hỏi mở" in talk.minutes.markdown()


def test_estimate_seconds():
    script = {"slides": [{"so": 1, "loi_noi": "một hai ba {tq:x}", "dung_giay": 2}]}
    assert estimate_seconds(script, syllables_per_second=3) == pytest.approx(2 + 1 + 3 / 3 + 6 / 3)
