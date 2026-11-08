"""A made-up FMCG sugar company, shaped like a real FP&A case, for the four-stage rehearsal (bhc_e2e):
drop the month's folder -> close the month -> review the draft BOD pack -> present it and take questions.
Practice (synthetic) numbers only: no real company's structure, codes or figures.

    python tools/dien_tap/bhc_case.py --tao <thu_muc>            (write the case)
    python tools/dien_tap/bhc_case.py --tao <thu_muc> --kiem     (write it, then check all four stages
                                                                  without Excel or PowerPoint)

The folder holds what the finance team would drop in one go:
  TB_T09.xlsx, TB_T10.xlsx            two trial balances; 5111 credit = the sales file's October gross,
                                      521 debit = its returns, 641/642 = the cost detail's October postings
  BaoCao_QuanTri_T10.xlsx             the monthly report workbook Clicky fills
  Mastersale_T07-T10.csv, GL_*.csv, KeHoach_*, NamTruoc_*, TyGia_*, CatGiam_*, DanhMuc_CauHinh.xlsx,
  Pack_BOD_T10_ban_nhap.xlsx          the BOD review case (tests/bod_case.py) with its planted mistakes
  trinh_bay/                          kich_ban.yaml, hoi_dap.yaml, kiem_tra_truoc.yaml, so_lieu.xlsx (Tong_quan),
                                      BOD_T10.pptx: the talk, figures read from the numbers the month close made
and dap_an.json, the expected result of every stage, for the robot.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

PERIOD, PREVIOUS = (2026, 10), (2026, 9)
REPORT = "BaoCao_QuanTri_T10.xlsx"
TALK = "trinh_bay"
COMPANY = "CÔNG TY CỔ PHẦN ĐƯỜNG SÔNG XANH (DỮ LIỆU GIẢ LẬP)"
D = Decimal


def _r0(x) -> Decimal:
    return D(x).quantize(D(1), rounding=ROUND_HALF_UP)


# ── stage 2: the trial balances agree with the sales file and the cost detail ─────────────────

def _sales(folder: Path, period: tuple):
    from engine import sources
    files = sources.find_order_files(folder)
    t = sources.truth(files, period)
    return t.gross, t.returns


def _costs(folder: Path, period: tuple) -> dict:
    """{account: amount} the cost detail posts in the month (both divisions; next month's reversals excluded)."""
    path = next(folder.glob("GL_*.csv"))
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    h = {name: i for i, name in enumerate(rows[0])}
    out = {}
    for r in rows[1:]:
        if not r[h["Thang"]].strip().isdigit() or int(r[h["Thang"]]) != period[1] or not r[h["Ngay_CT"]].startswith(str(period[0])):
            continue
        acct = r[h["TK_No"]].strip()
        out[acct] = out.get(acct, D(0)) + D(r[h["So_tien_VND"]])
    return out


NAMES = {"111": "Tiền mặt", "112": "Tiền gửi ngân hàng", "131": "Phải thu của khách hàng", "155": "Thành phẩm",
         "331": "Phải trả cho người bán", "333": "Thuế và các khoản phải nộp Nhà nước", "3334": "Thuế thu nhập doanh nghiệp",
         "411": "Vốn đầu tư của chủ sở hữu", "421": "Lợi nhuận sau thuế chưa phân phối",
         "4212": "LNST chưa phân phối năm nay", "511": "Doanh thu bán hàng và cung cấp dịch vụ",
         "5112": "Doanh thu bán thành phẩm", "521": "Các khoản giảm trừ doanh thu", "632": "Giá vốn hàng bán",
         "641": "Chi phí bán hàng", "6411": "Chi phí nhân viên bán hàng", "6417": "Chi phí dịch vụ mua ngoài",
         "6418": "Chi phí bằng tiền khác", "642": "Chi phí quản lý doanh nghiệp", "6421": "Chi phí nhân viên quản lý",
         "6422": "Chi phí vật liệu quản lý", "6428": "Chi phí bằng tiền khác", "821": "Chi phí thuế TNDN",
         "8211": "Chi phí thuế TNDN hiện hành", "911": "Xác định kết quả kinh doanh"}


def month_figures(folder: Path, period: tuple) -> dict:
    gross, returns = _sales(folder, period)
    costs = _costs(folder, period)
    cogs = _r0(gross * D("0.72"))
    sell = sum((v for a, v in costs.items() if a.startswith("641")), D(0))
    admin = sum((v for a, v in costs.items() if a.startswith("642")), D(0))
    pbt = gross - returns - cogs - sell - admin
    tax = _r0(max(pbt, D(0)) * D("0.2"))
    return {"gross": gross, "returns": returns, "net": gross - returns, "cogs": cogs, "costs": costs, "sell": sell,
            "admin": admin, "pbt": pbt, "tax": tax, "lnst": pbt - tax}


def _movements(fig: dict) -> dict:
    """{account: (debit, credit)} for one month, every entry double-sided, books closed to 911 and 4212."""
    g, ret, cogs, tax, lnst = fig["gross"], fig["returns"], fig["cogs"], fig["tax"], fig["lnst"]
    cost = fig["sell"] + fig["admin"]
    collected, paid, made = _r0(g * D("0.85")), _r0((cost + cogs) * D("0.9")), cogs
    mv = {a: [D(0), D(0)] for a in NAMES}

    def post(dr, cr, amount):
        mv[dr][0] += amount
        mv[cr][1] += amount
    post("131", "5112", g)
    post("521", "131", ret)
    post("632", "155", cogs)
    for acct, amount in fig["costs"].items():
        mv.setdefault(acct, [D(0), D(0)])
        post(acct, "331", amount)
    post("8211", "3334", tax)
    post("112", "131", collected)
    post("331", "112", paid)
    post("155", "331", made)
    post("5112", "911", g)
    post("911", "521", ret)
    post("911", "632", cogs)
    for acct, amount in fig["costs"].items():
        post("911", acct, amount)
    post("911", "8211", tax)
    if lnst >= 0:
        post("911", "4212", lnst)
    else:
        post("4212", "911", -lnst)
    return mv


PARENTS = {"333": ("3334",), "421": ("4212",), "511": ("5112",), "641": ("6411", "6417", "6418"),
           "642": ("6421", "6422", "6428"), "821": ("8211",)}
CREDIT_SIDE = {"331", "333", "3334", "411", "421", "4212"}


def _tb_rows(month: tuple, opening: dict, mv: dict) -> tuple:
    """(rows as Case 01 lays them out, closing balances)."""
    leaves = sorted(a for a in mv if a not in PARENTS)
    closing = {a: opening.get(a, D(0)) + mv[a][0] - mv[a][1] for a in leaves}
    for a in opening:
        closing.setdefault(a, opening[a])
    rows = [[COMPANY], ["BẢNG CÂN ĐỐI TÀI KHOẢN"], [f"Tháng {month[1]} năm {month[0]}"], [],
            ["Số hiệu TK", "Tên tài khoản", "Số dư đầu kỳ", None, "Số phát sinh", None, "Số dư cuối kỳ", None],
            [None, None, "Nợ", "Có", "Nợ", "Có", "Nợ", "Có"]]

    def side(x):
        return (int(x), 0) if x >= 0 else (0, int(-x))

    def line(a, kids):
        o = sum((opening.get(k, D(0)) for k in kids), D(0))
        dr = sum((mv[k][0] for k in kids if k in mv), D(0))
        cr = sum((mv[k][1] for k in kids if k in mv), D(0))
        c = sum((closing.get(k, D(0)) for k in kids), D(0))
        return [a, NAMES.get(a, a), *side(o), int(dr), int(cr), *side(c)]
    totals = [D(0)] * 6
    for a in sorted(set(leaves) | set(PARENTS), key=lambda x: (x[:3], len(x), x)):
        if len(a) > 3 and a[:3] in PARENTS:
            rows.append(line(a, (a,)))
            continue
        kids = PARENTS.get(a, (a,))
        if not any(k in mv or k in opening for k in kids):
            continue
        row = line(a, kids)
        rows.append(row)
        totals = [t + D(v) for t, v in zip(totals, row[2:])]
    rows.append(["Tổng cộng", None] + [int(t) for t in totals])
    return rows, closing


def _xlsx(path: Path, sheets: dict) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    wb.save(path)


def write_books(folder: Path) -> dict:
    sep, oct_ = month_figures(folder, PREVIOUS), month_figures(folder, PERIOD)
    opening_sep = {"111": D(350_000_000), "112": D(9_000_000_000), "131": D(6_500_000_000), "155": D(8_200_000_000),
                   "331": D(-5_100_000_000), "3334": D(-300_000_000), "411": D(-12_000_000_000)}
    opening_sep["4212"] = -sum(opening_sep.values(), D(0))
    rows9, closing9 = _tb_rows(PREVIOUS, opening_sep, _movements(sep))
    rows10, _ = _tb_rows(PERIOD, closing9, _movements(oct_))
    _xlsx(folder / "TB_T09.xlsx", {"TB_T09": rows9})
    _xlsx(folder / "TB_T10.xlsx", {"TB_T10": rows10})
    _xlsx(folder / REPORT, {"BaoCao_T10": [
        ["Báo cáo quản trị tháng · số liệu giả lập, không phải của công ty thật"],
        ["Công ty Cổ phần Đường Miền Tây (giả lập) · Tháng 10/2026. Clicky đọc các file trong thư mục này."]]})
    return {"t09": sep, "t10": oct_}


# ── stage 4: the talk, its figures read from the month close ──────────────────────────────────

def _bn(x: Decimal) -> float:
    return float((D(x) / D(1_000_000_000)).quantize(D("0.0001"), rounding=ROUND_HALF_UP))


def write_talk(folder: Path, fig: dict) -> dict:
    import yaml
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from present.vn_format import fmt
    t10 = fig["t10"]
    margin = float((t10["lnst"] / t10["net"]).quantize(D("0.0001"), rounding=ROUND_HALF_UP)) if t10["net"] else 0.0
    summary = [("dtt_t10", _bn(t10["net"]), "bn"), ("cp_ban_hang_t10", _bn(t10["sell"]), "bn"),
               ("cp_quan_ly_t10", _bn(t10["admin"]), "bn"), ("lnst_t10", _bn(t10["lnst"]), "bn"),
               ("bien_lnst_t10", margin, "pct2")]
    out = folder / TALK
    out.mkdir(parents=True, exist_ok=True)
    _xlsx(out / "so_lieu.xlsx", {"Tong_quan": [["TỔNG QUAN T10/2026 (giả lập)"], ["Khoá", "Giá trị", "Định dạng"], []]
                                 + [list(x) for x in summary]})
    shown = {k: fmt(v, kind) for k, v, kind in summary}

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)

    def slide(title: str, boxes: list):
        s = prs.slides.add_slide(prs.slide_layouts[5])
        s.shapes.title.text = title
        for i, text in enumerate(boxes):
            tb = s.shapes.add_textbox(Inches(0.8 + 4.2 * i), Inches(2.6), Inches(3.8), Inches(1.6))
            tb.text_frame.text = text
            tb.text_frame.paragraphs[0].runs[0].font.size = Pt(36)
    slide("Báo cáo quản trị tháng 10/2026", [])
    slide("Doanh thu thuần tháng 10", [shown["dtt_t10"]])
    slide("Chi phí bán hàng và quản lý", [shown["cp_ban_hang_t10"], shown["cp_quan_ly_t10"]])
    slide("Lợi nhuận sau thuế", [shown["lnst_t10"], shown["bien_lnst_t10"]])
    prs.save(out / "BOD_T10.pptx")

    script = {"meta": {"mo_dau": "Em xin trình bày báo cáo quản trị tháng 10.", "ket_thuc": "Em xin hết phần trình bày.",
                       "thoi_luong_muc_tieu_phut": 3},
              "slides": [
                  {"so": 1, "loi_noi": "{slide:title}.", "chi_vao": [{"shape": "tiêu đề"}], "dung_giay": 1},
                  {"so": 2, "loi_noi": "Doanh thu thuần tháng 10 đạt {tq:dtt_t10}.",
                   "chi_vao": [{"shape_chua": "{tq:dtt_t10}"}], "dung_giay": 1},
                  {"so": 3, "loi_noi": "Chi phí bán hàng {tq:cp_ban_hang_t10}, chi phí quản lý {tq:cp_quan_ly_t10}.",
                   "chi_vao": [{"shape_chua": "{tq:cp_ban_hang_t10}"}, {"shape_chua": "{tq:cp_quan_ly_t10}"}], "dung_giay": 1},
                  {"so": 4, "loi_noi": "Lợi nhuận sau thuế {tq:lnst_t10}, biên lợi nhuận {tq:bien_lnst_t10}.",
                   "chi_vao": [{"shape_chua": "{tq:lnst_t10}"}, {"shape_chua": "{tq:bien_lnst_t10}"}], "dung_giay": 1}]}
    bank = {"cau_hoi": [
        {"id": "Q01", "hoi_mau": "Doanh thu thuần tháng 10 là bao nhiêu?",
         "tu_khoa": {"va": [["doanh thu"]]}, "tra_loi": "Doanh thu thuần tháng 10 là {tq:dtt_t10}.", "nhay_slide": 2},
        {"id": "Q02", "hoi_mau": "Chi phí bán hàng tháng này bao nhiêu?",
         "tu_khoa": {"va": [["chi phi ban hang", "ban hang"]]}, "tra_loi": "Chi phí bán hàng là {tq:cp_ban_hang_t10}.",
         "nhay_slide": 3},
        {"id": "Q03", "hoi_mau": "Lợi nhuận sau thuế được bao nhiêu?",
         "tu_khoa": {"va": [["loi nhuan", "lnst"]]}, "tra_loi": "Lợi nhuận sau thuế là {tq:lnst_t10}, "
                                                                  "biên {tq:bien_lnst_t10}.", "nhay_slide": 4}]}
    checks = {"kiem_tra": [{"loai": "so_slide", "bang": 4}, {"loai": "co_sheet", "ten": ["Tong_quan"]},
                           {"loai": "placeholder"}, {"loai": "so_tren_slide", "slide": [2, 3, 4], "dinh_dang": "bn"},
                           {"loai": "an_toan"}]}
    for name, data in (("kich_ban.yaml", script), ("hoi_dap.yaml", bank), ("kiem_tra_truoc.yaml", checks)):
        (out / name).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"summary": {k: v for k, v, _ in summary}, "shown": shown,
            "questions": [(q["id"], q["hoi_mau"]) for q in bank["cau_hoi"]], "slides": 4}


def make(folder: Path, plant: bool = True) -> dict:
    """Write the whole case; returns what every stage must find (also saved as dap_an.json)."""
    from tests.bod_case import make_case
    folder = Path(folder)
    answers = make_case(folder, plant=plant)
    fig = write_books(folder)
    talk = write_talk(folder, fig)
    t10 = fig["t10"]
    key = {"report": REPORT, "period": "2026-10",
           "close": {"gross": str(t10["gross"]), "returns": str(t10["returns"]), "net": str(t10["net"]),
                     "lnst": str(t10["lnst"])},
           "bod": [{**a, "expected": str(a["expected"])} for a in answers],
           "talk": {"spec": TALK, "xlsx": f"{TALK}/so_lieu.xlsx", "deck": f"{TALK}/BOD_T10.pptx",
                    "questions": talk["questions"], "slides": talk["slides"], "shown": talk["shown"]}}
    (folder / "dap_an.json").write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")
    return key


# ── the four stages, checked without Excel or PowerPoint ──────────────────────────────────────

def check(folder: Path):
    """Run every stage's engine on the folder and grade it against dap_an.json (present_robot.Result)."""
    sys.path.insert(0, str(HERE))
    from present_robot import Result
    folder = Path(folder)
    key = json.loads((folder / "dap_an.json").read_text(encoding="utf-8"))
    res = Result()
    from office import drop
    got, problem = drop.source_folder([str(folder)])
    report = drop.report_workbook(folder)
    res.add("1. Thả thư mục: nhận thư mục, chọn đúng file báo cáo", got is not None and report is not None
            and report.name == key["report"], problem or f"file báo cáo: {report.name if report else 'không chọn được'}")

    from engine import sources
    from engine.cli import read_rows
    tbs = sources.find_trial_balances(folder, read_rows)
    names = [n for n, _, _ in tbs]
    tb10 = next((tb for n, rows, tb in tbs if sources.period_of(rows) == PERIOD), None)
    gross, returns = _sales(folder, PERIOD)
    ok = tb10 is not None and len(tbs) == 2
    detail = f"sổ cân đối: {', '.join(names) or 'không có'}"
    if tb10 is not None:
        rev = sources.ledger_movement(tb10, "511", "cr")
        ret = sources.ledger_movement(tb10, "521", "dr")
        profit = sources.ledger_movement(tb10, "4212", "cr") - sources.ledger_movement(tb10, "4212", "dr")
        ok = ok and rev == gross and ret == returns and profit == D(key["close"]["lnst"])
        detail += f"; Có 511 {rev:,.0f} / bán hàng {gross:,.0f}; Nợ 521 {ret:,.0f} / trả lại {returns:,.0f}; LNST {profit:,.0f}"
    res.add("2. Chốt số: sổ cân đối khớp file bán hàng, LNST đúng", ok, detail)

    from engine import bod_check
    reviews = bod_check.review(folder)
    found = reviews[0].findings if reviews else []
    want = key["bod"]
    hit = sum(1 for a in want if any(x.where == a["where"] and x.group == a["group"] for x in found))
    extra = len(found) - hit
    res.add(f"3. Soát bộ BOD: bắt {hit}/{len(want)} lỗi cài sẵn, {extra} báo thêm", hit == len(want) and extra <= 1,
            "; ".join(f"nhóm {x.group} {x.where}" for x in found if not any(x.where == a["where"] for a in want)))

    import yaml
    from present import preflight
    from present.placeholders import OpenpyxlSource, Resolver
    from present.rehearse import PptxDeck
    from present.talk import Minutes, Stop, Talk
    talk_dir = folder / key["talk"]["spec"]
    load = lambda n: yaml.safe_load((talk_dir / n).read_text(encoding="utf-8")) or {}
    script, bank, checks = load("kich_ban.yaml"), load("hoi_dap.yaml"), load("kiem_tra_truoc.yaml")
    resolver = Resolver(OpenpyxlSource(str(folder / key["talk"]["xlsx"])))
    deck = PptxDeck(str(folder / key["talk"]["deck"]))
    rep = preflight.run(checks.get("kiem_tra", []), resolver, deck, script, bank, {"synthetic": True})
    minutes, pointed = Minutes(), []
    talk = Talk(script, bank, resolver, deck, say=lambda shown, spoken: None,
                point=lambda rect, label: pointed.append(label), wait=lambda s: None, whatif=None, minutes=minutes)
    stop = ""
    routed = 0
    if rep.ok:
        try:
            talk.present(None)
            for qid, q in key["talk"]["questions"]:
                r = talk.answer(q)
                routed += r.get("id") == qid
        except Stop as exc:
            stop = str(exc)
    want_points = sum(len(s.get("chi_vao") or []) for s in script.get("slides", []))
    res.add("4. Thuyết trình + hỏi đáp: kiểm tra trước đạt, chỉ đúng hình, trả lời đúng câu",
            rep.ok and not stop and not minutes.stops and len(pointed) == want_points
            and routed == len(key["talk"]["questions"]),
            "; ".join([l for l in rep.lines() if l.startswith("KHÔNG")] + ([f"dừng: {stop}"] if stop else [])
                      + minutes.stops + [f"chỉ {len(pointed)}/{want_points} hình, trả lời đúng {routed}/"
                                         f"{len(key['talk']['questions'])} câu"]))
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tao", required=True, help="thư mục sẽ ghi case giả lập")
    ap.add_argument("--sach", action="store_true", help="bộ BOD sạch (không cài lỗi)")
    ap.add_argument("--kiem", action="store_true", help="kiểm 4 bước bằng engine, không cần Excel/PowerPoint")
    a = ap.parse_args(argv)
    folder = Path(a.tao)
    make(folder, plant=not a.sach)
    print(f"Đã tạo case giả lập: {folder}")
    if not a.kiem:
        return 0
    res = check(folder)
    print(res.markdown("Kiểm 4 bước (không cần Excel)"))
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
