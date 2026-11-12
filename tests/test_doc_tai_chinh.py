"""How Clicky reads an FP&A sentence aloud: numbers, signs, periods, abbreviations and breaths.

The corpus below is synthetic (no company data). It was checked by voicing each line with the offline
Piper voice and reading it back with a Vietnamese speech recogniser."""
import pytest

from audio.tts import vi_g2p
from present.doc_tai_chinh import ENGLISH, LETTERS, WORDS, doc, pieces
from present.vn_format import speakable

STRESS = [
    "Doanh thu thuần tháng 10 đạt 245,3 tỷ, vượt budget 3,2% và tăng 8,5% YoY.",
    "Lợi nhuận gộp −5,4 tỷ so với AOP do giá vốn tăng.",
    "Lợi nhuận trước thuế: −5,4 tỷ, tức là lỗ.",
    "Lợi nhuận tháng 10: -2,1 tỷ.",
    "Chênh lệch EBITDA: (1.234) triệu, chủ yếu do CPBH.",
    "EBITDA margin giảm 120 bps xuống 14,2%.",
    "Biên lợi nhuận gộp giảm 1,5pp so với cùng kỳ.",
    "Doanh thu Q3/2026 đạt 1,2 nghìn tỷ.",
    "Lũy kế 9M đạt 78% kế hoạch năm.",
    "H1 2026 doanh thu tăng 12% YoY.",
    "Doanh thu YTD đạt 2.345,6 tỷ, tăng 9,1%YoY.",
    "Kênh GT/MT chiếm 65%/35% doanh thu.",
    "Kênh GT giảm 3,4% trong khi MT tăng 11,2%.",
    "Số SKU hoạt động giảm từ 1.250 xuống 980.",
    "Thuế VAT phải nộp tháng 11 là 12,3 tỷ.",
    "LE năm 2026 dự kiến 2.980 tỷ, thấp hơn AOP 2%.",
    "Forecast Q4 giảm 4,5 tỷ so với LE tháng trước.",
    "MUF tăng 0,8 tỷ do sản lượng thấp.",
    "Bridge doanh thu: giá +12,1 tỷ, sản lượng −8,3 tỷ, cơ cấu +1,4 tỷ.",
    "Chương trình khuyến mãi 11/11 đóng góp 15,6 tỷ.",
    "Ngày 20/11/2026 em gửi chi tiết cho CFO.",
    "Kế hoạch FY2627 tăng trưởng 10%.",
    "Kế hoạch FY26 tăng trưởng 10%.",
    "Giai đoạn 2026-2027 cần tuyển 3-5 người mỗi khối.",
    "Chi phí bình quân 12,5 tr/người/tháng.",
    "ROE đạt 18,5%, ROA 9,2%.",
    "Tỷ lệ Opex/DT là 21,3%, CapEx 45 tỷ.",
    "Vòng quay hàng tồn kho 6,2x, DSO 45 ngày.",
    "Kịch bản bi quan: doanh thu giảm 5%.",
    "Kịch bản lạc quan và kịch bản cơ sở.",
    "T10/2026 doanh thu đạt 210 tỷ.",
    "Doanh thu tháng 10/2026 đạt 210 tỷ.",
    "Headcount cuối kỳ 1.312 FTE, tăng 2,5%.",
    "Chi phí marketing chiếm 4,25% doanh thu.",
    "Tỷ suất lợi nhuận 0,05%.",
    "Giá bán bình quân 1.250.000 đ/thùng.",
    "Doanh thu 1.234.567.890 VND.",
    "Tăng 1,05 tỷ, tương đương 0,5%.",
    "Lợi nhuận đạt 105% kế hoạch, 101,5% cùng kỳ.",
    "TỔNG CHI PHÍ NHÂN SỰ TĂNG 12,6%.",
    "DOANH THU THEO KÊNH.",
    "Chi phí khấu hao D&A 12 tỷ, COGS tăng 3%.",
    "NPV dự án 25 tỷ, IRR 15%, thời gian hoàn vốn 3,5 năm.",
    "Working capital tăng 30 tỷ, cash conversion cycle 62 ngày.",
    "Tồn kho cuối Q2 ở mức 1.020 tỷ.",
    "Doanh thu B2B tăng 20%.",
    "Chi phí logistics trên doanh thu 3,8% vs 3,5% AOP.",
    "Khối BU1 và BU2 vượt kế hoạch.",
    "Tăng 15 tỷ (+6,2%).",
    "Đơn giá giảm ~3%.",
    "Tỷ giá USD/VND 25.400.",
    "Doanh thu đạt 12,5 triệu USD.",
    "Lương tháng 13 chi trả trong tháng 1/2027.",
    "CP lãi vay giảm 0,3 tỷ.",
    "Biên EBIT 8,7%, EBIT tăng 1,1 tỷ.",
    "Q3 vs Q2 tăng 4%.",
    "Chi phí khác 0 tỷ.",
    "GM% đạt 32,4%.",
    "Tăng trưởng CAGR 2024–2026 là 9%.",
    "P&L tháng 10 lãi 12 tỷ.",
]
# the demo talk (synthetic HR cost report)
DEMO = [
    "Kính chào anh chị. Em là Clicky, trợ lý AI của anh Tuấn. Em xin trình bày AOP Revised FY2026–2027: "
    "Chi phí nhân sự quản trị.",
    "Tổng chi phí nhân sự AOPR: 186,4 tỷ, tăng 12,6% so với AOP gốc.",
    "Headcount bình quân: 1.248 người, tăng 64 người so với AOP gốc.",
    "Chi phí bình quân mỗi người: 149,4 triệu/năm, tăng 6,7% so với AOP gốc.",
    "Tỷ lệ chi phí nhân sự/DTT: 2,52%, AOP gốc 2,40%.",
    "Phần 1. Tổng quan: chi phí nhân sự tăng nhanh hơn doanh thu.",
    "Thông điệp chính: Chi phí nhân sự AOPR tăng 12,6% trong khi doanh thu chỉ tăng 7,2%. Hai phần ba phần "
    "tăng đến từ tuyển mới ở khối bán hàng.",
    "Khối Bán hàng tăng mạnh nhất, 14,1 tỷ, do kế hoạch tuyển thêm 48 nhân viên bán hàng kênh MT và HRC+KA.",
    "Phần 3. Đề xuất của FP&A.",
    "Đề xuất: giữ tỷ lệ chi phí nhân sự/DTT ở 2,40% như AOP gốc, tức trần 177,5 tỷ; giãn tuyển 30 vị trí "
    "bán hàng sang quý 3 và gắn với doanh thu trên mỗi người.",
    "Cần giảm so với AOPR: 8,9 tỷ.",
    "Anh Tuấn trả lời: Khối CNTT từ 15,8 lên 17,7 tỷ, chủ yếu do dự án ERP chuyển 1,5 tỷ thuê ngoài sang lương.",
]


def test_voice_reads_like_a_finance_person():
    assert doc("Tổng chi phí nhân sự AOPR: 186,4 tỷ, +12,6% so với AOP gốc.") == (
        "Tổng chi phí nhân sự ây-ô-pi điều chỉnh: một trăm tám mươi sáu phẩy bốn tỷ, "
        "tăng mười hai phẩy sáu phần trăm so với ây-ô-pi gốc.")
    assert doc("149,4 triệu/năm; nhân sự/DTT; FP&A") == (
        "một trăm bốn mươi chín phẩy bốn triệu mỗi năm; nhân sự trên doanh thu thuần; ép-pi-en-ây")
    # HRC+KA is one channel ("cộng"), HRC is said "hô-re-ca", headcount is "số nhân sự" (was "héc-cao")
    assert doc("AOP Revised FY2026–2027, kênh MT và HRC+KA, Headcount") == (
        "ây-ô-pi điều chỉnh năm tài chính hai nghìn không trăm hai mươi sáu đến hai nghìn không trăm hai mươi bảy, "
        "kênh em-ti và hô-re-ca cộng cây-ây, số nhân sự")


@pytest.mark.parametrize("text, heard", [
    # number words never glue to a neighbour; a hyphen between figures is a range
    ("tăng 9,1%YoY", "tăng chín phẩy một phần trăm so với cùng kỳ năm trước"),
    ("Giai đoạn 2026-2027 cần tuyển 3-5 người", "Giai đoạn hai nghìn không trăm hai mươi sáu đến hai nghìn không "
     "trăm hai mươi bảy cần tuyển ba đến năm người"),
    # signs: a change is tăng/giảm, a level after ':' and an accounting bracket are âm
    ("Lợi nhuận gộp −5,4 tỷ so với AOP", "Lợi nhuận gộp giảm năm phẩy bốn tỷ so với ây-ô-pi"),
    ("Lợi nhuận gộp -5,4 tỷ so với AOP", "Lợi nhuận gộp giảm năm phẩy bốn tỷ so với ây-ô-pi"),
    ("Lợi nhuận trước thuế: −5,4 tỷ, tức là lỗ.", "Lợi nhuận trước thuế: âm năm phẩy bốn tỷ, tức là lỗ."),
    ("Lợi nhuận tháng 10: -2,1 tỷ.", "Lợi nhuận tháng mười: âm hai phẩy một tỷ."),
    ("Chênh lệch EBITDA: (1.234) triệu", "Chênh lệch ê-bít-đa: âm một nghìn hai trăm ba mươi tư triệu"),
    ("Tăng 15 tỷ (+6,2%).", "Tăng mười lăm tỷ, tức tăng sáu phẩy hai phần trăm."),
    ("Giảm 15 tỷ (−6,2%).", "Giảm mười lăm tỷ, tức giảm sáu phẩy hai phần trăm."),
    ("Bridge: giá +12,1 tỷ, sản lượng −8,3 tỷ", "cầu chênh lệch: giá tăng mười hai phẩy một tỷ, sản lượng giảm "
     "tám phẩy ba tỷ"),
    ("Kế hoạch (2026), mục (1)", "Kế hoạch (hai nghìn không trăm hai mươi sáu), mục (một)"),
    ("Kênh MT 120 tỷ (35%)", "Kênh em-ti một trăm hai mươi tỷ (ba mươi lăm phần trăm)"),
    ("Chênh lệch: (2,1%), quý trước (5,4)", "Chênh lệch: âm hai phẩy một phần trăm, quý trước âm năm phẩy bốn"),
    # dates and periods come before '/' is read as 'trên'
    ("Ngày 20/11/2026 em gửi", "Ngày hai mươi tháng mười một năm hai nghìn không trăm hai mươi sáu em gửi"),
    ("họp 1.12.2026", "họp ngày một tháng mười hai năm hai nghìn không trăm hai mươi sáu"),
    ("trong tháng 1/2027", "trong tháng một năm hai nghìn không trăm hai mươi bảy"),
    ("Doanh thu 10/2026", "Doanh thu tháng mười năm hai nghìn không trăm hai mươi sáu"),
    ("khuyến mãi 11/11 đóng góp", "khuyến mãi ngày mười một tháng mười một đóng góp"),
    ("khuyến mãi 11/11.", "khuyến mãi ngày mười một tháng mười một."),
    ("tức 2/3 phần tăng", "tức hai phần ba phần tăng"),
    ("Doanh thu Q3/2026", "Doanh thu quý ba năm hai nghìn không trăm hai mươi sáu"),
    ("Q3 vs Q2 tăng 4%", "quý ba so với quý hai tăng bốn phần trăm"),
    ("Lũy kế 9M đạt 78%", "Lũy kế chín tháng đạt bảy mươi tám phần trăm"),
    ("H1 2026, H2", "sáu tháng đầu năm hai nghìn không trăm hai mươi sáu, sáu tháng cuối năm"),
    ("T10/2026 và T11", "tháng mười năm hai nghìn không trăm hai mươi sáu và tháng mười một"),
    ("Kế hoạch FY26", "Kế hoạch năm tài chính hai nghìn không trăm hai mươi sáu"),
    ("Kế hoạch FY2627", "Kế hoạch năm tài chính hai nghìn không trăm hai mươi sáu đến hai nghìn không trăm hai mươi bảy"),
    ("Kế hoạch FY26-27", "Kế hoạch năm tài chính hai nghìn không trăm hai mươi sáu đến hai nghìn không trăm hai mươi bảy"),
    # an all-caps Vietnamese heading is words, codes inside it are still spelled or expanded
    ("TỔNG CHI PHÍ NHÂN SỰ TĂNG 12,6%.", "tổng chi phí nhân sự tăng mười hai phẩy sáu phần trăm."),
    ("DOANH THU THEO KÊNH.", "doanh thu theo kênh."),
    ("KÊNH MT VÀ KA", "kênh em-ti và cây-ây"),
    ("BÁO CÁO P&L THÁNG 10", "báo cáo lãi lỗ tháng mười"),
    # codes and pairs
    ("Kênh GT/MT chiếm 65%/35% doanh thu.", "Kênh di-ti và em-ti chiếm sáu mươi lăm phần trăm và ba mươi lăm "
     "phần trăm doanh thu."),
    ("Tỷ lệ Opex/DT", "Tỷ lệ chi phí hoạt động trên doanh thu"),
    ("Khối BU1 và BU2", "Khối bi-giu một và bi-giu hai"),
    ("Doanh thu B2B, B2C", "Doanh thu bi-tu-bi, bi-tu-xi"),
    # native readings
    ("Số HC tăng", "Số nhân sự tăng"),
    ("LE năm 2026", "ước thực hiện năm hai nghìn không trăm hai mươi sáu"),
    ("Em là Clicky", "Em là Cờ-lích-ki"),
    ("Thuế VAT", "Thuế vát"),
    ("12,5 triệu USD; tỷ giá USD/VND", "mười hai phẩy năm triệu đô la Mỹ; tỷ giá đô la Mỹ"),
    ("COGS tăng 3%", "giá vốn hàng bán tăng ba phần trăm"),
    ("Tăng trưởng CAGR", "Tăng trưởng kép bình quân"),
    ("khấu hao D&A 12 tỷ", "khấu hao mười hai tỷ"),
    ("EBITDA margin giảm 120 bps", "biên ê-bít-đa giảm một trăm hai mươi điểm cơ bản"),
    ("margin 5%", "biên lợi nhuận năm phần trăm"),
    ("Biên EBIT 8,7%", "Biên ê-bít tám phẩy bảy phần trăm"),
    ("3,8% vs 3,5%", "ba phẩy tám phần trăm so với ba phẩy năm phần trăm"),
    ("Đơn giá giảm ~3%", "Đơn giá giảm khoảng ba phần trăm"),
    ("Giá 1.250.000 đ/thùng", "Giá một triệu hai trăm năm mươi nghìn đồng mỗi thùng"),
    ("12,5 tr/người/tháng", "mười hai phẩy năm triệu mỗi người mỗi tháng"),
    ("GM% đạt 32,4%", "biên lợi nhuận gộp đạt ba mươi hai phẩy bốn phần trăm"),
    ("Working capital, cash conversion cycle", "vốn lưu động, chu kỳ chuyển đổi tiền mặt"),
])
def test_finance_sentence_is_heard_the_way_a_vietnamese_fpa_person_says_it(text, heard):
    assert doc(text) == heard


def test_numbers_get_spaces_and_percent_never_glues_to_the_next_word():
    assert speakable("3%so") == "ba phần trăm so"
    assert speakable("Q3") == "Q ba"
    assert speakable("3-5 người, lỗ -2,1 tỷ") == "ba đến năm người, lỗ âm hai phẩy một tỷ"
    assert speakable("(1.234)") == "(một nghìn hai trăm ba mươi tư)"
    assert speakable("1,50 điểm %") == "một phẩy năm điểm phần trăm"


def test_bi_quan_is_vietnamese_not_bi_ai():
    t = vi_g2p.table()
    assert "bi" not in vi_g2p.LOANWORDS
    assert vi_g2p.phonemize("bi quan") == [t["bi"][0] + " " + t["quan"][1]]   # no "ai" inserted
    assert vi_g2p.phonemize("ebit") == vi_g2p.phonemize("ê bít")
    assert doc("Power BI") == "Power bi-ai"                             # BI the product is still spelled


def test_breaths_at_sentence_ends_and_only_long_colons():
    assert pieces("Đề xuất: giữ 2,40%; giãn tuyển 30 vị trí.") == ["Đề xuất: giữ 2,40%;", "giãn tuyển 30 vị trí."]
    assert pieces("Kính chào anh chị. Em là Clicky, trợ lý AI của anh Tuấn. Em xin trình bày AOP Revised "
                  "FY2026–2027: Chi phí nhân sự quản trị.") == [
        "Kính chào anh chị.", "Em là Clicky, trợ lý AI của anh Tuấn.",
        "Em xin trình bày AOP Revised FY2026–2027: Chi phí nhân sự quản trị."]
    assert pieces("Tổng chi phí nhân sự AOPR: 186,4 tỷ.") == ["Tổng chi phí nhân sự AOPR: 186,4 tỷ."]
    assert pieces("Đề xuất của bộ phận kế hoạch tài chính: giữ tỷ lệ cũ.") == [
        "Đề xuất của bộ phận kế hoạch tài chính:", "giữ tỷ lệ cũ."]
    assert pieces("Doanh thu 1.248 tỷ. Trích 1,5 tr. mỗi tháng!") == ["Doanh thu 1.248 tỷ.", "Trích 1,5 tr. mỗi tháng!"]
    # a two-word lead ("Phần 2.") voiced on its own came back as "tương lai": it is read into the next sentence
    assert pieces("Phần 2. Chi tiết theo khối.") == ["Phần 2. Chi tiết theo khối."]
    assert doc("Phần 2. Chi tiết theo khối.") == "Phần hai, Chi tiết theo khối."
    assert doc("Kính chào anh chị. Em là Clicky.") == "Kính chào anh chị. Em là Cờ-lích-ki."


def test_every_word_the_finance_reader_adds_is_known_to_the_offline_voice():
    missing = []
    vi_g2p.phonemize(" ".join([*LETTERS.values(), *WORDS.values(), *ENGLISH.values()]).replace("-", " "), missing)
    assert not missing


@pytest.mark.parametrize("line", STRESS + DEMO)
def test_no_word_of_the_fpa_corpus_is_missing_from_the_phoneme_table(line):
    missing = []
    for piece in pieces(line):
        heard = doc(piece)
        assert vi_g2p.phonemize(heard, missing), piece
        assert not any(c.isdigit() for c in heard), heard              # every figure is said in words
    assert missing == [], line


def test_owner_words_file_saved_by_notepad_in_any_encoding(tmp_path, monkeypatch):
    """TU_DOC.txt saved as UTF-8 with BOM keeps its first line; saved as ANSI it does not crash."""
    from present import doc_tai_chinh as d
    f = tmp_path / "TU_DOC.txt"
    monkeypatch.setenv("CLICKY_TU_DOC", str(f))
    f.write_bytes("GT = kênh truyền thống\nKH = khách hàng\n".encode("utf-8-sig"))
    assert d.owner_words()["GT"] == "kênh truyền thống"
    f.write_bytes(b"GT = k\xeanh truy\xea\xccn th\xf4\xecng\n")            # Notepad "ANSI" (cp1258)
    assert d.owner_words()["GT"] == "kênh truyền thống"


def test_underscores_in_report_text_are_not_spoken():
    """A report that shows a code such as __BIEN_BAN__ must not stop the build at the voice check."""
    from present.doc_tai_chinh import doc
    assert "_" not in doc("Mã __BIEN_BAN__ ở cuối trang")
