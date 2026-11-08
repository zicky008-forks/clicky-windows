"""A general ledger with segmented accounts read through the user's map file, on two made-up layouts (5 segments in
one column with Dr/Cr; 10 segments in their own columns with one signed amount). The layout, delimiter, roles and
codes all come from the map: the same October reads the same in both."""
from decimal import Decimal

import pytest

import segment_samples as sg
from engine import bod_inputs, fpa, segment_gl
from engine.trial_balance import read_trial_balance

M = sg.M


@pytest.fixture(params=["A", "B"])
def bundle(request, tmp_path):
    return bod_inputs.load(sg.folder(tmp_path / request.param, request.param), fy_start=1)


def _line(v, label):
    return next(l for l in v.month if l.label == label)


def test_the_cost_matrix_reads_the_same_from_either_layout(bundle):
    v = fpa.compute(bundle.gl, [], fy_start=1, config=bundle.config)
    assert v.period == "2026-10"
    for dept, element, vas, amount in sg.OPEX:
        names = {"KD": "Kinh doanh", "MKT": "Marketing", "HC": "Hành chính"}
        el = {"LUONG": "Lương", "QC": "Quảng cáo", "THUE": "Thuê văn phòng"}[element]
        line = _line(v, f"{names[dept]} · {el}")
        down = sg.WRITTEN_DOWN[3] if (dept, element) == sg.WRITTEN_DOWN[:2] else 0
        assert line.actual == amount - down and line.accounts == {vas} and isinstance(line.actual, Decimal)
    assert _line(v, "Hành chính · Thuê văn phòng").actual == 120 * M           # September stays in September
    assert sum(l.actual for l in v.ytd if l.element == "THUE") == 240 * M


def test_revenue_cogs_and_balance_sheet_lines_are_postings_not_costs(bundle):
    assert all(e.account.startswith(("641", "642")) for e in bundle.gl)
    by = {}
    for p in bundle.postings:
        if p.period == "2026-10":
            d, c = by.get(p.account, (0, 0))
            by[p.account] = (d + p.dr, c + p.cr)
    assert by["5111"] == (0, sg.REVENUE) and by["632"] == (sg.COGS, 0)


def test_codes_the_map_lacks_are_named_and_still_counted(bundle):
    said = [x for g in bundle.segment_gls for x in g.said()]
    assert len(said) == 1 and said[0].startswith("Mã tài khoản chưa có trong file ánh xạ: 641")
    v = fpa.compute(bundle.gl, [], fy_start=1, config=bundle.config)
    unknown = v.unknown["bộ phận"]
    assert len(unknown) == 1 and unknown[0] in ("2999", "D99")
    assert next(l for l in v.month if l.dept == unknown[0]).actual == sg.UNKNOWN_DEPT
    assert next(l for l in v.month if l.element.startswith("TK 641")).actual == sg.UNKNOWN_ACCOUNT


def test_every_row_is_used_or_skipped_with_its_reason(bundle):
    _f, role, used, skipped = next(r for r in bundle.receipt if r[1] == "Sổ cái theo tổ hợp tài khoản")
    reasons = sorted(reason for _n, reason in skipped)
    if bundle.segment_map.delimiter:                                   # layout B
        assert reasons == ["không có số tiền"]
    else:
        assert reasons == ["dòng tổng", "không có số tiền", "tổ hợp tài khoản có 3 phân đoạn, file ánh xạ ghi 5"]
    in_figures = len(bundle.postings)
    assert in_figures + len([r for r in skipped if r[1] != "dòng tổng"]) == used


def test_the_cost_lines_match_the_books(bundle):
    """The account a code maps to (and an unmapped 641 code, under the books' 6419) adds up to the trial balance."""
    debits = dict(sg.october_debits(), **{"6419": sg.UNKNOWN_ACCOUNT})
    rows = [["Số hiệu TK", "Tên tài khoản", "Số dư đầu kỳ Nợ", "Số dư đầu kỳ Có", "Phát sinh Nợ", "Phát sinh Có",
             "Số dư cuối kỳ Nợ", "Số dư cuối kỳ Có"]]
    for a, d in sorted(debits.items()):
        rows.append([a, "", 0, 0, d, d, 0, 0])
    m = fpa.match_books(bundle.gl, read_trial_balance(rows), "2026-10")
    assert m.ok, [(g.account, g.books, g.detail) for g in m.gaps]
    assert next(l for l in m.lines if l.account == "6418").reductions == sg.WRITTEN_DOWN[3]


def test_the_delimiter_is_found_from_the_data_when_the_map_does_not_say_it(tmp_path):
    b = bod_inputs.load(sg.folder(tmp_path / "A", "A"), fy_start=1)
    assert b.segment_map.delimiter == "" and b.segment_gls[0].delimiter == "-"


def test_a_map_row_can_name_its_segment_by_name_or_by_role_and_a_prefix_matches(tmp_path):
    m = segment_gl.read_map(sg.write_xlsx(tmp_path / "AnhXa.xlsx", {"Segments": sg.B_SEGMENTS, "Map": sg.B_MAP}))
    assert m.find("account", "40155")["tk"] == "5111" and m.find("account", "60110")["yeu_to"] == "LUONG"
    assert m.find("channel", "GT1")["ten"] == "Kênh truyền thống" and m.find("account", "99999") is None


def test_a_closing_journal_is_kept_for_the_books_and_never_counted_as_a_cost(tmp_path):
    rows = sg.ledger_a()
    rows.insert(-1, sg._a("2100", "641100", cr=420 * M, text="Kết chuyển chi phí", category="Closing"))
    rows.insert(-1, sg._a("3100", "642100", dr=1 * M, text="Kết chuyển chi phí trả trước", category="Manual"))
    d = tmp_path / "A"
    sg.folder(d, "A")
    sg.write_csv(d / "GL_T10_2026.csv", rows)
    b = bod_inputs.load(d, fy_start=1)
    v = fpa.compute(b.gl, [], fy_start=1, config=b.config)
    assert _line(v, "Kinh doanh · Lương").actual == 420 * M                 # the closing entry is not a cost
    assert _line(v, "Hành chính · Lương").actual == 311 * M                 # 'kết chuyển' in a description is
    assert any(p.contra == "911" and p.cr == 420 * M for p in b.postings)


# ── Shapes an ERP export really has ─────────────────────────────────────────

def _load_a(tmp_path, extra):
    return bod_inputs.load(sg.folder(tmp_path / "A", "A", extra), fy_start=1)


def test_a_journal_that_posts_to_911_is_closing_whatever_its_category(tmp_path):
    close = [sg._a("0000", "511100", dr=sg.REVENUE, journal="JE-KC", category="Manual", text="Kết chuyển"),
             sg._a("0000", "911000", cr=sg.REVENUE, journal="JE-KC", category="Manual", text="Kết chuyển"),
             sg._a("0000", "911000", dr=420 * M, journal="JE-KC2", category="Manual", text="Kết chuyển"),
             sg._a("2100", "641100", cr=420 * M, journal="JE-KC2", category="Manual", text="Kết chuyển")]
    b = _load_a(tmp_path, close)
    v = fpa.compute(b.gl, [], fy_start=1, config=b.config)
    assert _line(v, "Kinh doanh · Lương").actual == 420 * M                 # not zeroed by the closing entry
    closing = [p for p in b.postings if p.contra == "911"]
    assert len(closing) == 4 and {p.account for p in closing} == {"5111", "911", "6411"}


def test_a_provision_journal_in_segment_form_is_a_known_cause_not_a_warning(tmp_path):
    from engine import sales_pnl
    from engine.bod_inputs import Sale
    provision = [sg._a("0000", "632000", dr=3 * M, journal="JE-DP", text="Trích lập dự phòng"),
                 sg._a("0000", "229400", cr=3 * M, journal="JE-DP", text="Trích lập dự phòng")]
    b = _load_a(tmp_path, provision)
    sales = [Sale("2026-10", Decimal(sg.REVENUE), sku="X1", qty=Decimal(1), cogs=Decimal(sg.COGS), where="AR!2")]
    m = sales_pnl.match_gl(sales, b.postings, "2026-10", b.config)
    cogs = next(l for l in m.lines if l.code == "cogs")
    assert m.ok and cogs.causes == [("trích lập dự phòng giảm giá hàng tồn kho (Có TK 2294)", -3 * M)]
    assert m.warnings() == [] and m.notes()[0].startswith("Lưu ý: giá vốn hàng bán trên sổ cao hơn")


def test_adjusting_and_13th_periods_are_named_never_merged_into_a_month(tmp_path):
    adj = [sg._a("2100", "641100", dr=9 * M, period="ADJ-26", text="Điều chỉnh"),
           sg._a("3100", "642700", dr=4 * M, period="DEC-26 ADJ", text="Điều chỉnh"),
           sg._a("2200", "641800", dr=6 * M, period="13-2026", text="Kỳ 13")]
    b = _load_a(tmp_path, adj)
    v = fpa.compute(b.gl, [], fy_start=1, config=b.config)
    assert _line(v, "Kinh doanh · Lương").actual == 420 * M and all(e.period <= "2026-12" for e in b.gl)
    said = b.segment_gls[0].said()
    assert said[:3] == ["Kỳ điều chỉnh 13-2026: 1 dòng, phát sinh Nợ 6.000.000 đ, em không gộp vào tháng nào; chị cho "
                        "biết gộp vào tháng nào",
                        "Kỳ điều chỉnh ADJ-26: 1 dòng, phát sinh Nợ 9.000.000 đ, em không gộp vào tháng nào; chị cho "
                        "biết gộp vào tháng nào",
                        "Kỳ điều chỉnh DEC-26 ADJ: 1 dòng, phát sinh Nợ 4.000.000 đ, em không gộp vào tháng nào; chị "
                        "cho biết gộp vào tháng nào"]
    assert bod_inputs.month_key("13-2026") is None and bod_inputs.month_key("10-2026") == "2026-10"


def test_an_account_mapping_file_is_not_a_segment_map(tmp_path):
    d = tmp_path / "F"
    d.mkdir()
    sg.write_xlsx(d / "mapping TK cũ mới.xlsx", {"Map": [["TK cũ", "TK mới"], ["6411", "64111"]]})
    b = bod_inputs.load(d, fy_start=1)
    assert b.segment_map is None and not any("Ánh xạ phân đoạn" in r[1] for r in b.receipt)
    assert any(name == "mapping TK cũ mới.xlsx" for name, _why in b.unused)          # left in the normal flow


def test_two_segment_maps_are_both_named_and_neither_used(tmp_path):
    d = sg.folder(tmp_path / "A", "A")
    sg.write_xlsx(d / "Segment mapping v2.xlsx", {"Segments": sg.B_SEGMENTS, "Map": sg.B_MAP})
    b = bod_inputs.load(d, fy_start=1)
    assert b.segment_map is None and b.segment_gls == []
    named = [r for r in b.receipt if r[1].startswith("Ánh xạ phân đoạn, chưa dùng: thư mục có 2 file ánh xạ")]
    assert sorted(r[0] for r in named) == ["AnhXa_PhanDoan.xlsx", "Segment mapping v2.xlsx"]
