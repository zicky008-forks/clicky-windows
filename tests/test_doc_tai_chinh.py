"""How Clicky reads an FP&A sentence aloud: numbers, signs, periods, abbreviations and breaths.

The corpus below is synthetic (no company data). It was checked by voicing each line with the offline
Piper voice and reading it back with a Vietnamese speech recogniser."""
import pytest

from audio.tts import vi_g2p
from present.doc_tai_chinh import ENGLISH, LETTERS, WORDS, doc, pieces, unsaid
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
# round 2: signs, periods, Vietnamese accounting abbreviations
STRESS2 = [
    'Lợi nhuận −5,4 tỷ, thấp nhất 3 năm.',
    'Lợi nhuận tháng 10 −2,1 tỷ do trích lập dự phòng.',
    'Biên lợi nhuận giảm từ 15,2% xuống 13,8% (−1,4pp).',
    'Chênh lệch so với AOP: (2,1%).',
    'Doanh thu Q3/26 tăng 5%.',
    'Doanh thu Q3-2026 tăng 5%.',
    'Lũy kế 9T/2026 đạt 1.820 tỷ.',
    'Doanh thu 6T đầu năm đạt 1.200 tỷ.',
    'So sánh 2025A với 2026B và 2026F.',
    'Kế hoạch FY27 đã duyệt.',
    'Tỷ lệ hoàn thành ≥ 95%, tỷ lệ nghỉ việc <5%.',
    'Chi phí tăng gấp x2 so với năm trước.',
    'Biến động ±2% là chấp nhận được.',
    'Từ 01/01/2026 áp dụng mức lương mới.',
    'Hạch toán vào TK 6421 và TK 642.',
    'Chi phí BHXH, BHYT, BHTN tăng 8,5%.',
    'Thuế TNCN và thuế TNDN tháng 10.',
    'Thuế GTGT đầu vào được khấu trừ 3,2 tỷ.',
    'Khấu hao TSCĐ 12 tỷ, CCDC 0,8 tỷ.',
    'Chi phí NVL chiếm 62% giá vốn.',
    'Báo cáo KQKD và BCTC quý 3.',
    'Khối HC-NS có 45 người.',
    'Phòng KD và phòng KT tăng 3 người.',
    'Chi nhánh TP.HCM và HN.',
    'Chi phí MKT tăng 15%.',
    'Tăng trưởng 5% QoQ.',
    'Chi phí SG&A tăng 4%, R&D giảm 2%.',
    'NPAT đạt 120 tỷ, PBT 150 tỷ.',
    'GSV đạt 2.500 tỷ, NSV 2.200 tỷ.',
    'Chi phí OT tăng 1,2 tỷ.',
    'Top 3 khối tăng mạnh nhất.',
    'Lương bình quân 18,5 tr/tháng.',
    'Chi phí tăng 1,2 tỷ đồng/năm.',
    'Đơn giá 25.000 VNĐ/kg.',
    'Tỷ lệ 30/70 giữa cố định và biến đổi.',
    'Chi phí giảm 3–5%.',
    'Biên độ −3% đến −5%.',
    'Doanh thu 12.6% theo kiểu Anh.',
    'Số liệu đến hết ngày 31/10.',
    'Khối Bán hàng: 14,1 tỷ (18,0%).',
    'Chênh lệch: −14,1 tỷ, tức −18,0%.',
    'Headcount tăng 64 FTE, tương đương 5,4%.',
    'Tổng cộng 1.248 HC.',
    'Tỷ lệ chi phí nhân sự/DTT giảm 0,12 điểm %.',
    'Doanh thu tăng 1,2 nghìn tỷ, tương đương 1.200 tỷ.',
    'Ngân sách đào tạo 2,5 tỷ, mới dùng 40%.',
    'EBITDA đạt 320 tỷ, biên EBITDA 14,5%.',
    'Chi phí khác tăng 0,05 tỷ.',
    'Em xin trình bày báo cáo T10.',
    'Số tiền 1.234.567 đ.',
    'Kỳ này so với kỳ trước (T9) tăng 3%.',
    'Tăng 3 bậc lương cho 120 NV.',
    'Khối IT và khối HR.',
    'AI và tự động hóa giảm 20 vị trí.',
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
        "tăng mười hai phẩy sáu phần trăm so với ây-ô-pi ban đầu.")
    assert doc("149,4 triệu/năm; nhân sự/DTT; FP&A") == (
        "một trăm bốn mươi chín phẩy bốn triệu mỗi năm; nhân sự trên doanh thu thuần; ép-pi-en-ây")
    # HRC+KA is one channel ("cộng"), HRC is said "hô-rê-ca", headcount is "số nhân sự" (was "héc-cao"),
    # a year range names the century once
    assert doc("AOP Revised FY2026–2027, kênh MT và HRC+KA, Headcount") == (
        "ây-ô-pi điều chỉnh năm tài chính hai nghìn không trăm hai mươi sáu – hai mươi bảy, "
        "kênh em-ti và hô-rê-ca cộng cây-ây, số nhân sự")


@pytest.mark.parametrize("text, heard", [
    # number words never glue to a neighbour; a hyphen between figures is a range
    ("tăng 9,1%YoY", "tăng chín phẩy một phần trăm so với cùng kỳ năm trước"),
    ("Giai đoạn 2026-2027 cần tuyển 3-5 người", "Giai đoạn hai nghìn không trăm hai mươi sáu – hai mươi bảy cần tuyển ba đến năm người"),
    # signs: a change is tăng/giảm, a level after ':' and an accounting bracket are âm
    ("Lợi nhuận gộp −5,4 tỷ so với AOP", "Lợi nhuận gộp giảm năm phẩy bốn tỷ so với ây-ô-pi"),
    ("Lợi nhuận gộp -5,4 tỷ so với AOP", "Lợi nhuận gộp giảm năm phẩy bốn tỷ so với ây-ô-pi"),
    ("Lợi nhuận trước thuế: −5,4 tỷ, tức là lỗ.", "Lợi nhuận trước thuế: âm năm phẩy bốn tỷ, tức là lỗ."),
    ("Lợi nhuận tháng 10: -2,1 tỷ.", "Lợi nhuận tháng mười: âm hai phẩy một tỷ."),
    ("Chênh lệch EBITDA: (1.234) triệu", "Chênh lệch ê-bít-đa: âm một nghìn hai trăm ba mươi tư triệu"),
    ("Tăng 15 tỷ (+6,2%).", "Tăng mười lăm tỷ, tức tăng sáu phẩy hai phần trăm."),
    ("Giảm 15 tỷ (−6,2%).", "Giảm mười lăm tỷ, tức giảm sáu phẩy hai phần trăm."),
    ("Bridge: giá +12,1 tỷ, sản lượng −8,3 tỷ", "cầu chênh lệch: giá tăng mười hai phẩy một tỷ, sản lượng âm "
     "tám phẩy ba tỷ"),
    ("Kế hoạch (2026), mục (1)", "Kế hoạch (hai nghìn không trăm hai mươi sáu), mục (một)"),
    ("Kênh MT 120 tỷ (35%)", "Kênh em-ti một trăm hai mươi tỷ (ba mươi lăm phần trăm)"),
    ("Chênh lệch: (2,1%), quý trước (5,4)", "Chênh lệch: âm hai phẩy một phần trăm, quý trước (năm phẩy bốn)"),
    ("(1.234)", "âm một nghìn hai trăm ba mươi tư"),                    # the bracket is the whole cell
    # never negative after a figure with a unit, after "chiếm" or after a direction word (shared rule with the build)
    ("chiếm 92,3 tỷ (66,2%) tổng chi phí", "chiếm chín mươi hai phẩy ba tỷ (sáu mươi sáu phẩy hai phần trăm) tổng chi phí"),
    ("đạt 456,7 tỷ (37,0%)", "đạt bốn trăm năm mươi sáu phẩy bảy tỷ (ba mươi bảy phần trăm)"),
    ("Thị phần (35%)", "Thị phần (ba mươi lăm phần trăm)"),
    ("giảm (12,6%)", "giảm (mười hai phẩy sáu phần trăm)"),
    ("giảm (12,6)", "giảm (mười hai phẩy sáu)"),
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
    ("Kế hoạch FY2627", "Kế hoạch năm tài chính hai nghìn không trăm hai mươi sáu – hai mươi bảy"),
    ("Kế hoạch FY26-27", "Kế hoạch năm tài chính hai nghìn không trăm hai mươi sáu – hai mươi bảy"),
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
    ("Tổng cộng 1.248 HC.", "Tổng cộng một nghìn hai trăm bốn mươi tám người."),
    ("Phòng HC", "Phòng hát-xi"),                       # alone HC is the admin department code: spelled
    ("Doanh thu LE năm 2026", "Doanh thu số ước thực hiện năm hai nghìn không trăm hai mươi sáu"),
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


@pytest.mark.parametrize("line", STRESS + STRESS2 + DEMO)
def test_no_word_of_the_fpa_corpus_is_missing_from_the_phoneme_table(line):
    missing = []
    for piece in pieces(line):
        heard = doc(piece)
        assert vi_g2p.phonemize(heard, missing), piece
        assert not any(c.isdigit() for c in heard), heard              # every figure is said in words
        assert unsaid(heard) == [], heard                               # no sign left for the voice to skip
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


@pytest.mark.parametrize("text, heard", [
    # BLOCKER: a minus is "âm" (a level); "giảm" only for a change ("so với" in the clause, after "tức" or "(")
    ("Lợi nhuận là −2,1 tỷ.", "Lợi nhuận là âm hai phẩy một tỷ."),
    ("Lợi nhuận đạt −3,2 tỷ.", "Lợi nhuận đạt âm ba phẩy hai tỷ."),
    ("Dòng tiền thuần −12,5 tỷ.", "Dòng tiền thuần âm mười hai phẩy năm tỷ."),
    ("Lợi nhuận −5,4 tỷ, thấp nhất 3 năm.", "Lợi nhuận âm năm phẩy bốn tỷ, thấp nhất ba năm."),
    ("Lợi nhuận tháng 10 −2,1 tỷ do trích lập dự phòng.",
     "Lợi nhuận tháng mười âm hai phẩy một tỷ do trích lập dự phòng."),
    ("Chênh lệch: −14,1 tỷ, tức −18,0%.", "Chênh lệch: âm mười bốn phẩy một tỷ, tức giảm mười tám phần trăm."),
    ("Chênh lệch: −5,4 tỷ so với AOP.", "Chênh lệch: giảm năm phẩy bốn tỷ so với ây-ô-pi."),
    ("Biên lợi nhuận giảm từ 15,2% xuống 13,8% (−1,4pp).", "Biên lợi nhuận giảm từ mười lăm phẩy hai phần trăm xuống "
     "mười ba phẩy tám phần trăm, tức giảm một phẩy bốn điểm phần trăm."),
    ("Biên độ −3% đến −5%.", "Biên độ âm ba phần trăm đến âm năm phần trăm."),
    ("Chênh lệch so với AOP: (2,1%).", "Chênh lệch so với ây-ô-pi: âm hai phẩy một phần trăm."),
    ("Khối Bán hàng: 14,1 tỷ (18,0%).", "Khối Bán hàng: mười bốn phẩy một tỷ (mười tám phần trăm)."),
    # comparison signs
    ("Tỷ lệ hoàn thành ≥ 95%, tỷ lệ nghỉ việc <5%.", "Tỷ lệ hoàn thành lớn hơn hoặc bằng chín mươi lăm phần trăm, "
     "tỷ lệ nghỉ việc dưới năm phần trăm."),
    ("Nợ ≤ 3 lần, lãi >10%", "Nợ nhỏ hơn hoặc bằng ba lần, lãi trên mười phần trăm"),
    ("Chi phí tăng gấp x2 so với năm trước.", "Chi phí tăng gấp hai so với năm trước."),
    ("Biến động ±2% là chấp nhận được.", "Biến động cộng trừ hai phần trăm là chấp nhận được."),
    # list markers are never "âm một"
    ("Kính đề nghị Ban Giám đốc: (1) tăng ngân sách, (2) giảm tuyển.",
     "Kính đề nghị Ban Giám đốc: thứ nhất, tăng ngân sách, thứ hai, giảm tuyển."),
    ("Chênh lệch: (5) tỷ", "Chênh lệch: âm năm tỷ"),
    # "Ước thực hiện" first in a clip loses its first word; "AOP gốc" at the end loses "gốc"
    ("Ước thực hiện năm 2026 đạt 2.980 tỷ.", "Số ước thực hiện năm hai nghìn không trăm hai mươi sáu đạt hai nghìn "
     "chín trăm tám mươi tỷ."),
    ("LE năm 2026 đạt 2.980 tỷ.", "số ước thực hiện năm hai nghìn không trăm hai mươi sáu đạt hai nghìn chín trăm "
     "tám mươi tỷ."),
    ("Tăng 6,7% so với AOP gốc.", "Tăng sáu phẩy bảy phần trăm so với ây-ô-pi ban đầu."),
    ("AOP gốc 2,40%", "ây-ô-pi gốc hai phẩy bốn phần trăm"),
    # periods before ranges
    ("Doanh thu Q3/26 và Q3-2026", "Doanh thu quý ba năm hai nghìn không trăm hai mươi sáu và quý ba năm hai nghìn "
     "không trăm hai mươi sáu"),
    ("Lũy kế 9T/2026, 6T đầu năm", "Lũy kế chín tháng năm hai nghìn không trăm hai mươi sáu, sáu tháng đầu năm"),
    ("So sánh 2025A với 2026B, 2026F và 2026E.", "So sánh thực hiện năm hai nghìn không trăm hai mươi lăm với kế "
     "hoạch năm hai nghìn không trăm hai mươi sáu, dự báo năm hai nghìn không trăm hai mươi sáu và ước tính năm "
     "hai nghìn không trăm hai mươi sáu."),
    ("CAGR 2024–2026", "tăng trưởng kép bình quân hai nghìn không trăm hai mươi tư – hai mươi sáu"),
    # accounts, splits, English decimals
    ("Hạch toán vào TK 6421 và TK 642.", "Hạch toán vào tài khoản sáu bốn hai một và tài khoản sáu bốn hai."),
    ("Tỷ lệ 30/70 giữa cố định và biến đổi.", "Tỷ lệ ba mươi – bảy mươi giữa cố định và biến đổi."),
    ("Doanh thu 12.6% theo kiểu Anh.", "Doanh thu mười hai phẩy sáu phần trăm theo kiểu Anh."),
    # Vietnamese accounting and HR abbreviations
    ("Chi phí BHXH, BHYT, BHTN, KPCĐ", "Chi phí bảo hiểm xã hội, bảo hiểm y tế, bảo hiểm thất nghiệp, kinh phí công đoàn"),
    ("Thuế TNCN, TNDN, GTGT", "Thuế thu nhập cá nhân, thu nhập doanh nghiệp, giá trị gia tăng"),
    ("TSCĐ, CCDC, NVL, HTK", "tài sản cố định, công cụ dụng cụ, nguyên vật liệu, hàng tồn kho"),
    ("Báo cáo KQKD và BCTC", "Báo cáo kết quả kinh doanh và báo cáo tài chính"),
    ("Phòng KD, KT, NS, HCNS và khối HC-NS", "Phòng kinh doanh, kế toán, nhân sự, hành chính nhân sự và khối hành "
     "chính nhân sự"),
    ("Chi phí MKT ở TP.HCM và HN", "Chi phí ma-két-tinh ở thành phố Hồ Chí Minh và Hà Nội"),
    ("Tăng trưởng 5% QoQ.", "Tăng trưởng năm phần trăm so với quý trước."),
    ("Chi phí SG&A tăng 4%, R&D giảm 2%.", "Chi phí bán hàng và quản lý tăng bốn phần trăm, nghiên cứu và phát triển "
     "giảm hai phần trăm."),
    ("NPAT, PBT, GSV, NSV, GM", "lợi nhuận sau thuế, lợi nhuận trước thuế, doanh thu gộp, doanh thu thuần, biên lợi "
     "nhuận gộp"),
    ("Đơn giá 25.000 VNĐ/kg.", "Đơn giá hai mươi lăm nghìn đồng mỗi ki-lô-gam."),
])
def test_round_two_readings(text, heard):
    assert doc(text) == heard


def test_hore_ca_has_every_syllable_and_a_comma_break_is_decided_on_the_spoken_length():
    assert "rê" in vi_g2p.table()
    # 74 characters as written but well over 100 when said: it gets a breath at the comma
    line = "Doanh thu YTD đạt 2.345,6 tỷ, tăng 9,1% YoY so với cùng kỳ."
    assert len(line) < 100 < len(doc(line))
    assert len(pieces(line)) == 2


def test_a_sign_the_voice_cannot_say_stops_the_build():
    import io
    import wave

    from present import clicky_html, goi_html

    def wav(text):
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000)
            w.writeframes(b"\0\0" * 80)
        return out.getvalue()

    assert unsaid(doc("Biên độ ≠ 3 €")) == ["≠"]                      # € is now read "ơ rô"
    with pytest.raises(goi_html.PackError, match="≠"):
        clicky_html.voice_lines(["Biên độ ≠ 3%."], wav)
    _, starts = clicky_html.voice_lines(["Biên độ ≥ 3%, «tức» là cao."], wav)  # said in words, quotes dropped
    assert starts == [clicky_html.LEAD_IN]
    # markup copied from a report cell has no sound and must not stop the build
    assert unsaid(doc("mã __BIEN_BAN__ và <!--<script> trong ô, [ghi chú] #1")) == []


@pytest.mark.parametrize("text, heard", [
    ("Tổng 1 234 567 triệu.", "Tổng một triệu hai trăm ba mươi tư nghìn năm trăm sáu mươi bảy triệu."),
    ("Tổng 1\u00a0234 triệu", "Tổng một nghìn hai trăm ba mươi tư triệu"),            # no-break space
    ("Tổng 1\u202f234,5 tỷ", "Tổng một nghìn hai trăm ba mươi tư phẩy năm tỷ"),        # narrow no-break space
    ("Tổng 1\u2009234 tỷ", "Tổng một nghìn hai trăm ba mươi tư tỷ"),                  # thin space
    ("Tổng 1 234,5 tỷ", "Tổng một nghìn hai trăm ba mươi tư phẩy năm tỷ"),
    ("Năm 2026 120 tỷ", "Năm hai nghìn không trăm hai mươi sáu một trăm hai mươi tỷ"),   # two figures stay two
])
def test_thousands_split_by_a_space_are_one_number(text, heard):
    assert doc(text) == heard
    assert speakable(text) == heard


def test_the_company_word_file_wins_over_the_built_in_reading(tmp_path, monkeypatch):
    words = tmp_path / "TU_DOC.txt"
    words.write_text("# ghi chú\nAOP = kế hoạch năm\n", encoding="utf-8")
    monkeypatch.setenv("CLICKY_TU_DOC", str(words))
    assert doc("Tăng 6,7% so với AOP.") == "Tăng sáu phẩy bảy phần trăm so với kế hoạch năm."


def test_apostrophes_quotes_and_currency_signs_are_spoken_not_blocking():
    """A possessive apostrophe or € used to stop the build at the 'unsayable sign' check."""
    from present.doc_tai_chinh import doc, unsaid
    cases = {"Khối Sales' chi phí": "Sales chi phí", "Q3’26 đạt $12": "quý ba năm hai nghìn",
             "Giá 12 €/kg": "mười hai ơ rô", "tăng 5$ mỗi tấn": "năm đô la mỗi tấn"}
    for text, heard in cases.items():
        said = doc(text)
        assert heard in said and not unsaid(said), (text, said)
    assert "mười hai đô la" in doc("đạt $12")
